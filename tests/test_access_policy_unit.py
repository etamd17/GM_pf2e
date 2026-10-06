"""Focused unit tests for pure request-context and authorization primitives."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from flask import Flask

from core.access import (
    DenialResponseKind,
    decide_access,
    denial_contract,
    flask_denial_response,
)
from core.request_context import (
    CampaignRole,
    Principal,
    PrincipalKind,
    campaign_role_for,
    principal_from_user,
    resolve_campaign_context,
    resolve_character_context,
)
from core.route_policy import RoutePolicy


CAMPAIGN_ID = "campaign-a"
LIVE_ID = CAMPAIGN_ID
CAMPAIGN = {
    "id": CAMPAIGN_ID,
    "system": "pf2e",
    "members": [
        {"user_id": "gm-1", "role": "gm"},
        {"user_id": "player-1", "role": "player"},
        {"user_id": "editor-1", "role": "player"},
    ],
}


def _context(
    principal: Principal,
    *,
    campaign=CAMPAIGN,
    campaign_id: str | None = CAMPAIGN_ID,
    live_campaign_id: str | None = LIVE_ID,
):
    return resolve_campaign_context(
        principal,
        campaign_id=campaign_id,
        campaign=campaign,
        live_campaign_id=live_campaign_id,
    )


def _character(*, campaign_id: str = CAMPAIGN_ID):
    return resolve_character_context(
        campaign_id=campaign_id,
        character_id="character-1",
        owner_user_id="player-1",
        editor_user_ids=("editor-1",),
        legacy_ref="Amiri",
    )


def test_principals_are_explicit_validated_and_immutable():
    anonymous = Principal.anonymous()
    user = principal_from_user({"id": " user-1 ", "is_admin": True})
    token = Principal.publish_token("chronicle-v1", campaign_id=CAMPAIGN_ID)

    assert anonymous.kind is PrincipalKind.ANONYMOUS
    assert user == Principal.user("user-1", is_admin=True)
    assert token.campaign_id == CAMPAIGN_ID
    with pytest.raises(FrozenInstanceError):
        user.user_id = "other"
    with pytest.raises(ValueError, match="requires campaign_id"):
        Principal(kind=PrincipalKind.LEGACY_GM)
    with pytest.raises(ValueError, match="token principal requires"):
        Principal(kind=PrincipalKind.PUBLISH_TOKEN, credential_id="verified")


def test_campaign_context_derives_only_unambiguous_membership_and_keeps_ids():
    context = _context(Principal.user("player-1"), live_campaign_id="campaign-b")
    assert context.membership_role is CampaignRole.PLAYER
    assert context.campaign_id == CAMPAIGN_ID
    assert context.live_campaign_id == "campaign-b"
    assert not context.is_live

    contradictory = {
        "id": CAMPAIGN_ID,
        "members": [
            {"user_id": "same", "role": "player"},
            {"user_id": "same", "role": "gm"},
        ],
    }
    assert campaign_role_for(contradictory, "same") is None
    with pytest.raises(ValueError, match="does not match"):
        resolve_campaign_context(
            Principal.user("player-1"),
            campaign_id="campaign-b",
            campaign=CAMPAIGN,
            live_campaign_id=LIVE_ID,
        )


@pytest.mark.parametrize(
    ("policy", "principal", "allowed", "status", "code"),
    [
        (RoutePolicy.PUBLIC, Principal.anonymous(), True, 200, "public"),
        (RoutePolicy.AUTHENTICATED, Principal.anonymous(), False, 401, "login_required"),
        (RoutePolicy.AUTHENTICATED, Principal.user("player-1"), True, 200, "authenticated"),
        (RoutePolicy.SITE_ADMIN, Principal.user("player-1"), False, 403, "site_admin_required"),
        (RoutePolicy.SITE_ADMIN, Principal.user("admin", is_admin=True), True, 200, "site_admin"),
        (RoutePolicy.CAMPAIGN_MEMBER, Principal.user("player-1"), True, 200, "campaign_member"),
        (RoutePolicy.CAMPAIGN_MEMBER, Principal.user("outsider"), False, 403, "campaign_membership_required"),
        (RoutePolicy.CAMPAIGN_GM, Principal.user("gm-1"), True, 200, "campaign_gm"),
        (RoutePolicy.CAMPAIGN_GM, Principal.user("player-1"), False, 403, "campaign_gm_required"),
        (RoutePolicy.LIVE_CAMPAIGN_MEMBER, Principal.user("player-1"), True, 200, "live_campaign_member"),
        (RoutePolicy.LIVE_CAMPAIGN_GM, Principal.user("gm-1"), True, 200, "live_campaign_gm"),
    ],
)
def test_core_policy_matrix(policy, principal, allowed, status, code):
    decision = decide_access(policy, _context(principal))
    assert decision.allowed is allowed
    assert decision.status_code == status
    assert decision.code == code


def test_admin_bypasses_campaign_roles_but_not_token_policies():
    admin_context = _context(Principal.user("admin", is_admin=True))
    assert decide_access(RoutePolicy.CAMPAIGN_MEMBER, admin_context).allowed
    assert decide_access(RoutePolicy.CAMPAIGN_GM, admin_context).allowed
    assert decide_access(RoutePolicy.LIVE_CAMPAIGN_GM, admin_context).allowed

    denied = decide_access(RoutePolicy.INTEGRATION_TOKEN, admin_context)
    assert not denied.allowed
    assert denied.code == "integration_token_required"


def test_live_policy_checks_role_before_returning_mismatch():
    stale_member = _context(
        Principal.user("player-1"), live_campaign_id="campaign-b"
    )
    mismatch = decide_access(RoutePolicy.LIVE_CAMPAIGN_MEMBER, stale_member)
    assert (mismatch.status_code, mismatch.code) == (409, "campaign_not_live")

    outsider = _context(Principal.user("outsider"), live_campaign_id="campaign-b")
    hidden = decide_access(RoutePolicy.LIVE_CAMPAIGN_MEMBER, outsider)
    assert (hidden.status_code, hidden.code) == (403, "campaign_membership_required")

    anonymous = _context(Principal.anonymous(), live_campaign_id="campaign-b")
    unauthenticated = decide_access(RoutePolicy.LIVE_CAMPAIGN_MEMBER, anonymous)
    assert (unauthenticated.status_code, unauthenticated.code) == (401, "login_required")


@pytest.mark.parametrize(
    ("principal", "reason"),
    [
        (Principal.user("player-1"), "character_owner"),
        (Principal.user("editor-1"), "character_editor"),
        (Principal.user("gm-1"), "campaign_gm"),
        (Principal.user("admin", is_admin=True), "campaign_gm"),
    ],
)
def test_character_owner_editor_gm_and_admin_are_allowed(principal, reason):
    decision = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(principal),
        character=_character(),
    )
    assert decision.allowed
    assert decision.code == reason


@pytest.mark.parametrize(
    ("principal", "allowed", "code"),
    [
        (Principal.user("player-1"), True, "character_owner"),
        (Principal.user("gm-1"), True, "campaign_gm"),
        (Principal.user("admin", is_admin=True), True, "campaign_gm"),
        (Principal.user("editor-1"), False, "character_owner_private_access_required"),
        (Principal.user("viewer-1"), False, "character_owner_private_access_required"),
        (Principal.user("member-1"), False, "character_owner_private_access_required"),
        (Principal.user("outsider"), False, "campaign_membership_required"),
    ],
)
def test_live_character_owner_private_excludes_nonowners(principal, allowed, code):
    campaign = {
        **CAMPAIGN,
        "members": [
            *CAMPAIGN["members"],
            {"user_id": "viewer-1", "role": "player"},
            {"user_id": "member-1", "role": "player"},
        ],
    }
    character = resolve_character_context(
        campaign_id=CAMPAIGN_ID,
        character_id="character-1",
        owner_user_id="player-1",
        editor_user_ids=("editor-1",),
        viewer_user_ids=("viewer-1",),
        legacy_ref="Amiri",
    )

    decision = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        _context(principal, campaign=campaign),
        character=character,
    )

    assert decision.allowed is allowed
    assert decision.code == code


def test_character_access_requires_membership_and_matching_campaign():
    # Ownership/editor metadata alone cannot survive removal from a campaign.
    removed_owner_campaign = {
        **CAMPAIGN,
        "members": [member for member in CAMPAIGN["members"] if member["user_id"] != "player-1"],
    }
    removed = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(Principal.user("player-1"), campaign=removed_owner_campaign),
        character=_character(),
    )
    assert (removed.status_code, removed.code) == (403, "campaign_membership_required")

    wrong_campaign = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(Principal.user("player-1")),
        character=_character(campaign_id="campaign-b"),
    )
    assert (wrong_campaign.status_code, wrong_campaign.code) == (
        403,
        "character_campaign_mismatch",
    )

    gm_wrong_campaign = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(Principal.user("gm-1")),
        character=_character(campaign_id="campaign-b"),
    )
    assert (gm_wrong_campaign.status_code, gm_wrong_campaign.code) == (
        403,
        "character_campaign_mismatch",
    )
    gm_missing_character = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(Principal.user("gm-1")),
    )
    assert gm_missing_character.allowed
    assert gm_missing_character.code == "campaign_gm"


def test_live_character_policy_authorizes_resource_before_live_guard():
    stale_owner = _context(
        Principal.user("player-1"), live_campaign_id="campaign-b"
    )
    mismatch = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        stale_owner,
        character=_character(),
    )
    assert (mismatch.status_code, mismatch.code) == (409, "campaign_not_live")

    stale_gm = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        _context(Principal.user("gm-1"), live_campaign_id="campaign-b"),
    )
    assert (stale_gm.status_code, stale_gm.code) == (409, "campaign_not_live")

    # An outsider and a cross-campaign resource are rejected without revealing
    # whether the requested campaign occupies the live slot.
    outsider = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        _context(Principal.user("outsider"), live_campaign_id="campaign-b"),
        character=_character(),
    )
    assert (outsider.status_code, outsider.code) == (
        403,
        "campaign_membership_required",
    )
    wrong_resource = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        stale_owner,
        character=_character(campaign_id="campaign-b"),
    )
    assert (wrong_resource.status_code, wrong_resource.code) == (
        403,
        "character_campaign_mismatch",
    )

    stale_private = decide_access(
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        stale_owner,
        character=_character(),
    )
    assert (stale_private.status_code, stale_private.code) == (
        409,
        "campaign_not_live",
    )


def test_legacy_player_is_limited_to_explicit_campaign_and_character():
    principal = Principal.legacy_player("Amiri", campaign_id=CAMPAIGN_ID)
    allowed = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(principal),
        character=_character(),
    )
    assert allowed.allowed and allowed.code == "legacy_character"

    other = resolve_character_context(
        campaign_id=CAMPAIGN_ID,
        character_id="character-2",
        legacy_ref="Kyra",
    )
    denied = decide_access(
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        _context(principal),
        character=other,
    )
    assert denied.code == "character_access_required"


def test_verified_token_principals_are_campaign_scoped():
    integration = Principal.integration_token("obsidian-v1", campaign_id=CAMPAIGN_ID)
    assert decide_access(RoutePolicy.INTEGRATION_TOKEN, _context(integration)).allowed

    wrong_context = _context(
        integration,
        campaign={"id": "campaign-b", "members": []},
        campaign_id="campaign-b",
        live_campaign_id="campaign-b",
    )
    denied = decide_access(RoutePolicy.INTEGRATION_TOKEN, wrong_context)
    assert (denied.status_code, denied.code) == (403, "token_campaign_mismatch")

    publish = Principal.publish_token("chronicle-v1", campaign_id=CAMPAIGN_ID)
    non_live = _context(publish, live_campaign_id="campaign-b")
    token_decision = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        non_live,
    )
    assert token_decision.allowed and token_decision.code == "verified_token"

    gm_non_live = _context(Principal.user("gm-1"), live_campaign_id="campaign-b")
    gm_decision = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        gm_non_live,
    )
    assert gm_decision.allowed and gm_decision.code == "campaign_gm"

    admin_without_selection = _context(
        Principal.user("admin", is_admin=True),
        campaign=None,
        campaign_id=None,
        live_campaign_id="campaign-b",
    )
    admin_decision = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        admin_without_selection,
    )
    assert admin_decision.allowed and admin_decision.code == "site_admin"

    # The destructive unpublish policy deliberately has no automation-token
    # branch. It still supports a campaign GM and a site admin with no active
    # campaign because the view re-authorizes its explicit target.
    token_unpublish = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
        non_live,
    )
    assert not token_unpublish.allowed
    assert token_unpublish.code == "human_session_required"

    gm_unpublish = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
        gm_non_live,
    )
    assert gm_unpublish.allowed and gm_unpublish.code == "campaign_gm"

    admin_unpublish = decide_access(
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
        admin_without_selection,
    )
    assert admin_unpublish.allowed and admin_unpublish.code == "site_admin"


def test_legacy_open_is_explicit_narrow_and_cannot_bypass_live_guard():
    anonymous = _context(Principal.anonymous())
    closed = decide_access(RoutePolicy.CAMPAIGN_GM, anonymous)
    opened = decide_access(RoutePolicy.CAMPAIGN_GM, anonymous, legacy_open=True)
    assert not closed.allowed
    assert opened.allowed and opened.code == "campaign_gm"

    # Open-dev GM compatibility is not account authentication or site admin.
    assert not decide_access(
        RoutePolicy.AUTHENTICATED, anonymous, legacy_open=True
    ).allowed
    assert not decide_access(RoutePolicy.SITE_ADMIN, anonymous, legacy_open=True).allowed
    assert not decide_access(
        RoutePolicy.INTEGRATION_TOKEN, anonymous, legacy_open=True
    ).allowed

    stale = _context(Principal.anonymous(), live_campaign_id="campaign-b")
    mismatch = decide_access(
        RoutePolicy.LIVE_CAMPAIGN_GM,
        stale,
        legacy_open=True,
    )
    assert (mismatch.status_code, mismatch.code) == (409, "campaign_not_live")


def test_legacy_player_identity_is_not_an_account_login():
    legacy_player = Principal.legacy_player("Amiri", campaign_id=CAMPAIGN_ID)
    decision = decide_access(RoutePolicy.AUTHENTICATED, _context(legacy_player))
    assert (decision.status_code, decision.code) == (403, "human_session_required")


def test_all_route_policies_have_a_decision_and_unknown_values_raise_clearly():
    anonymous = _context(Principal.anonymous())
    assert {policy for policy in RoutePolicy} == {
        RoutePolicy.PUBLIC,
        RoutePolicy.AUTHENTICATED,
        RoutePolicy.SITE_ADMIN,
        RoutePolicy.CAMPAIGN_MEMBER,
        RoutePolicy.CAMPAIGN_GM,
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
        RoutePolicy.LIVE_CAMPAIGN_MEMBER,
        RoutePolicy.LIVE_CAMPAIGN_GM,
        RoutePolicy.INTEGRATION_TOKEN,
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
    }
    for policy in RoutePolicy:
        assert decide_access(policy, anonymous).code
    with pytest.raises(ValueError, match="unknown route policy"):
        decide_access("future_policy", anonymous)


def test_canonical_viewer_policy_never_grants_mutation_or_cosmere_access():
    viewer = _context(Principal.user('editor-1'))
    character = resolve_character_context(campaign_id=CAMPAIGN_ID, character_id='character-1',
        owner_user_id='player-1', viewer_user_ids=('editor-1',))
    assert decide_access(RoutePolicy.LIVE_CHARACTER_VIEW, viewer, character=character).allowed
    assert not decide_access(RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM, viewer, character=character).allowed
    cosmere = _context(Principal.user('editor-1'), campaign={**CAMPAIGN, 'system': 'cosmere'})
    assert not decide_access(RoutePolicy.LIVE_CHARACTER_VIEW, cosmere, character=character).allowed


def test_api_and_browser_denial_contracts_are_distinct_and_safe():
    decision = decide_access(
        RoutePolicy.AUTHENTICATED,
        _context(Principal.anonymous()),
    )
    api = denial_contract(decision, is_api=True, request_target="/api/private")
    assert api.kind is DenialResponseKind.JSON
    assert api.status_code == 401
    assert api.payload == {"error": "login_required", "message": "Login required."}

    browser = denial_contract(
        decision,
        is_api=False,
        request_target="/player/sheet/Amiri?tab=combat",
    )
    assert browser.kind is DenialResponseKind.REDIRECT
    assert browser.status_code == 302
    assert browser.location == "/login?next=%2Fplayer%2Fsheet%2FAmiri%3Ftab%3Dcombat"

    unsafe = denial_contract(
        decision,
        is_api=False,
        request_target="//attacker.example/",
        login_url="https://attacker.example/login",
    )
    assert unsafe.location == "/?next=%2F"


def test_forbidden_and_conflict_browser_contracts_preserve_status():
    member = _context(Principal.user("player-1"))
    forbidden = decide_access(RoutePolicy.CAMPAIGN_GM, member)
    forbidden_contract = denial_contract(forbidden, is_api=False)
    assert forbidden_contract.kind is DenialResponseKind.STATUS
    assert forbidden_contract.status_code == 403

    stale = _context(Principal.user("gm-1"), live_campaign_id="campaign-b")
    conflict = decide_access(RoutePolicy.LIVE_CAMPAIGN_GM, stale)
    conflict_contract = denial_contract(conflict, is_api=False)
    assert conflict_contract.kind is DenialResponseKind.STATUS
    assert conflict_contract.status_code == 409
    assert conflict_contract.error_code == "campaign_not_live"


def test_flask_adapter_returns_json_redirect_and_status_responses():
    app = Flask(__name__)
    with app.test_request_context("/"):
        login = decide_access(
            RoutePolicy.AUTHENTICATED,
            _context(Principal.anonymous()),
        )
        api_response = flask_denial_response(login, is_api=True)
        assert api_response.status_code == 401
        assert api_response.get_json()["error"] == "login_required"

        browser_response = flask_denial_response(
            login,
            is_api=False,
            request_target="/player",
        )
        assert browser_response.status_code == 302
        assert browser_response.headers["Location"].endswith("/login?next=%2Fplayer")

        conflict = decide_access(
            RoutePolicy.LIVE_CAMPAIGN_GM,
            _context(Principal.user("gm-1"), live_campaign_id="campaign-b"),
        )
        conflict_response = flask_denial_response(conflict, is_api=False)
        assert conflict_response.status_code == 409
        assert b"not the campaign loaded" in conflict_response.data
