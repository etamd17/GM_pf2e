"""E-2: POST /vtt/api/condition, the GM page's door to the tracker's condition
engines. The host's adapter is a recorder here (app.py's is tested in
tests/vtt/test_e2_conditions.py)."""

from __future__ import annotations

from .conftest import CSRF


def post(stack, body, csrf=CSRF):
    return stack.app.test_client().post("/vtt/api/condition", json=body, headers={"X-CSRF-Token": csrf},
                                        base_url="https://localhost")


GOOD = {"campaignId": "camp_a", "actorId": "cmb_a1", "condition": "frightened", "operation": "add",
        "expectedValue": 2}


def test_the_route_needs_the_campaign_gm_and_csrf(stack):
    calls = []
    stack.host.condition_change = lambda *args: calls.append(args) or ("ok", {})
    assert post(stack, {**GOOD, "campaignId": "camp_b"}).status_code == 403
    assert post(stack, GOOD, csrf="wrong").status_code == 403
    stack.host.member_id = None
    assert post(stack, GOOD).status_code == 403
    assert calls == []


def test_a_malformed_body_is_refused_before_the_tracker(stack):
    calls = []
    stack.host.condition_change = lambda *args: calls.append(args) or ("ok", {})
    for bad in ({**GOOD, "actorId": "chr_valeros"}, {**GOOD, "actorId": "../x"},
                {**GOOD, "condition": "Frightened"}, {**GOOD, "condition": "a b"}, {**GOOD, "condition": 3},
                {**GOOD, "operation": "toggle"}, {**GOOD, "expectedValue": True}, {**GOOD, "expectedValue": 100},
                {**GOOD, "expectedValue": -1}, {**GOOD, "rounds": 101}, {**GOOD, "rounds": "2"},
                {k: v for k, v in GOOD.items() if k != "expectedValue"}):
        assert post(stack, bad).status_code == 400, bad
    assert calls == []


def test_it_passes_exactly_what_the_page_showed_and_maps_the_outcome(stack):
    calls = []
    outcomes = iter([("ok", {"value": 3}), ("stale", {"value": 3}), ("not-live", {}), ("not-in-encounter", {}),
                     ("unsupported", {}), ("invalid", {"reason": "condition"}), ("blocked", {"reason": "x"})])
    stack.host.condition_change = lambda *args: calls.append(args) or next(outcomes)
    response = post(stack, {**GOOD, "rounds": 3})
    assert response.status_code == 200 and response.get_json() == {"outcome": "ok", "value": 3}
    assert calls == [("camp_a", "cmb_a1", "frightened", "add", 2, 3)]
    assert [post(stack, GOOD).status_code for _ in range(6)] == [409, 409, 409, 422, 422, 422]
    assert calls[-1][-1] == 0                               # rounds defaults to none


def test_a_host_without_condition_support_says_so(stack):
    response = post(stack, GOOD)
    assert response.status_code == 422 and response.get_json()["outcome"] == "unsupported"
