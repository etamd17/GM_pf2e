"""Coverage tests for the declarative route-policy inventory.

PR1 intentionally does not enforce these policies.  Its safety property is
that every registered Flask endpoint is classified exactly once and future
route additions cannot silently bypass review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from werkzeug.routing import Map, Rule

import app as A
from core.route_policy import (
    CHARACTER_OWNER_POLICIES,
    CHARACTER_OWNER_RESOLUTIONS,
    LEGACY_GM_POLICIES,
    METHOD_POLICY_OVERRIDES,
    POLICY_NOTES,
    ROUTE_POLICIES,
    SESSION_LIVE_CAMPAIGN_POLICIES,
    CharacterOwnerResolution,
    CharacterOwnerSource,
    RoutePolicy,
    RoutePolicyCoverageError,
    audit_url_map,
    build_character_owner_resolution_registry,
    build_policy_registry,
    character_owner_resolution_for,
    inventory_rows,
    policy_for,
    require_complete_inventory,
    requires_character_owner,
    requires_legacy_gm,
    requires_live_campaign_match,
)
from tools.route_policy_inventory import render_json, render_text


def _map(*rules: Rule) -> Map:
    return Map(list(rules))


def test_current_flask_url_map_is_classified_exactly():
    audit = audit_url_map(A.app.url_map)
    assert audit.ok, audit.describe()
    assert len(ROUTE_POLICIES) == len({rule.endpoint for rule in A.app.url_map.iter_rules()})
    require_complete_inventory(A.app.url_map)


def test_new_endpoint_is_unclassified_and_fails_closed():
    url_map = _map(Rule("/new", endpoint="new_endpoint", methods=["GET"]))
    audit = audit_url_map(url_map, policies={}, method_overrides={})
    assert audit.unclassified_endpoints == ("new_endpoint",)
    with pytest.raises(RoutePolicyCoverageError, match="unclassified endpoints: new_endpoint"):
        require_complete_inventory(url_map, policies={}, method_overrides={})


def test_removed_endpoint_leaves_a_stale_policy_and_fails():
    url_map = _map(Rule("/kept", endpoint="kept", methods=["GET"]))
    policies = {
        "kept": RoutePolicy.PUBLIC,
        "removed": RoutePolicy.AUTHENTICATED,
    }
    audit = audit_url_map(url_map, policies=policies, method_overrides={})
    assert audit.stale_endpoints == ("removed",)
    with pytest.raises(RoutePolicyCoverageError, match="stale policy endpoints: removed"):
        require_complete_inventory(url_map, policies=policies, method_overrides={})


def test_removed_method_leaves_a_stale_override():
    url_map = _map(Rule("/thing", endpoint="thing", methods=["GET"]))
    overrides = {("thing", "POST"): RoutePolicy.SITE_ADMIN}
    audit = audit_url_map(
        url_map,
        policies={"thing": RoutePolicy.PUBLIC},
        method_overrides=overrides,
    )
    assert audit.stale_method_overrides == (("thing", "POST"),)


def test_duplicate_endpoint_declarations_are_rejected():
    groups = (
        (RoutePolicy.PUBLIC, ("same",)),
        (RoutePolicy.AUTHENTICATED, ("same",)),
    )
    with pytest.raises(ValueError, match="duplicate policy declaration for 'same'"):
        build_policy_registry(groups)


def test_duplicate_character_resolution_declarations_are_rejected():
    groups = (
        (CharacterOwnerSource.ROUTE_PC_NAME, "pc_name", ("same",)),
        (CharacterOwnerSource.JSON_PC_NAME, "name", ("same",)),
    )
    with pytest.raises(ValueError, match="duplicate character owner resolution for 'same'"):
        build_character_owner_resolution_registry(groups)


def test_reviewed_method_boundaries_are_explicit():
    assert policy_for("api_campaign", "GET") is RoutePolicy.LIVE_CAMPAIGN_MEMBER
    assert policy_for("api_campaign", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_safety", "GET") is RoutePolicy.LIVE_CAMPAIGN_MEMBER
    assert policy_for("api_safety", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert METHOD_POLICY_OVERRIDES == {
        ("api_campaign", "POST"): RoutePolicy.LIVE_CAMPAIGN_GM,
        ("api_safety", "POST"): RoutePolicy.LIVE_CAMPAIGN_GM,
    }
    assert policy_for("api_scenes", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scenes", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene", "PATCH") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_background", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_background", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_token", "PATCH") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_token", "DELETE") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_token_image", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_token_image", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("api_scene_token_image", "DELETE") is RoutePolicy.LIVE_CAMPAIGN_GM


def test_critical_resource_policies_are_not_public():
    assert policy_for("backup_now", "POST") is RoutePolicy.AUTHENTICATED
    assert policy_for("player_sheet", "GET") is RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM
    assert policy_for("api_join_campaign", "POST") is RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM
    assert policy_for("api_notes", "GET") is RoutePolicy.CAMPAIGN_MEMBER
    assert policy_for("session_notes_page", "GET") is RoutePolicy.CAMPAIGN_MEMBER
    assert policy_for("chronicle_home", "GET") is RoutePolicy.CAMPAIGN_MEMBER
    assert policy_for("chronicle_manage", "GET") is RoutePolicy.CAMPAIGN_GM
    assert policy_for("chronicle_publish", "POST") is RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN
    assert policy_for("chronicle_unpublish", "POST") is RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN
    assert policy_for("chronicle_status", "GET") is RoutePolicy.CAMPAIGN_GM
    assert policy_for("chronicle_rollback", "POST") is RoutePolicy.CAMPAIGN_GM
    assert policy_for("chronicle_docs_api", "GET") is RoutePolicy.CAMPAIGN_GM
    assert policy_for("session_journal_get", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("healing_log_get", "GET") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("gm_secret_roll", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("obsidian_sync.state", "GET") is RoutePolicy.INTEGRATION_TOKEN
    assert policy_for("obsidian_sync.issue_token", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("admin_users", "GET") is RoutePolicy.SITE_ADMIN


def test_owner_private_character_endpoints_use_the_strict_policy():
    expected = {
        "export_character",
        "save_notes",
        "save_session_note",
        "delete_session_note",
        "cosmere_pc_notes",
    }
    assert {
        endpoint
        for endpoint, policy in ROUTE_POLICIES.items()
        if policy is RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE
    } == expected
    for endpoint in expected:
        assert character_owner_resolution_for(endpoint) is not None

    assert policy_for("api_pc_state", "GET") is RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM
    assert policy_for("cosmere_pc_state", "POST") is RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM
    assert policy_for("export_pdf", "GET") is RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM
    assert policy_for("api_notes", "GET") is RoutePolicy.CAMPAIGN_MEMBER
    assert policy_for("session_notes_page", "GET") is RoutePolicy.CAMPAIGN_MEMBER


def test_policy_families_distinguish_live_character_scope():
    assert CHARACTER_OWNER_POLICIES == {
        RoutePolicy.CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
    }
    assert SESSION_LIVE_CAMPAIGN_POLICIES == {
        RoutePolicy.LIVE_CAMPAIGN_MEMBER,
        RoutePolicy.LIVE_CAMPAIGN_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
    }
    assert requires_character_owner(RoutePolicy.CHARACTER_OWNER_OR_GM)
    assert requires_character_owner(RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM)
    assert requires_character_owner(RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE)
    assert not requires_character_owner(RoutePolicy.LIVE_CAMPAIGN_MEMBER)
    assert requires_live_campaign_match(RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM)
    assert requires_live_campaign_match(RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE)
    assert not requires_live_campaign_match(RoutePolicy.CHARACTER_OWNER_OR_GM)
    # A publish-token request has no browser session to compare; runtime must
    # validate that alternative credential on its own branch.
    assert not requires_live_campaign_match(
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN
    )
    assert not requires_live_campaign_match(RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN)


def test_legacy_gm_policy_family_is_explicit_and_exhaustive():
    expected = {
        RoutePolicy.CAMPAIGN_GM,
        RoutePolicy.LIVE_CAMPAIGN_GM,
        RoutePolicy.CAMPAIGN_GM_OR_PUBLISH_TOKEN,
        RoutePolicy.CAMPAIGN_GM_OR_SITE_ADMIN,
    }

    assert LEGACY_GM_POLICIES == expected
    assert {policy for policy in RoutePolicy if requires_legacy_gm(policy)} == expected
    assert not requires_legacy_gm(None)


def test_character_owner_resolution_metadata_is_exhaustive():
    character_endpoints = {
        endpoint
        for endpoint, policy in ROUTE_POLICIES.items()
        if requires_character_owner(policy)
    }
    character_endpoints.update(
        endpoint
        for (endpoint, _method), policy in METHOD_POLICY_OVERRIDES.items()
        if requires_character_owner(policy)
    )

    assert set(CHARACTER_OWNER_RESOLUTIONS) == character_endpoints
    assert {
        ROUTE_POLICIES[endpoint] for endpoint in character_endpoints
    } == {
        RoutePolicy.LIVE_CHARACTER_OWNER_OR_GM,
        RoutePolicy.LIVE_CHARACTER_OWNER_PRIVATE,
        RoutePolicy.LIVE_CHARACTER_VIEW,
    }


def test_route_character_locators_name_real_route_arguments():
    arguments_by_endpoint: dict[str, set[str]] = {}
    for rule in A.app.url_map.iter_rules():
        arguments_by_endpoint.setdefault(rule.endpoint, set()).update(rule.arguments)

    route_sources = {
        CharacterOwnerSource.ROUTE_PC_NAME,
        CharacterOwnerSource.ROUTE_COSMERE_PID,
    }
    for endpoint, resolution in CHARACTER_OWNER_RESOLUTIONS.items():
        if resolution.source in route_sources:
            assert resolution.field in arguments_by_endpoint[endpoint]


def test_non_route_character_locators_are_explicit_and_safe_to_dispatch():
    non_route_sources = {
        CharacterOwnerSource.QUERY_COSMERE_PID,
        CharacterOwnerSource.JSON_COSMERE_PID,
        CharacterOwnerSource.SESSION_PC_NAME,
        CharacterOwnerSource.JSON_PC_NAME,
    }
    actual = {
        endpoint: resolution
        for endpoint, resolution in CHARACTER_OWNER_RESOLUTIONS.items()
        if resolution.source in non_route_sources
    }
    assert actual == {
        "api_cosmere_my_combat": CharacterOwnerResolution(
            CharacterOwnerSource.QUERY_COSMERE_PID, "pid"
        ),
        "api_cosmere_my_initiative": CharacterOwnerResolution(
            CharacterOwnerSource.JSON_COSMERE_PID, "pid"
        ),
        "api_cosmere_my_speed": CharacterOwnerResolution(
            CharacterOwnerSource.JSON_COSMERE_PID, "pid"
        ),
        "api_cosmere_roll": CharacterOwnerResolution(
            CharacterOwnerSource.JSON_COSMERE_PID, "pid"
        ),
        "api_join_campaign": CharacterOwnerResolution(
            CharacterOwnerSource.JSON_PC_NAME, "name"
        ),
        "api_journal_append": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "api_journal_delete": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "api_journal_get": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "hero_nomination": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "log_roll": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "log_spell_cast": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "mobile_combat": CharacterOwnerResolution(
            CharacterOwnerSource.SESSION_PC_NAME, "player_name"
        ),
        "recall_knowledge": CharacterOwnerResolution(
            CharacterOwnerSource.JSON_PC_NAME, "pc_name"
        ),
    }
    assert character_owner_resolution_for("api_join_campaign") == actual[
        "api_join_campaign"
    ]
    assert character_owner_resolution_for("not_an_endpoint") is None


def test_inventory_rows_are_stable_and_include_route_aliases():
    first = inventory_rows(A.app.url_map)
    second = inventory_rows(A.app.url_map)
    assert first == second == tuple(sorted(first))
    assert all(row.policy != "UNCLASSIFIED" for row in first)
    assert [(row.method, row.rule) for row in first if row.endpoint == "mobile_combat"] == [
        ("GET", "/m"),
        ("GET", "/mobile"),
    ]


def test_text_and_json_reports_are_deterministic():
    assert render_text(A.app.url_map) == render_text(A.app.url_map)
    first = render_json(A.app.url_map)
    second = render_json(A.app.url_map)
    assert first == second
    parsed = json.loads(first)
    assert parsed["status"] == "ok"
    assert parsed["unclassified_endpoints"] == []
    assert parsed["stale_endpoints"] == []


def test_notes_and_method_overrides_only_reference_declared_endpoints():
    assert set(POLICY_NOTES) <= set(ROUTE_POLICIES)
    assert {endpoint for endpoint, _method in METHOD_POLICY_OVERRIDES} <= set(ROUTE_POLICIES)


def test_inventory_is_wired_before_legacy_runtime_authorization():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "from core.route_policy import" in source
    assert "def _enforce_route_policy" in source
    hooks = [fn.__name__ for fn in A.app.before_request_funcs.get(None, ())]
    assert hooks.index("_enforce_route_policy") < hooks.index("check_gm_access")
