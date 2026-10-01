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

The JSON tree is checked with the existing migration planner. SQL-only history is
retained in a private sidecar; the legacy application and PR4A importer do not
replay that sidecar. Returning to SQL therefore requires a separate reviewed
history reconciliation or retaining/restoring the database backup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
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
from tools import migrate_transactional_store as migration


HISTORY_FILENAME = ".gm-pf2e-runtime-history.json"
MANIFEST_FILENAME = ".gm-pf2e-runtime-export.json"


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
        or storage not in {"party_data", "cosmere_pcs"}
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
        candidate = root / root_name / campaign_id / storage / filename
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
            for storage in ("party_data", "cosmere_pcs"):
                directory = root / root_name / campaign["id"] / storage
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


def _write_legacy_tree(staging, root, records, envelopes, invitation_state):
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
        document["members"] = [
            {**member_metadata.get(member["user_id"], {}),
             **{key: member[key] for key in ("user_id", "role", "character_id")}}
            for member in records["campaign_memberships"]
            if member["campaign_id"] == row["id"]
        ]
        document.pop("_trashed_at", None)
        root_name = "campaigns"
        if row["trashed_at"] is not None:
            document["_trashed_at"] = row["trashed_at"]
            root_name = "campaigns_trash"
        _private_json(staging / root_name / row["id"] / "campaign.json", document)

    payload_fingerprints = {}
    for row in records["characters"]:
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
        document = _metadata(row["source_payload"])
        document.update({key: value for key, value in row.items()
                         if key not in {"source_payload", "source_checksum", "remaining_uses"}})
        # Legacy invite readers do not understand revocation timestamps.
        revoked = invitation_state[row["code"]]["revoked_at"] is not None
        document["uses_left"] = 0 if revoked else row["remaining_uses"]
        invites[row["code"]] = document
    _private_json(staging / "invites.json", {**envelopes["invites"], "invites": invites})
    return payload_fingerprints


def _verify_authority(staging, records, invitation_state) -> None:
    """Check legacy normalization preserves every SQL authorization field."""

    exported = migration.inspect_source(staging).records
    for entity in ("users", "campaigns", "campaign_memberships", "character_assignments", "invites"):
        expected = []
        actual = []
        for raw in records[entity]:
            row = {key: value for key, value in raw.items()
                   if key not in {"source_payload", "source_checksum"}}
            if entity == "invites" and invitation_state[row["code"]]["revoked_at"] is not None:
                row["remaining_uses"] = 0
            expected.append(row)
        for raw in exported[entity]:
            actual.append({key: value for key, value in raw.items()
                           if key not in {"source_payload", "source_checksum"}})
        if expected != actual:
            raise RuntimeExportError("Legacy normalization changed SQL authority; export refused")
    expected_locators = [{key: row[key] for key in (
        "id", "campaign_id", "system", "legacy_root", "legacy_storage", "legacy_file"
    )} for row in records["characters"]]
    actual_locators = [{key: row[key] for key in (
        "id", "campaign_id", "system", "legacy_root", "legacy_storage", "legacy_file"
    )} for row in exported["characters"]]
    if expected_locators != actual_locators:
        raise RuntimeExportError("Character registry changed during export validation")


def export_runtime(
    source: os.PathLike[str] | str,
    database_url: str,
    output_dir: os.PathLike[str] | str,
    *,
    quiesced: bool = False,
) -> dict[str, Any]:
    """Export current relational authority after the operator stops all writers."""

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
            _reject_pending_batches(root, records["campaigns"])
            envelopes = _envelopes(session)
            invitation_state = {
                invite.code: {
                    "created_at": migration._datetime_json(invite.created_at),
                    "revoked_at": migration._datetime_json(invite.revoked_at),
                }
                for invite in session.scalars(select(Invitation).order_by(Invitation.code))
            }
            fingerprints = _write_legacy_tree(staging, root, records, envelopes, invitation_state)
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
            raise RuntimeExportError("Current SQL state cannot form a valid legacy rollback tree")
        _verify_authority(staging, records, invitation_state)
        _reject_pending_batches(root, records["campaigns"])
        for path, expected in fingerprints.items():
            current = root
            for component in path.relative_to(root).parts:
                current = current / component
                if current.is_symlink():
                    raise RuntimeExportError("Character source path changed during export")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise RuntimeExportError("Character payload changed during export; keep writers quiesced")
        report = {
            "status": "exported",
            "output_dir": target.as_posix(),
            "source_digest": plan["source_digest"],
            "entity_counts": {entity: len(rows) for entity, rows in records.items()},
            "legacy_entity_counts": plan["entity_counts"],
            "entity_digests": plan["entity_digests"],
            "history_digest": migration._canonical_sha256(history),
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
    except (migration.MigrationToolError, SQLAlchemyError, OSError, ValueError, ImportError) as error:
        message = str(error) if isinstance(error, migration.MigrationToolError) else "Runtime export failed"
        print("error: " + message, file=sys.stderr)
        return 2
    migration._emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
