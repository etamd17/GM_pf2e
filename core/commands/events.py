"""The event writer and the session context of one command (contract C:394-419; PE-14b).

``EventWriter`` is created by the executor for one command transaction and
bound to its :class:`~core.commands.checks.CheckContext` as ``ctx.emit`` and
``ctx.bind_started_session``.  ``emit(EventDraft)`` allocates the next seq
from the stream row this transaction holds (``ctx.locks.stream_row``), so a
row ``apply`` writes after an emit can store that seq, and adds the event row
to the transaction at once.

What the client cannot set: the seq, epoch, time, operation id, principal
(``CheckedEnvelope`` only), origin (``client_origin``, read once at check 1),
rules profile (the campaign pin) and session id.  ``EventDraft`` has no
``session_id`` field; ``ctx.session_id`` is the active session read once
before the context was built, and only :meth:`EventWriter.bind_started_session`
(for ``session.start``, PE-14e) may change it.

Visibility (C:409-419): projections a draft does not list get ``none``,
``gm`` always gets ``full``, an unknown projection or redaction class is a
:class:`ValueError` (a type's bug, answered 500).  A ``notes`` change is
reduced to ``{"path": "notes", "changed": true}``, keeping only its record
``kind`` and ``id`` so the response filter can still hide it.  An override is recorded
in ``explain[]`` as ``{"override": {"reason": ...}}``.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from sqlalchemy import select

from core.persistence import streams
from core.persistence.models import CampaignSession, GameEvent, new_id, utc_now


PROJECTIONS = frozenset({"gm", "gm_integration", "owner", "editor", "viewer", "party",
                         "encounter_player", "qm", "member"})
REDACTION_CLASSES = frozenset({"exact_hp", "secret_roll", "unrevealed_effect",
                               "unrevealed_item", "hidden_actor"})
_REDACT = re.compile(r"redact:\[([a-z_,]*)\]")


class SessionBindError(RuntimeError):
    """``bind_started_session`` was misused by a command type (answered 500)."""


class NoEventsEmitted(RuntimeError):
    """``apply`` returned without emitting; an applied command always emits (answered 500)."""


@dataclass(frozen=True, kw_only=True)
class EventDraft:
    """What a command type says about one consequence; the writer fills the rest."""

    type: str
    subjects: list[Any] = field(default_factory=list)
    changes: list[Any] = field(default_factory=list)
    visibility: Mapping[str, str] = field(default_factory=dict)
    actor: Mapping[str, Any] = field(default_factory=dict)
    dice: list[Any] = field(default_factory=list)
    explain: list[Any] = field(default_factory=list)
    encounter_id: str | None = None
    action_id: str | None = None
    causation_event_id: str | None = None


def visibility(given: Mapping[str, str]) -> dict[str, str]:
    """Every projection with its value: unlisted ``none``, ``gm`` ``full``."""

    if not isinstance(given, Mapping):
        raise ValueError("visibility must be a mapping")
    out = dict.fromkeys(sorted(PROJECTIONS), "none")
    for name, value in given.items():
        if name not in PROJECTIONS:
            raise ValueError(f"unknown projection {name!r}")
        match = _REDACT.fullmatch(value) if isinstance(value, str) else None
        if value not in ("full", "none") and not (
                match and match.group(1) and set(match.group(1).split(",")) <= REDACTION_CLASSES):
            raise ValueError(f"unknown visibility {value!r} for {name}")
        out[name] = value
    out["gm"] = "full"
    return out


def _changes(changes: list[Any]) -> list[Any]:
    return [{**{k: change[k] for k in ("kind", "id") if k in change},
             "path": "notes", "changed": True}
            if isinstance(change, Mapping) and change.get("path") == "notes" else change
            for change in copy.deepcopy(list(changes))]


class EventWriter:
    """``ctx.emit`` and ``ctx.bind_started_session`` for one command."""

    def __init__(self, checked: Any, stream_epoch: int, origin: str) -> None:
        self.checked = checked
        self.stream_epoch = stream_epoch
        self.origin = origin
        self.ctx: Any = None
        self.events: list[GameEvent] = []
        self._bound = False

    def attach(self, ctx: Any) -> None:
        self.ctx = ctx
        self._snapshot = ctx.session_id
        ctx.emit = self.emit
        ctx.bind_started_session = self.bind_started_session

    def emit(self, draft: EventDraft) -> tuple[str, int]:
        if not isinstance(draft, EventDraft):
            raise TypeError("emit() takes an EventDraft")
        if not isinstance(draft.type, str) or not draft.type or len(draft.type) > 128:
            raise ValueError("an event needs a type")
        ctx, checked = self.ctx, self.checked
        explain = copy.deepcopy(list(draft.explain))
        override = checked.envelope.get("override")
        if override is not None:
            explain.append({"override": {"reason": override["reason"]}})
        seq = streams.allocate(ctx.locks.stream_row, 1)[0]
        row = GameEvent(
            event_id=new_id(), campaign_id=ctx.campaign_id, seq=seq,
            stream_epoch=self.stream_epoch, type=draft.type, occurred_at=utc_now(),
            session_id=ctx.session_id, encounter_id=draft.encounter_id,
            operation_id=checked.envelope["operation_id"], action_id=draft.action_id,
            causation_event_id=draft.causation_event_id,
            origin=self.origin, principal_kind=checked.principal_kind,
            principal_id=checked.principal_id, rules_profile=ctx.pin["profile"],
            actor=copy.deepcopy(dict(draft.actor)),
            subjects=copy.deepcopy(list(draft.subjects)),
            changes=_changes(draft.changes), dice=copy.deepcopy(list(draft.dice)), explain=explain,
            visibility=visibility(draft.visibility))
        ctx.session.add(row)
        ctx.session.flush()
        self.events.append(row)
        return row.event_id, seq

    def bind_started_session(self, session_id: str) -> None:
        """Tag this command's later events with the session ``apply`` just started."""

        ctx = self.ctx
        if self._bound or self._snapshot is not None:
            raise SessionBindError("a session was already active or already bound")
        if not isinstance(session_id, str) or not session_id:
            raise SessionBindError("session_id must be a non-empty string")
        ctx.session.flush()
        status = ctx.session.scalar(select(CampaignSession.status).where(
            CampaignSession.campaign_id == ctx.campaign_id,
            CampaignSession.session_id == session_id))
        if status != "active":
            raise SessionBindError(f"session {session_id} is not active in this campaign")
        self._bound = True
        ctx.session_id = session_id
