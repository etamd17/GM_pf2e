"""The campaign stream row: lock-order step 1 and seq allocation (PE-14a).

``lock_stream`` conforms to PE-13a's ``LockStream``; PE-14b passes it to
``CommandLocks(session, campaign_id, lock_stream=lock_stream)`` and PE-14d/e/f
call it directly.  The row is created lazily under the lock (``INSERT ... ON
CONFLICT DO NOTHING`` then ``SELECT ... FOR UPDATE``), so the campaign row must
already exist in the transaction (``create_campaign`` inserts it first).

Within one transaction a second call for the same campaign returns the held
row without waiting or inserting again; a call for another campaign raises,
because no writer reaches several campaigns.  The guard lives in
``session.info`` keyed to ``session.get_transaction()``, so it never survives
``commit()`` or ``rollback()``.  Taking the lock for the first time inside a
SAVEPOINT is refused: rolling the SAVEPOINT back would release the row lock
while the guard still reported it held.

``stream_watermark`` and ``events_after`` are plain reads for PE-31: no lock,
no projection, no page limit of their own.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session, object_session

from .models import CampaignStream, GameEvent

_HELD = "core.persistence.streams.held"


class StreamLockError(RuntimeError):
    """``lock_stream``/``allocate`` was used outside its contract (a caller bug)."""


def _held(session: Session):
    """``(campaign_id, row)`` held by the current transaction, or ``None``."""

    entry = session.info.get(_HELD)
    if entry is None or entry[0] is not session.get_transaction():
        return None
    return entry[1], entry[2]


def lock_stream(session: Session, campaign_id: str) -> CampaignStream:
    """Lock (creating if absent) ``campaign_id``'s stream row for this transaction."""

    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError("campaign_id must be a non-empty string")
    transaction = session.get_transaction()
    if transaction is None:
        raise StreamLockError("lock_stream requires an active transaction")
    held = _held(session)
    if held is not None:
        if held[0] != campaign_id:
            raise StreamLockError(
                f"this transaction already holds the stream of campaign {held[0]}")
        return held[1]
    if session.in_nested_transaction():
        raise StreamLockError("the stream lock cannot be taken first inside a SAVEPOINT")
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgresql.insert(CampaignStream)
    elif dialect == "sqlite":
        insert = sqlite.insert(CampaignStream)
    else:
        raise StreamLockError(f"unsupported database dialect {dialect}")
    session.execute(insert.values(campaign_id=campaign_id, last_seq=0)
                    .on_conflict_do_nothing(index_elements=["campaign_id"]))
    row = session.execute(
        select(CampaignStream).where(CampaignStream.campaign_id == campaign_id)
        .with_for_update().execution_options(populate_existing=True)
    ).scalar_one()
    session.info[_HELD] = (transaction, campaign_id, row)
    return row


def allocate(stream: CampaignStream, n: int) -> range:
    """Allocate ``n`` contiguous seqs (``last_seq+1 .. last_seq+n``) under the held lock."""

    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError("n must be a positive integer")
    session = object_session(stream)
    held = None if session is None else _held(session)
    if held is None or held[1] is not stream:
        raise StreamLockError("allocate needs the stream row this transaction locked")
    first = stream.last_seq + 1
    stream.last_seq += n
    session.flush()
    return range(first, first + n)


def stream_watermark(session: Session, campaign_id: str) -> int:
    """The campaign's ``last_seq`` (0 with no stream row), read without a lock."""

    value = session.scalar(
        select(CampaignStream.last_seq).where(CampaignStream.campaign_id == campaign_id))
    return 0 if value is None else value


def events_after(session: Session, campaign_id: str, after_seq: int,
                 limit: int) -> list[GameEvent]:
    """At most ``limit`` events with ``seq > after_seq``, in seq order."""

    for name, value, low in (("after_seq", after_seq, 0), ("limit", limit, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < low:
            raise ValueError(f"{name} must be an integer >= {low}")
    return list(session.execute(
        select(GameEvent).where(GameEvent.campaign_id == campaign_id,
                                GameEvent.seq > after_seq)
        .order_by(GameEvent.seq).limit(limit)
    ).scalars())
