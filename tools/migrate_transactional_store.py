#!/usr/bin/env python3
"""Plan, import, verify, and reverse-export the transactional JSON subset.

The application continues to use JSON as its runtime authority in PR4A.  This
tool only creates and validates the additive SQL shadow.  Its safety contract is
deliberately stricter than the application's tolerant readers:

* ``plan`` never writes to the source tree or database;
* every report and checksum is deterministic;
* unresolved identity, membership, ownership, or invite references block import;
* ``import`` requires the digest produced by a reviewed plan and commits once;
* retrying an already-completed digest is a no-op;
* ``export`` only targets a new or empty directory and never edits its source.

Typical use::

    python tools/migrate_transactional_store.py plan --source /data
    python tools/migrate_transactional_store.py import --source /data \
        --expect-digest <digest>
    python tools/migrate_transactional_store.py verify --source /data
    python tools/migrate_transactional_store.py export --source /data \
        --output-dir /data-export
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


# Make direct execution from ``tools/`` behave like ``python -m`` from the
# repository root without importing the Flask application.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


PLAN_SCHEMA_VERSION = 1
EXPORT_SCHEMA_VERSION = 1
EXPORT_MARKER = ".gm-pf2e-transactional-export.json"
TOOL_VERSION = "pr4a-1"
EPOCH_ISO = "1970-01-01T00:00:00+00:00"
ENTITY_NAMES = (
    "users",
    "campaigns",
    "campaign_memberships",
    "characters",
    "character_assignments",
    "invites",
    "invite_redemptions",
    "character_drafts",
    "character_workflow_receipts",
    "audit_events",
)
ASSIGNMENT_ROLES = ("owner", "editor", "viewer")
MEMBERSHIP_ROLES = ("gm", "player")
CAMPAIGN_ID_RE = re.compile(r"^[0-9a-f]{32}$")
POSTGRES_INTEGER_MAX = 2_147_483_647
# A PostgreSQL btree index entry must fit well below one third of an 8 KiB
# page.  Keep legacy usernames flexible while making the unique normalized
# username index predictably insertable under every supported collation.
MAX_INDEXED_USERNAME_BYTES = 2_000
IMPORT_ADVISORY_LOCK_KEY = 0x474D5F504634415F


class MigrationToolError(RuntimeError):
    """An expected, operator-actionable migration failure."""


class PlanBlockedError(MigrationToolError):
    """The source contains one or more blocking conflicts."""


class SourceDigestMismatch(MigrationToolError):
    """The source no longer matches the explicitly reviewed plan."""


class VerificationError(MigrationToolError):
    """SQL and JSON canonical state do not reconcile."""


class ExportTargetError(MigrationToolError):
    """The requested reverse-export destination is unsafe."""


class DuplicateJsonKey(ValueError):
    """A JSON object repeated a key and would otherwise lose one value."""


@dataclass(frozen=True)
class SourceBundle:
    """Private normalized records plus the safe, public plan report."""

    source_root: Path
    records: Mapping[str, tuple[dict[str, Any], ...]]
    envelope_metadata: Mapping[str, Mapping[str, Any]]
    report: Mapping[str, Any]


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _json_safe(value: Any) -> Any:
    """Return a detached JSON value and reject non-standard float values."""

    return json.loads(_canonical_json_bytes(value).decode("utf-8"))


def _pairs_without_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON number {value!r}")


def _parse_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"JSON number is outside the supported finite range: {value!r}")
    return parsed


def _validate_postgres_json(value: Any, *, location: str = "$") -> None:
    """Reject JSON values PostgreSQL JSON/text cannot represent safely."""

    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{location} contains a NUL character")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{location} contains an invalid Unicode scalar") from exc
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{location} contains a non-finite number")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _validate_postgres_json(key, location=f"{location}.<key>")
            _validate_postgres_json(item, location=f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_postgres_json(item, location=f"{location}[{index}]")


def _load_json_strict(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        document = json.load(
            handle,
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=_reject_json_constant,
            parse_float=_parse_json_float,
        )
    _validate_postgres_json(document)
    return document


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError as exc:
        raise MigrationToolError(f"path escapes source root: {path}") from exc


def _safe_source_root(source: os.PathLike[str] | str) -> Path:
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise MigrationToolError(f"source directory does not exist: {root}")
    return root


def _snapshot_files(root: Path) -> list[dict[str, Any]]:
    """Fingerprint only files this migration recognizes.

    Raw hashes make the report useful for backup/audit evidence.  The canonical
    ``source_digest`` below intentionally ignores insignificant JSON formatting.
    """

    candidates: set[Path] = set()
    for name in ("users.json", "invites.json"):
        path = root / name
        if path.is_file() and not path.is_symlink():
            candidates.add(path)
    for campaign_root_name in ("campaigns", "campaigns_trash"):
        campaigns_root = root / campaign_root_name
        if campaigns_root.is_dir() and not campaigns_root.is_symlink():
            for campaign_dir in campaigns_root.iterdir():
                if (
                    not campaign_dir.is_dir()
                    or campaign_dir.is_symlink()
                    or CAMPAIGN_ID_RE.fullmatch(campaign_dir.name) is None
                ):
                    continue
                campaign_file = campaign_dir / "campaign.json"
                if campaign_file.is_file() and not campaign_file.is_symlink():
                    candidates.add(campaign_file)
                for storage_name in ("party_data", "cosmere_pcs"):
                    storage_dir = campaign_dir / storage_name
                    if not storage_dir.is_dir() or storage_dir.is_symlink():
                        continue
                    candidates.update(
                        item
                        for item in storage_dir.iterdir()
                        if item.is_file()
                        and not item.is_symlink()
                        and item.suffix == ".json"
                    )
    from tools.workflow_transfer import private_files
    candidates.update(private_files(root)[0])
    snapshot = []
    for path in sorted(candidates, key=lambda item: _relative_posix(item, root)):
        payload = path.read_bytes()
        snapshot.append(
            {
                "path": _relative_posix(path, root),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return snapshot


def _conflict(
    conflicts: list[dict[str, str]],
    code: str,
    *,
    entity: str,
    entity_id: Any = "",
    path: str = "",
    message: str,
) -> None:
    conflicts.append(
        {
            "code": str(code),
            "entity": str(entity),
            "entity_id": "" if entity_id is None else str(entity_id),
            "path": str(path),
            "message": str(message),
        }
    )


def _warning(
    warnings: list[dict[str, str]],
    code: str,
    *,
    entity: str,
    entity_id: Any = "",
    path: str = "",
    message: str,
) -> None:
    warnings.append(
        {
            "code": str(code),
            "entity": str(entity),
            "entity_id": "" if entity_id is None else str(entity_id),
            "path": str(path),
            "message": str(message),
        }
    )


def _sort_issues(issues: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    fields = ("code", "entity", "entity_id", "path", "message")
    unique = {tuple(str(issue.get(field, "")) for field in fields) for issue in issues}
    return [dict(zip(fields, values)) for values in sorted(unique)]


def _read_expected_document(
    path: Path,
    root: Path,
    conflicts: list[dict[str, str]],
    *,
    entity: str,
) -> Any | None:
    relative = _relative_posix(path, root)
    try:
        return _load_json_strict(path)
    except DuplicateJsonKey as exc:
        _conflict(
            conflicts,
            "duplicate_json_key",
            entity=entity,
            path=relative,
            message=str(exc),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        _conflict(
            conflicts,
            "invalid_json",
            entity=entity,
            path=relative,
            message=f"JSON could not be read: {exc}",
        )
    return None


def _normalized_datetime(
    value: Any,
    *,
    conflicts: list[dict[str, str]],
    warnings: list[dict[str, str]],
    entity: str,
    entity_id: str,
    path: str,
    field: str,
    required: bool = False,
) -> str | None:
    if value is None:
        if required:
            _warning(
                warnings,
                "missing_timestamp_defaulted",
                entity=entity,
                entity_id=entity_id,
                path=path,
                message=f"{field} was missing and will use the Unix epoch sentinel",
            )
            return EPOCH_ISO
        return None
    if not isinstance(value, str) or not value.strip():
        _conflict(
            conflicts,
            "invalid_timestamp",
            entity=entity,
            entity_id=entity_id,
            path=path,
            message=f"{field} must be an ISO-8601 string",
        )
        return EPOCH_ISO if required else None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        _conflict(
            conflicts,
            "invalid_timestamp",
            entity=entity,
            entity_id=entity_id,
            path=path,
            message=f"{field} must be an ISO-8601 string",
        )
        return EPOCH_ISO if required else None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _nonnegative_int(
    value: Any,
    *,
    default: int,
    conflicts: list[dict[str, str]],
    entity: str,
    entity_id: str,
    path: str,
    field: str,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > POSTGRES_INTEGER_MAX
    ):
        _conflict(
            conflicts,
            "invalid_nonnegative_integer",
            entity=entity,
            entity_id=entity_id,
            path=path,
            message=(
                f"{field} must be a non-negative PostgreSQL INTEGER "
                f"(maximum {POSTGRES_INTEGER_MAX})"
            ),
        )
        return default
    return value


def _string_list(
    value: Any,
    *,
    conflicts: list[dict[str, str]],
    entity: str,
    entity_id: str,
    path: str,
    field: str,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        _conflict(
            conflicts,
            "invalid_reference_list",
            entity=entity,
            entity_id=entity_id,
            path=path,
            message=f"{field} must be a list of user ids",
        )
        return []
    output: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            _conflict(
                conflicts,
                "invalid_reference",
                entity=entity,
                entity_id=entity_id,
                path=path,
                message=f"{field} contains a non-string or empty user id",
            )
            continue
        output.append(item)
    duplicates = sorted(item for item, count in Counter(output).items() if count > 1)
    if duplicates:
        _conflict(
            conflicts,
            "duplicate_reference",
            entity=entity,
            entity_id=entity_id,
            path=path,
            message=f"{field} repeats user id(s): {', '.join(duplicates)}",
        )
    return output


def _character_name(document: Mapping[str, Any]) -> str:
    build = document.get("build")
    if isinstance(build, Mapping) and isinstance(build.get("name"), str):
        return build["name"]
    if isinstance(document.get("name"), str):
        return document["name"]
    return "?"


def _empty_records() -> dict[str, list[dict[str, Any]]]:
    return {name: [] for name in ENTITY_NAMES}


def inspect_source(source: os.PathLike[str] | str) -> SourceBundle:
    """Normalize a legacy tree and return records plus a deterministic plan.

    The function intentionally performs no writes.  Import calls it again and
    compares its digest with the reviewed value, closing the plan/import race.
    """

    root = _safe_source_root(source)
    records = _empty_records()
    conflicts: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    snapshot = _snapshot_files(root)

    # Users -----------------------------------------------------------------
    users_path = root / "users.json"
    users_document: Any = {"users": {}}
    if users_path.is_symlink():
        _conflict(
            conflicts,
            "unsupported_source_symlink",
            entity="users",
            path="users.json",
            message="migration refuses source documents reached through symbolic links",
        )
    elif users_path.is_file():
        loaded = _read_expected_document(users_path, root, conflicts, entity="users")
        if loaded is not None:
            users_document = loaded
    if not isinstance(users_document, Mapping) or not isinstance(users_document.get("users"), Mapping):
        _conflict(
            conflicts,
            "invalid_users_document",
            entity="users",
            path="users.json",
            message="users.json must be an object containing a users object",
        )
        raw_users: Mapping[Any, Any] = {}
    else:
        raw_users = users_document["users"]
    users_envelope = (
        {
            str(key): _json_safe(value)
            for key, value in users_document.items()
            if key != "users"
        }
        if isinstance(users_document, Mapping)
        else {}
    )

    usernames: dict[str, list[str]] = defaultdict(list)
    user_record_ids: dict[str, list[str]] = defaultdict(list)
    for key in sorted(raw_users, key=lambda item: str(item)):
        raw = raw_users[key]
        path = "users.json"
        if not isinstance(key, str) or not key:
            _conflict(
                conflicts,
                "invalid_user_id",
                entity="user",
                entity_id=key,
                path=path,
                message="user map key must be a non-empty string",
            )
            continue
        if len(key) > 64:
            _conflict(
                conflicts,
                "identifier_too_long",
                entity="user",
                entity_id=key,
                path=path,
                message="user id exceeds the 64-character database limit",
            )
        if not isinstance(raw, Mapping):
            _conflict(
                conflicts,
                "invalid_user",
                entity="user",
                entity_id=key,
                path=path,
                message="user record must be an object",
            )
            continue
        raw_id = raw.get("id")
        if isinstance(raw_id, str) and raw_id:
            user_record_ids[raw_id].append(key)
        if raw_id != key:
            _conflict(
                conflicts,
                "user_id_mismatch",
                entity="user",
                entity_id=key,
                path=path,
                message="user map key and record id must match",
            )
        username = raw.get("username")
        if not isinstance(username, str) or not username.strip():
            _conflict(
                conflicts,
                "invalid_username",
                entity="user",
                entity_id=key,
                path=path,
                message="username must be a non-empty string",
            )
            normalized_username = ""
        else:
            # Mirror core.auth exactly. Casefolding would collapse valid legacy
            # pairs such as German sharp-s versus ``ss`` that runtime lookup
            # currently treats as distinct accounts.
            normalized_username = username.strip().lower()
            usernames[normalized_username].append(key)
            if len(normalized_username.encode("utf-8")) > MAX_INDEXED_USERNAME_BYTES:
                _conflict(
                    conflicts,
                    "indexed_username_too_long",
                    entity="user",
                    entity_id=key,
                    path=path,
                    message=(
                        "normalized username exceeds the safe PostgreSQL unique-index "
                        f"limit of {MAX_INDEXED_USERNAME_BYTES} UTF-8 bytes"
                    ),
                )
        raw_display_name = raw.get("display_name")
        if raw_display_name is None or raw_display_name == "":
            display_name = username or ""
        elif isinstance(raw_display_name, str):
            display_name = raw_display_name
        else:
            _conflict(
                conflicts,
                "invalid_display_name",
                entity="user",
                entity_id=key,
                path=path,
                message="display_name must be a string",
            )
            display_name = ""
        password_hash = raw.get("password_hash")
        if not isinstance(password_hash, str) or not password_hash:
            _conflict(
                conflicts,
                "invalid_password_hash",
                entity="user",
                entity_id=key,
                path=path,
                message="password_hash must be a non-empty string",
            )
            password_hash = ""
        is_admin = raw.get("is_admin", False)
        if not isinstance(is_admin, bool):
            _conflict(
                conflicts,
                "invalid_admin_flag",
                entity="user",
                entity_id=key,
                path=path,
                message="is_admin must be a boolean",
            )
            is_admin = False
        last_campaign_id = raw.get("last_campaign_id")
        if last_campaign_id is not None and (
            not isinstance(last_campaign_id, str) or not last_campaign_id
        ):
            _conflict(
                conflicts,
                "invalid_last_campaign",
                entity="user",
                entity_id=key,
                path=path,
                message="last_campaign_id must be null or a non-empty string",
            )
            last_campaign_id = None
        elif isinstance(last_campaign_id, str) and len(last_campaign_id) > 64:
            _conflict(
                conflicts,
                "identifier_too_long",
                entity="user",
                entity_id=key,
                path=path,
                message="last_campaign_id exceeds the 64-character database limit",
            )
        records["users"].append(
            {
                "id": key,
                "username": username if isinstance(username, str) else "",
                "normalized_username": normalized_username,
                "display_name": display_name,
                "password_hash": password_hash,
                "is_admin": is_admin,
                "created_at": _normalized_datetime(
                    raw.get("created_at"),
                    conflicts=conflicts,
                    warnings=warnings,
                    entity="user",
                    entity_id=key,
                    path=path,
                    field="created_at",
                    required=True,
                ),
                "last_login": _normalized_datetime(
                    raw.get("last_login"),
                    conflicts=conflicts,
                    warnings=warnings,
                    entity="user",
                    entity_id=key,
                    path=path,
                    field="last_login",
                ),
                "session_version": _nonnegative_int(
                    raw.get("session_version", 0),
                    default=0,
                    conflicts=conflicts,
                    entity="user",
                    entity_id=key,
                    path=path,
                    field="session_version",
                ),
                "last_campaign_id": last_campaign_id,
                "source_payload": _json_safe(raw),
                "source_checksum": _canonical_sha256(raw),
            }
        )
    for normalized, ids in sorted(usernames.items()):
        if len(ids) > 1:
            _conflict(
                conflicts,
                "duplicate_username",
                entity="user",
                entity_id=",".join(sorted(ids)),
                path="users.json",
                message=f"username {normalized!r} is used by multiple accounts",
            )
    for raw_id, keys in sorted(user_record_ids.items()):
        if len(keys) > 1:
            _conflict(
                conflicts,
                "duplicate_user_id",
                entity="user",
                entity_id=raw_id,
                path="users.json",
                message="multiple user records declare the same id",
            )

    # Campaigns, memberships, and character locators -----------------------
    user_ids = {row["id"] for row in records["users"]}
    campaign_ids: set[str] = set()
    character_rows_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    membership_claims: dict[str, list[tuple[str, str]]] = defaultdict(list)
    campaign_member_ids: dict[str, set[str]] = defaultdict(set)
    campaign_dirs: list[tuple[str, Path]] = []
    for campaign_root_name in ("campaigns", "campaigns_trash"):
        campaigns_root = root / campaign_root_name
        if campaigns_root.is_symlink():
            _conflict(
                conflicts,
                "unsupported_source_symlink",
                entity="campaign",
                path=campaign_root_name,
                message="migration refuses campaign roots reached through symbolic links",
            )
            continue
        if not campaigns_root.is_dir():
            continue
        for item in sorted(campaigns_root.iterdir(), key=lambda candidate: candidate.name):
            lexical_path = item.relative_to(root).as_posix()
            if item.is_symlink():
                _conflict(
                    conflicts,
                    "unsupported_source_symlink",
                    entity="campaign",
                    entity_id=item.name,
                    path=lexical_path,
                    message="migration refuses campaign directories reached through symbolic links",
                )
                continue
            if not item.is_dir():
                continue
            campaign_file = item / "campaign.json"
            if CAMPAIGN_ID_RE.fullmatch(item.name) is None:
                if campaign_file.is_file():
                    _warning(
                        warnings,
                        "ignored_noncanonical_campaign_directory",
                        entity="campaign",
                        entity_id=item.name,
                        path=lexical_path,
                        message=(
                            "directory is ignored because runtime campaign ids must be "
                            "32 lowercase hexadecimal characters"
                        ),
                    )
                continue
            if campaign_file.is_symlink():
                _conflict(
                    conflicts,
                    "unsupported_source_symlink",
                    entity="campaign",
                    entity_id=item.name,
                    path=campaign_file.relative_to(root).as_posix(),
                    message="migration refuses campaign documents reached through symbolic links",
                )
                continue
            if campaign_file.is_file():
                campaign_dirs.append((campaign_root_name, item))
    campaign_dirs.sort(key=lambda item: (item[1].name, item[0]))

    for campaign_root_name, campaign_dir in campaign_dirs:
        folder_id = campaign_dir.name
        campaign_path = campaign_dir / "campaign.json"
        relative_campaign_path = _relative_posix(campaign_path, root)
        document = _read_expected_document(
            campaign_path, root, conflicts, entity="campaign"
        )
        if not isinstance(document, Mapping):
            if document is not None:
                _conflict(
                    conflicts,
                    "invalid_campaign",
                    entity="campaign",
                    entity_id=folder_id,
                    path=relative_campaign_path,
                    message="campaign.json must contain an object",
                )
            continue
        campaign_id = document.get("id")
        if not isinstance(campaign_id, str) or not campaign_id:
            _conflict(
                conflicts,
                "missing_campaign_id",
                entity="campaign",
                entity_id=folder_id,
                path=relative_campaign_path,
                message="campaign id is missing or invalid",
            )
            continue
        if campaign_id != folder_id:
            _conflict(
                conflicts,
                "campaign_id_mismatch",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign id must match its directory name",
            )
        if len(campaign_id) > 64:
            _conflict(
                conflicts,
                "identifier_too_long",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign id exceeds the 64-character database limit",
            )
        if campaign_id in campaign_ids:
            _conflict(
                conflicts,
                "duplicate_campaign_id",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign id occurs more than once",
            )
        campaign_ids.add(campaign_id)
        created_by = document.get("created_by")
        raw_campaign_system = document.get("system")
        campaign_system = (
            "pf2e"
            if raw_campaign_system is None or raw_campaign_system == ""
            else raw_campaign_system
        )
        if not isinstance(campaign_system, str) or campaign_system not in {
            "pf2e",
            "cosmere",
        }:
            _conflict(
                conflicts,
                "invalid_campaign_system",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign system must be pf2e or cosmere",
            )
            campaign_system = "pf2e"
        raw_campaign_name = document.get("name")
        campaign_name = "" if raw_campaign_name is None else raw_campaign_name
        if not isinstance(campaign_name, str):
            _conflict(
                conflicts,
                "invalid_campaign_name",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign name must be a string",
            )
            campaign_name = ""
        raw_campaign_slug = document.get("slug")
        campaign_slug = "" if raw_campaign_slug is None else raw_campaign_slug
        if not isinstance(campaign_slug, str):
            _conflict(
                conflicts,
                "invalid_campaign_slug",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign slug must be a string",
            )
            campaign_slug = ""
        session_number = _nonnegative_int(
            document.get("session_number", 1),
            default=1,
            conflicts=conflicts,
            entity="campaign",
            entity_id=campaign_id,
            path=relative_campaign_path,
            field="session_number",
        )
        raw_trashed_at = document.get("_trashed_at")
        trashed_at = None
        if campaign_root_name == "campaigns_trash":
            if not isinstance(raw_trashed_at, str) or not raw_trashed_at:
                _conflict(
                    conflicts,
                    "missing_trashed_at",
                    entity="campaign",
                    entity_id=campaign_id,
                    path=relative_campaign_path,
                    message="trashed campaign must retain its _trashed_at timestamp",
                )
            else:
                trashed_at = _normalized_datetime(
                    raw_trashed_at,
                    conflicts=conflicts,
                    warnings=warnings,
                    entity="campaign",
                    entity_id=campaign_id,
                    path=relative_campaign_path,
                    field="_trashed_at",
                    required=True,
                )
        elif raw_trashed_at is not None:
            _conflict(
                conflicts,
                "active_campaign_marked_trashed",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="active campaign must not contain _trashed_at",
            )
        settings = {
            key: _json_safe(value)
            for key, value in document.items()
            if key
            not in {
                "schema_version",
                "id",
                "slug",
                "name",
                "system",
                "created_by",
                "created_at",
                "members",
                "session_number",
                "_trashed_at",
            }
        }
        campaign_row = {
            "id": campaign_id,
            "slug": campaign_slug,
            "name": campaign_name,
            "system": campaign_system,
            "created_by": created_by,
            "created_at": _normalized_datetime(
                document.get("created_at"),
                conflicts=conflicts,
                warnings=warnings,
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                field="created_at",
                required=True,
            ),
            "trashed_at": trashed_at if campaign_root_name == "campaigns_trash" else None,
            "session_number": session_number,
            "settings": settings,
            "source_payload": _json_safe(document),
            "source_checksum": _canonical_sha256(document),
        }
        records["campaigns"].append(campaign_row)
        if not isinstance(created_by, str) or created_by not in user_ids:
            _conflict(
                conflicts,
                "missing_campaign_creator",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="created_by must reference an existing user",
            )

        members = document.get("members", [])
        if not isinstance(members, list):
            _conflict(
                conflicts,
                "invalid_memberships",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="members must be a list",
            )
            members = []
        seen_members: set[str] = set()
        gm_count = 0
        for member in members:
            if not isinstance(member, Mapping):
                _conflict(
                    conflicts,
                    "invalid_membership",
                    entity="campaign_membership",
                    entity_id=campaign_id,
                    path=relative_campaign_path,
                    message="membership must be an object",
                )
                continue
            user_id = member.get("user_id")
            role = member.get("role")
            if not isinstance(user_id, str) or not user_id:
                _conflict(
                    conflicts,
                    "invalid_membership_user",
                    entity="campaign_membership",
                    entity_id=campaign_id,
                    path=relative_campaign_path,
                    message="membership user_id must be a non-empty string",
                )
                continue
            if user_id in seen_members:
                _conflict(
                    conflicts,
                    "duplicate_membership",
                    entity="campaign_membership",
                    entity_id=f"{campaign_id}:{user_id}",
                    path=relative_campaign_path,
                    message="user occurs more than once in campaign members",
                )
            seen_members.add(user_id)
            campaign_member_ids[campaign_id].add(user_id)
            if user_id not in user_ids:
                _conflict(
                    conflicts,
                    "missing_membership_user",
                    entity="campaign_membership",
                    entity_id=f"{campaign_id}:{user_id}",
                    path=relative_campaign_path,
                    message="membership references a missing user",
                )
            if role not in MEMBERSHIP_ROLES:
                _conflict(
                    conflicts,
                    "invalid_membership_role",
                    entity="campaign_membership",
                    entity_id=f"{campaign_id}:{user_id}",
                    path=relative_campaign_path,
                    message="membership role must be gm or player",
                )
            if role == "gm":
                gm_count += 1
            character_id = member.get("character_id")
            if character_id is not None and (not isinstance(character_id, str) or not character_id):
                _conflict(
                    conflicts,
                    "invalid_membership_character",
                    entity="campaign_membership",
                    entity_id=f"{campaign_id}:{user_id}",
                    path=relative_campaign_path,
                    message="membership character_id must be null or a non-empty string",
                )
                character_id = None
            if character_id:
                membership_claims[character_id].append((campaign_id, user_id))
            records["campaign_memberships"].append(
                {
                    "campaign_id": campaign_id,
                    "user_id": user_id,
                    "role": role,
                    "character_id": character_id,
                }
            )
        if gm_count == 0:
            _conflict(
                conflicts,
                "campaign_has_no_gm",
                entity="campaign",
                entity_id=campaign_id,
                path=relative_campaign_path,
                message="campaign must retain at least one GM membership",
            )

        for storage_name in ("party_data", "cosmere_pcs"):
            storage_dir = campaign_dir / storage_name
            if storage_dir.is_symlink():
                _conflict(
                    conflicts,
                    "unsupported_source_symlink",
                    entity="character",
                    entity_id=campaign_id,
                    path=storage_dir.relative_to(root).as_posix(),
                    message="migration refuses character stores reached through symbolic links",
                )
                continue
            if not storage_dir.is_dir():
                continue
            character_paths: list[Path] = []
            for item in sorted(storage_dir.iterdir(), key=lambda candidate: candidate.name):
                lexical_path = item.relative_to(root).as_posix()
                if item.is_symlink():
                    _conflict(
                        conflicts,
                        "unsupported_source_symlink",
                        entity="character",
                        entity_id=item.stem,
                        path=lexical_path,
                        message="migration refuses character files reached through symbolic links",
                    )
                elif item.is_file() and item.suffix == ".json":
                    character_paths.append(item)
                elif item.is_file() and item.suffix.lower() == ".json":
                    _warning(
                        warnings,
                        "ignored_noncanonical_character_file",
                        entity="character",
                        entity_id=item.stem,
                        path=lexical_path,
                        message="file is ignored because runtime character discovery requires lowercase .json",
                    )
            for character_path in character_paths:
                relative_character_path = _relative_posix(character_path, root)
                character_document = _read_expected_document(
                    character_path, root, conflicts, entity="character"
                )
                if not isinstance(character_document, Mapping):
                    if character_document is not None:
                        _conflict(
                            conflicts,
                            "invalid_character",
                            entity="character",
                            path=relative_character_path,
                            message="character JSON must contain an object",
                        )
                    continue
                character_id = character_document.get("id")
                if not isinstance(character_id, str) or not character_id:
                    _conflict(
                        conflicts,
                        "missing_character_id",
                        entity="character",
                        path=relative_character_path,
                        message="character id is missing or invalid",
                    )
                    continue
                if len(character_id) > 64:
                    _conflict(
                        conflicts,
                        "identifier_too_long",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message="character id exceeds the 64-character database limit",
                    )
                declared_campaign_id = character_document.get("campaign_id")
                if declared_campaign_id is None and storage_name == "cosmere_pcs":
                    _warning(
                        warnings,
                        "cosmere_character_campaign_inferred",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message=(
                            "legacy Cosmere character has no campaign_id; "
                            "the containing campaign will be used relationally"
                        ),
                    )
                elif declared_campaign_id != campaign_id:
                    _conflict(
                        conflicts,
                        "character_campaign_mismatch",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message="character campaign_id must match its containing campaign",
                    )
                raw_character_system = character_document.get("system")
                character_system = (
                    campaign_row["system"]
                    if raw_character_system is None or raw_character_system == ""
                    else raw_character_system
                )
                if not isinstance(character_system, str) or character_system not in {
                    "pf2e",
                    "cosmere",
                }:
                    _conflict(
                        conflicts,
                        "invalid_character_system",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message="character system must be pf2e or cosmere",
                    )
                    character_system = campaign_row["system"]
                source_schema_version = character_document.get("schema_version")
                if source_schema_version is not None and (
                    not isinstance(source_schema_version, int)
                    or isinstance(source_schema_version, bool)
                    or source_schema_version < 0
                    or source_schema_version > POSTGRES_INTEGER_MAX
                ):
                    _conflict(
                        conflicts,
                        "invalid_character_schema_version",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message=(
                            "schema_version must be null or a non-negative PostgreSQL "
                            f"INTEGER (maximum {POSTGRES_INTEGER_MAX})"
                        ),
                    )
                    source_schema_version = None
                character_row = {
                    "id": character_id,
                    "campaign_id": campaign_id,
                    "system": character_system,
                    "display_name": _character_name(character_document),
                    "legacy_root": campaign_root_name,
                    "legacy_storage": storage_name,
                    "legacy_file": character_path.name,
                    "source_schema_version": source_schema_version,
                    "content_checksum": _canonical_sha256(character_document),
                }
                records["characters"].append(character_row)
                character_rows_by_id[character_id].append(character_row)
                owners = _string_list(
                    character_document.get("owner_user_ids"),
                    conflicts=conflicts,
                    entity="character",
                    entity_id=character_id,
                    path=relative_character_path,
                    field="owner_user_ids",
                )
                scalar_owner = character_document.get("owner_user_id")
                if scalar_owner is not None:
                    if isinstance(scalar_owner, str) and scalar_owner:
                        owners.append(scalar_owner)
                    else:
                        _conflict(
                            conflicts,
                            "invalid_character_owner",
                            entity="character",
                            entity_id=character_id,
                            path=relative_character_path,
                            message="owner_user_id must be null or a non-empty string",
                        )
                owners = sorted(set(owners))
                if len(owners) > 1:
                    _conflict(
                        conflicts,
                        "ambiguous_character_owner",
                        entity="character",
                        entity_id=character_id,
                        path=relative_character_path,
                        message="character declares more than one distinct owner",
                    )
                editors = sorted(
                    set(
                        _string_list(
                            character_document.get("editor_user_ids"),
                            conflicts=conflicts,
                            entity="character",
                            entity_id=character_id,
                            path=relative_character_path,
                            field="editor_user_ids",
                        )
                    )
                )
                viewers = sorted(
                    set(
                        _string_list(
                            character_document.get("viewer_user_ids"),
                            conflicts=conflicts,
                            entity="character",
                            entity_id=character_id,
                            path=relative_character_path,
                            field="viewer_user_ids",
                        )
                    )
                )
                assignments: list[tuple[str, str]] = []
                assignments.extend((user_id, "owner") for user_id in owners)
                assignments.extend((user_id, "editor") for user_id in editors)
                assignments.extend((user_id, "viewer") for user_id in viewers)
                roles_by_user: dict[str, set[str]] = defaultdict(set)
                for user_id, role in assignments:
                    roles_by_user[user_id].add(role)
                for user_id, roles in sorted(roles_by_user.items()):
                    if len(roles) > 1:
                        _conflict(
                            conflicts,
                            "overlapping_character_assignment",
                            entity="character_assignment",
                            entity_id=f"{character_id}:{user_id}",
                            path=relative_character_path,
                            message="a user cannot hold multiple explicit assignment roles",
                        )
                    if user_id not in user_ids:
                        _conflict(
                            conflicts,
                            "missing_assignment_user",
                            entity="character_assignment",
                            entity_id=f"{character_id}:{user_id}",
                            path=relative_character_path,
                            message="assignment references a missing user",
                        )
                    if user_id not in campaign_member_ids[campaign_id]:
                        _conflict(
                            conflicts,
                            "assignment_user_not_member",
                            entity="character_assignment",
                            entity_id=f"{character_id}:{user_id}",
                            path=relative_character_path,
                            message="assigned user is not a member of the character's campaign",
                        )
                    for role in sorted(roles):
                        records["character_assignments"].append(
                            {
                                "campaign_id": campaign_id,
                                "character_id": character_id,
                                "user_id": user_id,
                                "role": role,
                            }
                        )

    for character_id, rows in sorted(character_rows_by_id.items()):
        if len(rows) > 1:
            locators = sorted(
                f"{row['campaign_id']}/{row['legacy_storage']}/{row['legacy_file']}"
                for row in rows
            )
            _conflict(
                conflicts,
                "duplicate_character_id",
                entity="character",
                entity_id=character_id,
                message=f"character id occurs in multiple files: {', '.join(locators)}",
            )

    # Membership ``character_id`` is a legacy selection/association, not an
    # ownership authority. Release and later claim flows can legitimately leave
    # stale or multiple links. Validate referential scope, preserve the links,
    # and warn on disagreement; only the character document declares ownership.
    owner_by_character: dict[str, list[str]] = defaultdict(list)
    for row in records["character_assignments"]:
        if row["role"] == "owner":
            owner_by_character[row["character_id"]].append(row["user_id"])
    character_campaign_by_id = {
        row["id"]: row["campaign_id"] for row in records["characters"]
    }
    for character_id, claims in sorted(membership_claims.items()):
        distinct_claimants = sorted({user_id for _campaign_id, user_id in claims})
        if character_id not in character_campaign_by_id:
            _warning(
                warnings,
                "stale_membership_character_link",
                entity="campaign_membership",
                entity_id=character_id,
                message=(
                    "membership references a deleted or missing character; "
                    "the relational link will be cleared"
                ),
            )
            for membership in records["campaign_memberships"]:
                if membership.get("character_id") == character_id:
                    membership["character_id"] = None
            continue
        linked_campaigns = sorted({campaign_id for campaign_id, _user_id in claims})
        if linked_campaigns != [character_campaign_by_id[character_id]]:
            _conflict(
                conflicts,
                "membership_character_campaign_mismatch",
                entity="campaign_membership",
                entity_id=character_id,
                message="membership character link crosses a campaign boundary",
            )
        owners = sorted(set(owner_by_character.get(character_id, [])))
        if len(distinct_claimants) > 1:
            _warning(
                warnings,
                "multiple_membership_character_links",
                entity="character",
                entity_id=character_id,
                message="multiple memberships retain this legacy character link",
            )
        if owners != distinct_claimants:
            _warning(
                warnings,
                "stale_membership_character_link",
                entity="character",
                entity_id=character_id,
                message="membership character link does not match document ownership",
            )
    for character_id, owners in sorted(owner_by_character.items()):
        if owners and character_id not in membership_claims:
            _warning(
                warnings,
                "owner_without_membership_character_link",
                entity="character",
                entity_id=character_id,
                message="document owner has no legacy membership character link",
            )

    # Invitations ----------------------------------------------------------
    invites_path = root / "invites.json"
    invites_document: Any = {"invites": {}}
    if invites_path.is_symlink():
        _conflict(
            conflicts,
            "unsupported_source_symlink",
            entity="invitations",
            path="invites.json",
            message="migration refuses source documents reached through symbolic links",
        )
    elif invites_path.is_file():
        loaded = _read_expected_document(invites_path, root, conflicts, entity="invitations")
        if loaded is not None:
            invites_document = loaded
    if not isinstance(invites_document, Mapping) or not isinstance(
        invites_document.get("invites"), Mapping
    ):
        _conflict(
            conflicts,
            "invalid_invites_document",
            entity="invitations",
            path="invites.json",
            message="invites.json must be an object containing an invites object",
        )
        raw_invites: Mapping[Any, Any] = {}
    else:
        raw_invites = invites_document["invites"]
    invites_envelope = (
        {
            str(key): _json_safe(value)
            for key, value in invites_document.items()
            if key != "invites"
        }
        if isinstance(invites_document, Mapping)
        else {}
    )
    for key in sorted(raw_invites, key=lambda item: str(item)):
        raw = raw_invites[key]
        if not isinstance(key, str) or not key:
            _conflict(
                conflicts,
                "invalid_invite_code",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite map key must be a non-empty string",
            )
            continue
        if len(key) > 32:
            _conflict(
                conflicts,
                "identifier_too_long",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite code exceeds the 32-character database limit",
            )
        if not isinstance(raw, Mapping):
            _conflict(
                conflicts,
                "invalid_invitation",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite record must be an object",
            )
            continue
        code = raw.get("code")
        if code != key:
            _conflict(
                conflicts,
                "invite_code_mismatch",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite map key and record code must match",
            )
        if key != key.strip().upper():
            _conflict(
                conflicts,
                "noncanonical_invite_code",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite code must already be trimmed uppercase text",
            )
        campaign_id = raw.get("campaign_id")
        character_id = raw.get("character_id")
        created_by = raw.get("created_by")
        role = raw.get("role")
        if not isinstance(campaign_id, str) or campaign_id not in campaign_ids:
            _conflict(
                conflicts,
                "dangling_invite_campaign",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite references a missing campaign",
            )
        if role not in MEMBERSHIP_ROLES:
            _conflict(
                conflicts,
                "invalid_invite_role",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite role must be gm or player",
            )
        if character_id is not None:
            if not isinstance(character_id, str) or character_id not in character_campaign_by_id:
                _conflict(
                    conflicts,
                    "dangling_invite_character",
                    entity="invitation",
                    entity_id=key,
                    path="invites.json",
                    message="invite references a missing character",
                )
            elif character_campaign_by_id[character_id] != campaign_id:
                _conflict(
                    conflicts,
                    "invite_character_campaign_mismatch",
                    entity="invitation",
                    entity_id=key,
                    path="invites.json",
                    message="invite character belongs to another campaign",
                )
        if created_by is not None and (
            not isinstance(created_by, str) or created_by not in user_ids
        ):
            _conflict(
                conflicts,
                "dangling_invite_creator",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="invite created_by references a missing user",
            )
        uses_left = raw.get("uses_left")
        if (
            not isinstance(uses_left, int)
            or isinstance(uses_left, bool)
            or uses_left < 0
            or uses_left > POSTGRES_INTEGER_MAX
        ):
            _conflict(
                conflicts,
                "invalid_invite_uses",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message=(
                    "uses_left must be a non-negative PostgreSQL INTEGER "
                    f"(maximum {POSTGRES_INTEGER_MAX})"
                ),
            )
            uses_left = 0
        raw_creates_account = raw.get("creates_account", True)
        if not isinstance(raw_creates_account, bool):
            _conflict(
                conflicts,
                "invalid_invite_creates_account",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="creates_account must be a boolean",
            )
            creates_account = True
        else:
            creates_account = raw_creates_account
        raw_expires_at = raw.get("expires_at")
        expires_at: float | None = None
        invalid_expiration = False
        if raw_expires_at is not None:
            if not isinstance(raw_expires_at, (int, float)) or isinstance(
                raw_expires_at, bool
            ):
                invalid_expiration = True
            else:
                try:
                    numeric_expiration = float(raw_expires_at)
                except (OverflowError, ValueError):
                    invalid_expiration = True
                else:
                    if not math.isfinite(numeric_expiration) or numeric_expiration < 0:
                        invalid_expiration = True
                    elif numeric_expiration == 0:
                        _warning(
                            warnings,
                            "zero_invite_expiration_normalized",
                            entity="invitation",
                            entity_id=key,
                            path="invites.json",
                            message=(
                                "legacy JSON treats a zero expires_at as no expiration; "
                                "the relational value will be null"
                            ),
                        )
                    else:
                        expires_at = numeric_expiration
        if invalid_expiration:
            _conflict(
                conflicts,
                "invalid_invite_expiration",
                entity="invitation",
                entity_id=key,
                path="invites.json",
                message="expires_at must be null, zero, or a positive finite Unix epoch number",
            )
        records["invites"].append(
            {
                "code": key,
                "campaign_id": campaign_id,
                "role": role,
                "character_id": character_id,
                "creates_account": creates_account,
                "remaining_uses": uses_left,
                "created_by": created_by,
                "expires_at": expires_at,
                "source_payload": _json_safe(raw),
                "source_checksum": _canonical_sha256(raw),
            }
        )

    # User last-campaign references are checked after every campaign is known.
    for row in records["users"]:
        last_campaign_id = row.get("last_campaign_id")
        if last_campaign_id is not None and last_campaign_id not in campaign_ids:
            _warning(
                warnings,
                "stale_last_campaign",
                entity="user",
                entity_id=row["id"],
                path="users.json",
                message=(
                    "last_campaign_id references a purged campaign; "
                    "the nullable resume hint will be preserved"
                ),
            )

    from tools.workflow_transfer import inspect_private
    inspect_private(root, records, conflicts)
    sort_keys: dict[str, Any] = {
        "users": lambda row: row["id"],
        "campaigns": lambda row: row["id"],
        "campaign_memberships": lambda row: (row["campaign_id"], row["user_id"]),
        "characters": lambda row: row["id"],
        "character_assignments": lambda row: (
            row["campaign_id"],
            row["character_id"],
            row["user_id"],
            row["role"],
        ),
        "invites": lambda row: row["code"],
        "invite_redemptions": lambda row: str(row.get("id", "")),
        "character_drafts": lambda row: str(row.get("id", "")),
        "character_workflow_receipts": lambda row: str(row.get("id", "")),
        "audit_events": lambda row: str(row.get("id", "")),
    }
    normalized_records: dict[str, tuple[dict[str, Any], ...]] = {}
    for entity in ENTITY_NAMES:
        normalized_records[entity] = tuple(
            _json_safe(row) for row in sorted(records[entity], key=sort_keys[entity])
        )

    canonical_state = {
        entity: list(normalized_records[entity]) for entity in ENTITY_NAMES
    }
    envelope_metadata = {
        "users": users_envelope,
        "invites": invites_envelope,
    }
    envelope_report = {
        name: {
            "keys": sorted(metadata),
            "digest": _canonical_sha256(metadata),
        }
        for name, metadata in sorted(envelope_metadata.items())
    }
    counts = {entity: len(normalized_records[entity]) for entity in ENTITY_NAMES}
    entity_digests = {
        entity: _canonical_sha256(canonical_state[entity]) for entity in ENTITY_NAMES
    }
    final_snapshot = _snapshot_files(root)
    if final_snapshot != snapshot:
        _conflict(
            conflicts,
            "source_changed_during_scan",
            entity="source",
            path="",
            message="recognized source files changed while the plan was being built",
        )
    sorted_conflicts = _sort_issues(conflicts)
    sorted_warnings = _sort_issues(warnings)
    source_digest = _canonical_sha256(
        {"entities": canonical_state, "envelopes": envelope_metadata}
    )
    report = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_path": root.as_posix(),
        "snapshot": {
            "file_count": len(snapshot),
            "total_bytes": sum(item["bytes"] for item in snapshot),
            "files": snapshot,
        },
        "source_digest": source_digest,
        "entity_counts": counts,
        "entity_digests": entity_digests,
        "envelope_metadata": envelope_report,
        "blocking_conflicts": sorted_conflicts,
        "warnings": sorted_warnings,
        "import_allowed": not sorted_conflicts,
    }
    return SourceBundle(
        source_root=root,
        records=normalized_records,
        envelope_metadata=envelope_metadata,
        report=report,
    )


def build_plan(source: os.PathLike[str] | str) -> dict[str, Any]:
    """Return the safe public plan without mutating the source or database."""

    return copy.deepcopy(dict(inspect_source(source).report))


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _datetime_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _persistence_api():
    """Import optional SQL dependencies only for database subcommands."""

    try:
        from sqlalchemy import func, select, text

        from core.persistence.database import Database
        from core.persistence.models import (
            AuditEvent,
            Campaign,
            CampaignMembership,
            Character,
            CharacterAssignment,
            CharacterWorkflowReceipt,
            Draft,
            Invitation,
            InviteRedemption,
            MigrationRun,
            User,
        )
        from core.persistence.repositories import (
            CampaignRepository,
            CharacterRepository,
            InvitationRepository,
            MigrationRunRepository,
            UserRepository,
        )
    except ImportError as exc:  # pragma: no cover - exercised by deployment setup
        raise MigrationToolError(
            "SQLAlchemy persistence dependencies are not installed; install "
            "requirements.txt before using import, verify, or export"
        ) from exc
    return {
        "func": func,
        "select": select,
        "text": text,
        "Database": Database,
        "User": User,
        "Campaign": Campaign,
        "CampaignMembership": CampaignMembership,
        "Character": Character,
        "CharacterAssignment": CharacterAssignment,
        "CharacterWorkflowReceipt": CharacterWorkflowReceipt,
        "Invitation": Invitation,
        "InviteRedemption": InviteRedemption,
        "Draft": Draft,
        "AuditEvent": AuditEvent,
        "MigrationRun": MigrationRun,
        "UserRepository": UserRepository,
        "CampaignRepository": CampaignRepository,
        "CharacterRepository": CharacterRepository,
        "InvitationRepository": InvitationRepository,
        "MigrationRunRepository": MigrationRunRepository,
    }


def _database_records(session: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    api = _persistence_api()
    select = api["select"]
    rows: dict[str, list[dict[str, Any]]] = _empty_records()

    users = session.scalars(select(api["User"]).order_by(api["User"].id)).all()
    for user in users:
        rows["users"].append(
            {
                "id": user.id,
                "username": user.username,
                "normalized_username": user.normalized_username,
                "display_name": user.display_name,
                "password_hash": user.password_hash,
                "is_admin": bool(user.is_admin),
                "created_at": _datetime_json(user.created_at),
                "last_login": _datetime_json(user.last_login_at),
                "session_version": user.session_version,
                "last_campaign_id": user.last_campaign_id,
                "source_payload": _json_safe(user.source_payload or {}),
                "source_checksum": user.source_checksum,
            }
        )

    campaigns = session.scalars(
        select(api["Campaign"]).order_by(api["Campaign"].id)
    ).all()
    campaign_is_trashed = {campaign.id: campaign.trashed_at is not None for campaign in campaigns}
    for campaign in campaigns:
        rows["campaigns"].append(
            {
                "id": campaign.id,
                "slug": campaign.slug,
                "name": campaign.name,
                "system": campaign.system,
                "created_by": campaign.created_by_user_id,
                "created_at": _datetime_json(campaign.created_at),
                "trashed_at": _datetime_json(campaign.trashed_at),
                "session_number": campaign.session_number,
                "settings": _json_safe(campaign.settings or {}),
                "source_payload": _json_safe(campaign.source_payload or {}),
                "source_checksum": campaign.source_checksum,
            }
        )

    memberships = session.scalars(
        select(api["CampaignMembership"]).order_by(
            api["CampaignMembership"].campaign_id,
            api["CampaignMembership"].user_id,
        )
    ).all()
    for membership in memberships:
        rows["campaign_memberships"].append(
            {
                "campaign_id": membership.campaign_id,
                "user_id": membership.user_id,
                "role": membership.role,
                "character_id": membership.character_id,
            }
        )

    characters = session.scalars(
        select(api["Character"]).order_by(api["Character"].id)
    ).all()
    for character in characters:
        rows["characters"].append(
            {
                "id": character.id,
                "campaign_id": character.campaign_id,
                "system": character.system,
                "display_name": character.display_name,
                "legacy_root": (
                    "campaigns_trash"
                    if campaign_is_trashed.get(character.campaign_id, False)
                    else "campaigns"
                ),
                "legacy_storage": character.legacy_storage,
                "legacy_file": character.legacy_file,
                "source_schema_version": character.source_schema_version,
                "content_checksum": character.content_checksum,
            }
        )

    assignments = session.scalars(
        select(api["CharacterAssignment"]).order_by(
            api["CharacterAssignment"].campaign_id,
            api["CharacterAssignment"].character_id,
            api["CharacterAssignment"].user_id,
            api["CharacterAssignment"].role,
        )
    ).all()
    for assignment in assignments:
        rows["character_assignments"].append(
            {
                "campaign_id": assignment.campaign_id,
                "character_id": assignment.character_id,
                "user_id": assignment.user_id,
                "role": assignment.role,
            }
        )

    invites = session.scalars(
        select(api["Invitation"]).order_by(api["Invitation"].code)
    ).all()
    for invitation in invites:
        rows["invites"].append(
            {
                "code": invitation.code,
                "campaign_id": invitation.campaign_id,
                "role": invitation.role,
                "character_id": invitation.character_id,
                "creates_account": bool(invitation.creates_account),
                "remaining_uses": invitation.remaining_uses,
                "created_by": invitation.created_by_user_id,
                "expires_at": invitation.expires_at,
                "source_payload": _json_safe(invitation.source_payload or {}),
                "source_checksum": invitation.source_checksum,
            }
        )

    redemptions = session.scalars(
        select(api["InviteRedemption"]).order_by(api["InviteRedemption"].id)
    ).all()
    for redemption in redemptions:
        rows["invite_redemptions"].append(
            {
                "id": redemption.id,
                "invitation_code": redemption.invitation_code,
                "user_id": redemption.user_id,
                "campaign_id": redemption.campaign_id,
                "character_id": redemption.character_id,
                "redeemed_at": _datetime_json(redemption.redeemed_at),
                "details": _json_safe(redemption.details or {}),
            }
        )

    drafts = session.scalars(select(api["Draft"]).order_by(api["Draft"].id)).all()
    for draft in drafts:
        payload = _json_safe(draft.payload or {})
        if 'workflow_version' in payload:
            # Match the runtime store's authority overlay, never stale fields
            # embedded in a client-editable or historical payload envelope.
            payload.update(id=draft.id, campaign_id=draft.campaign_id,
                author_id=draft.user_id, target_id=draft.character_id,
                revision=draft.revision, state=draft.state,
                created_at=_datetime_json(draft.created_at), updated_at=_datetime_json(draft.updated_at))
        rows["character_drafts"].append(
            {
                "id": draft.id,
                "campaign_id": draft.campaign_id,
                "user_id": draft.user_id,
                "character_id": draft.character_id,
                "kind": draft.kind,
                "state": draft.state,
                "revision": draft.revision,
                "payload": payload,
                "source_checksum": draft.source_checksum,
                "created_at": _datetime_json(draft.created_at),
                "updated_at": _datetime_json(draft.updated_at),
                "expires_at": _datetime_json(draft.expires_at),
            }
        )

    for receipt in session.scalars(select(api["CharacterWorkflowReceipt"]).order_by(api["CharacterWorkflowReceipt"].id)):
        rows['character_workflow_receipts'].append({
            'id': receipt.id, 'campaign_id': receipt.campaign_id, 'author_id': receipt.author_id,
            'draft_id': receipt.draft_id, 'target_id': receipt.target_id,
            'operation': receipt.operation, 'key_hash': receipt.key_hash,
            'request_digest': receipt.request_digest, 'state': receipt.state,
            'metadata': _json_safe(receipt.details or {}),
        })

    audit_events = session.scalars(
        select(api["AuditEvent"]).order_by(api["AuditEvent"].id)
    ).all()
    for event in audit_events:
        rows["audit_events"].append(
            {
                "id": event.id,
                "actor_user_id": event.actor_user_id,
                "campaign_id": event.campaign_id,
                "action": event.action,
                "target_type": event.target_type,
                "target_id": event.target_id,
                "details": _json_safe(event.details or {}),
                "occurred_at": _datetime_json(event.occurred_at),
            }
        )

    return {entity: tuple(rows[entity]) for entity in ENTITY_NAMES}


def _state_report(
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    envelope_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    canonical = {entity: list(records[entity]) for entity in ENTITY_NAMES}
    envelopes = {
        name: _json_safe(envelope_metadata.get(name, {}) or {})
        for name in ("users", "invites")
    }
    return {
        "digest": _canonical_sha256(
            {"entities": canonical, "envelopes": envelopes}
        ),
        "counts": {entity: len(canonical[entity]) for entity in ENTITY_NAMES},
        "entity_digests": {
            entity: _canonical_sha256(canonical[entity]) for entity in ENTITY_NAMES
        },
        "envelope_digests": {
            name: _canonical_sha256(metadata)
            for name, metadata in sorted(envelopes.items())
        },
    }


def _stored_envelope_metadata(
    session: Any, source_digest: str
) -> dict[str, dict[str, Any]]:
    api = _persistence_api()
    run = api["MigrationRunRepository"](session).get_by_source_checksum(
        source_digest
    )
    details = run.details if run is not None and isinstance(run.details, Mapping) else {}
    stored = details.get("envelope_metadata", {})
    if not isinstance(stored, Mapping):
        stored = {}
    output: dict[str, dict[str, Any]] = {}
    for name in ("users", "invites"):
        metadata = stored.get(name, {})
        output[name] = (
            _json_safe(metadata) if isinstance(metadata, Mapping) else {}
        )
    return output


def _verify_session(bundle: SourceBundle, session: Any) -> dict[str, Any]:
    database_state = _state_report(
        _database_records(session),
        _stored_envelope_metadata(session, bundle.report["source_digest"]),
    )
    source_counts = dict(bundle.report["entity_counts"])
    source_entity_digests = dict(bundle.report["entity_digests"])
    source_envelope_digests = {
        name: details["digest"]
        for name, details in bundle.report["envelope_metadata"].items()
    }
    mismatches = []
    for entity in ENTITY_NAMES:
        if source_counts[entity] != database_state["counts"][entity]:
            mismatches.append(
                {
                    "entity": entity,
                    "kind": "count",
                    "source": source_counts[entity],
                    "database": database_state["counts"][entity],
                }
            )
        if source_entity_digests[entity] != database_state["entity_digests"][entity]:
            mismatches.append(
                {
                    "entity": entity,
                    "kind": "digest",
                    "source": source_entity_digests[entity],
                    "database": database_state["entity_digests"][entity],
                }
            )
    for name in ("users", "invites"):
        if source_envelope_digests[name] != database_state["envelope_digests"][name]:
            mismatches.append(
                {
                    "entity": f"{name}_envelope",
                    "kind": "digest",
                    "source": source_envelope_digests[name],
                    "database": database_state["envelope_digests"][name],
                }
            )
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "source_path": bundle.source_root.as_posix(),
        "source_digest": bundle.report["source_digest"],
        "database_digest": database_state["digest"],
        "source_counts": source_counts,
        "database_counts": database_state["counts"],
        "source_entity_digests": source_entity_digests,
        "database_entity_digests": database_state["entity_digests"],
        "source_envelope_digests": source_envelope_digests,
        "database_envelope_digests": database_state["envelope_digests"],
        "mismatches": mismatches,
        "verified": not mismatches,
    }


def _ensure_importable(bundle: SourceBundle, expected_digest: str) -> None:
    if bundle.report["blocking_conflicts"]:
        raise PlanBlockedError(
            "source plan has blocking conflicts; repair JSON and run plan again"
        )
    if expected_digest != bundle.report["source_digest"]:
        raise SourceDigestMismatch(
            "source digest changed after planning: "
            f"expected {expected_digest}, found {bundle.report['source_digest']}"
        )


def _assert_source_snapshot_current(bundle: SourceBundle) -> None:
    from tools.workflow_transfer import private_files
    if private_files(bundle.source_root)[1]:
        raise SourceDigestMismatch('Private workflow source contains a symbolic link')
    if _snapshot_files(bundle.source_root) != bundle.report["snapshot"]["files"]:
        raise SourceDigestMismatch(
            "recognized source files changed after the canonical scan; retry while writes are quiesced"
        )


def _target_has_rows(session: Any, api: Mapping[str, Any]) -> bool:
    select = api["select"]
    func = api["func"]
    models = (
        api["User"],
        api["Campaign"],
        api["CampaignMembership"],
        api["Character"],
        api["CharacterAssignment"],
        api["Invitation"],
        api["InviteRedemption"],
        api["Draft"],
        api["CharacterWorkflowReceipt"],
        api["AuditEvent"],
    )
    return any(
        session.scalar(select(func.count()).select_from(model))
        for model in models
    )


def _acquire_import_advisory_lock(session: Any, api: Mapping[str, Any]) -> None:
    """Serialize imports of every source digest for one PostgreSQL database."""

    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        api["text"]("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": IMPORT_ADVISORY_LOCK_KEY},
    )


def _configure_repeatable_read(session: Any) -> None:
    """Pin all verification/export reads to one PostgreSQL snapshot."""

    if session.get_bind().dialect.name == "postgresql":
        session.connection(
            execution_options={"isolation_level": "REPEATABLE READ"}
        )


def import_store(
    source: os.PathLike[str] | str,
    database_url: str,
    *,
    expected_digest: str,
) -> dict[str, Any]:
    """Atomically import one reviewed source snapshot into an empty SQL shadow."""

    bundle = inspect_source(source)
    _ensure_importable(bundle, expected_digest)
    _assert_source_snapshot_current(bundle)
    api = _persistence_api()
    database = api["Database"](database_url)
    try:
        with database.transaction() as session:
            _acquire_import_advisory_lock(session, api)
            run_repository = api["MigrationRunRepository"](session)
            existing = run_repository.get_by_source_checksum(expected_digest)
            if existing is not None and existing.status in {"succeeded", "verified"}:
                verification = _verify_store_session(bundle, session, api)
                if not verification["verified"]:
                    raise VerificationError(
                        "existing migration run does not match database contents"
                    )
                _assert_source_snapshot_current(bundle)
                return {
                    "status": "already_imported",
                    "migration_run_id": existing.id,
                    "source_digest": expected_digest,
                    "entity_counts": dict(bundle.report["entity_counts"]),
                    "verification": verification,
                }
            if existing is not None:
                raise MigrationToolError(
                    f"migration run {existing.id} is in unexpected status {existing.status!r}"
                )
            other_run = session.scalar(
                api["select"](api["MigrationRun"]).limit(1)
            )
            if other_run is not None or _target_has_rows(session, api):
                raise MigrationToolError(
                    "transactional store is not empty and was imported from a different snapshot"
                )

            run = run_repository.start(
                source_checksum=expected_digest,
                source_path=bundle.source_root.as_posix(),
                tool_version=TOOL_VERSION,
                entity_counts=dict(bundle.report["entity_counts"]),
                details={
                    "snapshot": copy.deepcopy(bundle.report["snapshot"]),
                    "entity_digests": copy.deepcopy(bundle.report["entity_digests"]),
                    "envelope_metadata": copy.deepcopy(bundle.envelope_metadata),
                    "warnings": copy.deepcopy(bundle.report["warnings"]),
                },
            )
            user_repository = api["UserRepository"](session)
            for row in bundle.records["users"]:
                user_repository.upsert(
                    user_id=row["id"],
                    username=row["username"],
                    display_name=row["display_name"],
                    password_hash=row["password_hash"],
                    is_admin=row["is_admin"],
                    session_version=row["session_version"],
                    created_at=_parse_datetime(row["created_at"]),
                    last_login_at=_parse_datetime(row["last_login"]),
                    last_campaign_id=row["last_campaign_id"],
                    source_payload=row["source_payload"],
                    source_checksum=row["source_checksum"],
                )

            campaign_repository = api["CampaignRepository"](session)
            for row in bundle.records["campaigns"]:
                campaign_repository.upsert(
                    campaign_id=row["id"],
                    slug=row["slug"],
                    name=row["name"],
                    system=row["system"],
                    created_by_user_id=row["created_by"],
                    created_at=_parse_datetime(row["created_at"]),
                    trashed_at=_parse_datetime(row["trashed_at"]),
                    session_number=row["session_number"],
                    settings=row["settings"],
                    source_payload=row["source_payload"],
                    source_checksum=row["source_checksum"],
                )

            character_repository = api["CharacterRepository"](session)
            for row in bundle.records["characters"]:
                character_repository.upsert_identity(
                    character_id=row["id"],
                    campaign_id=row["campaign_id"],
                    system=row["system"],
                    display_name=row["display_name"],
                    legacy_storage=row["legacy_storage"],
                    legacy_file=row["legacy_file"],
                    source_schema_version=row["source_schema_version"],
                    content_checksum=row["content_checksum"],
                )

            for row in bundle.records["campaign_memberships"]:
                campaign_repository.upsert_membership(
                    campaign_id=row["campaign_id"],
                    user_id=row["user_id"],
                    role=row["role"],
                    character_id=row["character_id"],
                )

            for row in bundle.records["character_assignments"]:
                character_repository.set_assignment(
                    campaign_id=row["campaign_id"],
                    character_id=row["character_id"],
                    user_id=row["user_id"],
                    role=row["role"],
                )

            invitation_repository = api["InvitationRepository"](session)
            for row in bundle.records["invites"]:
                invitation_repository.upsert(
                    code=row["code"],
                    campaign_id=row["campaign_id"],
                    role=row["role"],
                    character_id=row["character_id"],
                    creates_account=row["creates_account"],
                    remaining_uses=row["remaining_uses"],
                    created_by_user_id=row["created_by"],
                    expires_at=row["expires_at"],
                    source_payload=row["source_payload"],
                    source_checksum=row["source_checksum"],
                )

            for row in bundle.records['character_drafts']:
                values = dict(row)
                for name in ('created_at', 'updated_at', 'expires_at'):
                    values[name] = _parse_datetime(values[name])
                session.add(api['Draft'](**values))
            for row in bundle.records['character_workflow_receipts']:
                values = dict(row)
                values['details'] = values.pop('metadata')
                session.add(api['CharacterWorkflowReceipt'](**values))
            session.flush()
            _assert_source_snapshot_current(bundle)
            verification = _verify_session(bundle, session)
            if not verification["verified"]:
                raise VerificationError(
                    "database contents did not reconcile inside the import transaction"
                )
            run_repository.finish(
                run,
                status="succeeded",
                entity_counts=dict(bundle.report["entity_counts"]),
                details={
                    "snapshot": copy.deepcopy(bundle.report["snapshot"]),
                    "entity_digests": copy.deepcopy(bundle.report["entity_digests"]),
                    "envelope_metadata": copy.deepcopy(bundle.envelope_metadata),
                    "warnings": copy.deepcopy(bundle.report["warnings"]),
                    "verification": verification,
                },
            )
            _assert_source_snapshot_current(bundle)
            result = {
                "status": "imported",
                "migration_run_id": run.id,
                "source_digest": expected_digest,
                "entity_counts": dict(bundle.report["entity_counts"]),
                "verification": verification,
            }
        return result
    finally:
        database.dispose()


def _verify_store_session(
    bundle: SourceBundle, session: Any, api: Mapping[str, Any]
) -> dict[str, Any]:
    """Verify entity state and the durable evidence for one completed import."""

    report = _verify_session(bundle, session)
    run = api["MigrationRunRepository"](session).get_by_source_checksum(
        bundle.report["source_digest"]
    )
    report["migration_run"] = (
        {"id": run.id, "status": run.status} if run is not None else None
    )
    if run is None or run.status not in {"succeeded", "verified"}:
        report["mismatches"].append(
            {
                "entity": "migration_runs",
                "kind": "missing_completed_run",
                "source": bundle.report["source_digest"],
                "database": None if run is None else run.status,
            }
        )
    else:
        stored_counts = dict(run.entity_counts or {})
        expected_counts = dict(bundle.report["entity_counts"])
        if stored_counts != expected_counts:
            report["mismatches"].append(
                {
                    "entity": "migration_runs",
                    "kind": "evidence_counts",
                    "source": expected_counts,
                    "database": stored_counts,
                }
            )
        details = run.details if isinstance(run.details, Mapping) else {}
        stored_digests = details.get("entity_digests", {})
        if stored_digests != bundle.report["entity_digests"]:
            report["mismatches"].append(
                {
                    "entity": "migration_runs",
                    "kind": "evidence_entity_digests",
                    "source": bundle.report["entity_digests"],
                    "database": stored_digests,
                }
            )
    report["verified"] = not report["mismatches"]
    return report


def verify_store(
    source: os.PathLike[str] | str,
    database_url: str,
) -> dict[str, Any]:
    """Reconcile canonical entity counts and digests without modifying SQL."""

    bundle = inspect_source(source)
    if bundle.report["blocking_conflicts"]:
        raise PlanBlockedError(
            "source plan has blocking conflicts; verification is unsafe"
        )
    api = _persistence_api()
    database = api["Database"](database_url)
    try:
        with database.session() as session:
            _configure_repeatable_read(session)
            report = _verify_store_session(bundle, session, api)
            _assert_source_snapshot_current(bundle)
            return report
    finally:
        database.dispose()


def _validate_export_target(source_root: Path, output_dir: os.PathLike[str] | str) -> Path:
    raw_target = Path(output_dir).expanduser()
    if raw_target.is_symlink():
        raise ExportTargetError("export target must not be a symbolic link")
    target = raw_target.resolve(strict=False)
    if target == source_root or target.is_relative_to(source_root) or source_root.is_relative_to(target):
        raise ExportTargetError(
            "export target and source must be separate, non-nested directories"
        )
    if target.exists():
        if not target.is_dir():
            raise ExportTargetError("export target exists and is not a directory")
        if any(target.iterdir()):
            raise ExportTargetError("export target must be new or empty")
    return target


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_store(
    source: os.PathLike[str] | str,
    database_url: str,
    output_dir: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Reverse-export verified SQL state and checksum-verified character files."""

    bundle = inspect_source(source)
    if bundle.report["blocking_conflicts"]:
        raise PlanBlockedError("source plan has blocking conflicts; export refused")
    target = _validate_export_target(bundle.source_root, output_dir)
    api = _persistence_api()
    database = api["Database"](database_url)
    target.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        with database.session() as session:
            _configure_repeatable_read(session)
            verification = _verify_store_session(bundle, session, api)
            if not verification["verified"]:
                raise VerificationError(
                    "SQL does not match the source snapshot; export refused"
                )
            _assert_source_snapshot_current(bundle)
            envelope_metadata = _stored_envelope_metadata(
                session, bundle.report["source_digest"]
            )
            users = session.scalars(
                api["select"](api["User"]).order_by(api["User"].id)
            ).all()
            users_document = dict(envelope_metadata["users"])
            users_document["users"] = {
                user.id: _json_safe(user.source_payload) for user in users
            }
            _write_json(
                staging / "users.json",
                users_document,
            )

            campaigns = session.scalars(
                api["select"](api["Campaign"]).order_by(api["Campaign"].id)
            ).all()
            campaign_by_id = {campaign.id: campaign for campaign in campaigns}
            for campaign in campaigns:
                root_name = "campaigns_trash" if campaign.trashed_at is not None else "campaigns"
                _write_json(
                    staging / root_name / campaign.id / "campaign.json",
                    _json_safe(campaign.source_payload),
                )

            characters = session.scalars(
                api["select"](api["Character"]).order_by(api["Character"].id)
            ).all()
            for character in characters:
                campaign = campaign_by_id[character.campaign_id]
                root_name = "campaigns_trash" if campaign.trashed_at is not None else "campaigns"
                source_character = (
                    bundle.source_root
                    / root_name
                    / character.campaign_id
                    / character.legacy_storage
                    / character.legacy_file
                )
                document = _load_json_strict(source_character)
                actual_checksum = _canonical_sha256(document)
                if actual_checksum != character.content_checksum:
                    raise VerificationError(
                        "character source changed during export: "
                        f"{_relative_posix(source_character, bundle.source_root)}"
                    )
                destination = (
                    staging
                    / root_name
                    / character.campaign_id
                    / character.legacy_storage
                    / character.legacy_file
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_character, destination)

            invites = session.scalars(
                api["select"](api["Invitation"]).order_by(api["Invitation"].code)
            ).all()
            invites_document = dict(envelope_metadata["invites"])
            invites_document["invites"] = {
                invitation.code: _json_safe(invitation.source_payload)
                for invitation in invites
            }
            _write_json(
                staging / "invites.json",
                invites_document,
            )
            _write_json(
                staging / EXPORT_MARKER,
                {
                    "schema_version": EXPORT_SCHEMA_VERSION,
                    "source_digest": bundle.report["source_digest"],
                    "entity_counts": bundle.report["entity_counts"],
                },
            )

            from tools.workflow_transfer import write_private
            write_private(staging, _database_records(session))

        exported_plan = build_plan(staging)
        if exported_plan["blocking_conflicts"]:
            raise VerificationError("reverse export produced blocking conflicts")
        if exported_plan["source_digest"] != bundle.report["source_digest"]:
            raise VerificationError("reverse export digest does not match imported source")
        _assert_source_snapshot_current(bundle)

        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        return {
            "status": "exported",
            "output_dir": target.as_posix(),
            "source_digest": bundle.report["source_digest"],
            "entity_counts": dict(bundle.report["entity_counts"]),
            "verified": True,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        database.dispose()


def _database_url(value: str | None) -> str:
    resolved = value or os.environ.get("DATABASE_URL")
    if not resolved:
        raise MigrationToolError(
            "database URL is required via --database-url or DATABASE_URL"
        )
    return resolved


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _write_plan_file(report: Mapping[str, Any], source: Path, output: str) -> None:
    path = Path(output).expanduser().resolve(strict=False)
    if path == source or path.is_relative_to(source):
        raise MigrationToolError("plan output must not be written into the source tree")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    plan_parser = commands.add_parser("plan", help="inspect JSON without writing")
    plan_parser.add_argument("--source", required=True, help="legacy DATA_DIR")
    plan_parser.add_argument(
        "--output", help="create this report file outside the source tree"
    )

    import_parser = commands.add_parser("import", help="transactionally import SQL")
    import_parser.add_argument("--source", required=True, help="legacy DATA_DIR")
    import_parser.add_argument("--expect-digest", required=True)
    import_parser.add_argument("--database-url")

    verify_parser = commands.add_parser("verify", help="reconcile JSON and SQL")
    verify_parser.add_argument("--source", required=True, help="legacy DATA_DIR")
    verify_parser.add_argument("--database-url")

    export_parser = commands.add_parser("export", help="reverse-export verified SQL")
    export_parser.add_argument("--source", required=True, help="legacy DATA_DIR")
    export_parser.add_argument("--output-dir", required=True)
    export_parser.add_argument("--database-url")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            report = build_plan(args.source)
            if args.output:
                _write_plan_file(report, _safe_source_root(args.source), args.output)
            _emit(report)
            return 0 if report["import_allowed"] else 2
        if args.command == "import":
            _emit(
                import_store(
                    args.source,
                    _database_url(args.database_url),
                    expected_digest=args.expect_digest,
                )
            )
            return 0
        if args.command == "verify":
            report = verify_store(args.source, _database_url(args.database_url))
            _emit(report)
            return 0 if report["verified"] else 2
        if args.command == "export":
            _emit(
                export_store(
                    args.source,
                    _database_url(args.database_url),
                    args.output_dir,
                )
            )
            return 0
        raise AssertionError(args.command)
    except MigrationToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
