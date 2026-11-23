"""The command executor: one transaction per command (contract sections 2-4; PE-14b).

``execute`` runs check 1 with no transaction open, then, in one transaction:
the stream lock (lock-order step 1), the authority fence (check 2), the
receipt lookup (check 3), the authorizer (:data:`authorizer_factory`, bound
to PE-14c's ``authorizer.load``), the active-session snapshot, the
campaign pin, the type's ``lock_plan``, PE-13b's checks 4-9, ``apply``, the
receipt and the events, then ``COMMIT``.  Only after ``COMMIT`` returns is
``services.session_events.after_commit`` called.

A refusal rolls back.  Codes the code table says write a receipt (C:356-366,
:data:`RECEIPT_CODES`) then get a second short transaction that re-takes the
stream lock, re-runs the fence, re-checks the receipt key and only then
inserts the ``rejected`` receipt; the answer is returned after that commits.
Every other code writes nothing.

Everything ``lock_plan``, the checks, the rules adapter, ``apply`` and the
projector do goes through ``ctx.session`` and ``ctx.locks``: they never open a
session, read ``flask.g``, load rules content, write files, wait or take a
process lock.  ``ctx.envelope`` is the hashed request and none of them may
change it, nested values included (copy first); the digest is re-checked
before every receipt insert and again after the success projector, and the whole envelope (``client`` included) is
compared with a copy taken at check 1 (:class:`EnvelopeMutated`, answered 500).
The event origin is read once, at check 1.  An
applied command emits at least one event (:class:`~.events.NoEventsEmitted`).
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from core.persistence import grants, runtime, streams
from core.persistence.command_locks import CommandLocks
from core.persistence.models import (
    CampaignSession,
    CampaignStateAuthority,
    CommandReceipt,
    GameEvent,
)
from core.persistence.state_repositories import (
    DOMAIN_KINDS,
    KIND_CLOCK,
    KIND_ENCOUNTER,
    CampaignClockRepository,
    CampaignPinRepository,
    CharacterStateRepository,
    EncounterStateRepository,
    PinUnavailable,
    StateRecord,
    StateRowMissing,
    UnknownSchemaVersion,
)
from services import session_events

from .authorizer import load as load_authorizer
from .checks import CODES, CheckContext, CheckSpec, CommandRefusal, run_checks
from .conflicts import conflict_body
from .digest import command_digest
from .envelope import client_origin
from .events import EventWriter, NoEventsEmitted, event_views, held_set, visible_entry
from .registry import CheckedEnvelope, check_envelope

_LOG = logging.getLogger(__name__)

# ``SET LOCAL lock_timeout`` before every stream lock on PostgreSQL (PE-14d/f import it).
STREAM_LOCK_TIMEOUT_MS = 5000
# Refusals that write a ``rejected`` receipt (C:356-366); every other code writes nothing.
RECEIPT_CODES = frozenset({
    "not_found", "already_finalized", "stale_revision", "rules_mismatch", "item_locked",
    "insufficient_funds", "insufficient_stock", "insufficient_quantity",
    "validation_failed", "rule_violation", "not_live", "in_encounter", "unlock_required",
    "edition_mismatch", "target_invalid",
})
# Lock timeout, serialization failure, deadlock.
_RETRYABLE_SQLSTATES = frozenset({"55P03", "40001", "40P01"})
_UNAVAILABLE = (StateRowMissing, PinUnavailable, UnknownSchemaVersion)
_DOMAINS = {kind: domain for domain, kind in DOMAIN_KINDS.items()}
_SUBJECT_KINDS = {"character": "character.play"}

# ``factory(session, principal, campaign_id) -> Authorizer`` (PE-13b's protocol),
# reading only through ``session``: PE-14c's ``authorizer.load``.
authorizer_factory: Callable[..., Any] | None = load_authorizer


class EnvelopeMutated(RuntimeError):
    """A callback changed the hashed envelope (answered 500, nothing written)."""


class _CommitFailed(Exception):
    """``COMMIT`` itself raised: the outcome is unknown (503)."""


@dataclass(frozen=True)
class Applied:
    """What ``apply`` returns: the records it wrote, and whether an action is left open.

    Records with a revision become the receipt's ``revisions``; every written
    record is projected into ``result.records``.  Other result values (such as
    ``reconciliation``) travel on the events, through ``EventDraft.result``.
    """

    written: Mapping[tuple[str, str], StateRecord] = field(default_factory=dict)
    pending: bool = False


@dataclass(frozen=True)
class CommandResponse:
    status: int
    body: dict[str, Any] | None


def execute(envelope: Any, principal: Any, *, route_campaign_id: str,
            content_descriptor: Any, project: Callable[..., Mapping[str, Any]],
            held_projections: Callable[..., Any]) -> CommandResponse:
    """Run one command for ``principal``.

    ``principal`` is the verified ``core.request_context.Principal`` of the
    authenticated request, never derived from the body.  ``content_descriptor``
    is the installed rules content (``pin["content"]`` shape), loaded by the
    caller before this call.  ``project(principal, records)`` returns the
    caller's view keyed ``"<kind>:<id>"``; a record it omits is never named in
    the answer.  ``held_projections(authorizer, *, subjects, encounter_id)``
    returns the projections the caller holds now for one event (PE-14f's
    function in ``core/commands/authorizer.py`` has this signature); with the
    event's stored visibility it decides whether the event is listed and
    what of its result entries the caller sees (R2-1).
    """

    try:
        checked = check_envelope(envelope, principal, route_campaign_id=route_campaign_id)
    except Exception as exc:
        return _failure(exc, None)
    command = _Command(checked, content_descriptor, project, held_projections)
    try:
        response, last_seq = command.transact(command.run)
    except Exception as exc:
        return command.refuse(exc)
    if last_seq is not None:
        session_events.after_commit(command.campaign_id, last_seq)
    return response


def _code(exc: BaseException) -> str:
    """The contract code for an exception (Decision 4)."""

    if isinstance(exc, _CommitFailed):
        return "temporarily_unavailable"
    code = getattr(exc, "code", None)
    if (type(exc).__module__.startswith("core.commands") and code in CODES
            and isinstance(getattr(exc, "http_status", None), int)):
        return code
    if isinstance(exc, _UNAVAILABLE):
        return "authority_unavailable"
    if isinstance(exc, DBAPIError) and (
            getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    ) in _RETRYABLE_SQLSTATES:
        return "temporarily_unavailable"
    return "server_error"


def _failure(exc: BaseException, operation_id: str | None) -> CommandResponse:
    """The answer for a refusal or failure that writes nothing."""

    code = _code(exc)
    if code == "server_error":
        _LOG.error("command failed", exc_info=exc)
    if code == "stale_revision":
        return CommandResponse(409, conflict_body(operation_id,
                                                  getattr(exc, "conflicts", None) or [],
                                                  getattr(exc, "current", None) or {}))
    status = exc.http_status if code == getattr(exc, "code", None) else CODES[code]
    return CommandResponse(status, {"ok": False, "code": code, "operation_id": operation_id})


def _event_keys(event: GameEvent) -> list[tuple[str, str]] | None:
    """The records an event is about: its ``changes`` keys and its ``subjects``.

    ``None`` (never listed in an answer) when a subject is malformed or the
    event names no record: the response filter fails closed.  A ``character``
    subject is judged by its ``character.play`` record.
    """

    keys = [(c["kind"], c["id"]) for c in event.changes
            if isinstance(c, Mapping) and isinstance(c.get("kind"), str)
            and isinstance(c.get("id"), str)]
    for subject in event.subjects:
        if not (isinstance(subject, Mapping) and isinstance(subject.get("kind"), str)
                and isinstance(subject.get("id"), str)):
            return None
        keys.append((_SUBJECT_KINDS.get(subject["kind"], subject["kind"]), subject["id"]))
    return keys or None


class _Command:
    def __init__(self, checked: CheckedEnvelope, content_descriptor: Any,
                 project: Callable[..., Mapping[str, Any]],
                 held_projections: Callable[..., Any]) -> None:
        self.checked = checked
        self.content_descriptor = content_descriptor
        self.project = project
        self.held_projections = held_projections
        self.campaign_id = checked.envelope["campaign_id"]
        self.operation_id = checked.envelope["operation_id"]
        self.key = (self.campaign_id, checked.principal_kind, checked.principal_id,
                    self.operation_id)
        # The whole checked envelope, client included (the digest leaves some fields out).
        self.snapshot = copy.deepcopy(dict(checked.envelope))
        self.origin = client_origin(checked.envelope)

    @staticmethod
    def transact(work: Callable[[Any], Any]) -> Any:
        """``work(session)`` in one transaction; a raising ``COMMIT`` is :class:`_CommitFailed`."""

        finished = False
        try:
            with runtime.database().transaction() as session:
                result = work(session)
                finished = True
        except Exception as exc:
            if finished:
                raise _CommitFailed() from exc
            raise
        return result

    # -- checks 2 and 3 ------------------------------------------------------

    def lock_and_fence(self, session: Any) -> tuple[CommandLocks, int]:
        """Stream lock, then the authority fence under it (Decision 2)."""

        locks = CommandLocks(session, self.campaign_id, lock_stream=streams.lock_stream)
        if session.get_bind().dialect.name == "postgresql":
            session.execute(text(f"SET LOCAL lock_timeout = '{int(STREAM_LOCK_TIMEOUT_MS)}ms'"))
        locks.stream()
        t = CampaignStateAuthority.__table__
        row = session.execute(select(t.c.authority, t.c.stream_epoch)
                              .where(t.c.campaign_id == self.campaign_id)).first()
        if row is None or row.authority not in ("db", "migrating", "exporting"):
            raise CommandRefusal("authority_unavailable")
        if row.authority != "db":
            raise CommandRefusal("migration_in_progress")
        if row.stream_epoch != self.checked.envelope["epoch"]:
            raise CommandRefusal("resync_required")
        return locks, int(row.stream_epoch)

    def from_receipt(self, session: Any, receipt: CommandReceipt) -> CommandResponse:
        """Answer an existing receipt key (Decision 3), writing nothing."""

        if receipt.code == "abandoned":
            raise CommandRefusal("abandoned")
        if receipt.digest != self.checked.digest:
            raise CommandRefusal("operation_id_reused")
        if receipt.status == "rejected":
            return CommandResponse(CODES[receipt.code], {
                "ok": False, "code": receipt.code, "operation_id": self.operation_id,
                "replayed": True})
        authorizer = self.authorizer(session)
        if not authorizer.campaign_role_allows(self.checked.command_type.type):
            return CommandResponse(403, None)
        events = list(session.execute(select(GameEvent).where(
            GameEvent.event_id.in_(list(receipt.event_ids))).order_by(GameEvent.seq)).scalars())
        keys = {tuple(k.split(":", 1)) for k in receipt.revisions}
        keys.update(k for e in events for k in (_event_keys(e) or ()))
        records = {key: record for key in sorted(keys)
                   if (record := self.read_current(session, *key)) is not None}
        # A grant record carries no revision, so the receipt cannot name it:
        # the grant its events are about is the record the command wrote.
        written = set(receipt.revisions) | {f"{k}:{i}" for k, i in keys if k == "grant"}
        return self.answer(authorizer, receipt.applied_status, True, dict(receipt.revisions),
                           events, receipt.seq_last, records, written)

    def read_current(self, session: Any, kind: str, id: str) -> StateRecord | None:
        """An unlocked read of one revision record for a replay's fresh projection."""

        cid = self.campaign_id
        if kind in _DOMAINS:
            return CharacterStateRepository(session).get(cid, id, _DOMAINS[kind])
        if kind == KIND_ENCOUNTER:
            return EncounterStateRepository(session).get(cid, id)
        if kind == KIND_CLOCK:
            return CampaignClockRepository(session).get(cid, id)
        if kind == "campaign" and id == cid:
            return StateRecord.serialized("campaign", id, {})
        if kind == "grant":
            row = grants.get(session, cid, id)
            return None if row is None else grants.record(row)
        return None

    def authorizer(self, session: Any) -> Any:
        if authorizer_factory is None:
            raise RuntimeError("no authorizer is configured (PE-14c)")
        return authorizer_factory(session, self.checked.principal, self.campaign_id)

    # -- the command transaction --------------------------------------------

    def run(self, session: Any) -> tuple[CommandResponse, int | None]:
        checked, cid = self.checked, self.campaign_id
        locks, epoch = self.lock_and_fence(session)
        receipt = session.get(CommandReceipt, self.key)
        if receipt is not None:
            return self.from_receipt(session, receipt), None
        authorizer = self.authorizer(session)
        session_id = session.scalar(select(CampaignSession.session_id).where(
            CampaignSession.campaign_id == cid, CampaignSession.status == "active"))
        pin = CampaignPinRepository(session).pin(cid)
        ctx = CheckContext(
            session=session, locks=locks, principal=checked.principal, campaign_id=cid,
            envelope=checked.envelope, authorizer=authorizer, project=self.project,
            content_descriptor=self.content_descriptor, pin=pin, session_id=session_id,
            envelope_checked=True, fence_passed=True, receipt_absent=True)
        writer = EventWriter(checked, epoch, self.origin)
        writer.attach(ctx)
        t = checked.command_type
        t.lock_plan(ctx)
        run_checks(ctx, CheckSpec(type=t.type, subject=t.subject, dependencies=t.dependencies,
                                  payload_ids=t.payload_ids, terminal=t.terminal,
                                  derives_rules=t.derives_rules), t.rules_adapter)
        applied = t.apply(ctx)
        applied = Applied() if applied is None else applied
        if not isinstance(applied, Applied):
            raise TypeError("apply() must return Applied or None")
        if not writer.events:
            raise NoEventsEmitted(f"{t.type} applied without emitting an event")
        self.require_unchanged()
        written = {key: record for key, record in applied.written.items() if record is not None}
        revisions = {f"{kind}:{id}": record.revision for (kind, id), record in written.items()
                     if record.revision is not None}
        first, last = writer.events[0].seq, writer.events[-1].seq
        status = "pending" if applied.pending else "committed"
        session.add(CommandReceipt(
            campaign_id=cid, principal_kind=checked.principal_kind,
            principal_id=checked.principal_id, operation_id=self.operation_id,
            digest=checked.digest, status="committed", applied_status=status, code=None,
            revisions=revisions, event_ids=[e.event_id for e in writer.events],
            seq_first=first, seq_last=last, stream_epoch=epoch))
        session.flush()
        records = {key: r for key in (k for e in writer.events for k in (_event_keys(e) or ()))
                   if (r := ctx.records.get(key)) is not None}
        records.update(written)
        response = self.answer(authorizer, status, False, revisions, writer.events, last,
                               records, {f"{kind}:{id}" for kind, id in written})
        self.require_unchanged()  # the projector ran after the first check (R1-1)
        return response, last

    def require_unchanged(self) -> None:
        if (command_digest(self.checked.envelope) != self.checked.digest
                or dict(self.checked.envelope) != self.snapshot):
            raise EnvelopeMutated("a callback changed the hashed envelope")

    def answer(self, authorizer: Any, status: str, replayed: bool,
               revisions: Mapping[str, int], events: list[GameEvent], watermark: int,
               records: Mapping[tuple[str, str], StateRecord],
               result_keys: set[str]) -> CommandResponse:
        """The success body (C:388-392), every record-naming field projected.

        ``result.records`` is the caller's projection of the records the command
        wrote (read fresh on replay).  An event is listed only if the projector
        returned every record it is about and its stored visibility lets the
        caller's current projections see it; every other ``result`` section is
        rebuilt from those events, in seq order, each entry redacted to what
        that visibility allows (``events.event_views``, ``visible_entry``).
        """

        shown = dict(self.project(self.checked.principal, MappingProxyType(dict(records))))
        result: dict[str, Any] = {
            "records": {key: value for key, value in shown.items() if key in result_keys}}
        visible = []
        for event in events:
            keys = _event_keys(event)
            if not keys or not all(f"{k}:{i}" in shown for k, i in keys):
                continue
            held = held_set(self.held_projections(
                authorizer, subjects=copy.deepcopy(list(event.subjects)),
                encounter_id=event.encounter_id))
            views = event_views(event.visibility, held)
            if views is None:
                continue
            visible.append(event)
            for item in event.explain:
                if isinstance(item, Mapping) and isinstance(item.get("result"), str):
                    entry = visible_entry(item["result"], item.get("entry"), views)
                    if entry is not None:
                        result.setdefault(item["result"], []).append(entry)
        body = {
            "ok": True, "operation_id": self.operation_id, "status": status,
            "replayed": replayed,
            "revisions": {key: rev for key, rev in revisions.items() if key in shown},
            "events": [{"event_id": e.event_id, "seq": e.seq} for e in visible],
            "watermark": watermark, "result": result,
        }
        return CommandResponse(202 if status == "pending" else 200, body)

    # -- refusals ------------------------------------------------------------

    def refuse(self, exc: BaseException) -> CommandResponse:
        """Answer a failed first transaction (already rolled back)."""

        if _code(exc) not in RECEIPT_CODES:
            return _failure(exc, self.operation_id)
        try:
            self.require_unchanged()
            return self.transact(lambda session: self.write_refusal(session, exc))
        except Exception as second:
            return _failure(second, self.operation_id)

    def write_refusal(self, session: Any, exc: BaseException) -> CommandResponse:
        """The refusal transaction (C:368-378): lock, fence, receipt key, then insert."""

        _, epoch = self.lock_and_fence(session)
        receipt = session.get(CommandReceipt, self.key)
        if receipt is not None:
            return self.from_receipt(session, receipt)
        checked = self.checked
        session.add(CommandReceipt(
            campaign_id=self.campaign_id, principal_kind=checked.principal_kind,
            principal_id=checked.principal_id, operation_id=self.operation_id,
            digest=checked.digest, status="rejected", applied_status=None, code=exc.code,
            stream_epoch=epoch))
        session.flush()
        return _failure(exc, self.operation_id)
