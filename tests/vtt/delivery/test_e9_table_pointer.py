"""E-9: every GM knows what the table shows. A campaign-wide `table` entity in
every GM snapshot (with or without a scene open), upserted whenever the table
pointer moves; never sent to a table."""

from __future__ import annotations

from vtt.storage import writes as W

from .conftest import add_grant, payload, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table
from .test_m2_commands import expect, send


def _pointer(entities):
    return [e["sceneId"] for e in entities if e["kind"] == "table"]


def test_a_gm_with_no_scene_open_still_follows_the_table(stack):
    seed_scene(stack.db)                    # scene_a on the table
    gm, _ = subscribe_gm(stack, scene_id=None)
    snap = payload(received(gm, "vtt.snapshot")[0])
    assert snap["sceneId"] is None and _pointer(snap["entities"]) == ["scene_a"]
    W.set_table_pointer(stack.db, "camp_a", None)
    stack.hub.notify("camp_a")
    [patch] = [payload(m) for m in received(gm, "vtt.patch")]
    assert _pointer(patch["upserted"]) == [None] and len(patch["upserted"]) == 1


def test_show_and_clear_from_another_gm_reach_a_gm_on_another_scene(stack):
    seed_scene(stack.db, point_table=False)
    W.create_scene(stack.db, "camp_a", "scene_b", name="Docks")
    watcher, _ = subscribe_gm(stack, scene_id="scene_b")
    shower, _ = subscribe_gm(stack)                          # on scene_a; its snapshot is the latest
    received(watcher)
    send(stack, shower, "table.show", {}, expected=expect(stack, "scene", "scene_a"))
    assert _pointer(payload(received(watcher, "vtt.patch")[0])["upserted"]) == ["scene_a"]
    send(stack, shower, "table.clear", {}, expected=expect(stack, "scene", "scene_a"))
    assert _pointer(payload(received(watcher, "vtt.patch")[0])["upserted"]) == [None]


def test_the_table_never_receives_the_pointer_entity(stack):
    seed_scene(stack.db)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    snap = payload(received(table, "vtt.snapshot")[0])
    assert _pointer(snap["entities"]) == []


def test_an_unchanged_pointer_sends_nothing(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack, scene_id=None)
    received(gm)
    W.set_table_pointer(stack.db, "camp_a", "scene_a")      # same scene again
    stack.hub.notify("camp_a")
    assert received(gm, "vtt.patch") == []
