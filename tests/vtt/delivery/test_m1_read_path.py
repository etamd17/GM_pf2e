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
    assert keys(snap) == [("fog", "fog"), ("light", "light_torch"), ("scene", "scene_a"), ("terrain", "terrain"),
                          ("token", "tok_ambush"), ("token", "tok_hero"), ("wall", "wall_secret")]
    ambush = next(e for e in snap["entities"] if e["id"] == "tok_ambush")
    assert ambush["hidden"] is True


def test_table_snapshot_is_its_own_projection_and_leaks_nothing(stack):
    seed_scene(stack.db)
    client, ack = subscribe_table(stack, add_grant(stack.db))
    assert ack is None
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    REGISTRY.validate("table", snap)
    assert snap["principal"] == {"kind": "table"} and snap["capabilities"] == []
    assert keys(snap) == [("fog", "fog"), ("light", "light_torch"), ("scene", "scene_a"), ("terrain", "terrain"),
                          ("token", "tok_hero")]
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


def test_publishing_another_scene_resyncs_the_table_and_the_server_sends_the_replacement(stack):
    # Handoff 025 finding 1: a recoverable resync is completed by the server. The
    # accepted client waits for the replacement on the same connection; it does
    # not send another subscribe.
    seed_scene(stack.db)
    W.create_scene(stack.db, "camp_a", "scene_b", name="Docks", published=True)
    W.upsert_entity(stack.db, "camp_a", "scene_b", "token", "tok_dock", {"x": 10, "y": 10, "label": "Crate"})
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    table_sub = payload(received(table, "vtt.snapshot")[0])["subscriptionId"]
    received(gm)
    W.set_table_pointer(stack.db, "camp_a", "scene_b")
    stack.hub.notify("camp_a")
    messages = received(table)
    assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
    resync, snap = payload(messages[0]), payload(messages[1])
    REGISTRY.validate("resync-required", resync)
    REGISTRY.validate("table", snap)
    assert resync == {"schemaVersion": 1, "subscriptionId": table_sub, "reason": "scene-changed"}
    assert snap["sceneId"] == "scene_b" and snap["seq"] == 0 and snap["subscriptionId"] != table_sub
    assert keys(snap) == [("fog", "fog"), ("scene", "scene_b"), ("terrain", "terrain"), ("token", "tok_dock")]
    assert received(gm) == []
    # live delivery resumes on the replacement subscription
    W.upsert_entity(stack.db, "camp_a", "scene_b", "token", "tok_dock", {"x": 20, "y": 10, "label": "Crate"})
    stack.hub.notify("camp_a")
    [patch] = [payload(m) for m in received(table, "vtt.patch")]
    assert patch["subscriptionId"] == snap["subscriptionId"] and patch["seq"] == 1
    assert [e["x"] for e in patch["upserted"]] == [20]


def test_falling_too_far_behind_resyncs_and_the_replacement_has_the_latest_state(stack, monkeypatch):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    old_sub = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    monkeypatch.setattr(hub_module, "REPLAY_LIMIT", 3)
    for x in range(5):
        W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"x": 100 + x, "y": 64})
    stack.hub.notify("camp_a")
    messages = received(gm)
    assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
    assert payload(messages[0]) == {"schemaVersion": 1, "subscriptionId": old_sub, "reason": "buffer-overflow"}
    snap = payload(messages[1])
    assert snap["subscriptionId"] != old_sub
    assert next(e for e in snap["entities"] if e["id"] == "tok_hero")["x"] == 104


# --- access is rechecked on live delivery (handoff 025 finding 2) ---------------------------

def _set_grant(db, **fields):
    from vtt.storage.models import VttTableGrant
    with db.transaction() as session:
        for grant in session.query(VttTableGrant).all():
            for name, value in fields.items():
                setattr(grant, name, value)


def _move_hero(db, x):
    W.upsert_entity(db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": x, "y": 64, "label": "Valeros", "color": 1, "controllerIds": ["user_p"]})


def _assert_ended(stack, client, sub_id):
    assert [(e, d) for _, e, d in stack.sent] == [
        ("vtt.resync_required", {"schemaVersion": 1, "subscriptionId": sub_id, "reason": "revoked"})]
    assert not client.is_connected(NAMESPACE)
    assert stack.hub.stats["patches"] == 0 and stack.hub.stats["ended"] == 1
    assert not stack.hub._by_sid
    # and a further commit sends nothing anywhere
    _move_hero(stack.db, 300)
    stack.hub.notify("camp_a")
    assert len(stack.sent) == 1


def test_a_gm_who_loses_gm_access_gets_no_more_patches(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    sub_id = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    stack.sent.clear()
    stack.host.gms.clear()
    _move_hero(stack.db, 128)
    stack.hub.notify("camp_a")
    _assert_ended(stack, gm, sub_id)


@pytest.mark.parametrize("change", ["expired", "revoked"])
def test_a_table_whose_grant_ends_gets_no_more_patches(stack, change):
    from datetime import datetime, timedelta, timezone
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    sub_id = payload(received(table, "vtt.snapshot")[0])["subscriptionId"]
    stack.sent.clear()
    now = datetime.now(timezone.utc)
    _set_grant(stack.db, **({"expires_at": now - timedelta(seconds=1)} if change == "expired"
                            else {"revoked_at": now}))
    _move_hero(stack.db, 128)
    stack.hub.notify("camp_a")
    _assert_ended(stack, table, sub_id)


# --- the cursor moves only with a successful emit (handoff 025 finding 3) ------------------

def test_a_failed_patch_emit_recovers_with_the_committed_state(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    old_sub = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    stack.sent.clear()
    real_emit = stack.hub.emit
    failures = {"left": 1}

    def fail_once(sid, event, data):
        if event == "vtt.patch" and failures["left"]:
            failures["left"] -= 1
            raise OSError("transport write failed")
        real_emit(sid, event, data)

    stack.hub.emit = fail_once
    _move_hero(stack.db, 128)
    stack.hub.notify("camp_a")
    events = [(e, d) for _, e, d in stack.sent]
    assert [e for e, _ in events] == ["vtt.resync_required", "vtt.snapshot"]
    assert events[0][1] == {"schemaVersion": 1, "subscriptionId": old_sub, "reason": "state-lost"}
    snap = events[1][1]
    assert next(e for e in snap["entities"] if e["id"] == "tok_hero")["x"] == 128   # not lost
    assert stack.hub.stats["delivery_errors"] == 1
    # and live delivery continues on the replacement
    _move_hero(stack.db, 256)
    stack.hub.notify("camp_a")
    last = stack.sent[-1]
    assert last[1] == "vtt.patch" and last[2]["seq"] == 1 and last[2]["subscriptionId"] == snap["subscriptionId"]
    assert [e["x"] for e in last[2]["upserted"]] == [256]


def test_a_projection_failure_never_leaves_a_stale_live_subscription(stack):
    # Canonical data the serializer cannot project (a token without x). Recovery's
    # snapshot fails the same way, so the connection is closed, not left live.
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    stack.sent.clear()
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero", {"y": 64, "label": "Broken"})
    stack.hub.notify("camp_a")
    assert [e for _, e, _ in stack.sent] == ["vtt.resync_required"]
    assert stack.hub.stats["delivery_errors"] >= 1 and stack.hub.stats["patches"] == 0
    assert not stack.hub._by_sid
    assert not gm.is_connected(NAMESPACE)


def test_an_invisible_change_still_moves_the_cursor_without_a_frame(stack):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    stack.sent.clear()
    sub = next(iter(stack.hub._by_sid.values()))
    before = sub.cursor
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_ambush",
                    {"x": 1, "y": 1, "label": "Hidden Assassin", "color": 2}, hidden=True)
    stack.hub.notify("camp_a")
    assert stack.sent == [] and sub.seq == 0 and sub.cursor == before + 1


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


def test_a_patch_the_contract_refuses_is_never_sent_and_bad_data_cannot_loop(stack):
    # Canonical data the contract forbids (a control character in a label) is a
    # server bug. No patch goes out. Recovery sends state-lost and tries a fresh
    # snapshot; that snapshot carries the same bad data, so instead of looping the
    # connection is closed. (M2 validates at write time so this cannot be stored.)
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    sub_id = payload(received(gm, "vtt.snapshot")[0])["subscriptionId"]
    stack.sent.clear()
    W.upsert_entity(stack.db, "camp_a", "scene_a", "token", "tok_hero",
                    {"x": 64, "y": 64, "label": "Vale\u0000ros", "color": 1})
    stack.hub.notify("camp_a")
    assert [(e, d.get("reason")) for _, e, d in stack.sent] == [("vtt.resync_required", "state-lost")]
    assert stack.sent[0][2]["subscriptionId"] == sub_id
    assert stack.hub.stats["invalid"] == 1 and stack.hub.stats["patches"] == 0
    assert not gm.is_connected(NAMESPACE)
