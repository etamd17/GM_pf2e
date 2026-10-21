"""E-7: scene.delete. A version-checked delete of a scene and everything on it,
refused while the table shows the scene, never undoable. A GM looking at the
deleted scene is resynced into an empty snapshot, not cut off."""

from __future__ import annotations

from vtt.storage import writes as W
from vtt.storage.models import VttEntity, VttFog, VttImportedScene, VttScene, VttTerrain
from vtt.transport.socket import NAMESPACE

from .conftest import add_grant, payload, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table
from .test_m2_commands import _rejected, count, expect, send


def _second_scene(stack):
    W.create_scene(stack.db, "camp_a", "scene_b", name="Cellar")
    W.upsert_entity(stack.db, "camp_a", "scene_b", "token", "tok_rat", {"x": 8, "y": 8, "label": "Rat", "color": 1})


def test_delete_removes_the_scene_and_everything_on_it_and_nothing_else(stack):
    seed_scene(stack.db, point_table=False)
    _second_scene(stack)
    client, _ = subscribe_gm(stack)
    send(stack, client, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    with stack.db.transaction() as session:
        session.add(VttImportedScene(campaign_id="camp_a", scene_id="scene_a", source="legacy:abc"))
    _, ack, _ = send(stack, client, "scene.delete", {}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    for model in (VttScene, VttFog, VttTerrain, VttImportedScene):
        where = {"id": "scene_a"} if model is VttScene else {"scene_id": "scene_a"}
        assert count(stack, model, campaign_id="camp_a", **where) == 0, model.__name__
    assert count(stack, VttEntity, campaign_id="camp_a", scene_id="scene_a") == 0
    assert count(stack, VttScene, campaign_id="camp_a", id="scene_b") == 1
    assert count(stack, VttEntity, campaign_id="camp_a", scene_id="scene_b") == 1


def test_the_gm_viewing_it_is_resynced_to_no_scene_not_revoked(stack):
    seed_scene(stack.db, point_table=False)
    deleter, _ = subscribe_gm(stack)
    watcher, _ = subscribe_gm(stack)       # a second GM tab on the same scene
    received(deleter)
    received(watcher)
    _, ack, _ = send(stack, deleter, "scene.delete", {}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    for client in (deleter, watcher):
        messages = [m for m in received(client) if m["name"] != "vtt.ack"]
        assert [m["name"] for m in messages] == ["vtt.resync_required", "vtt.snapshot"]
        assert payload(messages[0])["reason"] == "scene-changed"
        snapshot = payload(messages[1])
        assert snapshot["sceneId"] is None and snapshot["entities"] == []
        assert client.is_connected(NAMESPACE)


def test_a_stale_version_is_refused_and_nothing_is_deleted(stack):
    seed_scene(stack.db, point_table=False)
    client, _ = subscribe_gm(stack)
    old = expect(stack, "scene", "scene_a")
    send(stack, client, "scene.update", {"name": "Renamed"}, expected=old)
    cid, ack, _ = send(stack, client, "scene.delete", {}, expected=old)
    _rejected(stack, ack, cid, "stale")
    assert count(stack, VttScene, campaign_id="camp_a", id="scene_a") == 1


def test_the_scene_on_the_table_is_refused_until_the_table_is_cleared(stack):
    seed_scene(stack.db)                    # published and on the table
    client, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    received(table)
    cid, ack, _ = send(stack, client, "scene.delete", {}, expected=expect(stack, "scene", "scene_a"))
    _rejected(stack, ack, cid, "rule-violation")
    assert count(stack, VttScene, campaign_id="camp_a", id="scene_a") == 1
    assert received(table) == []            # the table saw nothing
    send(stack, client, "table.clear", {}, expected=expect(stack, "scene", "scene_a"))
    received(table)
    _, ack, _ = send(stack, client, "scene.delete", {}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    assert received(table) == []            # deleting a scene the table is not showing changes nothing there


def test_without_an_expected_version_it_is_invalid(stack):
    seed_scene(stack.db, point_table=False)
    client, _ = subscribe_gm(stack)
    _, ack, _ = send(stack, client, "scene.delete", {})
    assert ack["outcome"] == "rejected" and ack["code"] == "invalid-schema"
    assert count(stack, VttScene, campaign_id="camp_a", id="scene_a") == 1


def test_another_campaigns_scene_is_the_usual_validation_failure(stack):
    seed_scene(stack.db, point_table=False)
    client, _ = subscribe_gm(stack)
    W.create_scene(stack.db, "camp_b", "scene_x", name="Not yours")
    fake = [{"kind": "scene", "id": "scene_x", "version": "v" + "A" * 24}]
    cid, ack, _ = send(stack, client, "scene.delete", {}, expected=fake, scene="scene_x")
    _rejected(stack, ack, cid, "validation-failed")
    assert count(stack, VttScene, campaign_id="camp_b", id="scene_x") == 1
