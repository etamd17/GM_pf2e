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
    METHOD_POLICY_OVERRIDES,
    POLICY_NOTES,
    ROUTE_POLICIES,
    RoutePolicy,
    RoutePolicyCoverageError,
    audit_url_map,
    build_policy_registry,
    inventory_rows,
    policy_for,
    require_complete_inventory,
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


def test_reviewed_method_boundaries_are_explicit():
    assert policy_for("api_campaign", "GET") is RoutePolicy.PUBLIC
    assert policy_for("api_campaign", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert METHOD_POLICY_OVERRIDES == {
        ("api_campaign", "POST"): RoutePolicy.LIVE_CAMPAIGN_GM,
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
    assert policy_for("player_sheet", "GET") is RoutePolicy.CHARACTER_OWNER_OR_GM
    assert policy_for("gm_secret_roll", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("obsidian_sync.state", "GET") is RoutePolicy.INTEGRATION_TOKEN
    assert policy_for("obsidian_sync.issue_token", "POST") is RoutePolicy.LIVE_CAMPAIGN_GM
    assert policy_for("admin_users", "GET") is RoutePolicy.SITE_ADMIN


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


def test_foundation_is_not_wired_into_runtime_authorization():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "core.route_policy" not in source
    assert "route_policy" not in source
