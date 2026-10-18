"""Parity P2: area templates and terrain (handoff 065).

The seeded scene is 2048 x 2048 at 64 px: 32 x 32 cells.
"""

from __future__ import annotations

import base64

from vtt import terrain as terrain_rules
from vtt.commands import handlers

from .test_m1_read_path import subscribe_gm
from .test_m2_commands import send
from .test_p1_fog import Table, expect, gm_sid, setup

TEMPLATE = {"shape": "cone", "x": 96, "y": 96, "aimX": 288, "aimY": 96, "size": 3, "color": 0x75C5EB, "hidden": False}


def kinds(entity):
    text = entity["cells"]
    data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    return terrain_rules.TerrainView(entity["cols"], entity["rows"], data)


def gm_held(stack):
    held: dict = {}
    gm = gm_sid(stack)
    for sid, event, data in stack.sent:
        if sid != gm:
            continue
        if event == "vtt.snapshot":
            held = {(e["kind"], e["id"]): e for e in data["entities"]}
        elif event == "vtt.patch":
            for ref in data["removed"]:
                held.pop((ref["kind"], ref["id"]), None)
            for e in data["upserted"]:
                held[(e["kind"], e["id"])] = e
    return held


# --- templates -----------------------------------------------------------------------------

def test_a_template_is_created_moved_and_deleted_and_the_table_follows(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    table.now()
    _, ack, _ = send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_cone"})
    assert ack["outcome"] == "applied"
    held = table.now()
    assert held[("template", "tpl_cone")]["shape"] == "cone" and "hidden" not in held[("template", "tpl_cone")]
    _, ack, _ = send(stack, gm, "template.update", {"id": "tpl_cone", "x": 160, "size": 5},
                     expected=expect(stack, "template", "tpl_cone"))
    assert ack["outcome"] == "applied"
    held = table.now()
    assert (held[("template", "tpl_cone")]["x"], held[("template", "tpl_cone")]["size"]) == (160, 5)
    _, ack, _ = send(stack, gm, "entity.delete", {"kind": "template", "id": "tpl_cone"},
                     expected=expect(stack, "template", "tpl_cone"))
    assert ack["outcome"] == "applied"
    assert ("template", "tpl_cone") not in table.now()


def test_a_hidden_template_is_the_gms_alone(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_secret", "hidden": True})
    assert ("template", "tpl_secret") not in table.now()
    assert gm_held(stack)[("template", "tpl_secret")]["hidden"] is True
    send(stack, gm, "template.update", {"id": "tpl_secret", "hidden": False}, expected=expect(stack, "template", "tpl_secret"))
    assert ("template", "tpl_secret") in table.now()


def test_a_template_anchored_under_fog_stays_off_the_table(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_dark"})
    assert ("template", "tpl_dark") not in table.now()
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1]]})
    assert ("template", "tpl_dark") in table.now()


def test_a_stale_template_update_changes_nothing(stack):
    gm, _ = setup(stack)
    send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_a"})
    stale = [{"kind": "template", "id": "tpl_a", "version": "v" + "A" * 24}]
    _, ack, _ = send(stack, gm, "template.update", {"id": "tpl_a", "x": 500}, expected=stale)
    assert ack["code"] == "stale"
    assert gm_held(stack)[("template", "tpl_a")]["x"] == 96


def test_templates_are_capped_per_scene(stack, monkeypatch):
    monkeypatch.setattr(handlers, "MAX_TEMPLATES", 2)
    gm, _ = setup(stack)
    for name in ("one", "two"):
        _, ack, _ = send(stack, gm, "template.create", {**TEMPLATE, "id": f"tpl_{name}"})
        assert ack["outcome"] == "applied"
    _, ack, _ = send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_three"})
    assert ack["outcome"] == "rejected" and ack["code"] == "rule-violation"


# --- terrain -------------------------------------------------------------------------------

def test_every_scene_has_an_empty_terrain_entity(stack):
    gm, table_client = setup(stack)
    held = Table(table_client).now()
    terrain = held[("terrain", "terrain")]
    assert (terrain["cols"], terrain["rows"]) == (32, 32) and not kinds(terrain).any_painted()


def test_painting_sets_a_kind_per_cell_and_both_audiences_see_it(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    _, ack, _ = send(stack, gm, "terrain.paint", {"cells": [[0, 0, 1], [1, 0, 2], [31, 31, 4]]})
    assert ack["outcome"] == "applied"
    view = kinds(table.now()[("terrain", "terrain")])
    assert (view.kind_at(0, 0), view.kind_at(1, 0), view.kind_at(31, 31), view.kind_at(2, 2)) == (1, 2, 4, 0)
    send(stack, gm, "terrain.paint", {"cells": [[1, 0, 0]]})          # 0 erases
    assert kinds(table.now()[("terrain", "terrain")]).kind_at(1, 0) == 0


def test_terrain_under_fog_never_reaches_the_table(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "terrain.paint", {"cells": [[1, 1, 1], [20, 20, 3]]})
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    view = kinds(table.now()[("terrain", "terrain")])
    assert not view.any_painted()                                      # all of it is under fog
    gm_view = kinds(gm_held(stack)[("terrain", "terrain")])
    assert (gm_view.kind_at(1, 1), gm_view.kind_at(20, 20)) == (1, 3)  # the GM sees it all
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1]]})
    view = kinds(table.now()[("terrain", "terrain")])
    assert (view.kind_at(1, 1), view.kind_at(20, 20)) == (1, 0)
    # painting inside fog changes nothing the table can see
    send(stack, gm, "terrain.paint", {"cells": [[25, 25, 2]]})
    assert kinds(table.now()[("terrain", "terrain")]).kind_at(25, 25) == 0


def test_a_cell_outside_the_grid_is_refused(stack):
    gm, _ = setup(stack)
    _, ack, _ = send(stack, gm, "terrain.paint", {"cells": [[32, 0, 1]]})
    assert ack["outcome"] == "rejected" and ack["code"] == "validation-failed"


def test_reset_clears_terrain_against_its_version(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "terrain.paint", {"cells": [[0, 0, 1]]})
    _, ack, _ = send(stack, gm, "terrain.reset", {}, expected=[{"kind": "terrain", "id": "terrain", "version": "v" + "A" * 24}])
    assert ack["code"] == "stale"
    _, ack, _ = send(stack, gm, "terrain.reset", {}, expected=expect(stack, "terrain", "terrain"))
    assert ack["outcome"] == "applied" and not kinds(table.now()[("terrain", "terrain")]).any_painted()


def test_the_grid_cannot_change_while_terrain_is_painted(stack):
    gm, _ = setup(stack)
    send(stack, gm, "terrain.paint", {"cells": [[0, 0, 1]]})
    _, ack, _ = send(stack, gm, "scene.update", {"gridSize": 128}, expected=expect(stack, "scene", "scene_a"))
    assert ack["code"] == "rule-violation"
    send(stack, gm, "terrain.reset", {}, expected=expect(stack, "terrain", "terrain"))
    _, ack, _ = send(stack, gm, "scene.update", {"gridSize": 128}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"


def test_terrain_view_rules():
    view = terrain_rules.TerrainView(3, 1, terrain_rules.empty_cells(3, 1))
    painted = terrain_rules.TerrainView(3, 1, terrain_rules.with_cells(view, [[0, 0, 1], [1, 0, 4], [2, 0, 3]]))
    assert [painted.kind_at(c, 0) for c in range(3)] == [1, 4, 3]
    assert painted.cells == bytes([0x41, 0x03])                        # low nibble first, spare nibble zero
    for bad in ([[3, 0, 1]], [[0, 0, 5]]):
        try:
            terrain_rules.with_cells(view, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)


def test_a_fresh_gm_snapshot_carries_templates_and_terrain(stack):
    gm, _ = setup(stack)
    send(stack, gm, "template.create", {**TEMPLATE, "id": "tpl_b"})
    send(stack, gm, "terrain.paint", {"cells": [[2, 2, 2]]})
    fresh, _ = subscribe_gm(stack)
    from .conftest import payload, received
    [snap] = [payload(m) for m in received(fresh, "vtt.snapshot")]
    held = {(e["kind"], e["id"]): e for e in snap["entities"]}
    assert ("template", "tpl_b") in held and kinds(held[("terrain", "terrain")]).kind_at(2, 2) == 2


# --- the /map import brings templates and terrain across ------------------------------------

def _import(stack, scene):
    from vtt.importer import legacy_scenes as importer
    return importer.import_campaign(stack.db, "camp_a", [scene], None, apply=True)["scenes"][0]


def test_legacy_templates_and_terrain_come_across(stack):
    from vtt.storage.models import VttEntity, VttTerrain
    from .test_m5_import import full_scene
    scene = full_scene()                                               # 70 px grid, 20 x 15 cells
    scene["templates"] = [
        {"id": "t1", "kind": "cone", "x1": 140, "y1": 140, "x2": 420, "y2": 140, "radius": 210, "width": 70,
         "visible_to_players": True},
        {"id": "t2", "kind": "emanation", "x1": 105, "y1": 105, "x2": 105, "y2": 105, "radius": 175, "width": 70,
         "visible_to_players": False, "source_token_id": "tok1"},
        {"id": "t3", "kind": "cube", "x1": 0, "y1": 0, "x2": 0, "y2": 0, "radius": 70, "width": 70},
    ]
    scene["terrain"] = [{"kind": "lava", "cells": ["1,1", "2,1"]}, {"kind": "water", "cells": ["5,5", "99,99"]}]
    report = _import(stack, scene)
    assert report["status"] == "imported"
    dropped = " | ".join(report["dropped"])
    assert "emanation link to its token on 1" in dropped and "1 template(s) of an unknown shape" in dropped
    assert "1 terrain cell(s) outside the scene" in dropped
    with stack.db.session() as session:
        cone = session.get(VttEntity, ("camp_a", "legacy_abc123", "template", "legacy_t1"))
        aura = session.get(VttEntity, ("camp_a", "legacy_abc123", "template", "legacy_t2"))
        terrain = session.get(VttTerrain, ("camp_a", "legacy_abc123"))
        assert cone.data["shape"] == "cone" and cone.data["size"] == 3 and (cone.data["aimX"], cone.data["aimY"]) == (420, 140)
        assert aura.data["size"] == 2 and aura.hidden is True             # 175 / 70 = 2.5, rounds to 2
        view = terrain_rules.TerrainView(terrain.cols, terrain.rows, bytes(terrain.cells))
        assert (view.kind_at(1, 1), view.kind_at(2, 1), view.kind_at(5, 5), view.kind_at(0, 0)) == (1, 1, 2, 0)


def test_reimporting_empty_terrain_removes_the_earlier_terrain(stack):
    from vtt.storage.models import VttTerrain
    from .test_m5_import import full_scene
    scene = full_scene()
    scene["terrain"] = [{"kind": "blood", "cells": ["1,1"]}]
    _import(stack, scene)
    scene["terrain"] = []
    assert _import(stack, scene)["status"] == "imported"
    with stack.db.session() as session:
        assert session.get(VttTerrain, ("camp_a", "legacy_abc123")) is None
