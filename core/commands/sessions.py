"""Session start and end on the executor (contract section 4, Sessions; PE-14e).

``session.start`` (subject: the campaign) and ``session.end`` (subject: a
``campaign_sessions`` row) are open to the GM, the site admin and a token of
this campaign whose scope permits the type (PE-14c's built-in session rule).
Only the GM or the site admin may send ``override`` on ``session.end``
(``override_gm_only``), so a token is refused at check 4 whatever its scope.

Every writer of a campaign's session rows holds its stream row, so the
check-6 and check-9 reads here are stable without row locks; ``lock_plan``
only registers the subject (``register_serialized``).  A PF2e start also
takes :data:`~core.persistence.identity.PF2E_SESSION_SLOT_LOCK_KEY` after the
stream row on PostgreSQL, then refuses while any other PF2e campaign has an
active row.  It never reads or writes the process slot or another campaign.

Open actions are asked of one registered provider (:data:`OPEN_ACTIONS`).
There is no action table yet, so the shipped provider is
:class:`NoActionTable`; PE-30's follow-on registers the real one.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from sqlalchemy import select, text

from core.persistence.identity import PF2E_SESSION_SLOT_LOCK_KEY
from core.persistence.models import Campaign, CampaignSession, utc_now
from core.persistence.state_repositories import StateRecord
from core.request_context import PrincipalKind

from .authorizer import CommandAuthorizer, register_visibility
from .checks import CommandRefusal
from .envelope import principal_key
from .events import EventDraft
from .executor import Applied
from .folder_form import folder_key
from .registry import CommandType, register

START, END = "session.start", "session.end"
EVENT_STARTED, EVENT_ENDED = "session.started", "session.ended"  # C:1102
SESSION_ID = re.compile(r"[a-z0-9][a-z0-9_-]{7,99}")  # C:26
_T = CampaignSession.__table__
# Session events tell the token's mirror and every player member that the
# campaign went live or stopped being live (C:842).
_VISIBILITY = {"gm_integration": "full", "member": "full"}


class OpenActionsRefusal(CommandRefusal):
    """``422 rule_violation`` naming the open actions that block ``session.end``."""

    def __init__(self, actions: list[Any]) -> None:
        super().__init__("rule_violation")
        self.actions = list(actions)
        self.args = (f"open actions: {self.actions!r}",)


class SessionMismatch(RuntimeError):
    """``session.end`` reached ``apply`` for a row other than the active one (a bug, 500)."""


class NoActionTable:
    """The open-actions provider until PE-30 adds an action table: none are open."""

    def open_actions(self, ctx: Any) -> list[Any]:
        return []

    def lock(self, ctx: Any) -> None:
        return None

    def cancel_all(self, ctx: Any) -> None:
        return None


class OpenActionsRegistry:
    """Holds exactly one provider; there is no silent default when none is given."""

    def __init__(self, provider: Any) -> None:
        self.provider = self._checked(provider)

    @staticmethod
    def _checked(provider: Any) -> Any:
        if provider is None or not all(callable(getattr(provider, name, None))
                                       for name in ("open_actions", "lock", "cancel_all")):
            raise ValueError("an open-actions provider needs open_actions, lock and cancel_all")
        return provider

    def register(self, provider: Any) -> None:
        """Replace :class:`NoActionTable` with the real provider (once, at import time)."""

        if not isinstance(self.provider, NoActionTable):
            raise ValueError("an open-actions provider is already registered")
        self.provider = self._checked(provider)


OPEN_ACTIONS = OpenActionsRegistry(NoActionTable())


def _read(ctx: Any, session_id: str) -> StateRecord | None:
    return session_record(ctx.session, ctx.campaign_id, session_id)


def session_record(session: Any, campaign_id: str, session_id: str) -> StateRecord | None:
    """This campaign's row as a serialized record (never the raw ORM row), or ``None``.
    Also the executor's replay reader for the ``session`` kind."""

    row = session.execute(select(_T).where(
        _T.c.campaign_id == campaign_id, _T.c.session_id == session_id)).first()
    if row is None:
        return None
    payload = dict(row._mapping)
    for name in ("started_at", "ended_at"):
        payload[name] = None if payload[name] is None else payload[name].isoformat()
    return StateRecord.serialized("session", session_id, payload)


def _new_id(ctx: Any) -> Any:
    return ctx.envelope["payload"].get("session_id")


# -- session.start -----------------------------------------------------------

def _lock_start(ctx: Any) -> None:
    campaign_id = ctx.envelope["subject"]["campaign_id"]
    ctx.locks.register_serialized(
        "campaign", campaign_id, StateRecord.serialized("campaign", campaign_id, {}))


def _start_terminal(ctx: Any, subject: StateRecord) -> bool:
    """Check 6: an existing row with this id, or with an equal case-folded folder form.

    A missing or non-string id is left to check 9 (``422 validation_failed``).
    """

    new = _new_id(ctx)
    if not isinstance(new, str):
        return False
    key = folder_key(new)
    return any(existing == new or folder_key(existing) == key
               for existing in ctx.session.scalars(select(_T.c.session_id).where(
                   _T.c.campaign_id == ctx.campaign_id)))


class _StartRules:
    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool:
        return True  # the subject is the campaign, not a character

    def check(self, ctx: Any, override: Mapping[str, Any] | None) -> None:
        new = _new_id(ctx)
        if (set(ctx.envelope["payload"]) != {"session_id"} or not isinstance(new, str)
                or not SESSION_ID.fullmatch(new)):
            raise CommandRefusal("validation_failed")
        if ctx.session.scalar(select(_T.c.session_id).where(
                _T.c.campaign_id == ctx.campaign_id, _T.c.status == "active")) is not None:
            raise CommandRefusal("rule_violation")  # a second active session
        self._pf2e_slot(ctx)

    @staticmethod
    def _pf2e_slot(ctx: Any) -> None:
        """Refuse while another PF2e campaign has an active row (C:486-487, C:810-812)."""

        campaigns = Campaign.__table__
        system = ctx.session.scalar(select(campaigns.c.system).where(
            campaigns.c.id == ctx.campaign_id))
        if system != "pf2e":
            return
        if ctx.session.get_bind().dialect.name == "postgresql":
            # Lock order: the stream row (held), then the slot key.  SQLite's
            # single writer already serializes these reads and inserts.
            ctx.session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                                {"key": PF2E_SESSION_SLOT_LOCK_KEY})
        other = ctx.session.execute(
            select(_T.c.campaign_id).join(campaigns, campaigns.c.id == _T.c.campaign_id)
            .where(_T.c.status == "active", campaigns.c.system == "pf2e",
                   _T.c.campaign_id != ctx.campaign_id).limit(1)).first()
        if other is not None:
            raise CommandRefusal("rule_violation")


def _apply_start(ctx: Any) -> Applied:
    session_id = _new_id(ctx)
    row = CampaignSession(campaign_id=ctx.campaign_id, session_id=session_id, status="active",
                          started_at=utc_now(), started_by=principal_key(ctx.principal)[1],
                          origin="command")
    ctx.session.add(row)
    ctx.bind_started_session(session_id)  # flushes; the event then carries the new id
    ctx.emit(EventDraft(type=EVENT_STARTED, subjects=[{"kind": "session", "id": session_id}],
                        visibility=_VISIBILITY))
    return Applied(written={("session", session_id): _read(ctx, session_id)})


# -- session.end -------------------------------------------------------------

def _lock_end(ctx: Any) -> None:
    session_id = ctx.envelope["subject"]["session_id"]
    ctx.locks.register_serialized("session", session_id, _read(ctx, session_id))
    OPEN_ACTIONS.provider.lock(ctx)  # lock-order step 3 for the rows it reads


def _end_terminal(ctx: Any, subject: StateRecord) -> bool:
    return subject.payload["status"] == "ended"


class _EndRules:
    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool:
        return True  # the subject is a session row, not a character

    def check(self, ctx: Any, override: Mapping[str, Any] | None) -> None:
        if ctx.envelope["payload"]:
            raise CommandRefusal("validation_failed")
        # An override that reaches check 9 already passed check 4 (GM or site admin).
        if override is None:
            actions = list(OPEN_ACTIONS.provider.open_actions(ctx))
            if actions:
                raise OpenActionsRefusal(actions)


def _apply_end(ctx: Any) -> Applied:
    session_id = ctx.envelope["subject"]["session_id"]
    if session_id != ctx.session_id:
        raise SessionMismatch(f"{session_id} is not the active session {ctx.session_id}")
    if ctx.envelope.get("override") is not None:
        OPEN_ACTIONS.provider.cancel_all(ctx)  # its events carry ctx.session_id
    row = ctx.session.get(CampaignSession, (ctx.campaign_id, session_id))
    row.status, row.ended_at = "ended", utc_now()
    row.ended_by = principal_key(ctx.principal)[1]
    ctx.session.flush()
    ctx.emit(EventDraft(type=EVENT_ENDED, subjects=[{"kind": "session", "id": session_id}],
                        visibility=_VISIBILITY))
    return Applied(written={("session", session_id): _read(ctx, session_id)})


def _session_visible(a: CommandAuthorizer, record: StateRecord) -> bool:
    """The GM or site admin, and a token of this campaign; hidden from everyone else."""

    if record.payload.get("campaign_id") != a.campaign_id:
        return False
    return a.is_gm or (a.principal_kind is PrincipalKind.INTEGRATION_TOKEN
                       and a.token_campaign_id == a.campaign_id)


_PRINCIPALS = frozenset({PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN})
register(CommandType(
    type=START, subject=("campaign_id", "campaign"), dependencies={}, payload_ids={},
    requires_expected=False, derives_rules=False, allowed_principals=_PRINCIPALS,
    rules_adapter=_StartRules(), lock_plan=_lock_start, apply=_apply_start,
    terminal=_start_terminal))
register(CommandType(
    type=END, subject=("session_id", "session"), dependencies={}, payload_ids={},
    requires_expected=False, derives_rules=False, allowed_principals=_PRINCIPALS,
    rules_adapter=_EndRules(), lock_plan=_lock_end, apply=_apply_end,
    terminal=_end_terminal, override_gm_only=True))
register_visibility("session", _session_visible)
