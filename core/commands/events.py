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

Command results (PE-14b R1-2).  A draft's ``result`` maps a section name to an
ordered list of entries, such as PE-13c's ``reconciliation`` entries.  They are
stored in the event's ``explain[]`` as ``{"result": name, "entry": entry}``, in
order, so the response's ``result.<name>[]`` is rebuilt from durable events,
the same way fresh and on replay.  ``records`` is reserved for the response's
record projections.

Result visibility (PE-14b R2-1).  An entry is event content, so the event's
stored ``visibility`` governs it, judged against the projections the caller
holds now (:func:`event_access`).  Seeing the event's records is necessary
but never enough.  ``full`` returns the entry; ``redact:[classes]`` passes it
through the section's registered redactor (:func:`register_result_redactor`),
once per view the caller holds (:func:`event_views`, :func:`visible_entry`);
anything that cannot be resolved (a malformed map or value, an unknown
class, a section with no redactor, an entry its redactor does not know)
returns nothing.  The stored event is never changed.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from sqlalchemy import select

from core.persistence import streams
from core.persistence.models import CampaignSession, GameEvent, new_id, utc_now


PROJECTIONS = frozenset({"gm", "gm_integration", "owner", "editor", "viewer", "party",
                         "encounter_player", "qm", "member"})
REDACTION_CLASSES = frozenset({"exact_hp", "secret_roll", "unrevealed_effect",
                               "unrevealed_item", "hidden_actor"})
_REDACT = re.compile(r"redact:\[([a-z_,]*)\]")
_SECTION = re.compile(r"[a-z][a-z0-9_]{0,63}")


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
    result: Mapping[str, list[Mapping[str, Any]]] = field(default_factory=dict)


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


def result_items(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A draft's result sections as ``explain[]`` items, in order."""

    if not isinstance(result, Mapping):
        raise ValueError("result must map section names to lists of entries")
    items = []
    for name, entries in result.items():
        if not (isinstance(name, str) and _SECTION.fullmatch(name) and name != "records"):
            raise ValueError(f"invalid result section {name!r}")
        if not isinstance(entries, (list, tuple)) or not all(
                isinstance(entry, Mapping) for entry in entries):
            raise ValueError(f"result section {name} must be a list of objects")
        items += [{"result": name, "entry": copy.deepcopy(dict(entry))} for entry in entries]
    return items


def held_set(held: Any) -> frozenset[str]:
    """Validate what a ``held_projections`` callback returned (a bug is a ``ValueError``)."""

    if not isinstance(held, (set, frozenset)) or not all(
            isinstance(name, str) and name in PROJECTIONS for name in held):
        raise ValueError(f"held_projections must return a set of projection names, not {held!r}")
    return frozenset(held)


def _classes(value: Any) -> frozenset[str] | None:
    match = _REDACT.fullmatch(value) if isinstance(value, str) else None
    classes = frozenset(match.group(1).split(",")) if match and match.group(1) else None
    return classes if classes and classes <= REDACTION_CLASSES else None


def event_views(stored: Any, held: frozenset[str]) -> tuple[frozenset[str], ...] | None:
    """The views of an event with visibility ``stored`` that ``held`` gives a caller.

    ``None``: the caller sees nothing of it.  Otherwise one set of redaction
    classes per held view (``(frozenset(),)`` for ``full``), fewest classes
    first; the caller sees the union of them (C:647-648), entry by entry
    (:func:`visible_entry`).  ``gm`` always sees in full.  A missing or
    non-object map counts as ``none`` for every other projection, as PE-14f
    states.  One deliberate difference from PE-14f's ``event_visible_to``
    (which lists an event for any value starting ``redact:``): a malformed
    ``redact:`` value, an empty or unknown class list, counts as ``none``
    here, because these views release event content, not just an event id.
    """

    if "gm" in held:
        return (frozenset(),)
    if not isinstance(stored, Mapping):
        return None
    views = set()
    for name in held:
        value = stored.get(name)
        if value == "full":
            return (frozenset(),)
        if (classes := _classes(value)) is not None:
            views.add(classes)
    return tuple(sorted(views, key=lambda v: (len(v), sorted(v)))) or None


# Section name -> redactor(entry, classes) -> the entry to show, or None.
_REDACTORS: dict[str, Callable[[dict[str, Any], frozenset[str]], Mapping[str, Any] | None]] = {}


def register_result_redactor(
        name: str,
        redactor: Callable[[dict[str, Any], frozenset[str]], Mapping[str, Any] | None]) -> None:
    """Declare how a result section is redacted; without one it is shown only in full."""

    if not (isinstance(name, str) and _SECTION.fullmatch(name) and name != "records"):
        raise ValueError(f"invalid result section {name!r}")
    if name in _REDACTORS:
        raise ValueError(f"result section {name} already has a redactor")
    _REDACTORS[name] = redactor


def result_entry(name: str, entry: Any, classes: frozenset[str]) -> Any | None:
    """One stored result entry as the caller may see it (a copy), or ``None``."""

    if not isinstance(entry, Mapping):
        return None
    if not classes:
        return copy.deepcopy(dict(entry))
    redactor = _REDACTORS.get(name)
    if redactor is None:
        return None
    shown = redactor(copy.deepcopy(dict(entry)), classes)
    return None if shown is None else copy.deepcopy(dict(shown))


def visible_entry(name: str, entry: Any, views: tuple[frozenset[str], ...]) -> Any | None:
    """The most a caller may see of one entry through any of its views, or ``None``.

    Each view is applied on its own (never a merge of two redacted forms);
    the form keeping the most fields wins, the earlier view on a tie.
    """

    best = None
    for classes in views:
        shown = result_entry(name, entry, classes)
        if shown is not None and (best is None or len(shown) > len(best)):
            best = shown
    return best


# PE-13c entry type -> (classes it is shown in full under, the fields kept otherwise).
# An entry under any other class, or of a type not listed, is withheld.
_RECONCILIATION: dict[str, tuple[frozenset[str], tuple[str, ...] | None]] = {
    "hp": (frozenset({"secret_roll", "hidden_actor"}), ("type",)),
    "pool_clamped": (frozenset({"exact_hp", "secret_roll", "hidden_actor"}), None),
    "pool_removed": (frozenset({"exact_hp", "secret_roll"}), None),
    "reservation_released": (frozenset({"exact_hp", "secret_roll"}), None),
    "effect_ended": (frozenset({"exact_hp", "secret_roll"}), None),
    "choice_prompt": (frozenset({"exact_hp", "secret_roll"}), None),
}


def redact_reconciliation(entry: dict[str, Any],
                          classes: frozenset[str]) -> Mapping[str, Any] | None:
    """PE-13c's ``reconciliation`` entries under redaction.

    ``exact_hp``, ``unrevealed_effect`` or ``unrevealed_item`` reduces an
    ``hp`` entry to ``{"type": "hp"}`` (a moving maximum can reveal an
    effect's or item's size).  Every other entry is shown whole or withheld:
    pool, reservation, effect and choice entries are withheld under
    ``unrevealed_effect`` and ``unrevealed_item`` (an effect or item may be
    behind a pool or a choice), and all but ``pool_clamped`` (numbers and a
    pool name) under ``hidden_actor`` too (they can name targets, effect
    bodies or choice sources).
    """

    rule = _RECONCILIATION.get(entry.get("type"))
    if rule is None:
        return None
    safe, kept = rule
    if classes <= safe:
        return entry
    return None if kept is None else {key: entry[key] for key in kept}


register_result_redactor("reconciliation", redact_reconciliation)


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
        if any(isinstance(item, Mapping) and "result" in item for item in explain):
            raise ValueError("result entries go in EventDraft.result, not explain")
        explain.extend(result_items(draft.result))
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
