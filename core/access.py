"""Central, side-effect-free access decisions for the route-policy inventory.

``decide_access`` performs no Flask, session, storage, or environment lookups.
The application boundary supplies one resolved :class:`CampaignContext`, then
may translate a denial through ``denial_contract`` or the thin Flask adapter.
Keeping the decision separate from rendering makes every authorization branch
unit-testable and prevents API routes from accidentally receiving redirects.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlencode

from core.request_context import (
    CampaignContext,
    CharacterContext,
    Principal,
    PrincipalKind,
)
from core.route_policy import RoutePolicy


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """A stable allow/deny result before transport-specific rendering."""

    allowed: bool
    status_code: int
    code: str
    message: str


def _allow(code: str = "allowed") -> AccessDecision:
    return AccessDecision(True, 200, code, "Access granted.")


def _deny(status_code: int, code: str, message: str) -> AccessDecision:
    return AccessDecision(False, status_code, code, message)


LOGIN_REQUIRED = _deny(401, "login_required", "Login required.")
HUMAN_SESSION_REQUIRED = _deny(
    403,
    "human_session_required",
    "This operation requires a user session.",
)
SITE_ADMIN_REQUIRED = _deny(
    403,
    "site_admin_required",
    "Site administrator access required.",
)
CAMPAIGN_REQUIRED = _deny(
    403,
    "campaign_required",
    "A valid campaign context is required.",
)
CAMPAIGN_MEMBER_REQUIRED = _deny(
    403,
    "campaign_membership_required",
    "Campaign membership required.",
)
CAMPAIGN_GM_REQUIRED = _deny(
    403,
    "campaign_gm_required",
    "Campaign GM access required.",
)
CHARACTER_REQUIRED = _deny(
    403,
    "character_required",
    "A campaign-scoped character is required.",
)
CHARACTER_CAMPAIGN_MISMATCH = _deny(
    403,
    "character_campaign_mismatch",
    "The character does not belong to the authorized campaign.",
)
CHARACTER_ACCESS_REQUIRED = _deny(
    403,
    "character_access_required",
    "Character owner, editor, or campaign GM access required.",
)
CHARACTER_OWNER_PRIVATE_ACCESS_REQUIRED = _deny(
    403,
    "character_owner_private_access_required",
    "Character owner or campaign GM access required for private character data.",
)
CAMPAIGN_NOT_LIVE = _deny(
    409,
    "campaign_not_live",
    "The authorized campaign is not the campaign loaded by this legacy handler.",
)
INTEGRATION_TOKEN_REQUIRED = _deny(
    401,
    "integration_token_required",
    "A valid integration token is required.",
)
TOKEN_CAMPAIGN_MISMATCH = _deny(
    403,
    "token_campaign_mismatch",
    "The verified token is not valid for this campaign.",
)


_CAMPAIGN_POLICIES = frozenset(
    {
        RoutePolicy.CAMPAIGN_MEMBER,
        RoutePolicy.CAMPAIGN_GM,
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
        RoutePolicy.LIVE_CAMPAIGN_MEMBER,
        RoutePolicy.LIVE_CAMPAIGN_GM,
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
    }
)


def _coerce_policy(policy: RoutePolicy | str) -> RoutePolicy:
    try:
        return RoutePolicy(policy)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown route policy: {policy!r}") from exc


def _legacy_scope_matches(principal: Principal, context: CampaignContext) -> bool:
    return bool(
        principal.campaign_id
        and context.campaign_id
        and principal.campaign_id == context.campaign_id
    )


def _legacy_open_gm(context: CampaignContext, legacy_open: bool) -> bool:
    # Account users and bearer-token principals must never inherit an open-dev
    # bypass if the integration layer accidentally enables compatibility mode.
    return bool(
        legacy_open
        and context.principal.kind
        in {
            PrincipalKind.ANONYMOUS,
            PrincipalKind.LEGACY_GM,
            PrincipalKind.LEGACY_PLAYER,
        }
    )


def _is_campaign_member(context: CampaignContext, *, legacy_open: bool) -> bool:
    principal = context.principal
    if principal.kind is PrincipalKind.USER:
        return principal.is_admin or context.is_member
    if _legacy_open_gm(context, legacy_open):
        return True
    return (
        principal.kind in {PrincipalKind.LEGACY_GM, PrincipalKind.LEGACY_PLAYER}
        and _legacy_scope_matches(principal, context)
    )


def _is_campaign_gm(context: CampaignContext, *, legacy_open: bool) -> bool:
    principal = context.principal
    if principal.kind is PrincipalKind.USER:
        return principal.is_admin or context.is_gm
    if _legacy_open_gm(context, legacy_open):
        return True
    return principal.kind is PrincipalKind.LEGACY_GM and _legacy_scope_matches(
        principal, context
    )


def _require_human(context: CampaignContext, *, legacy_open: bool) -> AccessDecision | None:
    principal = context.principal
    if principal.is_human or _legacy_open_gm(context, legacy_open):
        return None
    return LOGIN_REQUIRED if principal.kind is PrincipalKind.ANONYMOUS else HUMAN_SESSION_REQUIRED


def _require_campaign(context: CampaignContext) -> AccessDecision | None:
    if not context.campaign_exists or not context.campaign_id:
        return CAMPAIGN_REQUIRED
    return None


def _require_live(context: CampaignContext) -> AccessDecision | None:
    return None if context.is_live else CAMPAIGN_NOT_LIVE


def _token_access(
    context: CampaignContext,
    *,
    expected_kind: PrincipalKind,
) -> AccessDecision:
    principal = context.principal
    if principal.kind is not expected_kind:
        return INTEGRATION_TOKEN_REQUIRED
    campaign_denial = _require_campaign(context)
    if campaign_denial is not None:
        return campaign_denial
    if principal.campaign_id != context.campaign_id:
        return TOKEN_CAMPAIGN_MISMATCH
    return _allow("verified_token")


def _character_access(
    context: CampaignContext,
    character: CharacterContext | None,
    *,
    legacy_open: bool,
    allow_editor: bool = True,
) -> AccessDecision:
    is_gm = _is_campaign_gm(context, legacy_open=legacy_open)
    if not is_gm and not _is_campaign_member(context, legacy_open=legacy_open):
        return CAMPAIGN_MEMBER_REQUIRED
    if character is not None and character.campaign_id != context.campaign_id:
        return CHARACTER_CAMPAIGN_MISMATCH
    if is_gm:
        return _allow("campaign_gm")
    if character is None:
        return CHARACTER_REQUIRED

    principal = context.principal
    if principal.kind is PrincipalKind.USER and principal.user_id:
        if principal.user_id == character.owner_user_id:
            return _allow("character_owner")
        if allow_editor and principal.user_id in character.editor_user_ids:
            return _allow("character_editor")
    if (
        principal.kind is PrincipalKind.LEGACY_PLAYER
        and _legacy_scope_matches(principal, context)
        and character.legacy_ref
        and principal.character_ref == character.legacy_ref
    ):
        return _allow("legacy_character")
    return (
        CHARACTER_ACCESS_REQUIRED
        if allow_editor
        else CHARACTER_OWNER_PRIVATE_ACCESS_REQUIRED
    )


def decide_access(
    policy: RoutePolicy | str,
    context: CampaignContext,
    *,
    character: CharacterContext | None = None,
    legacy_open: bool = False,
) -> AccessDecision:
    """Evaluate one accepted route policy against explicit request context.

    Identity/role checks deliberately precede the live-slot comparison.  An
    unauthenticated caller therefore receives 401 and an outsider receives 403
    rather than learning whether a campaign currently occupies the live slot.
    Authorized callers of legacy-global handlers receive the explicit 409 guard.
    """

    policy = _coerce_policy(policy)
    if not isinstance(context, CampaignContext):
        raise ValueError("context must be a CampaignContext")
    if not isinstance(legacy_open, bool):
        raise ValueError("legacy_open must be a bool")

    principal = context.principal
    if policy is RoutePolicy.PUBLIC:
        return _allow("public")

    if policy is RoutePolicy.INTEGRATION_TOKEN:
        return _token_access(context, expected_kind=PrincipalKind.INTEGRATION_TOKEN)

    # A campaign-scoped publish credential is verified at the request boundary.
    # The application's older deploy-wide Chronicle compatibility secret is
    # handled separately by its narrow app-layer adapter and never represented
    # as this stronger principal type.
    if (
        policy is RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN
        and principal.kind is PrincipalKind.PUBLISH_TOKEN
    ):
        return _token_access(context, expected_kind=PrincipalKind.PUBLISH_TOKEN)

    if policy is RoutePolicy.AUTHENTICATED:
        if principal.kind is PrincipalKind.USER:
            return _allow("authenticated")
        return LOGIN_REQUIRED if principal.kind is PrincipalKind.ANONYMOUS else HUMAN_SESSION_REQUIRED

    if policy is RoutePolicy.SITE_ADMIN:
        if principal.kind is PrincipalKind.USER and principal.is_admin:
            return _allow("site_admin")
        if principal.kind is PrincipalKind.ANONYMOUS:
            return LOGIN_REQUIRED
        return SITE_ADMIN_REQUIRED

    if policy not in _CAMPAIGN_POLICIES:
        # Kept as a defensive exhaustiveness assertion if RoutePolicy grows.
        raise ValueError(f"route policy has no decision rule: {policy.value}")

    # Explicit-target Chronicle operations authorize the target again in their
    # view. Preserve site-admin operation even when that admin has not selected
    # a browsing campaign in this session.
    if (
        policy
        in {
            RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
            RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
        }
        and principal.kind is PrincipalKind.USER
        and principal.is_admin
    ):
        return _allow("site_admin")

    identity_denial = _require_human(context, legacy_open=legacy_open)
    if identity_denial is not None:
        return identity_denial
    campaign_denial = _require_campaign(context)
    if campaign_denial is not None:
        return campaign_denial

    if policy is RoutePolicy.CAMPAIGN_MEMBER:
        return (
            _allow("campaign_member")
            if _is_campaign_member(context, legacy_open=legacy_open)
            else CAMPAIGN_MEMBER_REQUIRED
        )

    if policy is RoutePolicy.CAMPAIGN_GM:
        return (
            _allow("campaign_gm")
            if _is_campaign_gm(context, legacy_open=legacy_open)
            else CAMPAIGN_GM_REQUIRED
        )

    if policy in {
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
    }:
        return (
            _allow("campaign_gm")
            if _is_campaign_gm(context, legacy_open=legacy_open)
            else CAMPAIGN_GM_REQUIRED
        )

    if policy is RoutePolicy.CHARACTER_OWNER_OR_GM:
        return _character_access(context, character, legacy_open=legacy_open)

    if policy in (
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
    ):
        character_decision = _character_access(
            context,
            character,
            legacy_open=legacy_open,
            allow_editor=policy is not RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        )
        if (policy is RoutePolicy.LIVE_CHARACTER_VIEW and not character_decision.allowed
                and context.system == 'pf2e' and context.is_member and character is not None
                and character.campaign_id == context.campaign_id
                and principal.kind is PrincipalKind.USER
                and principal.user_id in character.viewer_user_ids):
            character_decision = _allow('character_viewer')
        # Ownership/role denial comes first so the live slot cannot be used as
        # a campaign-existence oracle by an unauthorized caller.
        if not character_decision.allowed:
            return character_decision
        live_denial = _require_live(context)
        return live_denial if live_denial is not None else character_decision

    if policy is RoutePolicy.LIVE_CAMPAIGN_MEMBER:
        if not _is_campaign_member(context, legacy_open=legacy_open):
            return CAMPAIGN_MEMBER_REQUIRED
        live_denial = _require_live(context)
        return live_denial if live_denial is not None else _allow("live_campaign_member")

    # The remaining policy uses the human/admin GM branch against live globals.
    if policy is RoutePolicy.LIVE_CAMPAIGN_GM:
        if not _is_campaign_gm(context, legacy_open=legacy_open):
            return CAMPAIGN_GM_REQUIRED
        live_denial = _require_live(context)
        return live_denial if live_denial is not None else _allow("live_campaign_gm")

    raise AssertionError(f"unhandled route policy: {policy}")


class DenialResponseKind(str, Enum):
    JSON = "json"
    REDIRECT = "redirect"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class DenialContract:
    """Framework-neutral instructions for rendering an access denial."""

    kind: DenialResponseKind
    status_code: int
    error_code: str
    message: str
    location: str | None = None

    @property
    def payload(self) -> dict[str, str]:
        return {"error": self.error_code, "message": self.message}


def _safe_request_target(target: Any) -> str:
    if not isinstance(target, str):
        return "/"
    target = target.strip()
    if (
        not target.startswith("/")
        or target.startswith("//")
        or target.startswith("/\\")
        or any(character in target for character in "\r\n\t")
    ):
        return "/"
    return target[:1024]


def denial_contract(
    decision: AccessDecision,
    *,
    is_api: bool,
    request_target: str = "/",
    login_url: str = "/login",
) -> DenialContract:
    """Translate a denial into the stable API or browser response contract."""

    if not isinstance(decision, AccessDecision):
        raise ValueError("decision must be an AccessDecision")
    if decision.allowed:
        raise ValueError("an allowed decision has no denial contract")
    if not isinstance(is_api, bool):
        raise ValueError("is_api must be a bool")

    if is_api:
        return DenialContract(
            DenialResponseKind.JSON,
            decision.status_code,
            decision.code,
            decision.message,
        )

    if decision.code == "login_required":
        safe_login = _safe_request_target(login_url)
        safe_target = _safe_request_target(request_target)
        separator = "&" if "?" in safe_login else "?"
        location = f"{safe_login}{separator}{urlencode({'next': safe_target})}"
        return DenialContract(
            DenialResponseKind.REDIRECT,
            302,
            decision.code,
            decision.message,
            location,
        )

    return DenialContract(
        DenialResponseKind.STATUS,
        decision.status_code,
        decision.code,
        decision.message,
    )


def flask_denial_response(
    decision: AccessDecision,
    *,
    is_api: bool,
    request_target: str = "/",
    login_url: str = "/login",
):
    """Render a denial as a Flask response without reading ``flask.request``."""

    # Local import keeps the core decision layer usable in non-Flask jobs and
    # makes the framework dependency explicit at the final adapter boundary.
    from flask import jsonify, make_response, redirect

    contract = denial_contract(
        decision,
        is_api=is_api,
        request_target=request_target,
        login_url=login_url,
    )
    if contract.kind is DenialResponseKind.JSON:
        response = jsonify(contract.payload)
        response.status_code = contract.status_code
        return response
    if contract.kind is DenialResponseKind.REDIRECT:
        return redirect(contract.location, code=contract.status_code)
    return make_response(contract.message, contract.status_code)
