"""E-1: POST /vtt/api/turn, the GM page's door to the tracker's turn change.
The host's adapter is a recorder here (app.py's is tested in tests/vtt/test_e1_turns.py)."""

from __future__ import annotations

from .conftest import CSRF


def post(stack, body, csrf=CSRF):
    return stack.app.test_client().post("/vtt/api/turn", json=body, headers={"X-CSRF-Token": csrf},
                                        base_url="https://localhost")


GOOD = {"campaignId": "camp_a", "direction": "next", "expectedActorId": "cmb_a1", "expectedRound": 3,
        "turnToken": "a1b2c3-7"}


def test_the_route_needs_the_campaign_gm_and_csrf(stack):
    calls = []
    stack.host.turn_step = lambda *args: calls.append(args) or ("ok", {})
    assert post(stack, {**GOOD, "campaignId": "camp_b"}).status_code == 403
    assert post(stack, GOOD, csrf="wrong").status_code == 403
    stack.host.member_id = None
    assert post(stack, GOOD).status_code == 403
    assert calls == []


def test_a_malformed_body_is_refused_before_the_tracker(stack):
    calls = []
    stack.host.turn_step = lambda *args: calls.append(args) or ("ok", {})
    for bad in ({**GOOD, "direction": "skip"}, {**GOOD, "expectedActorId": "../x"},
                {**GOOD, "expectedRound": "3"}, {**GOOD, "expectedRound": True}, {**GOOD, "expectedRound": -1},
                {k: v for k, v in GOOD.items() if k != "expectedActorId"},
                {k: v for k, v in GOOD.items() if k != "turnToken"}, {**GOOD, "turnToken": "a b"},
                {**GOOD, "turnToken": 7}, {**GOOD, "turnToken": "x" * 65}):
        assert post(stack, bad).status_code == 400, bad
    assert calls == []


def test_it_passes_exactly_what_the_page_showed_and_maps_the_outcome(stack):
    calls = []
    outcomes = iter([("ok", {"actorId": "cmb_b2", "round": 3}), ("stale", {"actorId": "cmb_b2", "round": 3}),
                     ("not-live", {}), ("no-encounter", {}), ("unsupported", {"system": "dnd5e"}),
                     ("blocked", {"reason": "PermissionError"})])
    stack.host.turn_step = lambda *args: calls.append(args) or next(outcomes)
    response = post(stack, GOOD)
    assert response.status_code == 200 and response.get_json() == {"outcome": "ok", "actorId": "cmb_b2", "round": 3}
    assert calls == [("camp_a", "next", "cmb_a1", 3, "a1b2c3-7")]
    assert [post(stack, GOOD).status_code for _ in range(5)] == [409, 409, 409, 422, 422]


def test_a_host_without_turn_support_says_so(stack):
    response = post(stack, GOOD)                      # the test host has no turn_step
    assert response.status_code == 422 and response.get_json()["outcome"] == "unsupported"
