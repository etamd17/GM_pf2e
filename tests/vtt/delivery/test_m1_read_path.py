"""M1: the read path. GM and table subscribe, snapshot, delivery and refusal.

Every frame here went through the real namespace handlers, the hub, the
serializers, ``ContractRegistry.validate`` (schema plus frame rules) and the
StrictJSON codec."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from vtt.delivery import hub as hub_module
from vtt.protocol import ContractRegistry
from vtt.storage import writes as W
from vtt.transport.socket import NAMESPACE

from .conftest import add_grant, payload, received

REGISTRY = ContractRegistry()


def seed_scene(db, *, published=True, point_table=True):
    W.create_scene(db, "camp_a", "scene_a", name="Battlefield", published=published)
    W.upsert_entity(db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 64, "y": 64, "label": "Valeros", "color": 1, "controllerIds": ["user_p"]})
    W.upsert_entity(db, "camp_a", "scene_a", "token", "tok_ambush",
                    {"x": 900, "y": 900, "label": "Hidden Assassin", "color": 2}, hidden=True)
    W.upsert_entity(db, "camp_a", "scene_a", "light", "light_torch", {"x": 70, "y": 70, "radius": 128, "color": 3})
    W.upsert_entity(db, "camp_a", "scene_a", "wall", "wall_secret",
                    {"x1": 0, "y1": 0, "x2": 128, "y2": 0, "doorState": "secret"})
    if point_table:
        W.set_table_pointer(db, "camp_a", "scene_a")


def ack_value(ack):
    """Flask-SocketIO's test client returns [] when the handler acknowledges with nothing."""
    return None if ack == [] else ack


def subscribe_gm(stack, scene_id="scene_a", campaign_id="camp_a"):
    client = stack.gm_client()
    assert client.is_connected(NAMESPACE)
    ack = client.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "gm", "campaignId": campaign_id,
                                        "sceneId": scene_id}, namespace=NAMESPACE, callback=True)
    return client, ack_value(ack)


def subscribe_table(stack, credential):
    client = stack.table_client(credential)
    assert client.is_connected(NAMESPACE)
    ack = client.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "table"}, namespace=NAMESPACE, callback=True)
    return client, ack_value(ack)


def keys(frame, field="entities"):
    return sorted((e["kind"], e["id"]) for e in frame[field])


# --- snapshots ------------------------------------------------------------------------------

def test_gm_snapshot_carries_everything_the_gm_may_see(stack):
    seed_scene(stack.db)
    client, ack = subscribe_gm(stack)
    assert ack is None
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    REGISTRY.validate("gm", snap)
    assert snap["seq"] == 0 and snap["sceneId"] == "scene_a" and snap["audience"] == "gm"
    assert snap["principal"] == {"kind": "member", "id": "user_gm", "campaignRole": "gm"}
    assert keys(snap) == [("light", "light_torch"), ("scene", "scene_a"), ("token", "tok_ambush"),
                          ("token", "tok_hero"), ("wall", "wall_secret")]
    ambush = next(e for e in snap["entities"] if e["id"] == "tok_ambush")
    assert ambush["hidden"] is True


def test_table_snapshot_is_its_own_projection_and_leaks_nothing(stack):
    seed_scene(stack.db)
    client, ack = subscribe_table(stack, add_grant(stack.db))
    assert ack is None
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    REGISTRY.validate("table", snap)
    assert snap["principal"] == {"kind": "table"} and snap["capabilities"] == []
    assert keys(snap) == [("light", "light_torch"), ("scene", "scene_a"), ("token", "tok_hero")]
    text = json.dumps(snap)
    for secret in ("tok_ambush", "Hidden Assassin", "wall_secret", "secret", "controllerIds", "user_p",
                   "hidden", "published", "locked"):
        assert secret not in text, secret


def test_table_with_nothing_published_sees_no_scene(stack):
    seed_scene(stack.db, published=False)
    client, _ = subscribe_table(stack, add_grant(stack.db))
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    assert snap["sceneId"] is None and snap["entities"] == []


def test_gm_and_table_version_tokens_differ_for_the_same_entity(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    g = {e["id"]: e["version"] for e in payload(received(gm, "vtt.snapshot")[0])["entities"]}
    t = {e["id"]: e["version"] for e in payload(received(table, "vtt.snapshot")[0])["entities"]}
    assert g["tok_hero"] != t["tok_hero"]


# --- delivery -------------------------------------------------------------------------------

def test_a_visible_move_reaches_both_audiences_as_seq_1(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 128, "y": 64, "label": "Valeros", "color": 1, "controllerIds": ["user_p"]})
    stack.hub.notify("camp_a")
    for client, audience in ((gm, "gm"), (table, "table")):
        [patch] = [payload(m) for m in received(client, "vtt.patch")]
        REGISTRY.validate(audience, patch)
        assert patch["seq"] == 1 and [e["x"] for e in patch["upserted"]] == [128] and patch["removed"] == []


def test_a_change_the_table_cannot_see_sends_the_table_nothing(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    # controllers and locked are GM-only fields of a visible token
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 64, "y": 64, "label": "Valeros", "color": 1, "controllerIds": ["user_q"], "locked": True})
    # and the hidden token moves
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_ambush",
                    {"x": 800, "y": 900, "label": "Hidden Assassin", "color": 2}, hidden=True)
    stack.hub.notify("camp_a")
    assert received(table, "vtt.patch") == []
    gm_patches = [payload(m) for m in received(gm, "vtt.patch")]
    assert [p["seq"] for p in gm_patches] == [1]
    assert keys(gm_patches[0], "upserted") == [("token", "tok_ambush"), ("token", "tok_hero")]


def test_hiding_and_revealing_a_token_are_indistinguishable_from_remove_and_add(stack):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 64, "y": 64, "label": "Valeros", "color": 1}, hidden=True)
    stack.hub.notify("camp_a")
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_ambush",
                    {"x": 900, "y": 900, "label": "Hidden Assassin", "color": 2}, hidden=False)
    stack.hub.notify("camp_a")
    patches = [payload(m) for m in received(table, "vtt.patch")]
    assert [p["seq"] for p in patches] == [1, 2]
    assert patches[0]["removed"] == [{"kind": "token", "id": "tok_hero"}] and patches[0]["upserted"] == []
    assert keys(patches[1], "upserted") == [("token", "tok_ambush")]


def test_a_commit_before_the_snapshot_is_not_delivered_again(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    stack.hub.notify("camp_a")          # everything is at or below the snapshot cursor
    assert received(gm, "vtt.patch") == []


def test_publishing_another_scene_resyncs_only_the_table(stack):
    seed_scene(stack.db)
    W.create_scene(stack.db, "camp_a", "scene_b", name="Docks", published=True)
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    table_sub = payload(received(table, "vtt.snapshot")[0])["subscriptionId"]
    received(gm)
    W.set_table_pointer(stack.db, "camp_a", "scene_b")
    stack.hub.notify("camp_a")
    [resync] = [payload(m) for m in received(table, "vtt.resync_required")]
    REGISTRY.validate("resync-required", resync)
    assert resync == {"schemaVersion": 1, "subscriptionId": table_sub, "reason": "scene-changed"}
    assert received(gm) == []
    # the table resubscribes and gets the new scene under a new subscription
    table.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "table"}, namespace=NAMESPACE, callback=True)
    [snap] = [payload(m) for m in received(table, "vtt.snapshot")]
    assert snap["sceneId"] == "scene_b" and snap["subscriptionId"] != table_sub


def test_falling_too_far_behind_resyncs_with_buffer_overflow(stack, monkeypatch):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    monkeypatch.setattr(hub_module, "REPLAY_LIMIT", 3)
    for x in range(5):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": 100 + x, "y": 64})
    stack.hub.notify("camp_a")
    assert [payload(m)["reason"] for m in received(gm, "vtt.resync_required")] == ["buffer-overflow"]
    assert received(gm, "vtt.patch") == []


# --- refusal --------------------------------------------------------------------------------

def test_member_without_a_session_or_csrf_is_refused(stack):
    stack.host.member_id = None
    assert not stack.gm_client().is_connected(NAMESPACE)
    stack.host.member_id = "user_gm"
    assert not stack.gm_client(csrf="wrong").is_connected(NAMESPACE)
    assert not stack.gm_client(csrf=None).is_connected(NAMESPACE)


@pytest.mark.parametrize("kwargs", [{"revoked": True}, {"expires_in": timedelta(seconds=-1)}])
def test_revoked_and_expired_grants_cannot_connect(stack, kwargs):
    assert not stack.table_client(add_grant(stack.db, **kwargs)).is_connected(NAMESPACE)


def test_an_unknown_table_credential_cannot_connect(stack):
    assert not stack.table_client("x" * 40).is_connected(NAMESPACE)


def test_a_member_who_is_not_gm_of_that_campaign_gets_session_ended(stack):
    seed_scene(stack.db)
    client, ack = subscribe_gm(stack, campaign_id="camp_b", scene_id=None)
    assert ack == {"schemaVersion": 1, "reason": "session-ended"}
    assert received(client, "vtt.snapshot") == []
    # the principal is forgotten: the next event is refused by disconnecting
    client.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "gm", "campaignId": "camp_a",
                                  "sceneId": "scene_a"}, namespace=NAMESPACE)
    assert not client.is_connected(NAMESPACE)      # disconnected before any snapshot


def test_an_unsupported_version_gets_the_stable_v1_envelope(stack):
    client = stack.gm_client()
    ack = client.emit("vtt.subscribe", {"schemaVersion": 2, "audience": "gm", "campaignId": "camp_a",
                                        "sceneId": None}, namespace=NAMESPACE, callback=True)
    assert ack == {"schemaVersion": 1, "reason": "unsupported-version"}
    REGISTRY.validate("bootstrap-rejection", ack)


def test_a_table_cannot_choose_its_campaign_or_scene(stack):
    seed_scene(stack.db)
    client = stack.table_client(add_grant(stack.db))
    client.emit("vtt.subscribe", {"schemaVersion": 1, "audience": "table", "campaignId": "camp_b",
                                  "sceneId": "scene_a"}, namespace=NAMESPACE)
    assert not client.is_connected(NAMESPACE)      # fail closed: no snapshot, no reply
    assert stack.hub.stats["snapshots"] == 0


def test_a_table_sending_a_command_is_disconnected(stack):
    seed_scene(stack.db)
    client, _ = subscribe_table(stack, add_grant(stack.db))
    client.emit("vtt.command", {"schemaVersion": 1}, namespace=NAMESPACE)
    assert not client.is_connected(NAMESPACE)


def test_a_grant_for_another_campaign_never_sees_this_one(stack):
    seed_scene(stack.db)
    client, _ = subscribe_table(stack, add_grant(stack.db, "camp_b"))
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    assert snap["campaignId"] == "camp_b" and snap["entities"] == []


def test_a_patch_the_contract_refuses_is_never_sent_and_forces_a_fresh_snapshot(stack):
    # Canonical data the contract forbids (a control character in a label) is a
    # server bug: no patch goes out, and the subscription resyncs rather than
    # drifting from what the client holds. (M2 validates at write time.)
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    sub_id = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 64, "y": 64, "label": "Vale\u0000ros", "color": 1})
    stack.hub.notify("camp_a")
    messages = received(gm)
    assert [m["name"] for m in messages] == ["vtt.resync_required"]
    assert payload(messages[0]) == {"schemaVersion": 1, "subscriptionId": sub_id, "reason": "state-lost"}
    assert stack.hub.stats["invalid"] == 1 and stack.hub.stats["patches"] == 0
