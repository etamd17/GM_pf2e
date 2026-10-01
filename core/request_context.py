"""Pure request identity and campaign-scope value objects.

The current application obtains these inputs from Flask sessions, campaign
documents, and verified bearer tokens.  This module deliberately does not.  It
accepts those values from its caller so authorization can be tested without a
request context and so a later app-factory migration does not inherit ambient
process state.

Only verified identities belong in :class:`Principal`: raw passwords and raw
tokens must be checked by the integration layer first. Token principals in this
model are bound to one explicit campaign. The application's older deploy-wide
Chronicle environment secret is intentionally not adapted into such a principal;
it remains a narrow compatibility branch whose target is validated by the
Chronicle view until credential scoping is migrated separately.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PrincipalKind(str, Enum):
    """The already-verified credential that established a request identity."""

    ANONYMOUS = "anonymous"
    USER = "user"
    LEGACY_GM = "legacy_gm"
    LEGACY_PLAYER = "legacy_player"
    INTEGRATION_TOKEN = "integration_token"
    PUBLISH_TOKEN = "publish_token"


class CampaignRole(str, Enum):
    """Recognized campaign membership roles."""

    PLAYER = "player"
    GM = "gm"


def _identifier(value: Any, label: str, *, required: bool = False) -> str | None:
    """Validate an identifier without coercing attacker-controlled values."""

    if value is None:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a non-empty string")
    value = value.strip()
    if not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class Principal:
    """One verified request identity, with no implicit configuration lookups."""

    kind: PrincipalKind = PrincipalKind.ANONYMOUS
    user_id: str | None = None
    is_admin: bool = False
    credential_id: str | None = None
    campaign_id: str | None = None
    character_ref: str | None = None

    def __post_init__(self) -> None:
        try:
            kind = PrincipalKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown principal kind: {self.kind!r}") from exc
        object.__setattr__(self, "kind", kind)

        user_id = _identifier(self.user_id, "user_id")
        credential_id = _identifier(self.credential_id, "credential_id")
        campaign_id = _identifier(self.campaign_id, "campaign_id")
        character_ref = _identifier(self.character_ref, "character_ref")
        object.__setattr__(self, "user_id", user_id)
        object.__setattr__(self, "credential_id", credential_id)
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "character_ref", character_ref)

        if not isinstance(self.is_admin, bool):
            raise ValueError("is_admin must be a bool")

        if kind is PrincipalKind.ANONYMOUS:
            if any((user_id, credential_id, campaign_id, character_ref)) or self.is_admin:
                raise ValueError("anonymous principal cannot carry identity attributes")
            return

        if kind is PrincipalKind.USER:
            if not user_id:
                raise ValueError("user principal requires user_id")
            if any((credential_id, campaign_id, character_ref)):
                raise ValueError("user principal cannot carry token or legacy attributes")
            return

        if self.is_admin or user_id:
            raise ValueError("only user principals can carry user_id or is_admin")

        if kind is PrincipalKind.LEGACY_GM:
            if not campaign_id:
                raise ValueError("legacy GM principal requires campaign_id")
            if credential_id or character_ref:
                raise ValueError("legacy GM principal has incompatible attributes")
            return

        if kind is PrincipalKind.LEGACY_PLAYER:
            if not campaign_id or not character_ref:
                raise ValueError(
                    "legacy player principal requires campaign_id and character_ref"
                )
            if credential_id:
                raise ValueError("legacy player principal cannot carry credential_id")
            return

        if kind in {PrincipalKind.INTEGRATION_TOKEN, PrincipalKind.PUBLISH_TOKEN}:
            if not credential_id or not campaign_id:
                raise ValueError("token principal requires credential_id and campaign_id")
            if character_ref:
                raise ValueError("token principal cannot carry character_ref")
            return

        raise AssertionError(f"unhandled principal kind: {kind}")

    @classmethod
    def anonymous(cls) -> "Principal":
        return cls()

    @classmethod
    def user(cls, user_id: str, *, is_admin: bool = False) -> "Principal":
        return cls(kind=PrincipalKind.USER, user_id=user_id, is_admin=is_admin)

    @classmethod
    def legacy_gm(cls, campaign_id: str) -> "Principal":
        return cls(kind=PrincipalKind.LEGACY_GM, campaign_id=campaign_id)

    @classmethod
    def legacy_player(cls, character_ref: str, *, campaign_id: str) -> "Principal":
        return cls(
            kind=PrincipalKind.LEGACY_PLAYER,
            campaign_id=campaign_id,
            character_ref=character_ref,
        )

    @classmethod
    def integration_token(cls, credential_id: str, *, campaign_id: str) -> "Principal":
        return cls(
            kind=PrincipalKind.INTEGRATION_TOKEN,
            credential_id=credential_id,
            campaign_id=campaign_id,
        )

    @classmethod
    def publish_token(cls, credential_id: str, *, campaign_id: str) -> "Principal":
        return cls(
            kind=PrincipalKind.PUBLISH_TOKEN,
            credential_id=credential_id,
            campaign_id=campaign_id,
        )

    @property
    def is_human(self) -> bool:
        return self.kind in {
            PrincipalKind.USER,
            PrincipalKind.LEGACY_GM,
            PrincipalKind.LEGACY_PLAYER,
        }


def principal_from_user(user: Mapping[str, Any] | None) -> Principal:
    """Adapt a verified user record without importing the account store."""

    if user is None:
        return Principal.anonymous()
    if not isinstance(user, Mapping):
        raise ValueError("user must be a mapping or None")
    return Principal.user(user.get("id"), is_admin=user.get("is_admin") is True)


def campaign_role_for(
    campaign: Mapping[str, Any] | None,
    user_id: str | None,
) -> CampaignRole | None:
    """Return one unambiguous recognized role for ``user_id``, else ``None``."""

    if campaign is None or not user_id:
        return None
    members = campaign.get("members", ())
    if not isinstance(members, Iterable) or isinstance(members, (str, bytes, Mapping)):
        return None
    roles: set[CampaignRole] = set()
    for member in members:
        if not isinstance(member, Mapping) or member.get("user_id") != user_id:
            continue
        try:
            roles.add(CampaignRole(member.get("role")))
        except (TypeError, ValueError):
            continue
    # Duplicate contradictory memberships are invalid data, not an escalation.
    return next(iter(roles)) if len(roles) == 1 else None


@dataclass(frozen=True, slots=True)
class CampaignContext:
    """The immutable campaign scope authorized and addressed by one request."""

    principal: Principal
    campaign_id: str | None
    live_campaign_id: str | None
    campaign_exists: bool
    system: str | None
    membership_role: CampaignRole | None

    def __post_init__(self) -> None:
        if not isinstance(self.principal, Principal):
            raise ValueError("principal must be a Principal")
        object.__setattr__(
            self, "campaign_id", _identifier(self.campaign_id, "campaign_id")
        )
        object.__setattr__(
            self,
            "live_campaign_id",
            _identifier(self.live_campaign_id, "live_campaign_id"),
        )
        if not isinstance(self.campaign_exists, bool):
            raise ValueError("campaign_exists must be a bool")
        if self.system is not None and (
            not isinstance(self.system, str) or not self.system.strip()
        ):
            raise ValueError("system must be a non-empty string or None")
        if self.system is not None:
            object.__setattr__(self, "system", self.system.strip())
        if self.membership_role is not None:
            try:
                role = CampaignRole(self.membership_role)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"unknown campaign role: {self.membership_role!r}"
                ) from exc
            object.__setattr__(self, "membership_role", role)
        if self.campaign_exists and not self.campaign_id:
            raise ValueError("an existing campaign requires campaign_id")
        if self.membership_role is not None and not self.campaign_exists:
            raise ValueError("membership cannot exist without a campaign")

    @property
    def is_live(self) -> bool:
        return bool(
            self.campaign_exists
            and self.campaign_id
            and self.live_campaign_id
            and self.campaign_id == self.live_campaign_id
        )

    @property
    def is_member(self) -> bool:
        return self.membership_role in {CampaignRole.PLAYER, CampaignRole.GM}

    @property
    def is_gm(self) -> bool:
        return self.membership_role is CampaignRole.GM


def resolve_campaign_context(
    principal: Principal,
    *,
    campaign_id: str | None,
    campaign: Mapping[str, Any] | None,
    live_campaign_id: str | None,
) -> CampaignContext:
    """Resolve a context solely from caller-supplied, request-scoped values.

    ``campaign_id`` is the resource/selection the handler will address.  If a
    campaign document is also supplied, its ID must agree.  The separate live
    ID is retained even when it differs so a 409 decision is diagnosable.
    """

    if not isinstance(principal, Principal):
        raise ValueError("principal must be a Principal")
    explicit_id = _identifier(campaign_id, "campaign_id")
    live_id = _identifier(live_campaign_id, "live_campaign_id")
    if campaign is not None and not isinstance(campaign, Mapping):
        raise ValueError("campaign must be a mapping or None")

    document_id = (
        _identifier(campaign.get("id"), "campaign.id") if campaign is not None else None
    )
    if explicit_id and document_id and explicit_id != document_id:
        raise ValueError(
            f"campaign_id {explicit_id!r} does not match campaign document {document_id!r}"
        )
    resolved_id = explicit_id or document_id
    exists = campaign is not None
    if exists and not resolved_id:
        raise ValueError("campaign document requires an explicit or document campaign id")

    raw_system = campaign.get("system") if campaign is not None else None
    if raw_system is not None and not isinstance(raw_system, str):
        raise ValueError("campaign.system must be a string or None")
    system = raw_system.strip() if isinstance(raw_system, str) and raw_system.strip() else None
    role = (
        campaign_role_for(campaign, principal.user_id)
        if principal.kind is PrincipalKind.USER
        else None
    )
    return CampaignContext(
        principal=principal,
        campaign_id=resolved_id,
        live_campaign_id=live_id,
        campaign_exists=exists,
        system=system,
        membership_role=role,
    )


@dataclass(frozen=True, slots=True)
class CharacterContext:
    """Authorization metadata for one campaign-scoped character resource."""

    campaign_id: str
    character_id: str
    owner_user_id: str | None = None
    editor_user_ids: frozenset[str] = frozenset()
    legacy_ref: str | None = None
    viewer_user_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "campaign_id",
            _identifier(self.campaign_id, "character.campaign_id", required=True),
        )
        object.__setattr__(
            self,
            "character_id",
            _identifier(self.character_id, "character_id", required=True),
        )
        object.__setattr__(
            self,
            "owner_user_id",
            _identifier(self.owner_user_id, "owner_user_id"),
        )
        object.__setattr__(
            self,
            "legacy_ref",
            _identifier(self.legacy_ref, "legacy_ref"),
        )
        if isinstance(self.editor_user_ids, (str, bytes)):
            raise ValueError("editor_user_ids must be an iterable of user ids")
        try:
            editors = frozenset(
                _identifier(value, "editor_user_id", required=True)
                for value in self.editor_user_ids
            )
        except TypeError as exc:
            raise ValueError("editor_user_ids must be an iterable of user ids") from exc
        object.__setattr__(self, "editor_user_ids", editors)
        if isinstance(self.viewer_user_ids, (str, bytes)):
            raise ValueError("viewer_user_ids must be an iterable of user ids")
        try:
            viewers = frozenset(
                _identifier(value, "viewer_user_id", required=True)
                for value in self.viewer_user_ids
            )
        except TypeError as exc:
            raise ValueError("viewer_user_ids must be an iterable of user ids") from exc
        object.__setattr__(self, "viewer_user_ids", viewers)


def resolve_character_context(
    *,
    campaign_id: str,
    character_id: str,
    owner_user_id: str | None = None,
    editor_user_ids: Iterable[str] = (),
    legacy_ref: str | None = None,
    viewer_user_ids: Iterable[str] = (),
) -> CharacterContext:
    """Build character authorization metadata from an already-loaded record."""

    return CharacterContext(
        campaign_id=campaign_id,
        character_id=character_id,
        owner_user_id=owner_user_id,
        editor_user_ids=frozenset(editor_user_ids),
        legacy_ref=legacy_ref,
        viewer_user_ids=viewer_user_ids,
    )
