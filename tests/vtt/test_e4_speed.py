"""E-4: Speed per system, in feet per move, for measuring a dragged token on
/map and in the VTT. The live index used to read base_speed (absent on a
monster, so 0) and Cosmere's Speed attribute score (shown as feet)."""

from __future__ import annotations

from types import SimpleNamespace

import app as app_module
import systems.cosmere


def test_pf2e_reads_the_current_speed_of_a_pc_and_the_statblock_speed_of_a_monster():
    pc = SimpleNamespace(system="pf2e", base_speed=30, active_speed=20, speed=20)       # e.g. encumbered
    assert app_module._live_speed_feet(pc) == 20
    monster = app_module.Monster({"name": "M", "type": "npc",
                                  "system": {"attributes": {"speed": {"value": 35}}, "traits": {"value": []}}})
    assert app_module._live_speed_feet(monster) == 35
    assert app_module._live_speed_feet(SimpleNamespace(system="pf2e")) == 0                 # a hazard: unknown


def test_cosmere_reads_the_movement_rate_not_the_attribute():
    docs = {d["name"]: d for d in systems.cosmere.adversary_docs()}
    archer = systems.cosmere.CosmereActor(docs["Archer"])
    assert archer.attributes.get("spd") == 1 and app_module._live_speed_feet(archer) == 25
    axehound = systems.cosmere.CosmereActor(docs["Axehound"])            # its statblock overrides the rate
    assert app_module._live_speed_feet(axehound) == 40


def test_cosmere_bonus_and_score_bands():
    def actor(score, **rate):
        return SimpleNamespace(system="cosmere", attributes={"spd": score},
                               _sys={"movement": {"walk": {"rate": {"bonus": 0, "override": None,
                                                                    "useOverride": False, **rate}}}})

    assert [app_module._live_speed_feet(actor(s)) for s in (0, 3, 5, 9)] == [20, 30, 40, 80]
    assert app_module._live_speed_feet(actor(3, bonus=10)) == 40

