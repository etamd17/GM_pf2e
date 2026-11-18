"""SQL token records for the Obsidian integration in ``sql`` mode (task PE-14c, Decision 1).

Every function here opens its own short transaction through
``runtime.database()`` and closes it before returning; none runs inside a
command transaction.  The ``tokens.json`` side is reached only from
:func:`repair_token_mirror`, which imports ``core.obsidian_sync`` inside the
function and takes ``campaign_lock`` only with no transaction open (Decision 7).

Every read and write selects ``WHERE campaign_id = :campaign_id``: a
credential id held by another campaign counts as not found here, and an
import never inserts or changes another campaign's row.

PE-14d later adds its ``lock_timeout`` statement and ``lock_stream`` at the
start of the import, mint and revoke write transactions; this task leaves
them as plain SQL transactions.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import insert, select, update

from . import runtime
from .models import IntegrationToken

logger = logging.getLogger(__name__)
_T = IntegrationToken.__table__
_ISO = "%Y-%m-%dT%H:%M:%SZ"
_DIGEST = re.compile(r"\A[0-9a-f]{64}\Z")
LIVE_SCOPE = ["@live"]
LAST_USED_GRANULARITY_SECONDS = 300


def parse_time(value: Any) -> datetime | None:
    """A file timestamp (``YYYY-MM-DDTHH:MM:SSZ``) as an aware UTC datetime, else None."""

    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, _ISO).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def format_time(value: datetime | None) -> str | None:
    value = _aware(value)
    return None if value is None else value.astimezone(timezone.utc).strftime(_ISO)


def _row(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    for name in ("created_at", "last_used_at", "revoked_at"):
        data[name] = _aware(data[name])
    return data


def public_record(row: dict[str, Any]) -> dict[str, Any]:
    """The same public fields ``core.obsidian_sync.public_token_record`` returns."""

    return {
        "id": row["credential_id"],
        "label": row["label"],
        "last_four": row["last_four"],
        "created_at": format_time(row["created_at"]),
        "last_used_at": format_time(row["last_used_at"]),
        "revoked_at": format_time(row["revoked_at"]),
    }


def file_record(row: dict[str, Any]) -> dict[str, Any]:
    """A ``tokens.json`` record built from a SQL row (mirror and revoke tombstone)."""

    return {
        "id": row["credential_id"],
        "label": row["label"],
        "token_hash": row["token_digest"],
        "last_four": row["last_four"],
        "created_at": format_time(row["created_at"]),
        "last_used_at": format_time(row["last_used_at"]),
        "revoked_at": format_time(row["revoked_at"]),
    }


def _rows(session: Any, campaign_id: str) -> list[dict[str, Any]]:
    return [_row(r) for r in session.execute(
        select(_T).where(_T.c.campaign_id == campaign_id)
        .order_by(_T.c.created_at, _T.c.credential_id)).all()]


def list_rows(campaign_id: str) -> list[dict[str, Any]]:
    """Every SQL token row of the campaign, in a short read-only transaction."""

    with runtime.database().transaction() as session:
        return _rows(session, campaign_id)


def get_row(campaign_id: str, credential_id: str) -> dict[str, Any] | None:
    """One SQL row in this campaign (revoke step 0), in a short read-only transaction."""

    with runtime.database().transaction() as session:
        row = session.execute(select(_T).where(
            _T.c.campaign_id == campaign_id, _T.c.credential_id == credential_id)).first()
        return None if row is None else _row(row)


def find_by_digest(campaign_id: str, digest: str) -> dict[str, Any] | None:
    """The campaign's SQL row whose digest matches, if any (the bearer check)."""

    with runtime.database().transaction() as session:
        row = session.execute(select(_T).where(
            _T.c.campaign_id == campaign_id, _T.c.token_digest == digest)
            .order_by(_T.c.credential_id).limit(1)).first()
        return None if row is None else _row(row)


def _importable(record: Any) -> bool:
    return (isinstance(record, dict) and isinstance(record.get("id"), str) and record["id"]
            and len(record["id"]) <= 64 and isinstance(record.get("token_hash"), str)
            and _DIGEST.match(record["token_hash"]) is not None)


def _owners(session: Any, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    ids = list(ids)
    if not ids:
        return {}
    return {r["credential_id"]: r for r in (_row(row) for row in session.execute(
        select(_T).where(_T.c.credential_id.in_(ids))).all())}


def _pending(campaign_id: str, records: list[dict[str, Any]],
             held: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """File records SQL lacks, or whose revocation a live SQL row lacks."""

    pending = []
    for record in records:
        row = held.get(record["id"])
        if row is None:
            pending.append(record)
        elif row["campaign_id"] != campaign_id:
            continue
        elif record.get("revoked_at") and row["revoked_at"] is None:
            pending.append(record)
    return pending


def import_file_records(campaign_id: str, records: Iterable[Any]) -> int:
    """The shared import transaction (``verify_token`` and ``repair_token_mirror``).

    Reads the SQL side first and opens a write transaction only when a file
    record is missing from SQL or carries a revocation a live SQL row lacks.
    Inserts are insert-if-absent with ``credential_id`` = the file ``id`` and
    scope ``["@live"]`` (C:851-852); a file revocation is copied onto a live
    row and never cleared.  An id held by another campaign is skipped and
    logged with no secret.  Returns the number of rows written.
    """

    records = [r for r in records if _importable(r)]
    if not records:
        return 0
    with runtime.database().transaction() as session:
        held = _owners(session, (r["id"] for r in records))
    _log_conflicts(campaign_id, records, held)
    pending = _pending(campaign_id, records, held)
    if not pending:
        return 0
    written = 0
    with runtime.database().transaction() as session:
        held = _owners(session, (r["id"] for r in pending))
        for record in _pending(campaign_id, pending, held):
            row = held.get(record["id"])
            revoked = parse_time(record.get("revoked_at")) if record.get("revoked_at") else None
            if record.get("revoked_at") and revoked is None:
                revoked = datetime.now(timezone.utc)
            if row is None:
                session.execute(insert(_T).values(
                    credential_id=record["id"], campaign_id=campaign_id,
                    label=str(record.get("label") or "Obsidian")[:80],
                    token_digest=record["token_hash"],
                    last_four=str(record.get("last_four") or "")[:8],
                    scope=list(LIVE_SCOPE), replaces_credential_id=None, minted_by_user_id=None,
                    created_at=parse_time(record.get("created_at")) or datetime.now(timezone.utc),
                    last_used_at=parse_time(record.get("last_used_at")), revoked_at=revoked))
            else:
                session.execute(update(_T).where(
                    _T.c.credential_id == record["id"], _T.c.campaign_id == campaign_id,
                    _T.c.revoked_at.is_(None)).values(revoked_at=revoked))
            written += 1
    return written


def _log_conflicts(campaign_id: str, records: list[dict[str, Any]],
                   held: dict[str, dict[str, Any]]) -> None:
    for record in records:
        row = held.get(record["id"])
        if row is not None and row["campaign_id"] != campaign_id:
            logger.warning("obsidian_token_import_conflict campaign=%s", campaign_id)


def insert_minted(campaign_id: str, record: dict[str, Any], *,
                  scope: list[str] | None = None, replaces_credential_id: str | None = None,
                  minted_by_user_id: str | None = None) -> None:
    """Mint's SQL row, committed before the file copy (Decision 1)."""

    with runtime.database().transaction() as session:
        session.execute(insert(_T).values(
            credential_id=record["id"], campaign_id=campaign_id, label=record["label"],
            token_digest=record["token_hash"], last_four=record["last_four"],
            scope=list(LIVE_SCOPE if scope is None else scope),
            replaces_credential_id=replaces_credential_id, minted_by_user_id=minted_by_user_id,
            created_at=parse_time(record.get("created_at")) or datetime.now(timezone.utc),
            last_used_at=None, revoked_at=None))


def revoke_row(campaign_id: str, credential_id: str,
               file_record_seen: dict[str, Any] | None) -> None:
    """Revoke step 2: stamp ``revoked_at`` where NULL, or insert the row already revoked.

    The insert happens only when SQL has no row in this campaign and the file
    had one (``file_record_seen``), with scope ``["@live"]`` and no minter.
    """

    now = datetime.now(timezone.utc)
    with runtime.database().transaction() as session:
        row = session.execute(select(_T.c.campaign_id, _T.c.revoked_at).where(
            _T.c.credential_id == credential_id)).first()
        if row is not None:
            if row.campaign_id == campaign_id and row.revoked_at is None:
                session.execute(update(_T).where(
                    _T.c.credential_id == credential_id, _T.c.campaign_id == campaign_id,
                    _T.c.revoked_at.is_(None)).values(revoked_at=now))
            return
        if file_record_seen is None or not _importable(file_record_seen):
            return
        record = file_record_seen
        session.execute(insert(_T).values(
            credential_id=credential_id, campaign_id=campaign_id,
            label=str(record.get("label") or "Obsidian")[:80],
            token_digest=record["token_hash"], last_four=str(record.get("last_four") or "")[:8],
            scope=list(LIVE_SCOPE), replaces_credential_id=None, minted_by_user_id=None,
            created_at=parse_time(record.get("created_at")) or now,
            last_used_at=parse_time(record.get("last_used_at")),
            revoked_at=parse_time(record.get("revoked_at")) or now))


def touch_last_used(campaign_id: str, credential_id: str,
                    previous: datetime | None) -> bool:
    """Write ``last_used_at`` at most every 300 s, in its own short transaction.

    Not an authorization input (C:460-465), so no stream lock and no
    ``lock_timeout``.  Returns whether a write was made.
    """

    now = datetime.now(timezone.utc)
    previous = _aware(previous)
    if previous is not None and (now - previous).total_seconds() < LAST_USED_GRANULARITY_SECONDS:
        return False
    with runtime.database().transaction() as session:
        session.execute(update(_T).where(
            _T.c.credential_id == credential_id, _T.c.campaign_id == campaign_id)
            .values(last_used_at=now))
    return True


def repair_token_mirror(campaign_id: str) -> bool:
    """Copy SQL records and revocations to ``tokens.json``, then complete partial revokes.

    Reads SQL with no lock held; under ``campaign_lock`` (no transaction open)
    adds missing records and missing revocations to the file, insert-if-absent,
    writing only when something differs, never clearing a revocation and never
    deleting a record; after releasing the lock, runs the import transaction so
    a file revocation a live SQL row lacks is completed.  Raises on failure
    (``list_tokens`` logs it).  Returns whether the file was written.
    """

    from core import obsidian_sync as store
    from core import storage

    rows = list_rows(campaign_id)
    with store.campaign_lock(campaign_id):
        doc = store._load_tokens_strict(campaign_id)
        tokens = doc["tokens"]
        by_id = {r.get("id"): r for r in tokens if isinstance(r, dict)}
        changed = False
        for row in rows:
            record = by_id.get(row["credential_id"])
            if record is None:
                record = file_record(row)
                tokens.append(record)
                by_id[record["id"]] = record
                changed = True
            elif row["revoked_at"] is not None and not record.get("revoked_at"):
                record["revoked_at"] = format_time(row["revoked_at"])
                changed = True
        if changed:
            storage.atomic_write_json(store.tokens_file(campaign_id), doc)
        snapshot = [dict(r) for r in tokens if isinstance(r, dict)]
    import_file_records(campaign_id, snapshot)
    return changed
