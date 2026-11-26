"""Legacy session-history backfill: the types and the database half (PE-14e).

On every ``file -> migrating -> db`` the file-era session ids become ``ended``
``campaign_sessions`` rows (C:495-507).  The files are read before any
transaction by ``core.obsidian_session_history.read_session_history``, which
returns a :class:`LegacyHistory`; :func:`backfill_session_history` then runs in
the caller's transaction.  This module does no file I/O, takes no process lock
and imports neither ``core.obsidian_sync`` nor the reader.

A backfill never claims a complete read it did not make: ``read_complete`` is
true only when nothing was unreadable, and accepting the unreadable items
changes ``gate_satisfied`` (the C:666 "complete or accepted" input), never
``read_complete``.  Nothing is inserted unless every unreadable item and
every collision is accepted and no legacy session is ``live``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from core.persistence import streams
from core.persistence.models import CampaignSession

from .folder_form import folder_key

ORIGIN = "file_backfill"
MAX_LEGACY_ID = 120  # main's start_session cuts any id at 120 characters


def is_legacy_id(value: Any) -> bool:
    """A string of 1-120 characters that SQL can store (no NUL, no lone surrogate)."""

    return (isinstance(value, str) and 1 <= len(value) <= MAX_LEGACY_ID and "\x00" not in value
            and not any("\ud800" <= ch <= "\udfff" for ch in value))


@dataclass(frozen=True)
class UnreadableItem:
    """One file, line (1-based, or None) and field the reader could not use."""

    file: str
    line: int | None
    field: str | None
    reason: str  # malformed_json, not_object, malformed_session_id, io_error

    @property
    def key(self) -> tuple:
        return ("unreadable", self.file, self.line, self.field)


@dataclass(frozen=True)
class Collision:
    """``row``: a legacy id that reuses or folder-collides with a row; ``legacy``: legacy ids."""

    kind: str
    ids: tuple[str, ...]

    @property
    def key(self) -> tuple:
        return ("collision", self.kind, tuple(sorted(self.ids)))


@dataclass(frozen=True)
class LegacyHistory:
    """What the reader found: candidate ``ended`` ids, a ``live`` session, unreadable items."""

    ids: tuple[str, ...]
    # The live session's id, or a JSON rendering of an id that cannot be stored
    # (that id is also unreadable).  Reported only, never inserted (C:665).
    live_session: str | None
    unreadable: tuple[UnreadableItem, ...]
    source_sha256: str

    def __post_init__(self) -> None:
        if not all(is_legacy_id(i) for i in self.ids) or not (
                self.live_session is None or is_legacy_id(self.live_session)):
            raise ValueError("a legacy session id is not a storable 1-120 character string")

    @property
    def read_complete(self) -> bool:
        return not self.unreadable


@dataclass(frozen=True)
class BackfillReport:
    unreadable: tuple[UnreadableItem, ...]
    collisions: tuple[Collision, ...]
    quarantined: tuple[UnreadableItem | Collision, ...]  # accepted items
    unaccepted: tuple[UnreadableItem | Collision, ...]
    live_session: str | None
    source_sha256: str
    inserted: tuple[str, ...]
    skipped_existing: tuple[str, ...]
    read_complete: bool = field(init=False)
    gate_satisfied: bool = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "read_complete", not self.unreadable)
        object.__setattr__(self, "gate_satisfied", not self.unaccepted)


def backfill_session_history(session: Any, campaign_id: str, history: LegacyHistory,
                             accepted: frozenset = frozenset()) -> BackfillReport:
    """Classify ``history`` against this campaign's rows; insert only when the gate holds.

    ``accepted`` holds the ``key`` of each unreadable item and collision the
    GM accepted.  An accepted exact reuse of a non-backfill row keeps that row
    unchanged; every other new id, those in accepted collisions included, is
    inserted as ``ended`` with origin ``file_backfill`` and no times.
    """

    streams.lock_stream(session, campaign_id)  # every session-row writer holds the stream
    rows = dict(session.execute(select(CampaignSession.session_id, CampaignSession.origin)
                                .where(CampaignSession.campaign_id == campaign_id)).all())
    by_folder: dict[str, set[str]] = {}
    for row_id in rows:
        by_folder.setdefault(folder_key(row_id), set()).add(row_id)

    found: dict[tuple, Collision] = {}

    def collide(kind: str, ids: set[str]) -> None:
        collision = Collision(kind, tuple(sorted(ids)))
        found.setdefault(collision.key, collision)

    skipped, new = [], []
    for legacy_id in sorted(set(history.ids)):
        if legacy_id in rows:
            if rows[legacy_id] == ORIGIN:
                skipped.append(legacy_id)  # settled when it was inserted
            else:
                collide("row", {legacy_id})  # a file-era start reused a db-era id
            continue
        new.append(legacy_id)
        if by_folder.get(folder_key(legacy_id)):
            collide("row", {legacy_id} | by_folder[folder_key(legacy_id)])
    groups: dict[str, set[str]] = {}
    for legacy_id in new:
        groups.setdefault(folder_key(legacy_id), set()).add(legacy_id)
    for ids in groups.values():
        if len(ids) > 1:
            collide("legacy", ids)

    collisions = tuple(found.values())
    items = (*history.unreadable, *collisions)
    unaccepted = tuple(item for item in items if item.key not in accepted)
    inserted: tuple[str, ...] = ()
    if not unaccepted and history.live_session is None:
        inserted = tuple(new)
        session.add_all(CampaignSession(campaign_id=campaign_id, session_id=legacy_id,
                                        status="ended", origin=ORIGIN) for legacy_id in new)
        session.flush()
    return BackfillReport(
        unreadable=tuple(history.unreadable), collisions=collisions,
        quarantined=tuple(item for item in items if item.key in accepted),
        unaccepted=unaccepted, live_session=history.live_session,
        source_sha256=history.source_sha256, inserted=inserted,
        skipped_existing=tuple(skipped))
