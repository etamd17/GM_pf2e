"""E-5: POST /vtt/api/area-effect. The host's adapter is a recorder here
(app.py's is tested in tests/vtt/test_e5_area.py)."""

from __future__ import annotations

from .conftest import CSRF


def post(stack, body, csrf=CSRF):
    return stack.app.test_client().post("/vtt/api/area-effect", json=body, headers={"X-CSRF-Token": csrf},
                                        base_url="https://localhost")


GOOD = {"campaignId": "camp_a", "requestId": "req-area-0001", "encounterToken": "abc-g2", "action": "damage",
        "actorIds": ["cmb_a1", "cmb_b2"], "amount": 6, "damageType": "fire"}


def test_the_route_needs_the_campaign_gm_and_csrf(stack):
    calls = []
    stack.host.area_apply = lambda *args: calls.append(args) or ("ok", {"results": []})
    assert post(stack, {**GOOD, "campaignId": "camp_b"}).status_code == 403
    assert post(stack, GOOD, csrf="wrong").status_code == 403
    stack.host.member_id = None
    assert post(stack, GOOD).status_code == 403
    assert calls == []


def test_a_malformed_body_is_refused_before_the_tracker(stack):
    calls = []
    stack.host.area_apply = lambda *args: calls.append(args) or ("ok", {"results": []})
    for bad in ({**GOOD, "action": "condition"}, {**GOOD, "actorIds": []}, {**GOOD, "actorIds": "cmb_a1"},
                {**GOOD, "actorIds": ["chr_valeros"]}, {**GOOD, "actorIds": [f"cmb_{i}" for i in range(51)]},
                {**GOOD, "requestId": "x"}, {**GOOD, "encounterToken": "a b"}, {**GOOD, "amount": [6]},
                {**GOOD, "damageType": "x" * 41}, {k: v for k, v in GOOD.items() if k != "encounterToken"}):
        assert post(stack, bad).status_code == 400, bad
    assert calls == []


def test_it_passes_the_request_through_and_maps_the_outcome(stack):
    calls = []
    outcomes = iter([("ok", {"results": [{"actorId": "cmb_a1", "outcome": "applied", "net": 6, "defeated": False}]}),
                     ("stale", {"missing": ["cmb_b2"]}), ("not-live", {}), ("unsupported", {}), ("invalid", {}),
                     ("blocked", {})])
    stack.host.area_apply = lambda *args: calls.append(args) or next(outcomes)
    response = post(stack, {**GOOD, "ignored": "x"})
    assert response.status_code == 200 and response.get_json()["results"][0]["net"] == 6
    assert calls == [("camp_a", "req-area-0001", "abc-g2", "damage", ["cmb_a1", "cmb_b2"],
                      {"amount": 6, "damageType": "fire"})]
    assert [post(stack, GOOD).status_code for _ in range(5)] == [409, 409, 422, 422, 422]


def test_a_host_without_area_support_says_so(stack):
    assert post(stack, GOOD).status_code == 422
