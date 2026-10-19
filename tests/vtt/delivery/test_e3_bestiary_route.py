"""E-3: GET /vtt/api/bestiary and POST /vtt/api/bestiary/add. The host's
adapters are recorders here (app.py's are tested in tests/vtt/test_e3_bestiary.py)."""

from __future__ import annotations

from .conftest import CSRF


def client(stack):
    return stack.app.test_client()


def search(stack, **params):
    return client(stack).get("/vtt/api/bestiary", query_string=params, base_url="https://localhost")


def add(stack, body, csrf=CSRF):
    return client(stack).post("/vtt/api/bestiary/add", json=body, headers={"X-CSRF-Token": csrf},
                              base_url="https://localhost")


GOOD = {"campaignId": "camp_a", "ref": "pathfinder-bestiary\\goblin-warrior.json", "requestId": "req-1a2b3c4d",
        "encounterToken": "a1b2c3-g4"}


def test_both_need_the_campaign_gm_and_add_needs_csrf(stack):
    calls = []
    stack.host.bestiary_search = lambda *args: calls.append(args) or ("ok", {"results": []})
    stack.host.bestiary_add = lambda *args: calls.append(args) or ("ok", {})
    assert search(stack, campaignId="camp_b", q="gob").status_code == 403
    assert add(stack, {**GOOD, "campaignId": "camp_b"}).status_code == 403
    assert add(stack, GOOD, csrf="wrong").status_code == 403
    stack.host.member_id = None
    assert search(stack, campaignId="camp_a", q="gob").status_code == 403
    assert calls == []


def test_a_malformed_request_is_refused_before_the_tracker(stack):
    calls = []
    stack.host.bestiary_search = lambda *args: calls.append(args) or ("ok", {"results": []})
    stack.host.bestiary_add = lambda *args: calls.append(args) or ("ok", {})
    assert search(stack, campaignId="camp_a", q="x" * 81).status_code == 400
    for bad in ({**GOOD, "ref": ""}, {**GOOD, "ref": "a\nb"}, {**GOOD, "ref": "x" * 241}, {**GOOD, "ref": 3},
                {**GOOD, "requestId": "short"}, {**GOOD, "requestId": "has space here"},
                {k: v for k, v in GOOD.items() if k != "requestId"},
                {k: v for k, v in GOOD.items() if k != "encounterToken"}, {**GOOD, "encounterToken": "a b"}):
        assert add(stack, bad).status_code == 400, bad
    assert calls == []


def test_it_passes_the_request_through_and_maps_the_outcome(stack):
    calls = []
    stack.host.bestiary_search = lambda *args: calls.append(args) or (
        "ok", {"results": [{"ref": "r", "name": "Goblin", "level": -1, "size": 1}]})
    response = search(stack, campaignId="camp_a", q="gob")
    assert response.status_code == 200 and response.get_json()["results"][0]["name"] == "Goblin"
    outcomes = iter([("ok", {"actorId": "cmb_x", "name": "Goblin", "size": 1}), ("stale", {}), ("not-live", {}),
                     ("not-found", {}), ("unsupported", {}), ("blocked", {"reason": "x"})])
    stack.host.bestiary_add = lambda *args: calls.append(args) or next(outcomes)
    response = add(stack, GOOD)
    assert response.status_code == 200 and response.get_json() == {
        "outcome": "ok", "actorId": "cmb_x", "name": "Goblin", "size": 1}
    assert calls == [("camp_a", "gob"), ("camp_a", GOOD["ref"], GOOD["requestId"], "a1b2c3-g4")]
    assert [add(stack, GOOD).status_code for _ in range(5)] == [409, 409, 404, 422, 422]


def test_a_host_without_bestiary_support_says_so(stack):
    assert search(stack, campaignId="camp_a", q="gob").get_json()["outcome"] == "unsupported"
    assert add(stack, GOOD).status_code == 422
