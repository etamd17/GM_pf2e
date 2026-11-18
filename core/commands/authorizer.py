"""The step-4 authorizer: checks 4 and 5 from a fresh SQL read (task PE-14c).

:func:`load` reads every authorization input from the caller's session, in
the command transaction that already holds the campaign's stream row (C:429-436):
the user row, the campaign row (no ``FOR UPDATE``: the stream lock serializes
authorization writes, PE-14d), the membership, the assignments, the live
grants, the token record and its scope, and the active session.  It never
reads a ``flask.g`` or request cache, takes no lock of its own and never
touches the ``tokens.json`` file: a token with no SQL row is simply not
authorized.  This module imports neither ``core.obsidian_sync`` nor
``core.persistence.integration_tokens``.

Which non-GM roles may send a command type is a per-type :class:`RolePolicy`,
registered by the module that owns the type (:func:`register_role_policy`).
A type with no policy is GM-only, and a token reaches it only by naming it in
its scope.  ``session.start`` and ``session.end`` carry the built-in session
rule (Decision 3).  Visibility for check 5 dispatches on ``record.kind``
through :func:`register_visibility`; an unregistered kind is not visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from sqlalchemy import select

from core.persistence.models import (
    Campaign,
    CampaignGrant,
    CampaignMembership,
    CampaignSession,
    CharacterAssignment,
    IntegrationToken,
    User,
)
from core.persistence.state_repositories import (
    KIND_BUILD,
    KIND_CLOCK,
    KIND_COMBATANT,
    KIND_ENCOUNTER,
    KIND_PLAY,
    EncounterStateRepository,
    StateRecord,
)
from core.request_context import Principal, PrincipalKind

from . import registry

LIVE_SCOPE = "@live"
OVERRIDE_PREFIX = "override:"
SESSION_TYPES = frozenset({"session.start", "session.end"})
# Non-GM roles a policy may name (contract section 8 matrix columns, plus
# ``member`` for campaign-level subjects any player member may act on).
ROLES = frozenset({"owner", "editor", "viewer", "party", "qm", "member"})
# A token can never manage membership, ownership, tokens or QM grants (C:849-851),
# whatever its scope: any type with one of these dot-separated name parts.
# Matched as substrings of the lower-cased type name, so ``character.set_owner``
# and ``campaign.add_member`` fail closed too (C:849-851).
TOKEN_FORBIDDEN_WORDS = ("member", "owner", "assign", "token", "quartermaster")
_GRANT_T = CampaignGrant.__table__


@dataclass(frozen=True)
class RolePolicy:
    """Who besides the GM may send a type.

    ``roles`` are the non-GM roles allowed (check 4 asks whether the caller
    holds one anywhere in the campaign; check 5 asks whether it holds one on
    the subject).  ``live`` puts the type in the ``"@live"`` token scope set
    (C:852).  ``token`` False refuses every token whatever its scope.
    """

    roles: frozenset[str] = frozenset()
    live: bool = False
    token: bool = True

    def __post_init__(self) -> None:
        roles = frozenset(self.roles)
        if not roles <= ROLES:
            raise ValueError(f"unknown roles {sorted(roles - ROLES)}")
        object.__setattr__(self, "roles", roles)
        if not isinstance(self.live, bool) or not isinstance(self.token, bool):
            raise ValueError("live and token must be bool")


_SESSION_POLICY = RolePolicy(roles=frozenset(), live=True, token=True)
_POLICIES: dict[str, RolePolicy] = {t: _SESSION_POLICY for t in SESSION_TYPES}
_DEFAULT_POLICY = RolePolicy()

Resolver = Callable[["CommandAuthorizer", StateRecord], bool]
_RESOLVERS: dict[str, Resolver] = {}


def register_role_policy(type: str, policy: RolePolicy) -> None:
    """Register the non-GM roles for one command type (import time of its module)."""

    if not isinstance(type, str) or not type:
        raise ValueError("a role policy needs a type name")
    if not isinstance(policy, RolePolicy):
        raise TypeError("register_role_policy takes a RolePolicy")
    if type in _POLICIES:
        raise ValueError(f"a role policy for {type} is already registered")
    _POLICIES[type] = policy


def role_policy(type: str) -> RolePolicy:
    return _POLICIES.get(type, _DEFAULT_POLICY)


def register_visibility(kind: str, resolver: Resolver) -> None:
    """Register ``resolver(authorizer, record) -> bool`` for a record kind (import time)."""

    if not isinstance(kind, str) or not kind or not callable(resolver):
        raise ValueError("register_visibility needs a kind and a callable")
    if kind in _RESOLVERS:
        raise ValueError(f"visibility for {kind} is already registered")
    _RESOLVERS[kind] = resolver


def token_forbidden(type: str) -> bool:
    """A type a token may never be authorized for, whatever its scope (C:849-851)."""

    name = type.lower()
    return any(word in name for word in TOKEN_FORBIDDEN_WORDS)


class CommandAuthorizer:
    """One principal's authorization inputs, read once in the command transaction.

    Public read-only attributes for visibility resolvers (Decision 3):
    ``is_gm``, ``is_site_admin``, ``principal_kind``, ``user_id``,
    ``token_campaign_id`` and ``campaign_id``.
    """

    def __init__(self, session: Any, *, campaign_id: str, principal_kind: PrincipalKind,
                 user_id: str | None, token_campaign_id: str | None, valid: bool,
                 is_site_admin: bool, is_gm: bool, membership_role: str | None,
                 assignments: Mapping[str, str], grants: tuple[dict[str, Any], ...],
                 token: dict[str, Any] | None, active_session: dict[str, Any] | None) -> None:
        self._session = session
        self._campaign_id = campaign_id
        self._principal_kind = principal_kind
        self._user_id = user_id
        self._token_campaign_id = token_campaign_id
        self._valid = valid
        self._is_site_admin = is_site_admin
        self._is_gm = is_gm
        self._membership_role = membership_role
        self._assignments = dict(assignments)
        self._grants = grants
        self._token = token
        self._scope = frozenset(token["scope"]) if token is not None else frozenset()
        self._active_session = active_session

    # -- read-only attributes ------------------------------------------------
    @property
    def campaign_id(self) -> str:
        return self._campaign_id

    @property
    def principal_kind(self) -> PrincipalKind:
        return self._principal_kind

    @property
    def user_id(self) -> str | None:
        return self._user_id

    @property
    def token_campaign_id(self) -> str | None:
        return self._token_campaign_id

    @property
    def is_site_admin(self) -> bool:
        return self._valid and self._is_site_admin

    @property
    def is_gm(self) -> bool:
        """The campaign's GM or the site admin (C:847), from SQL rows only."""

        return self._valid and self._is_gm

    # -- derived facts -------------------------------------------------------
    @property
    def _is_token(self) -> bool:
        return self._valid and self._principal_kind is PrincipalKind.INTEGRATION_TOKEN

    @property
    def _is_player(self) -> bool:
        return (self._valid and self._principal_kind is PrincipalKind.USER
                and not self._is_gm and self._membership_role == "player")

    @property
    def _is_qm(self) -> bool:
        return self._is_player and any(
            g["kind"] == "quartermaster" and g["user_id"] == self._user_id for g in self._grants)

    def _campaign_roles(self) -> frozenset[str]:
        if not self._is_player:
            return frozenset()
        roles = {"member", "party", *self._assignments.values()}
        if self._is_qm:
            roles.add("qm")
        return frozenset(roles)

    def _subject_roles(self, subject: StateRecord) -> frozenset[str]:
        if not self._is_player:
            return frozenset()
        roles = {"member"}
        if subject.kind in (KIND_BUILD, KIND_PLAY):
            roles.add(self._assignments.get(subject.id, "party"))
        if self._is_qm:
            roles.add("qm")
        return frozenset(roles)

    def _scope_permits(self, type: str) -> bool:
        policy = role_policy(type)
        if not self._is_token or not policy.token or token_forbidden(type):
            return False
        return type in self._scope or (policy.live and LIVE_SCOPE in self._scope)

    # -- the Authorizer protocol (PE-13b) -------------------------------------
    def campaign_role_allows(self, type: str) -> bool:
        if not self._valid:
            return False
        if self._is_token:
            return self._scope_permits(type)
        if self._is_gm:
            return True
        return bool(role_policy(type).roles & self._campaign_roles())

    def override_allowed(self, type: str) -> bool:
        """GM or site admin; a token only with ``override:<type>`` on a type that allows it."""

        if not self._valid:
            return False
        if self._is_token:
            command_type = registry.get(type)
            return (command_type is not None and command_type.override_gm_only is False
                    and not token_forbidden(type)
                    and OVERRIDE_PREFIX + type in self._scope)
        return self._is_gm

    def visible(self, record: StateRecord) -> bool:
        if not self._valid:
            return False
        resolver = _RESOLVERS.get(getattr(record, "kind", None))
        return bool(resolver(self, record)) if resolver is not None else False

    def subject_role_allows(self, type: str, subject: StateRecord) -> bool:
        if not self._valid:
            return False
        if self._is_token:
            return self._scope_permits(type)
        if self._is_gm:
            return True
        return bool(role_policy(type).roles & self._subject_roles(subject))

    # -- further reads for later tasks ----------------------------------------
    def is_live(self, character_id: str) -> bool:
        """An active SQL session and a PC combatant of the active encounter (C:842-843)."""

        if not self._valid or self._active_session is None:
            return False
        return EncounterStateRepository(self._session).is_combatant(
            self._campaign_id, character_id)

    def is_recorded_replacement(self, original_credential_id: str) -> bool:
        """This token replaces ``original`` in the same campaign and was minted by its GM."""

        token = self._token
        if (not self._is_token or token is None or not isinstance(original_credential_id, str)
                or token["replaces_credential_id"] != original_credential_id
                or token["minted_by_user_id"] is None):
            return False
        t = IntegrationToken.__table__
        found = self._session.execute(select(t.c.credential_id).where(
            t.c.credential_id == original_credential_id,
            t.c.campaign_id == self._campaign_id)).first()
        if found is None:
            return False
        # The minter must be this campaign's GM or a site admin.  The rows read
        # are today's, so a GM who has since been removed no longer counts.
        minter = token["minted_by_user_id"]
        users, members = User.__table__, CampaignMembership.__table__
        admin = self._session.execute(select(users.c.is_admin).where(
            users.c.id == minter)).first()
        if admin is None:
            return False
        if admin[0] is True:
            return True
        role = self._session.execute(select(members.c.role).where(
            members.c.campaign_id == self._campaign_id,
            members.c.user_id == minter)).first()
        return role is not None and role[0] == "gm"

    def token_revoked(self, credential_id: str) -> bool:
        """SQL ``revoked_at`` only (C:266, C:275), within this campaign."""

        t = IntegrationToken.__table__
        revoked = self._session.execute(select(t.c.revoked_at).where(
            t.c.credential_id == credential_id, t.c.campaign_id == self._campaign_id)).first()
        return revoked is not None and revoked[0] is not None


def _first(session: Any, statement: Any) -> dict[str, Any] | None:
    row = session.execute(statement).first()
    return None if row is None else dict(row._mapping)


def load(session: Any, principal: Principal, campaign_id: str) -> CommandAuthorizer:
    """Read every authorization input for ``principal`` from ``session`` (C:429-436).

    ``Principal.is_admin`` is ignored: admin and GM status come from the SQL
    user and campaign rows only.  Works with no ``campaign_state_authority``
    row and under any authority.  An unknown user, a token with no live SQL
    row in this campaign, a missing or trashed campaign, or an unsupported
    principal kind yields an authorizer that refuses everything.
    """

    if not isinstance(principal, Principal):
        raise TypeError("load needs a verified core.request_context.Principal")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise TypeError("campaign_id must be a non-empty string")
    kind = principal.kind
    users, campaigns = User.__table__, Campaign.__table__
    members, assignments = CampaignMembership.__table__, CharacterAssignment.__table__
    tokens, sessions = IntegrationToken.__table__, CampaignSession.__table__

    campaign = _first(session, select(campaigns.c.id, campaigns.c.trashed_at)
                      .where(campaigns.c.id == campaign_id))
    valid = campaign is not None and campaign["trashed_at"] is None
    user_id = token_campaign_id = membership_role = token = None
    is_site_admin = is_gm = False
    held: dict[str, str] = {}

    if kind is PrincipalKind.USER and principal.user_id is not None:
        user_id = principal.user_id
        user = _first(session, select(users.c.id, users.c.is_admin)
                      .where(users.c.id == user_id))
        membership = _first(session, select(members.c.role).where(
            members.c.campaign_id == campaign_id, members.c.user_id == user_id))
        membership_role = None if membership is None else membership["role"]
        is_site_admin = bool(user is not None and user["is_admin"] is True)
        is_gm = is_site_admin or membership_role == "gm"
        valid = valid and user is not None and (is_site_admin or membership_role is not None)
        if valid:
            held = {row.character_id: row.role for row in session.execute(
                select(assignments.c.character_id, assignments.c.role).where(
                    assignments.c.campaign_id == campaign_id,
                    assignments.c.user_id == user_id)).all()}
    elif kind is PrincipalKind.INTEGRATION_TOKEN and principal.credential_id is not None:
        token = _first(session, select(tokens).where(
            tokens.c.credential_id == principal.credential_id,
            tokens.c.campaign_id == campaign_id))
        valid = (valid and principal.campaign_id == campaign_id and token is not None
                 and token["revoked_at"] is None)
        if token is not None:
            token_campaign_id = token["campaign_id"]
            scope = token["scope"]
            token["scope"] = [s for s in scope if isinstance(s, str)] if isinstance(
                scope, list) else []
    else:
        valid = False

    grants: tuple[dict[str, Any], ...] = ()
    active = None
    if valid:
        grants = tuple(dict(row._mapping) for row in session.execute(
            select(_GRANT_T).where(_GRANT_T.c.campaign_id == campaign_id,
                                   _GRANT_T.c.consumed_at.is_(None),
                                   _GRANT_T.c.revoked_at.is_(None))
            .order_by(_GRANT_T.c.grant_id)).all())
        active = _first(session, select(sessions.c.session_id).where(
            sessions.c.campaign_id == campaign_id, sessions.c.status == "active"))

    return CommandAuthorizer(
        session, campaign_id=campaign_id, principal_kind=kind, user_id=user_id,
        token_campaign_id=token_campaign_id, valid=valid, is_site_admin=is_site_admin,
        is_gm=is_gm and kind is PrincipalKind.USER, membership_role=membership_role,
        assignments=held, grants=grants, token=token, active_session=active)


# -- built-in visibility (Decision 5; projections C:635-648) ------------------

def _staff(a: CommandAuthorizer) -> bool:
    """GM, site admin, or a token of this campaign (``gm_integration``)."""

    return a.is_gm or a._is_token


def _campaign(a: CommandAuthorizer, record: StateRecord) -> bool:
    return record.id == a.campaign_id and (_staff(a) or a._is_player)


def _build(a: CommandAuthorizer, record: StateRecord) -> bool:
    # ``party`` and ``qm`` see party HP and conditions only, never the build (C:642, C:856).
    return _staff(a) or (a._is_player and record.id in a._assignments)


def _play(a: CommandAuthorizer, record: StateRecord) -> bool:
    return _staff(a) or a._is_player


def _encounter(a: CommandAuthorizer, record: StateRecord) -> bool:
    return _staff(a) or a._is_player


def _combatant(a: CommandAuthorizer, record: StateRecord) -> bool:
    if _staff(a):
        return True
    # A player sees PCs, and another combatant only once revealed (``encounter_player``).
    return a._is_player and (record.payload.get("kind") == "pc"
                             or record.payload.get("revealed") is True)


def _clock(a: CommandAuthorizer, record: StateRecord) -> bool:
    return _staff(a) or a._is_player


def _grant(a: CommandAuthorizer, record: StateRecord) -> bool:
    if a.is_gm:
        return True
    if not a._is_player:
        return False  # tokens included
    payload = record.payload
    if payload.get("kind") == "quartermaster":
        return payload.get("user_id") == a.user_id
    return a._assignments.get(payload.get("character_id")) in ("owner", "editor")


for _kind, _resolver in (("campaign", _campaign), (KIND_BUILD, _build), (KIND_PLAY, _play),
                         (KIND_ENCOUNTER, _encounter), (KIND_COMBATANT, _combatant),
                         (KIND_CLOCK, _clock), ("grant", _grant)):
    register_visibility(_kind, _resolver)
