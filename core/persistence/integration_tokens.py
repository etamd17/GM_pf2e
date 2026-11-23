"""SQL token records for the Obsidian integration in ``sql`` mode (task PE-14c, Decision 1).

Every function here opens its own short transaction through
``runtime.database()`` and closes it before returning; none runs inside a
command transaction.  The ``tokens.json`` side is reached only from
:func:`repair_token_mirror`, which imports ``core.obsidian_sync`` inside the
function and takes ``campaign_lock`` only with no transaction open (Decision 7).

Every read and write selects ``WHERE campaign_id = :campaign_id``: a
credential id held by another campaign counts as not found here, and an
import never inserts or changes another campaign's row.

The import, mint and revoke write transactions start with
``lock_stream_for_write`` (``lock_timeout`` on PostgreSQL, then the stream
row; PE-14d).  Reads and the ``last_used_at`` update take no stream lock.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import insert, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite

from . import runtime
from .models import CampaignMembership, IntegrationToken, User
from .streams import lock_stream_for_write

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


def _held(session: Any, campaign_id: str, ids: Iterable[str]) -> dict[str, datetime | None]:
    """``{credential_id: revoked_at}`` for this campaign's rows among ``ids`` (scoped read)."""

    ids = sorted(set(ids))
    if not ids:
        return {}
    return {row.credential_id: _aware(row.revoked_at) for row in session.execute(
        select(_T.c.credential_id, _T.c.revoked_at).where(
            _T.c.campaign_id == campaign_id, _T.c.credential_id.in_(ids))).all()}


def _foreign(session: Any, campaign_id: str, ids: Iterable[str]) -> set[str]:
    """Which of ``ids`` another campaign holds: the ids only, nothing else of the row."""

    ids = sorted(set(ids))
    if not ids:
        return set()
    return set(session.execute(select(_T.c.credential_id).where(
        _T.c.campaign_id != campaign_id, _T.c.credential_id.in_(ids))).scalars())


def _revocation(record: dict[str, Any], now: datetime) -> datetime | None:
    if not record.get("revoked_at"):
        return None
    return parse_time(record.get("revoked_at")) or now


def _insert_if_absent(session: Any, values: dict[str, Any]) -> bool:
    """INSERT ... ON CONFLICT (credential_id) DO NOTHING; True when this call inserted.

    The key is global, so a concurrent writer of any campaign may win the id;
    a collision never raises and never rolls back the caller's transaction.
    The signal is the RETURNING row, not ``rowcount``: psycopg may close a
    non-returning cursor before its count is read.
    """

    name = session.get_bind().dialect.name
    if name == "postgresql":
        statement = postgresql.insert(_T)
    elif name == "sqlite":
        statement = sqlite.insert(_T)
    else:  # pragma: no cover - only the two supported dialects
        raise RuntimeError(f"unsupported dialect {name}")
    return session.execute(statement.values(**values)
                           .on_conflict_do_nothing(index_elements=["credential_id"])
                           .returning(_T.c.credential_id)).first() is not None


def _file_values(campaign_id: str, record: dict[str, Any], now: datetime) -> dict[str, Any]:
    return dict(
        credential_id=record["id"], campaign_id=campaign_id,
        label=str(record.get("label") or "Obsidian")[:80], token_digest=record["token_hash"],
        last_four=str(record.get("last_four") or "")[:8], scope=list(LIVE_SCOPE),
        replaces_credential_id=None, minted_by_user_id=None, minted_as=None,
        created_at=parse_time(record.get("created_at")) or now,
        last_used_at=parse_time(record.get("last_used_at")),
        revoked_at=_revocation(record, now))


def _revoke_live(session: Any, campaign_id: str, credential_id: str, when: datetime) -> bool:
    """Stamp ``revoked_at`` on this campaign's live row; never clears or moves one."""

    return session.execute(update(_T).where(
        _T.c.credential_id == credential_id, _T.c.campaign_id == campaign_id,
        _T.c.revoked_at.is_(None)).values(revoked_at=when)
        .returning(_T.c.credential_id)).first() is not None


def _reconcile(session: Any, campaign_id: str, record: dict[str, Any], now: datetime) -> int:
    """Import one file record: insert-if-absent, else copy a revocation onto our live row.

    Returns 1 when a row was written.  A row another campaign holds is
    skipped and logged with no secret.
    """

    if _insert_if_absent(session, _file_values(campaign_id, record, now)):
        return 1
    held = _held(session, campaign_id, [record["id"]])
    if record["id"] not in held:
        logger.warning("obsidian_token_import_conflict campaign=%s", campaign_id)
        return 0
    revoked = _revocation(record, now)
    if revoked is not None and held[record["id"]] is None:
        return int(_revoke_live(session, campaign_id, record["id"], revoked))
    return 0


def import_file_records(campaign_id: str, records: Iterable[Any]) -> int:
    """The shared import transaction (``verify_token`` and ``repair_token_mirror``).

    Reads this campaign's rows first (scoped) and opens a write transaction
    only when a file record is missing from SQL or carries a revocation a
    live SQL row lacks.  An id another campaign holds is logged and skipped,
    learned from an id-only read.  Inserts are conflict-safe (``ON CONFLICT
    DO NOTHING``) with ``credential_id`` = the file ``id`` and scope
    ``["@live"]`` (C:851-852): a row another writer inserted first is then
    reconciled within this campaign, and a collision never rolls back the
    other records of the batch.  A file revocation is copied onto a live row
    and never cleared.  Repeated ids are reconciled one record at a time
    against fresh reads, so any revoked copy wins.  Returns rows written.
    """

    records = [r for r in records if _importable(r)]
    if not records:
        return 0
    ids = [r["id"] for r in records]
    with runtime.database().transaction() as session:
        held = _held(session, campaign_id, ids)
        foreign = _foreign(session, campaign_id, set(ids) - set(held))
    for _ in foreign:
        logger.warning("obsidian_token_import_conflict campaign=%s", campaign_id)
    pending = [r for r in records if r["id"] not in foreign and (
        r["id"] not in held or (r.get("revoked_at") and held[r["id"]] is None))]
    if not pending:
        return 0
    now = datetime.now(timezone.utc)
    with runtime.database().transaction() as session:
        lock_stream_for_write(session, campaign_id)
        return sum(_reconcile(session, campaign_id, record, now) for record in pending)


class ReplacementNotAuthorized(ValueError):
    """A replacement mint whose minter or original token does not qualify (Decision 4)."""


def _minter_role(session: Any, campaign_id: str, user_id: str) -> str | None:
    """``"admin"``, ``"gm"`` or None: the minter's authority in this campaign now (at mint)."""

    users, members = User.__table__, CampaignMembership.__table__
    admin = session.execute(select(users.c.is_admin).where(users.c.id == user_id)).first()
    if admin is None:
        return None
    if admin[0] is True:
        return "admin"
    role = session.execute(select(members.c.role).where(
        members.c.campaign_id == campaign_id, members.c.user_id == user_id)).first()
    return "gm" if role is not None and role[0] == "gm" else None


def insert_minted(campaign_id: str, record: dict[str, Any], *,
                  scope: list[str] | None = None, replaces_credential_id: str | None = None,
                  minted_by_user_id: str | None = None) -> None:
    """Mint's SQL row, committed before the file copy (Decision 1).

    With ``minted_by_user_id`` the minter's role in this campaign at this
    moment is recorded as ``minted_as`` (``"gm"`` or ``"admin"``, else None).
    A replacement (``replaces_credential_id``) is the historical fact the
    status read trusts (Decision 4), so it is established here, in the mint
    transaction: the minter must be this campaign's GM or a site admin now,
    and the original must be a credential of this campaign.  Otherwise
    :class:`ReplacementNotAuthorized` is raised and nothing is written.
    PE-14d adds the stream lock to this transaction.
    """

    with runtime.database().transaction() as session:
        lock_stream_for_write(session, campaign_id)
        minted_as = (None if minted_by_user_id is None
                     else _minter_role(session, campaign_id, minted_by_user_id))
        if replaces_credential_id is not None:
            original = session.execute(select(_T.c.credential_id).where(
                _T.c.campaign_id == campaign_id,
                _T.c.credential_id == replaces_credential_id)).first()
            if minted_as is None or original is None:
                raise ReplacementNotAuthorized(
                    "a replacement needs this campaign's GM or a site admin as minter "
                    "and an original credential of this campaign")
        session.execute(insert(_T).values(
            credential_id=record["id"], campaign_id=campaign_id, label=record["label"],
            token_digest=record["token_hash"], last_four=record["last_four"],
            scope=list(LIVE_SCOPE if scope is None else scope),
            replaces_credential_id=replaces_credential_id, minted_by_user_id=minted_by_user_id,
            minted_as=minted_as,
            created_at=parse_time(record.get("created_at")) or datetime.now(timezone.utc),
            last_used_at=None, revoked_at=None))


def revoke_row(campaign_id: str, credential_id: str,
               file_record_seen: dict[str, Any] | None) -> None:
    """Revoke step 2: stamp ``revoked_at`` where NULL, or insert the row already revoked.

    The insert happens only when SQL has no row in this campaign and the file
    had one (``file_record_seen``), with scope ``["@live"]`` and no minter.
    It is conflict-safe: if another writer inserts the id first, this
    campaign's row is revoked instead, and another campaign's row is left
    alone and logged.
    """

    now = datetime.now(timezone.utc)
    with runtime.database().transaction() as session:
        lock_stream_for_write(session, campaign_id)
        if _revoke_live(session, campaign_id, credential_id, now):
            return
        held = _held(session, campaign_id, [credential_id])
        if credential_id in held:
            if held[credential_id] is None:
                # A live row committed after the UPDATE's snapshot (READ
                # COMMITTED): this statement sees it.  A row revoked meanwhile
                # keeps its timestamp; another campaign's row never matches.
                _revoke_live(session, campaign_id, credential_id, now)
            return
        if file_record_seen is None or not _importable(file_record_seen):
            return
        record = dict(file_record_seen, id=credential_id)
        record["revoked_at"] = record.get("revoked_at") or format_time(now)
        _reconcile(session, campaign_id, record, now)


def touch_last_used(campaign_id: str, credential_id: str) -> datetime | None:
    """Write ``last_used_at`` at most every 300 s, in its own short transaction.

    The cutoff is a predicate of the UPDATE, so it holds against the stored
    row however many requests observed the same old value.  Not an
    authorization input (C:460-465), so no stream lock and no
    ``lock_timeout``.  Returns the time written, or None when no row changed
    (too recent, or no such row in this campaign).
    """

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=LAST_USED_GRANULARITY_SECONDS)
    with runtime.database().transaction() as session:
        written = session.execute(update(_T).where(
            _T.c.credential_id == credential_id, _T.c.campaign_id == campaign_id,
            or_(_T.c.last_used_at.is_(None), _T.c.last_used_at <= cutoff))
            .values(last_used_at=now).returning(_T.c.credential_id)).first()
        return None if written is None else now


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
