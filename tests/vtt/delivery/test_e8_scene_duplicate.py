"""E-8: scene.duplicate. Copies a scene, its entities, fog and terrain into a
new unpublished scene in one transaction; the source is untouched."""

from __future__ import annotations

from vtt.storage.models import VttEntity, VttFog, VttImportedScene, VttScene, VttTablePointer, VttTerrain

from .conftest import add_grant, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table
from .test_m2_commands import _rejected, count, expect, send

COPY = {"sourceSceneId": "scene_a", "name": "Battlefield (copy)"}


def _scene(stack, scene_id):
    with stack.db.session() as session:
        return session.get(VttScene, ("camp_a", scene_id))


def test_a_duplicate_copies_the_scene_its_entities_fog_and_terrain(stack):
    seed_scene(stack.db)                    # published and on the table
    client, _ = subscribe_gm(stack)
    send(stack, client, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    _, painted, _ = send(stack, client, "terrain.paint", {"cells": [[1, 1, 2]]}, expected=[])
    assert painted["outcome"] == "applied", painted
    with stack.db.transaction() as session:
        session.add(VttImportedScene(campaign_id="camp_a", scene_id="scene_a", source="legacy:abc"))
    _, ack, _ = send(stack, client, "scene.duplicate", COPY, scene="scene_copy")
    assert ack["outcome"] == "applied"
    source, copy = _scene(stack, "scene_a"), _scene(stack, "scene_copy")
    assert copy.name == "Battlefield (copy)" and copy.published is False
    assert (copy.width, copy.height, copy.grid_size, copy.show_grid, copy.background_asset_id) == \
           (source.width, source.height, source.grid_size, source.show_grid, source.background_asset_id)
    with stack.db.session() as session:
        def rows(scene_id):
            return {(e.kind, e.id): (e.hidden, e.data) for e in session.query(VttEntity).filter_by(
                campaign_id="camp_a", scene_id=scene_id)}
        assert rows("scene_copy") == rows("scene_a") and rows("scene_a")
        fog_a, fog_c = session.get(VttFog, ("camp_a", "scene_a")), session.get(VttFog, ("camp_a", "scene_copy"))
        assert (fog_c.enabled, fog_c.cols, fog_c.rows, bytes(fog_c.revealed)) == \
               (fog_a.enabled, fog_a.cols, fog_a.rows, bytes(fog_a.revealed))
        ter_a, ter_c = session.get(VttTerrain, ("camp_a", "scene_a")), session.get(VttTerrain, ("camp_a", "scene_copy"))
        assert ter_a is not None and (ter_c.cols, ter_c.rows, bytes(ter_c.cells)) == (ter_a.cols, ter_a.rows, bytes(ter_a.cells))
        assert session.get(VttTablePointer, "camp_a").scene_id == "scene_a"    # the TV did not move
    assert count(stack, VttImportedScene, scene_id="scene_copy") == 0           # provenance stays put


def test_editing_the_copy_leaves_the_source_alone(stack):
    seed_scene(stack.db, point_table=False)
    client, _ = subscribe_gm(stack)
    send(stack, client, "scene.duplicate", COPY, scene="scene_copy")
    with stack.db.transaction() as session:
        row = session.get(VttEntity, ("camp_a", "scene_copy", "token", "tok_hero"))
        row.data = {**row.data, "x": 999}
    with stack.db.session() as session:
        assert session.get(VttEntity, ("camp_a", "scene_a", "token", "tok_hero")).data["x"] == 64


def test_the_table_sees_nothing_of_a_duplicate(stack):
    seed_scene(stack.db)
    client, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    received(table)
    send(stack, client, "scene.duplicate", COPY, scene="scene_copy")
    assert received(table) == []


def test_an_existing_id_is_a_conflict_and_a_missing_source_the_usual_failure(stack):
    seed_scene(stack.db, point_table=False)
    client, _ = subscribe_gm(stack)
    cid, ack, _ = send(stack, client, "scene.duplicate", COPY, scene="scene_a")
    _rejected(stack, ack, cid, "conflict")
    cid, ack, _ = send(stack, client, "scene.duplicate", {**COPY, "sourceSceneId": "scene_none"}, scene="scene_x")
    _rejected(stack, ack, cid, "validation-failed")
    assert count(stack, VttScene, campaign_id="camp_a", id="scene_x") == 0
