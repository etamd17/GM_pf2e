"""Campaign grants: reads and the one-use consume (task PE-14c, Decision 2).

Every function works in the caller's session and transaction; none opens a
transaction of its own.  ``consume`` is a compare-and-set that runs inside a
command transaction, which already holds the campaign's stream row, so no
row lock is taken here (contract C:460-466).  Issue and revoke are PE-14d's.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select, update

from .models import CampaignGrant

_T = CampaignGrant.__table__


class GrantNotLive(RuntimeError):
    """``consume`` found no live grant with that id: already consumed, revoked or missing."""


def _live():
    return and_(_T.c.consumed_at.is_(None), _T.c.revoked_at.is_(None))


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


def live_grants(session: Any, campaign_id: str) -> list[dict[str, Any]]:
    """Every live grant of the campaign, in grant id order."""

    rows = session.execute(select(_T).where(_T.c.campaign_id == campaign_id, _live())
                           .order_by(_T.c.grant_id)).all()
    return [_row(row) for row in rows]


def live_level_unlock(session: Any, campaign_id: str, character_id: str,
                      target_level: int) -> dict[str, Any] | None:
    """The live ``level_unlock`` for exactly this character and target level, if any."""

    row = session.execute(select(_T).where(
        _T.c.campaign_id == campaign_id, _T.c.kind == "level_unlock",
        _T.c.character_id == character_id, _T.c.target_level == target_level, _live(),
    ).order_by(_T.c.grant_id).limit(1)).first()
    return None if row is None else _row(row)


def live_retrain(session: Any, campaign_id: str, character_id: str,
                 build_revision: int) -> dict[str, Any] | None:
    """The live ``retrain_grant`` for exactly this character and build revision, if any."""

    row = session.execute(select(_T).where(
        _T.c.campaign_id == campaign_id, _T.c.kind == "retrain_grant",
        _T.c.character_id == character_id, _T.c.build_revision == build_revision, _live(),
    ).order_by(_T.c.grant_id).limit(1)).first()
    return None if row is None else _row(row)


def consume(session: Any, grant_id: str, operation_id: str, *,
            campaign_id: str | None = None) -> None:
    """Mark a live grant consumed by ``operation_id``; ``GrantNotLive`` when none is live.

    Command transaction only: the caller's executor holds the stream row.  A
    rowcount of 0 writes nothing, so a second consume, or a consume of a
    revoked grant, raises and leaves the row as it was.
    """

    if not isinstance(grant_id, str) or not isinstance(operation_id, str) or not operation_id:
        raise ValueError("consume needs a grant id and an operation id")
    # Only the one-use kinds are consumed; a QM grant ends by revocation.
    where = [_T.c.grant_id == grant_id, _T.c.kind.in_(("level_unlock", "retrain_grant")),
             _live()]
    if campaign_id is not None:
        where.append(_T.c.campaign_id == campaign_id)
    result = session.execute(update(_T).where(*where).values(
        consumed_at=datetime.now(timezone.utc), consumed_by_operation_id=operation_id))
    if result.rowcount != 1:
        raise GrantNotLive(f"grant {grant_id} is not live")
