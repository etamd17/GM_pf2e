"""P-3: the pilot gate. Only the campaigns in VTT_PILOT_CAMPAIGNS reach the VTT:
the GM page, every /vtt/api route, pairing, subscriptions and table displays.
Unset means none. The account-page link is tested in tests/vtt/test_image_contents.py."""

from __future__ import annotations

import pytest
from flask import Flask

from vtt.transport import app_wiring
from vtt.transport.pilot import EVERYONE, Pilot, from_env
from vtt.transport.socket import NAMESPACE

from .conftest import CSRF, add_grant, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table

ONLY_A = Pilot(frozenset({"camp_a"}))
only_a = pytest.mark.parametrize("stack", [ONLY_A], indirect=True)
nobody = pytest.mark.parametrize("stack", [Pilot()], indirect=True)
everyone = pytest.mark.parametrize("stack", [EVERYONE], indirect=True)


def get(stack, path):
    return stack.app.test_client().get(path, base_url="https://localhost")


def post(stack, path, body):
    return stack.app.test_client().post(path, json=body, headers={"X-CSRF-Token": CSRF},
                                        base_url="https://localhost")


def gm_of_both(stack):
    stack.host.gms.add(("user_gm", "camp_b"))


# --- the list --------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, a, b", [
    (None, False, False), ("", False, False), (" , ", False, False),
    ("camp_a", True, False), (" camp_a , camp_b ", True, True), ("*", True, True), ("camp_a,*", True, True),
])
def test_the_list_is_ids_or_star_and_unset_is_nobody(raw, a, b):
    pilot = from_env({} if raw is None else {"VTT_PILOT_CAMPAIGNS": raw})
    assert (pilot.allows("camp_a"), pilot.allows("camp_b")) == (a, b)
    assert not pilot.allows("") and not pilot.allows(None) and not pilot.allows(["camp_a"])


def test_the_startup_description_never_names_a_campaign():
    assert from_env({"VTT_PILOT_CAMPAIGNS": "camp_secret,camp_b"}).describe() == "2 campaign(s)"
    assert from_env({}).describe() == "no campaigns (unset)"
    assert EVERYONE.describe() == "every campaign (*)"


# --- the GM page and the API -------------------------------------------------------------

@only_a
def test_the_gm_page_opens_inside_the_pilot_and_explains_outside_it(stack):
    gm_of_both(stack)
    seed_scene(stack.db)
    assert get(stack, "/vtt/gm?campaignId=camp_a").status_code == 200
    outside = get(stack, "/vtt/gm?campaignId=camp_b")
    assert outside.status_code == 403 and "not in the tabletop pilot" in outside.get_data(as_text=True)


@only_a
def test_only_a_gm_of_that_campaign_is_told_about_the_pilot(stack):
    # camp_b is outside the pilot, but this member is not its GM: the plain refusal.
    response = get(stack, "/vtt/gm?campaignId=camp_b")
    assert response.status_code == 403 and "pilot" not in response.get_data(as_text=True)
    assert post(stack, "/vtt/api/turn", {"campaignId": "camp_b"}).get_json() == {"error": "unauthorized"}
    stack.host.member_id = None
    gm_of_both(stack)
    assert "pilot" not in get(stack, "/vtt/gm?campaignId=camp_b").get_data(as_text=True)


@nobody
def test_an_unset_list_lets_no_campaign_in(stack):
    seed_scene(stack.db)
    response = get(stack, "/vtt/gm?campaignId=camp_a")
    assert response.status_code == 403 and "not in the tabletop pilot" in response.get_data(as_text=True)
    assert get(stack, "/vtt/api/scenes?campaignId=camp_a").get_json()["error"] == "not-in-pilot"


@only_a
def test_every_api_route_refuses_outside_the_pilot_before_the_tracker(stack):
    gm_of_both(stack)
    calls = []
    for name in ("turn_step", "condition_change", "bestiary_search", "bestiary_add", "area_apply"):
        setattr(stack.host, name, lambda *args, _n=name: calls.append(_n) or ("ok", {}))
    turn = {"direction": "next", "expectedActorId": "cmb_a1", "expectedRound": 1, "turnToken": "t-1"}
    condition = {"actorId": "cmb_a1", "condition": "frightened", "operation": "add", "expectedValue": 0}
    add = {"ref": "goblin.json", "requestId": "req-00000001", "encounterToken": "t-1"}
    area = {"requestId": "req-00000001", "encounterToken": "t-1", "action": "damage", "actorIds": ["cmb_a1"],
            "amount": 3}
    refused = [
        get(stack, "/vtt/api/scenes?campaignId=camp_b"),
        get(stack, "/vtt/api/bestiary?campaignId=camp_b&q=gob"),
        post(stack, "/vtt/api/turn", {"campaignId": "camp_b", **turn}),
        post(stack, "/vtt/api/condition", {"campaignId": "camp_b", **condition}),
        post(stack, "/vtt/api/bestiary/add", {"campaignId": "camp_b", **add}),
        post(stack, "/vtt/api/area-effect", {"campaignId": "camp_b", **area}),
        post(stack, "/vtt/api/import-legacy", {"campaignId": "camp_b"}),
    ]
    for response in refused:
        assert response.status_code == 403 and response.get_json()["error"] == "not-in-pilot", response.request.path
    assert calls == []
    # and the same routes still work inside it
    assert get(stack, "/vtt/api/scenes?campaignId=camp_a").status_code == 200
    assert post(stack, "/vtt/api/turn", {"campaignId": "camp_a", **turn}).status_code == 200
    assert calls == ["turn_step"]


@only_a
def test_pairing_a_table_is_refused_outside_the_pilot(stack):
    gm_of_both(stack)
    assert post(stack, "/vtt/api/table-pairing", {"campaignId": "camp_b"}).status_code == 403
    assert post(stack, "/vtt/api/table-pairing", {"campaignId": "camp_a"}).status_code == 200


# --- subscriptions and tables ------------------------------------------------------------

@only_a
def test_a_gm_subscription_outside_the_pilot_is_refused(stack):
    gm_of_both(stack)
    seed_scene(stack.db)
    client, ack = subscribe_gm(stack, campaign_id="camp_b", scene_id=None)
    assert ack == {"schemaVersion": 1, "reason": "session-ended"}
    assert received(client, "vtt.snapshot") == []
    inside, ack = subscribe_gm(stack)
    assert ack is None and received(inside, "vtt.snapshot")


@only_a
def test_a_table_paired_to_a_campaign_outside_the_pilot_cannot_connect(stack):
    # A grant issued before the campaign left the list (the list changes only on restart).
    assert not stack.table_client(add_grant(stack.db, "camp_b")).is_connected(NAMESPACE)
    seed_scene(stack.db)
    client, ack = subscribe_table(stack, add_grant(stack.db, "camp_a"))
    assert ack is None and received(client, "vtt.snapshot")


@everyone
def test_star_lets_every_campaign_in(stack):
    gm_of_both(stack)
    seed_scene(stack.db)
    assert get(stack, "/vtt/gm?campaignId=camp_a").status_code == 200
    assert get(stack, "/vtt/api/scenes?campaignId=camp_b").status_code == 200
    assert stack.table_client(add_grant(stack.db, "camp_b")).is_connected(NAMESPACE)


# --- the real wiring ---------------------------------------------------------------------

def test_the_app_wiring_reads_the_list_from_the_environment(tmp_path, capsys):
    url = f"sqlite+pysqlite:///{(tmp_path / 'p.sqlite').as_posix()}"
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "pilot-secret"
    service = app_wiring.init_from_app(app, environ={"VTT_ENABLED": "1", "DATABASE_URL": url,
                                                     "VTT_PILOT_CAMPAIGNS": "camp_a"})
    assert service.pilot.allows("camp_a") and not service.pilot.allows("camp_b")
    assert "[VTT] pilot: 1 campaign(s)" in capsys.readouterr().out
    app2 = Flask(__name__)
    app2.config["SECRET_KEY"] = "pilot-secret"
    unset = app_wiring.init_from_app(app2, environ={"VTT_ENABLED": "1", "DATABASE_URL": url})
    assert not unset.pilot.allows("camp_a") and "[VTT] pilot: no campaigns (unset)" in capsys.readouterr().out
