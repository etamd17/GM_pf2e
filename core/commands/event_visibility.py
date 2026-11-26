"""Which committed events a reader may see listed (task PE-14f, Decision 7).

An event's ``visibility`` is fixed when it commits (C:409-417), so whether a
reader sees it listed depends only on that stored map and the projections the
reader holds now (:func:`~core.commands.authorizer.held_projections`), never
on the current visibility of any record: a later reveal never re-exposes an
earlier event (C:417).

A held ``gm`` sees every event.  Otherwise an event is listed when some held
projection's stored value is ``full`` or a well-formed ``redact:[classes]``,
judged by :func:`core.commands.events.event_views`, so an id is listed exactly
when the executor would show the event's content; a malformed ``redact:``
value, or a missing, NULL or non-object map, counts as ``none`` (PE-14c's
carried note).  PE-31 imports :func:`event_visible_to` and does not edit this
module.
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import select

from core.persistence.models import GameEvent

from .authorizer import held_projections
from .events import event_views


class MissingEvent(RuntimeError):
    """A receipt names an event id with no row in its campaign (answered 500)."""


def event_visible_to(authorizer: Any, event_row: Any) -> bool:
    """Whether the reader behind ``authorizer`` may see ``event_row`` listed."""

    held = held_projections(authorizer, subjects=event_row.subjects,
                            encounter_id=event_row.encounter_id)
    return event_views(event_row.visibility, held) is not None


def visible_event_ids(session: Any, authorizer: Any, campaign_id: str,
                      event_ids: Iterable[str]) -> list[str]:
    """``event_ids`` in their given order, keeping those the reader may see.

    Rows are read from ``session`` (the caller's transaction) within
    ``campaign_id``.  An id with no matching row is an integrity error
    (:class:`MissingEvent`), never a silent drop.
    """

    ids = list(event_ids)
    if not ids:
        return []
    if not all(isinstance(event_id, str) for event_id in ids):
        raise MissingEvent("receipt event ids must be strings")
    rows = {row.event_id: row for row in session.scalars(select(GameEvent).where(
        GameEvent.campaign_id == campaign_id, GameEvent.event_id.in_(ids)))}
    missing = [event_id for event_id in ids if event_id not in rows]
    if missing:
        raise MissingEvent(f"receipt names {len(missing)} event id(s) with no row")
    return [event_id for event_id in ids if event_visible_to(authorizer, rows[event_id])]
