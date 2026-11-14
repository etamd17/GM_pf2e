#!/usr/bin/env python3
"""Export current SQL ownership into a new, validated JSON rollback directory.

Stop all application writers first, keep them stopped until rollback is complete,
and take ordinary database and DATA_DIR backups. Then run::

    python tools/export_transactional_runtime.py --source /data \
        --output-dir /rollback-new --quiesced

DATABASE_URL supplies the database connection. This tool does not change the
source, database, runtime backend, or deployment. It exports the transactional
subset and registered character payloads, not binary assets or all game state.
Merge those separately from the quiesced DATA_DIR backup during operator-led
rollback. The output contains password hashes and must remain private.
On Windows, use an output parent with restrictive ACLs already configured;
Unix-style mode bits do not establish or verify Windows access permissions.

The JSON tree is checked with the migration planner. PR5 drafts and independent
workflow receipts are resumable private records. Unrelated SQL-only history is
retained in a private sidecar; the application/importer do not replay that
sidecar. Returning to SQL still requires separate history reconciliation or a
retained/restored database backup for that history.

D&D character files stay authoritative until PE-15: every located file under a
dnd5e campaign's dnd5e/characters/ is copied as it is, with no SQL ownership
overlay, and differences from SQL are listed in the manifest's ``dnd5e_drift``.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core.persistence import Database, Invitation, MigrationRun
from core.character_layout import DND5E_STORAGE, LEGACY_STORAGE_DIRS, is_dnd5e_character_id
from core.character_workflows.types import WorkflowError
from tools import migrate_transactional_store as migration


HISTORY_FILENAME = ".gm-pf2e-runtime-history.json"
MANIFEST_FILENAME = ".gm-pf2e-runtime-export.json"
DND5E_PARTS = LEGACY_STORAGE_DIRS[DND5E_STORAGE]
LOCATOR_KEYS = ("id", "campaign_id", "system", "legacy_root", "legacy_storage", "legacy_file")
# Planner conflicts that mean a copied D&D file itself is bad (named on refusal).
DND5E_FILE_CONFLICTS = {"dnd5e_locator_mismatch", "invalid_character_system"}


class RuntimeExportError(migration.MigrationToolError):
    """Current state cannot be exported safely or without losing authority."""


def _is_windows() -> bool:
    return os.name == "nt"


def _private_json(path: Path, value: Any) -> None:
    migration._write_json(path, value)
    path.chmod(0o600)


def _metadata(raw: Any) -> dict[str, Any]:
    return migration._json_safe(raw) if isinstance(raw, Mapping) else {}


def _source_character(root: Path, character: Mapping[str, Any]) -> Path:
    campaign_id = character["campaign_id"]
    filename = character["legacy_file"]
    storage = character["legacy_storage"]
    if (
        migration.CAMPAIGN_ID_RE.fullmatch(campaign_id) is None
        or storage not in LEGACY_STORAGE_DIRS
        or Path(filename).name != filename
        or "\\" in filename
        or not filename.endswith(".json")
    ):
        raise RuntimeExportError("Unsafe character locator; export refused")
    # SQL soft-trash retains the active directory. Imported trash may still use
    # campaigns_trash; an active copy is the runtime payload when both exist.
    root_names = ["campaigns"]
    if character["legacy_root"] == "campaigns_trash":
        root_names.append("campaigns_trash")
    for root_name in root_names:
        candidate = root / root_name / campaign_id / Path(*LEGACY_STORAGE_DIRS[storage]) / filename
        current = root
        for part in candidate.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise RuntimeExportError("Character source uses a symbolic link")
        if candidate.is_file():
            if not candidate.resolve().is_relative_to(root):
                raise RuntimeExportError("Character source escapes DATA_DIR")
            return candidate
    raise RuntimeExportError("Registered character payload is missing")


def _envelopes(session) -> dict[str, dict[str, Any]]:
    run = session.scalars(
        select(MigrationRun)
        # A per-campaign run (PE-12) never carries the legacy envelopes.
        .where(MigrationRun.campaign_id.is_(None))
        .where(MigrationRun.status.in_(["succeeded", "verified"]))
        .order_by(MigrationRun.completed_at.desc(), MigrationRun.id)
    ).first()
    if run is None:
        return {"users": {}, "invites": {}}
    return migration._stored_envelope_metadata(session, run.source_checksum)


def _reject_pending_batches(root, campaigns) -> None:
    """A stable crashed batch can still contain a partially published party."""
    for campaign in campaigns:
        if migration.CAMPAIGN_ID_RE.fullmatch(campaign["id"]) is None:
            raise RuntimeExportError("Unsafe campaign locator; export refused")
        root_names = ["campaigns"]
        if campaign["trashed_at"] is not None:
            root_names.append("campaigns_trash")
        for root_name in root_names:
            for storage_parts in LEGACY_STORAGE_DIRS.values():
                directory = root / root_name / campaign["id"] / Path(*storage_parts)
                current = root
                for component in directory.relative_to(root).parts:
                    current = current / component
                    if current.is_symlink():
                        raise RuntimeExportError("Campaign source uses a symbolic link")
                if directory.is_dir() and any(
                    entry.name.endswith(".character-batch-journal")
                    for entry in directory.iterdir()
                ):
                    raise RuntimeExportError("Unresolved character batch blocks runtime export")


def _dnd5e_located(root, campaign) -> dict[str, Path]:
    """Located D&D character files by id; the active copy wins over trash.

    Runs after ``_reject_pending_batches``, which refuses symlinked store
    directories. Only exact PE-11a stems count; other files are not character
    payloads and return with the operator's DATA_DIR merge.
    """
    root_names = ["campaigns"]
    if campaign["trashed_at"] is not None:
        root_names.append("campaigns_trash")
    located: dict[str, Path] = {}
    for root_name in root_names:
        directory = root / root_name / campaign["id"] / Path(*DND5E_PARTS)
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir(), key=lambda entry: entry.name):
            stem = item.name[: -len(".json")]
            if not item.name.endswith(".json") or not is_dnd5e_character_id(stem):
                continue
            if item.is_symlink():
                raise RuntimeExportError("Character source uses a symbolic link")
            # is_symlink() misses a Windows junction; containment does not.
            if not item.resolve().is_relative_to(root):
                raise RuntimeExportError("Character source escapes DATA_DIR")
            if item.is_file():
                located.setdefault(stem, item)
    return located


def _strict_document(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=migration._pairs_without_duplicates,
            parse_constant=migration._reject_json_constant,
            parse_float=migration._parse_json_float,
        )
    except ValueError:  # includes UnicodeDecodeError; the planner reports it
        return None


def _stage_dnd5e(staging, root, records, excluded) -> dict[str, Any]:
    """Copy D&D files as authoritative; collect drift instead of overlaying SQL."""
    members = defaultdict(set)
    for member in records["campaign_memberships"]:
        members[member["campaign_id"]].add(member["user_id"])
    sql_owner = {(row["campaign_id"], row["character_id"]): row["user_id"]
                 for row in records["character_assignments"] if row["role"] == "owner"}
    rows = {(row["campaign_id"], row["id"]) for row in records["characters"]
            if row["legacy_storage"] == DND5E_STORAGE}
    drift, locators, fingerprints, listings = [], [], {}, {}
    for campaign in records["campaigns"]:
        if campaign["system"] != "dnd5e":
            continue
        cid = campaign["id"]
        root_name = "campaigns_trash" if campaign["trashed_at"] is not None else "campaigns"
        located = _dnd5e_located(root, campaign)
        listings[cid] = located
        for stem, path in sorted(located.items()):
            if stem in excluded:
                continue
            raw = path.read_bytes()
            fingerprints[path] = hashlib.sha256(raw).hexdigest()
            document = _strict_document(raw)
            entry = {"campaign_id": cid, "character_id": stem}
            if isinstance(document, dict):
                owner = document.get("owner_user_id")
                if (cid, stem) not in rows:
                    drift.append({"kind": "file_without_row", **entry})
                elif owner != sql_owner.get((cid, stem)):
                    drift.append({"kind": "owner_differs", **entry, "file_owner": owner,
                                  "sql_owner": sql_owner.get((cid, stem))})
                if document.get("campaign_id") != cid:
                    drift.append({"kind": "dnd5e_character_campaign_inferred", **entry,
                                  "file_campaign_id": document.get("campaign_id")})
                if owner is not None and (not isinstance(owner, str) or owner not in members[cid]):
                    # A rollback must not depend on GM cleanup; the source is untouched.
                    drift.append({"kind": "stale_owner_cleared", **entry, "owner_user_id": owner})
                    document["owner_user_id"] = None
                    raw = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            # Malformed files are copied as read; the planner refuses them by name.
            target = staging / root_name / cid / Path(*DND5E_PARTS) / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            target.chmod(0o600)
            locators.append({"id": stem, "campaign_id": cid, "system": "dnd5e",
                             "legacy_root": root_name, "legacy_storage": DND5E_STORAGE,
                             "legacy_file": path.name})
    staged = {(locator["campaign_id"], locator["id"]) for locator in locators}
    omitted = rows - staged
    for cid, character_id in sorted(omitted):
        if character_id not in excluded:
            drift.append({"kind": "row_file_missing", "campaign_id": cid,
                          "character_id": character_id})
    return {"drift": drift, "locators": locators, "omitted": omitted,
            "fingerprints": fingerprints, "listings": listings}


def _invite_state(row, invitation_state) -> str:
    if invitation_state[row["code"]]["revoked_at"] is not None:
        return "revoked"
    return "exhausted" if row["remaining_uses"] == 0 else "live"


def _write_legacy_tree(staging, root, records, envelopes, invitation_state,
                       excluded_character_ids=frozenset()):
    dnd5e = _stage_dnd5e(staging, root, records, frozenset(excluded_character_ids))
    omitted = dnd5e["omitted"]
    users = {}
    for row in records["users"]:
        document = _metadata(row["source_payload"])
        document.update({key: value for key, value in row.items()
                         if key not in {"source_payload", "source_checksum", "normalized_username"}})
        users[row["id"]] = document
    _private_json(staging / "users.json", {**envelopes["users"], "users": users})

    for row in records["campaigns"]:
        if migration.CAMPAIGN_ID_RE.fullmatch(row["id"]) is None:
            raise RuntimeExportError("Campaign id cannot be represented by the legacy runtime")
        document = _metadata(row["settings"])
        document["schema_version"] = 1
        document.update({key: value for key, value in row.items()
                         if key not in {"source_payload", "source_checksum", "settings", "trashed_at"}})
        source_members = _metadata(row["source_payload"]).get("members", [])
        member_metadata = {
            member.get("user_id"): _metadata(member)
            for member in source_members
            if isinstance(member, Mapping) and isinstance(member.get("user_id"), str)
        } if isinstance(source_members, list) else {}
        document["members"] = []
        for member in records["campaign_memberships"]:
            if member["campaign_id"] != row["id"]:
                continue
            character_id = member["character_id"]
            if (row["id"], character_id) in omitted:
                # Keep the member; only the selection of a missing D&D character goes.
                dnd5e["drift"].append({"kind": "membership_selection_cleared",
                                       "campaign_id": row["id"], "user_id": member["user_id"],
                                       "character_id": character_id})
                character_id = None
            document["members"].append({
                **member_metadata.get(member["user_id"], {}),
                "user_id": member["user_id"], "role": member["role"],
                "character_id": character_id,
            })
        document.pop("_trashed_at", None)
        root_name = "campaigns"
        if row["trashed_at"] is not None:
            document["_trashed_at"] = row["trashed_at"]
            root_name = "campaigns_trash"
        _private_json(staging / root_name / row["id"] / "campaign.json", document)

    payload_fingerprints = dict(dnd5e["fingerprints"])
    for row in records["characters"]:
        if row["legacy_storage"] == DND5E_STORAGE:
            continue  # copied by _stage_dnd5e; SQL is not D&D's authority until PE-15
        source_path = _source_character(root, row)
        fingerprint = hashlib.sha256(source_path.read_bytes()).hexdigest()
        document = migration._load_json_strict(source_path)
        if not isinstance(document, dict) or document.get("id") != row["id"]:
            raise RuntimeExportError("Character payload identity differs from its SQL locator")
        payload_fingerprints[source_path] = fingerprint
        assignments = [assignment for assignment in records["character_assignments"]
                       if assignment["campaign_id"] == row["campaign_id"]
                       and assignment["character_id"] == row["id"]]
        owners = [assignment["user_id"] for assignment in assignments
                  if assignment["role"] == "owner"]
        if len(owners) > 1:
            raise RuntimeExportError("Ambiguous SQL ownership; export refused")
        document.update({
            "schema_version": 1,
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "system": row["system"],
            "owner_user_id": owners[0] if owners else None,
            "editor_user_ids": [assignment["user_id"] for assignment in assignments
                                if assignment["role"] == "editor"],
            "viewer_user_ids": [assignment["user_id"] for assignment in assignments
                                if assignment["role"] == "viewer"],
        })
        document.pop("owner_user_ids", None)
        _private_json(staging / row["legacy_root"] / row["campaign_id"]
                      / row["legacy_storage"] / row["legacy_file"], document)

    invites = {}
    for row in records["invites"]:
        if (row["campaign_id"], row["character_id"]) in omitted:
            # Never widen a character-scoped invite to the whole campaign; the
            # SQL row stays in the history sidecar.
            dnd5e["drift"].append({"kind": "invite_omitted", "code": row["code"],
                                   "campaign_id": row["campaign_id"],
                                   "character_id": row["character_id"],
                                   "state": _invite_state(row, invitation_state)})
            continue
        document = _metadata(row["source_payload"])
        document.update({key: value for key, value in row.items()
                         if key not in {"source_payload", "source_checksum", "remaining_uses"}})
        # Legacy invite readers do not understand revocation timestamps.
        revoked = invitation_state[row["code"]]["revoked_at"] is not None
        document["uses_left"] = 0 if revoked else row["remaining_uses"]
        invites[row["code"]] = document
    _private_json(staging / "invites.json", {**envelopes["invites"], "invites": invites})
    from tools.workflow_transfer import write_private
    write_private(staging, records)
    return payload_fingerprints, dnd5e


def _locators(rows, *, dnd5e: bool) -> list[dict[str, Any]]:
    return [{key: row[key] for key in LOCATOR_KEYS} for row in rows
            if (row["legacy_storage"] == DND5E_STORAGE) == dnd5e]


def _verify_authority(staging, records, invitation_state, dnd5e) -> None:
    """Check legacy normalization preserves every SQL authorization field.

    The expected projection applies exactly the staged D&D adjustments:
    selections of omitted D&D characters are cleared and invites scoped to them
    are absent. D&D assignments are left out because the D&D file, not SQL, is
    the ownership authority until PE-15.
    """

    exported = migration.inspect_source(staging).records
    omitted = dnd5e["omitted"]
    dnd5e_characters = {(row["campaign_id"], row["id"]) for row in records["characters"]
                        if row["legacy_storage"] == DND5E_STORAGE}
    dnd5e_characters |= {(row["campaign_id"], row["id"]) for row in dnd5e["locators"]}

    def comparable(entity, raw):
        if entity == "character_assignments" and (
            (raw["campaign_id"], raw["character_id"]) in dnd5e_characters
        ):
            return None  # PE-15 makes SQL the D&D ownership authority
        return {key: value for key, value in raw.items()
                if key not in {"source_payload", "source_checksum"}}

    for entity in ("users", "campaigns", "campaign_memberships", "character_assignments", "invites"):
        expected = []
        for raw in records[entity]:
            row = comparable(entity, raw)
            if row is None:
                continue
            reference = (row.get("campaign_id"), row.get("character_id"))
            if entity == "invites":
                if reference in omitted:
                    continue
                if invitation_state[row["code"]]["revoked_at"] is not None:
                    row["remaining_uses"] = 0
            if entity == "campaign_memberships" and reference in omitted:
                row["character_id"] = None
            expected.append(row)
        actual = [row for row in (comparable(entity, raw) for raw in exported[entity])
                  if row is not None]
        if expected != actual:
            raise RuntimeExportError("Legacy normalization changed SQL authority; export refused")
    by_character = lambda locator: (locator["campaign_id"], locator["id"])  # noqa: E731
    if (
        _locators(records["characters"], dnd5e=False)
        != _locators(exported["characters"], dnd5e=False)
        or sorted(dnd5e["locators"], key=by_character)
        != sorted(_locators(exported["characters"], dnd5e=True), key=by_character)
    ):
        raise RuntimeExportError("Character registry changed during export validation")
    for entity in ('character_drafts', 'character_workflow_receipts'):
        expected = [row for row in records[entity] if entity != 'character_drafts'
                    or 'workflow_version' in row['payload']]
        if expected != list(exported[entity]):
            raise RuntimeExportError('Private workflows changed during export validation')


def export_runtime(
    source: os.PathLike[str] | str,
    database_url: str,
    output_dir: os.PathLike[str] | str,
    *,
    quiesced: bool = False,
    excluded_character_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Export current relational authority after the operator stops all writers.

    ``excluded_character_ids`` leaves those D&D character files out (PE-41
    passes archived ids); references to them are handled as for deleted files.
    """

    if not quiesced:
        raise RuntimeExportError("Application writes must be quiesced; pass --quiesced after stopping writers")
    root = migration._safe_source_root(source)
    target = migration._validate_export_target(root, output_dir)
    if target.exists():
        raise migration.ExportTargetError("Runtime export requires a new output directory")
    database = Database(database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        with database.session() as session:
            migration._configure_repeatable_read(session)
            records = migration._database_records(session)
            from tools.workflow_transfer import assert_quiescent
            assert_quiescent(records)
            _reject_pending_batches(root, records["campaigns"])
            envelopes = _envelopes(session)
            invitation_state = {
                invite.code: {
                    "created_at": migration._datetime_json(invite.created_at),
                    "revoked_at": migration._datetime_json(invite.revoked_at),
                }
                for invite in session.scalars(select(Invitation).order_by(Invitation.code))
            }
            fingerprints, dnd5e = _write_legacy_tree(
                staging, root, records, envelopes, invitation_state, excluded_character_ids
            )
            history = {
                "schema_version": 1,
                "records": records,
                "invitation_state": invitation_state,
                "envelope_metadata": envelopes,
                "migration_runs": [{
                    column.name: migration._datetime_json(value) if column.name in {"started_at", "completed_at"} else value
                    for column in MigrationRun.__table__.columns
                    for value in [getattr(run, column.name)]
                } for run in session.scalars(select(MigrationRun).order_by(MigrationRun.id))],
            }
            _private_json(staging / HISTORY_FILENAME, history)

        plan = migration.build_plan(staging)
        if plan["blocking_conflicts"]:
            for conflict in plan["blocking_conflicts"]:
                parts = PurePosixPath(conflict["path"]).parts
                if (
                    conflict["code"] in DND5E_FILE_CONFLICTS
                    and len(parts) == 5
                    and parts[0] in {"campaigns", "campaigns_trash"}
                    and parts[2:4] == DND5E_PARTS
                ):
                    raise RuntimeExportError(
                        f"D&D character file {conflict['path']} cannot be exported ({conflict['code']})"
                    )
            raise RuntimeExportError("Current SQL state cannot form a valid legacy rollback tree")
        _verify_authority(staging, records, invitation_state, dnd5e)
        # The operator's quiescence acknowledgment is required, but also catch
        # an accidental writer which changed SQL while files were staged.
        with database.session() as current_session:
            current_records = migration._database_records(current_session)
            if current_records != records:
                raise RuntimeExportError('SQL state changed during export; keep writers quiesced')
        _reject_pending_batches(root, records["campaigns"])
        for path, expected in fingerprints.items():
            current = root
            for component in path.relative_to(root).parts:
                current = current / component
                if current.is_symlink():
                    raise RuntimeExportError("Character source path changed during export")
            if (
                not path.is_file()
                or not path.resolve().is_relative_to(root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            ):
                raise RuntimeExportError("Character payload changed during export; keep writers quiesced")
        # D&D files are discovered on disk, so an added or removed file is a change too.
        for campaign in records["campaigns"]:
            if campaign["id"] in dnd5e["listings"] and (
                _dnd5e_located(root, campaign) != dnd5e["listings"][campaign["id"]]
            ):
                raise RuntimeExportError("Character payload changed during export; keep writers quiesced")
        report = {
            "status": "exported",
            "output_dir": target.as_posix(),
            "source_digest": plan["source_digest"],
            "entity_counts": {entity: len(rows) for entity, rows in records.items()},
            "legacy_entity_counts": plan["entity_counts"],
            "entity_digests": plan["entity_digests"],
            "history_digest": migration._canonical_sha256(history),
            "dnd5e_drift": dnd5e["drift"],
            "verified": True,
        }
        _private_json(staging / MANIFEST_FILENAME, report)
        if _is_windows():
            # Windows rename refuses any existing destination atomically;
            # replacing a reserved empty directory is unsupported there.
            os.rename(staging, target)
        else:
            # POSIX rename can replace an empty directory. Reserve this new
            # destination so a concurrently created directory is not replaced.
            target.mkdir(mode=0o700)
            os.replace(staging, target)
        return report
    except (SQLAlchemyError, OSError, ValueError, ImportError):
        raise RuntimeExportError("Runtime export failed; verify database and source availability") from None
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        database.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="quiesced DATA_DIR")
    parser.add_argument("--output-dir", required=True, help="new private rollback directory")
    parser.add_argument("--database-url", help="defaults to DATABASE_URL")
    parser.add_argument("--quiesced", action="store_true", help="acknowledge all application writers are stopped")
    args = parser.parse_args(argv)
    try:
        report = export_runtime(
            args.source, migration._database_url(args.database_url),
            args.output_dir, quiesced=args.quiesced,
        )
    except (migration.MigrationToolError, WorkflowError, SQLAlchemyError, OSError, ValueError, ImportError) as error:
        message = str(error) if isinstance(error, (migration.MigrationToolError, WorkflowError)) else "Runtime export failed"
        print("error: " + message, file=sys.stderr)
        return 2
    migration._emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
