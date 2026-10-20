"""E-6: dynamic lighting and vision, the server half.

The table gets the segments that block sight (walls and doors that are not
open, without their door state) only while the scene's lighting is on, and a
party token (linked to a player character) carries how far it sees. The seeded
scene has one secret door (wall_secret), which blocks sight.
"""

from __future__ import annotations

from vtt.storage import writes as W

from .test_m2_commands import send
from .test_p1_fog import Table, expect, setup
from .test_p3_live import Gm, VALEROS, entry, link, mirror


def lighting(stack, gm, on=True, default_vision=None):
    payload = {"lighting": on}
    if default_vision is not None:
        payload["defaultVision"] = default_vision
    _, ack, _ = send(stack, gm, "scene.update", payload, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied", ack


def test_the_table_sees_no_walls_until_lighting_is_on(stack):
    gm, table = setup(stack)
    view = Table(table)
    held = view.now()
    assert ("wall", "wall_secret") not in held and held[("scene", "scene_a")]["lighting"] is False
    lighting(stack, gm)
    held = view.now()
    assert held[("scene", "scene_a")]["lighting"] is True
    wall = held[("wall", "wall_secret")]
    assert set(wall) == {"kind", "id", "version", "x1", "y1", "x2", "y2"}      # no door state
    lighting(stack, gm, False)
    assert ("wall", "wall_secret") not in view.now()


def test_an_open_door_does_not_block_and_leaves_the_table(stack):
    gm, table = setup(stack)
    view = Table(table)
    lighting(stack, gm)
    assert ("wall", "wall_secret") in view.now()
    _, ack, _ = send(stack, gm, "wall.update", {"id": "wall_secret", "doorState": "open"},
                     expected=expect(stack, "wall", "wall_secret"))
    assert ack["outcome"] == "applied"
    assert ("wall", "wall_secret") not in view.now()
    _, ack, _ = send(stack, gm, "wall.update", {"id": "wall_secret", "doorState": "closed"},
                     expected=expect(stack, "wall", "wall_secret"))
    assert ("wall", "wall_secret") in view.now()


def test_a_party_token_carries_its_vision_and_a_monster_does_not(stack):
    gm, table = setup(stack)
    view = Table(table)
    mirror(stack, {"skel": entry("Skeleton", 7, 20)}, {"valeros": VALEROS})
    link(stack, gm, "tok_hero", "chr_valeros")
    lighting(stack, gm, default_vision=448)
    assert view.now()[("token", "tok_hero")]["vision"] == 448
    _, ack, _ = send(stack, gm, "token.update", {"id": "tok_hero", "vision": 192},
                     expected=expect(stack, "token", "tok_hero"))
    assert ack["outcome"] == "applied"
    assert view.now()[("token", "tok_hero")]["vision"] == 192
    link(stack, gm, "tok_hero", "cmb_skel")                     # a monster sees nothing for the table
    assert "vision" not in view.now()[("token", "tok_hero")]
    lighting(stack, gm, False)
    link(stack, gm, "tok_hero", "chr_valeros")
    assert "vision" not in view.now()[("token", "tok_hero")]  # lighting off: no vision at all


def test_the_gm_sees_the_settings_and_token_vision(stack):
    gm, _table = setup(stack)
    held = Gm(gm)
    lighting(stack, gm, default_vision=320)
    scene = held.now()[("scene", "scene_a")]
    assert (scene["lighting"], scene["defaultVision"]) == (True, 320)
    _, ack, _ = send(stack, gm, "token.create",
                     {"id": "tok_scout", "x": 300, "y": 300, "size": 1, "label": "Scout", "color": 1,
                      "artAssetId": None, "actorId": None, "hidden": False, "locked": False, "controllerIds": [],
                      "vision": 640})
    assert ack["outcome"] == "applied"
    assert held.now()[("token", "tok_scout")]["vision"] == 640


def test_a_token_created_without_vision_still_works(stack):
    gm, _table = setup(stack)
    held = Gm(gm)
    _, ack, _ = send(stack, gm, "token.create",
                     {"id": "tok_plain", "x": 300, "y": 300, "size": 1, "label": "Plain", "color": 1,
                      "artAssetId": None, "actorId": None, "hidden": False, "locked": False, "controllerIds": []})
    assert ack["outcome"] == "applied"
    assert held.now()[("token", "tok_plain")]["vision"] is None


def test_hidden_lights_never_reach_the_table_lit_or_not(stack):
    gm, table = setup(stack)
    view = Table(table)
    W.upsert_entity(stack.db, "camp_a", "scene_a", "light", "light_gm", {"x": 500, "y": 500, "radius": 64, "color": 1},
                    hidden=True)
    lighting(stack, gm)
    assert ("light", "light_gm") not in view.now()
