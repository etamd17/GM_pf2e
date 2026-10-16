"""The commit-ordered campaign stream (VTT-ADR-0002 section 5).

Writers
-------
Every durable change that can affect a projection goes through ``advance``
inside the caller's transaction: it locks the campaign's stream row
(``SELECT ... FOR UPDATE`` on PostgreSQL), increments the position and writes
one outbox row at that position. A second writer for the same campaign waits on
the row lock until the first commits or rolls back, so positions become visible
in commit order. An auto-increment outbox ID is *not* used as a cursor: a
sequence value can be allocated before an earlier transaction commits.

Readers
-------
``snapshot_transaction`` opens one transaction at ``REPEATABLE READ`` on
PostgreSQL (``SERIALIZABLE`` on SQLite, the strongest it offers). Inside it the
caller rechecks access, reads the projection and reads the committed position
with ``committed_position``; all three come from one consistent view.
``outbox_after`` then returns what committed after that cursor, in order.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from core.persistence.database import Database

from .models import VttCampaignStream, VttOutbox

# Event kinds written to the outbox. Internal only: an outbox event is never
# sent; delivery re-reads canonical state and projects it per audience.
# "table-access" (M3): a table grant was revoked or the campaign table epoch
# moved. It names nothing; delivery's access recheck does the rest.
EVENT_KINDS = ("entities", "scene", "table-pointer", "table-access")


def _ensure_stream_row(session: Session, campaign_id: str) -> None:
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    session.execute(
        insert(VttCampaignStream)
        .values(campaign_id=campaign_id, position=0)
        .on_conflict_do_nothing(index_elements=["campaign_id"])
    )


def lock_stream(session: Session, campaign_id: str) -> VttCampaignStream:
    """Take the campaign's stream-row lock for the rest of the transaction.

    The row lock is the ordering guarantee. Lock the existing row directly;
    only a campaign's very first write inserts it (concurrent first writers
    meet on the insert's conflict, then both take the lock below). Commands
    take it first, before their receipt lookup, so it also serializes
    concurrent use of one receipt key (section 7 step 4)."""
    locked = select(VttCampaignStream).where(VttCampaignStream.campaign_id == campaign_id).with_for_update()
    row = session.execute(locked).scalar_one_or_none()
    if row is None:
        _ensure_stream_row(session, campaign_id)
        row = session.execute(locked).scalar_one()
    return row


def advance(session: Session, campaign_id: str, event: dict[str, Any]) -> int:
    """Lock the campaign stream, take the next position and record ``event``
    at it. Call inside the same transaction as the change itself.

    ``event["origin"]`` (commands only) is ``{"subscriptionId", "commandId"}``:
    the subscription that issued the command, which alone receives the
    correlated completion frame (section 7). Server-only, like the rest."""
    if event.get("kind") not in EVENT_KINDS:
        raise ValueError("unknown outbox event kind")
    origin = event.get("origin")
    if origin is not None and (not isinstance(origin, dict) or set(origin) != {"subscriptionId", "commandId"}):
        raise ValueError("malformed command origin")
    row = lock_stream(session, campaign_id)
    row.position += 1
    session.add(VttOutbox(campaign_id=campaign_id, position=row.position, event=event))
    session.flush()
    return row.position


def committed_position(session: Session, campaign_id: str) -> int:
    value = session.execute(
        select(VttCampaignStream.position).where(VttCampaignStream.campaign_id == campaign_id)
    ).scalar_one_or_none()
    return int(value or 0)


@contextmanager
def snapshot_transaction(db: Database) -> Iterator[Session]:
    """One consistent read: REPEATABLE READ on PostgreSQL. Read-only; rolled back."""
    dialect = db.engine.dialect.name
    level = "REPEATABLE READ" if dialect == "postgresql" else "SERIALIZABLE"
    with db.engine.connect().execution_options(isolation_level=level) as connection:
        session = Session(bind=connection, expire_on_commit=False)
        try:
            yield session
        finally:
            session.rollback()
            session.close()


def outbox_after(db: Database, campaign_id: str, cursor: int, limit: int) -> list[VttOutbox]:
    """Committed outbox rows after ``cursor``, in position order (at most ``limit``)."""
    with db.session() as session:
        rows = session.execute(
            select(VttOutbox)
            .where(VttOutbox.campaign_id == campaign_id, VttOutbox.position > cursor)
            .order_by(VttOutbox.position)
            .limit(limit)
        ).scalars().all()
        return list(rows)
