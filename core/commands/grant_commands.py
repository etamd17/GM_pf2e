"""Grant commands on the executor (task PE-14d, Decision 5).

Four db-only command types (contract C:794-797, issue row C:860):

- ``grant.issue_level_unlock`` and ``grant.issue_retrain``: the GM, or a token
  whose scope names the type.  The subject is the character, keyed as its
  ``character.build`` record.
- ``grant.set_quartermaster`` and ``grant.revoke``: the GM only; a token is
  refused at check 1 whatever its scope.

Grant rows are not a lock-manager step: the stream row the executor holds
serializes every grant write and every ``consume``.  Each ``apply`` here calls
the ``core.persistence.grants`` write helpers directly, which is what the
"command transaction only" inventory test checks.

This module never touches the token file mirror and imports no process-lock
module (PE-14b's subprocess guard).
"""

from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import select

from core.persistence import grants
from core.persistence.models import CampaignMembership
from core.persistence.state_repositories import KIND_BUILD, StateRecord
from core.request_context import PrincipalKind

from .authorizer import RolePolicy, register_role_policy
from .checks import CommandRefusal
from .envelope import principal_key
from .events import EventDraft
from .executor import Applied
from .registry import CommandType, register

ISSUE_LEVEL_UNLOCK = "grant.issue_level_unlock"
ISSUE_RETRAIN = "grant.issue_retrain"
SET_QUARTERMASTER = "grant.set_quartermaster"
REVOKE = "grant.revoke"

# Event names registered by this task (C:1102).
EVENT_ISSUED = "grant.issued"
EVENT_REVOKED = "grant.revoked"

# Profile -> the build edition a character of that campaign must carry (C:35-39).
_EDITIONS = {"dnd5e-2014": "classic", "dnd5e-2024": "one"}
_MAX_INT = 2147483647

_KINDS = {ISSUE_LEVEL_UNLOCK: "level_unlock", ISSUE_RETRAIN: "retrain_grant",
          SET_QUARTERMASTER: "quartermaster"}
_VALUE_FIELD = {ISSUE_LEVEL_UNLOCK: ("target_level", 1), ISSUE_RETRAIN: ("build_revision", 0),
                SET_QUARTERMASTER: ("user_id", None), REVOKE: (None, None)}


def _subject_id(ctx: Any) -> str:
    (value,) = ctx.envelope["subject"].values()
    return value


def _value(ctx: Any) -> Any:
    """The type's one payload value, or ``422 rule_violation`` for any other payload."""

    name, minimum = _VALUE_FIELD[ctx.envelope["type"]]
    payload = ctx.envelope["payload"]
    if set(payload) != ({name} if name else set()):
        raise CommandRefusal("rule_violation")
    if name is None:
        return None
    value = payload[name]
    if minimum is None:
        if not isinstance(value, str) or not value:
            raise CommandRefusal("rule_violation")
    elif isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= _MAX_INT:
        raise CommandRefusal("rule_violation")
    return value


class _CharacterGrantRules:
    """Unlock and retrain: the contract's edition rule, and one live grant per target."""

    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool:
        edition = _EDITIONS.get(pin.get("profile"))
        return edition is None or subject_record.payload.get("edition") == edition

    def check(self, ctx: Any, override: Mapping[str, Any] | None) -> None:
        value, character_id = _value(ctx), _subject_id(ctx)
        find = (grants.live_level_unlock if ctx.envelope["type"] == ISSUE_LEVEL_UNLOCK
                else grants.live_retrain)
        if find(ctx.session, ctx.campaign_id, character_id, value) is not None:
            raise CommandRefusal("rule_violation")  # the same grant is already live


class _QuartermasterRules:
    """``user_id`` is a value: it must name an active player member of this campaign."""

    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool:
        return True

    def check(self, ctx: Any, override: Mapping[str, Any] | None) -> None:
        user_id = _value(ctx)
        members = CampaignMembership.__table__
        role = ctx.session.execute(select(members.c.role).where(
            members.c.campaign_id == ctx.campaign_id, members.c.user_id == user_id)).first()
        if role is None or role[0] != "player":
            raise CommandRefusal("rule_violation")
        if any(row["kind"] == "quartermaster" and row["user_id"] == user_id
               for row in grants.live_grants(ctx.session, ctx.campaign_id)):
            raise CommandRefusal("rule_violation")  # already the quartermaster


class _RevokeRules:
    def build_edition_matches(self, subject_record: StateRecord, pin: Mapping[str, Any]) -> bool:
        return True

    def check(self, ctx: Any, override: Mapping[str, Any] | None) -> None:
        _value(ctx)


def _lock_character(ctx: Any) -> None:
    # Always the subject's build row, whatever ``expected`` holds: an intent
    # command still reaches check 5 with the key registered, and a character
    # of another campaign registers None (404).  Every ``expected`` build is
    # locked too, so one naming another character is 404 at check 5, not an
    # unregistered key.  The subject's play row is the record the event's
    # ``character`` subject is judged by.
    subject = _subject_id(ctx)
    builds = {subject} | {entry["id"] for entry in ctx.envelope["expected"]}
    ctx.locks.character_states([(i, "build") for i in builds] + [(subject, "play")])


def _lock_campaign(ctx: Any) -> None:
    campaign_id = _subject_id(ctx)
    ctx.locks.register_serialized(
        "campaign", campaign_id, StateRecord.serialized("campaign", campaign_id, {}))


def _lock_grant(ctx: Any) -> None:
    grant_id = _subject_id(ctx)
    row = grants.get(ctx.session, ctx.campaign_id, grant_id)
    if row is not None and row["character_id"] is not None:
        ctx.locks.character_states([(row["character_id"], "play")])
    ctx.locks.register_serialized("grant", grant_id, None if row is None else grants.record(row))


def _revoke_terminal(ctx: Any, subject: StateRecord) -> bool:
    return not subject.payload["live"]


def _emit(ctx: Any, event_type: str, row: Mapping[str, Any]) -> Applied:
    record = grants.record(row)
    # A character grant concerns that character's owner and editor, who hold
    # those projections only on an event's subject character (C:412-413); who
    # the quartermaster is concerns every member.
    subjects = [{"kind": "grant", "id": row["grant_id"]}]
    if row["kind"] == "quartermaster":
        seen = {"member": "full"}
    else:
        seen = {"owner": "full", "editor": "full"}
        subjects.append({"kind": "character", "id": row["character_id"]})
    ctx.emit(EventDraft(type=event_type, subjects=subjects,
                        visibility={"gm_integration": "full", **seen}))
    return Applied(written={("grant", row["grant_id"]): record})


def _apply_issue(ctx: Any) -> Applied:
    command, value = ctx.envelope["type"], _value(ctx)
    issued_by_kind, issued_by_id = principal_key(ctx.principal)
    fields = {_VALUE_FIELD[command][0]: value}
    if command != SET_QUARTERMASTER:
        fields["character_id"] = _subject_id(ctx)
    row = grants.issue(ctx.session, ctx.campaign_id, kind=_KINDS[command],
                       issued_by_kind=issued_by_kind, issued_by_id=issued_by_id, **fields)
    return _emit(ctx, EVENT_ISSUED, row)


def _apply_revoke(ctx: Any) -> Applied:
    try:
        row = grants.revoke(ctx.session, ctx.campaign_id, _subject_id(ctx))
    except grants.GrantNotLive:
        raise CommandRefusal("already_finalized") from None
    return _emit(ctx, EVENT_REVOKED, row)


def _same_character(ctx: Any, subject: StateRecord, reference: StateRecord) -> bool:
    return reference.id == subject.id


_USER = frozenset({PrincipalKind.USER})
_USER_OR_TOKEN = frozenset({PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN})

for _type, _subject, _principals, _rules, _lock_plan, _apply in (
        (ISSUE_LEVEL_UNLOCK, ("character_id", KIND_BUILD), _USER_OR_TOKEN,
         _CharacterGrantRules(), _lock_character, _apply_issue),
        (ISSUE_RETRAIN, ("character_id", KIND_BUILD), _USER_OR_TOKEN,
         _CharacterGrantRules(), _lock_character, _apply_issue),
        (SET_QUARTERMASTER, ("campaign_id", "campaign"), _USER,
         _QuartermasterRules(), _lock_campaign, _apply_issue),
        (REVOKE, ("grant_id", "grant"), _USER, _RevokeRules(), _lock_grant, _apply_revoke)):
    _character = _subject[1] == KIND_BUILD
    register(CommandType(
        type=_type, subject=_subject,
        dependencies={KIND_BUILD: _same_character} if _character else {},
        payload_ids={}, requires_expected=False, derives_rules=False,
        allowed_principals=_principals, rules_adapter=_rules,
        lock_plan=_lock_plan, apply=_apply,
        **({"terminal": _revoke_terminal} if _type == REVOKE else {})))
    # No player role issues or revokes a grant (a QM can issue nothing,
    # C:879-882).  Only the two character grants may be named in a token scope.
    register_role_policy(_type, RolePolicy(token=_character))
