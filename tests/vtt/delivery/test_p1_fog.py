"""Parity P1: fog of war, applied on the server (handoff 060).

The seeded scene is 2048 x 2048 with a 64 px grid: 32 x 32 cells. Valeros
(tok_hero) and the torch both sit in cell (1, 1); the assassin is hidden.
"""

from __future__ import annotations

import base64

from vtt import fog
from vtt.protocol import ContractRegistry
from vtt.storage import writes as W

from .conftest import add_grant, payload, received
from .test_m1_read_path import seed_scene, subscribe_gm, subscribe_table
from .test_m2_commands import TOKEN, send

REGISTRY = ContractRegistry()


def gm_sid(stack):
    return next(sid for sid, event, data in stack.sent if event == "vtt.snapshot" and data["audience"] == "gm")


def expect(stack, kind, entity_id):
    """The GM's current version of an entity: only the GM subscription's frames
    (a table patch carries the table's own, different, version tokens)."""
    versions = {}
    gm = gm_sid(stack)
    for sid, event, data in stack.sent:
        if sid != gm:
            continue
        if event == "vtt.snapshot":
            versions = {(e["kind"], e["id"]): e["version"] for e in data["entities"]}
        elif event == "vtt.patch":
            for e in data["upserted"]:
                versions[(e["kind"], e["id"])] = e["version"]
    return [{"kind": kind, "id": entity_id, "version": versions[(kind, entity_id)]}]


def setup(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    table, _ = subscribe_table(stack, add_grant(stack.db))
    received(gm)
    return gm, table


class Table:
    """Accumulates the table's frames across calls."""

    def __init__(self, client):
        self.client = client
        self.held: dict = {}

    def now(self):
        for message in self.client.get_received("/vtt"):
            frame = payload(message)
            REGISTRY.validate("table", frame)
            if message["name"] == "vtt.snapshot":
                self.held = {(e["kind"], e["id"]): e for e in frame["entities"]}
            elif message["name"] == "vtt.patch":
                for ref in frame["removed"]:
                    self.held.pop((ref["kind"], ref["id"]), None)
                for e in frame["upserted"]:
                    self.held[(e["kind"], e["id"])] = e
        return self.held


def bits(entity):
    text = entity["revealed"]
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def revealed(entity, col, row):
    index = row * entity["cols"] + col
    return bool(bits(entity)[index >> 3] >> (index & 7) & 1)


def test_every_scene_has_a_fog_entity_off_and_dark_by_default(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    held = table.now()
    fog_e = held[("fog", "fog")]
    assert (fog_e["enabled"], fog_e["cols"], fog_e["rows"]) == (False, 32, 32)
    assert not any(bits(fog_e))
    assert ("token", "tok_hero") in held and ("light", "light_torch") in held   # fog off: all visible


def test_turning_fog_on_hides_tokens_and_lights_from_the_table_only(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    table.now()
    _, ack, after = send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    assert ack["outcome"] == "applied"
    held = table.now()
    assert ("token", "tok_hero") not in held and ("light", "light_torch") not in held
    assert held[("fog", "fog")]["enabled"] is True
    # the GM still holds everything: its patches remove nothing
    gm = gm_sid(stack)
    gm_patches = [d for sid, e, d in after if e == "vtt.patch" and sid == gm]
    assert gm_patches and all(not p["removed"] for p in gm_patches)


def test_revealing_a_cell_shows_what_is_in_it_and_hiding_takes_it_away(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    table.now()
    _, ack, _ = send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1], [2, 1]]})
    assert ack["outcome"] == "applied"
    held = table.now()
    assert ("token", "tok_hero") in held and ("light", "light_torch") in held
    assert revealed(held[("fog", "fog")], 1, 1) and revealed(held[("fog", "fog")], 2, 1)
    assert not revealed(held[("fog", "fog")], 0, 0)
    send(stack, gm, "fog.reveal", {"mode": "hide", "cells": [[1, 1]]})
    held = table.now()
    assert ("token", "tok_hero") not in held and ("light", "light_torch") not in held


def test_a_token_walking_into_fog_leaves_the_table_and_back_out_returns(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1]]})
    assert ("token", "tok_hero") in table.now()
    send(stack, gm, "token.move", {"id": "tok_hero", "x": 640, "y": 640}, expected=expect(stack, "token", "tok_hero"))
    assert ("token", "tok_hero") not in table.now()
    send(stack, gm, "token.move", {"id": "tok_hero", "x": 96, "y": 96}, expected=expect(stack, "token", "tok_hero"))
    assert ("token", "tok_hero") in table.now()


def test_a_new_token_created_under_fog_never_reaches_the_table(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    table.now()
    send(stack, gm, "token.create", {**TOKEN, "id": "tok_lurker"})
    held = table.now()
    assert ("token", "tok_lurker") not in held
    for message in table_client.get_received("/vtt"):
        assert "tok_lurker" not in str(payload(message))


def test_the_table_snapshot_with_fog_on_leaves_covered_entities_out(stack):
    seed_scene(stack.db)
    gm, _ = subscribe_gm(stack)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    client, _ = subscribe_table(stack, add_grant(stack.db))
    [snap] = [payload(m) for m in received(client, "vtt.snapshot")]
    REGISTRY.validate("table", snap)
    kinds = {(e["kind"], e["id"]) for e in snap["entities"]}
    assert kinds == {("scene", "scene_a"), ("fog", "fog")}
    assert "Valeros" not in str(snap) and "light_torch" not in str(snap)


def test_reset_hides_everything_again_and_needs_the_fog_version(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.update", {"enabled": True}, expected=expect(stack, "fog", "fog"))
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1]]})
    assert ("token", "tok_hero") in table.now()
    stale = [{"kind": "fog", "id": "fog", "version": "v" + "A" * 24}]
    _, ack, _ = send(stack, gm, "fog.reset", {}, expected=stale)
    assert ack["outcome"] == "rejected" and ack["code"] == "stale"
    _, ack, _ = send(stack, gm, "fog.reset", {}, expected=expect(stack, "fog", "fog"))
    assert ack["outcome"] == "applied"
    held = table.now()
    assert ("token", "tok_hero") not in held and not any(bits(held[("fog", "fog")]))


def test_a_repeated_reveal_changes_nothing_but_still_completes(stack):
    gm, table_client = setup(stack)
    table = Table(table_client)
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[3, 3]]})
    before = dict(table.now())
    _, ack, after = send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[3, 3]]})
    assert ack["outcome"] == "applied"
    assert any(e == "vtt.patch" for _, e, _ in after)          # the GM's completion
    assert table.now() == before


def test_a_cell_outside_the_grid_is_refused(stack):
    gm, _ = setup(stack)
    _, ack, _ = send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[32, 0]]})
    assert ack["outcome"] == "rejected" and ack["code"] == "validation-failed"


def test_a_scene_too_large_for_fog_has_no_fog_entity_and_refuses_fog(stack):
    W.create_scene(stack.db, "camp_a", "scene_huge", name="Huge", width=16384, height=16384, grid_size=8,
                   published=True)
    gm, _ = subscribe_gm(stack, scene_id="scene_huge")
    [snap] = [payload(m) for m in received(gm, "vtt.snapshot")]
    assert ("fog", "fog") not in {(e["kind"], e["id"]) for e in snap["entities"]}
    _, ack, _ = send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[0, 0]]}, scene="scene_huge")
    assert ack["outcome"] == "rejected" and ack["code"] == "rule-violation"


def test_changing_the_grid_is_refused_while_anything_is_revealed(stack):
    gm, _ = setup(stack)
    send(stack, gm, "fog.reveal", {"mode": "reveal", "cells": [[1, 1]]})
    _, ack, _ = send(stack, gm, "scene.update", {"gridSize": 70}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "rejected" and ack["code"] == "rule-violation"
    # a rename is not a geometry change
    _, ack, _ = send(stack, gm, "scene.update", {"name": "Renamed"}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    send(stack, gm, "fog.reset", {}, expected=expect(stack, "fog", "fog"))
    _, ack, after = send(stack, gm, "scene.update", {"gridSize": 128}, expected=expect(stack, "scene", "scene_a"))
    assert ack["outcome"] == "applied"
    gm = gm_sid(stack)
    fog_e = next(e for sid, ev, d in after if ev == "vtt.patch" and sid == gm
                 for e in d["upserted"] if e["kind"] == "fog")
    assert (fog_e["cols"], fog_e["rows"]) == (16, 16)


def test_fog_view_rules():
    view = fog.FogView(True, 4, 2, fog.empty_bits(4, 2), 64)
    assert view.hides(10, 10)
    lit = fog.FogView(True, 4, 2, fog.with_cells(view, [[0, 0], [3, 1]], True), 64)
    assert not lit.hides(10, 10) and not lit.hides(255, 127) and lit.hides(100, 10)
    assert lit.cell_of(-5, 99999) == (0, 1)                     # clamped to the grid
    assert not fog.FogView(False, 4, 2, fog.empty_bits(4, 2), 64).hides(10, 10)
    assert fog.grid_of(2048, 2000, 64) == (32, 32) and fog.available(512, 512) and not fog.available(513, 512)


# --- the /map import brings fog across ------------------------------------------------------

def _import(stack, scene):
    from vtt.importer import legacy_scenes as importer
    return importer.import_campaign(stack.db, "camp_a", [scene], None, apply=True)["scenes"][0]


def _fog_row(stack, scene_id="legacy_abc123"):
    from vtt.storage.models import VttFog
    with stack.db.session() as session:
        row = session.get(VttFog, ("camp_a", scene_id))
        return None if row is None else (row.enabled, row.cols, row.rows, bytes(row.revealed))


def test_legacy_revealed_cells_and_fog_on_come_across(stack):
    from .test_m5_import import full_scene
    scene = full_scene()                                         # 1400 x 1000 at 70 px: 20 x 15 cells
    scene["fog"] = {"enabled": True, "operations": [{"mode": "reveal", "x": 1, "y": 1, "radius": 5}],
                    "revealed_cells": ["1,1", "2,1", "40,40", "junk"]}
    report = _import(stack, scene)
    assert report["status"] == "imported"
    assert "1 old fog brush stroke(s)" in report["dropped"]
    assert "2 revealed fog cell(s) outside the scene" in report["dropped"]
    enabled, cols, rows, bits = _fog_row(stack)
    view = fog.FogView(enabled, cols, rows, bits, 70)
    assert (enabled, cols, rows) == (True, 20, 15)
    assert view.revealed(1, 1) and view.revealed(2, 1) and not view.revealed(0, 0)


def test_legacy_fog_on_an_offset_grid_is_reported_not_guessed(stack):
    from .test_m5_import import full_scene
    scene = full_scene()
    scene["grid"]["offset_x"] = 10
    scene["fog"] = {"enabled": True, "operations": [], "revealed_cells": ["1,1"]}
    report = _import(stack, scene)
    assert any("the legacy grid is offset" in d for d in report["dropped"])
    assert _fog_row(stack) is None


def test_no_legacy_fog_writes_no_fog_row(stack):
    from .test_m5_import import full_scene
    assert _import(stack, full_scene())["status"] == "imported"
    assert _fog_row(stack) is None
