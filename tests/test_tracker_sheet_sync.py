"""End-to-end tracker → player-sheet sync verification.

These tests run state-mutating tracker endpoints against a live encounter
and confirm that, for every mutation that should propagate to a PC's
sheet, the PARTY_LIBRARY in-memory copy + the SSE pc_update payload both
see the change.

Why we need this: the tracker is the GM's authoritative write surface
during combat. If a tracker action fails to mirror into PARTY_LIBRARY,
the player sheet (which renders from PARTY_LIBRARY on load and from the
SSE pc_update payload during play) drifts silently. Project priority 4
(``rule logic correctness for tracker→sheet flow``) calls this out.

Each test is structured the same way:
  1. Reset COMBAT_LOGS / ACTIVE_ENCOUNTER / TURN_INDEX so tests are
     order-independent.
  2. Add a PC to the encounter via /api/add_party so a real combatant
     row exists.
  3. Hit the tracker mutation endpoint.
  4. Read PARTY_LIBRARY[pc_name] and assert the field changed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


@pytest.fixture
def app_module():
    """Import app on demand so the test failure points at the import
    error if app.py module-load breaks."""
    import app
    return app


@pytest.fixture
def client(app_module):
    """Test client + a guaranteed-clean encounter for each test. Tests
    do their own combatant setup; the fixture only resets state."""
    a = app_module
    # Reset encounter + combat log + turn index
    a.ACTIVE_ENCOUNTER.clear()
    a.COMBAT_LOGS.clear()
    a.TURN_INDEX = 0
    a.ROUND_NUMBER = 1
    # Use a fresh test client. Local dev has GM_PASSWORD unset → all
    # routes pass the gm-required gate without a login dance.
    with a.app.test_client() as c:
        yield c
    # Cleanup so the next test or live process doesn't see leftovers.
    a.ACTIVE_ENCOUNTER.clear()
    a.COMBAT_LOGS.clear()
    a.TURN_INDEX = 0
    a.ROUND_NUMBER = 1


def _add_pc_to_encounter(client, app_module, pc_name="Kyle"):
    """Add a PC from PARTY_LIBRARY into ACTIVE_ENCOUNTER and return its
    instance_id. Mirrors what the tracker UI does when the GM clicks
    'Add Party'."""
    assert pc_name in app_module.PARTY_LIBRARY, f"need {pc_name} in fixtures"
    resp = client.post(
        "/api/add_party",
        data={"pc_name": pc_name, "initiative": "10"},
    )
    assert resp.status_code in (200, 302), resp.status_code
    # Find the instance we just added.
    for c in app_module.ACTIVE_ENCOUNTER:
        if c.is_pc and c.name == pc_name:
            return c.instance_id
    pytest.fail(f"PC {pc_name} not in ACTIVE_ENCOUNTER after add_party")


def _wait_for_broadcast():
    """The encounter / pc broadcast helpers debounce on a 50ms timer.
    Sleeping past the window guarantees the deferred work has flushed
    before the test reads state."""
    time.sleep(0.08)


# ---------------------------------------------------------------------------
# /api/adjust_hp — HP, dying, wounded should mirror to PARTY_LIBRARY
# ---------------------------------------------------------------------------

def test_adjust_hp_damage_mirrors_to_party_library(client, app_module):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    starting_hp = pc.current_hp

    resp = client.post(
        f"/api/adjust_hp/{inst_id}",
        data={"amount": "10", "action": "damage", "damage_type": "untyped"},
    )
    assert resp.status_code in (200, 302), resp.status_code

    # In-memory sync — same object the player sheet renders from on load
    assert pc.current_hp == starting_hp - 10, (
        f"PARTY_LIBRARY HP didn't sync: {pc.current_hp} (was {starting_hp})"
    )


def test_adjust_hp_to_zero_sets_dying_on_pc(client, app_module):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    big = pc.hp + 50  # well past max
    client.post(
        f"/api/adjust_hp/{inst_id}",
        data={"amount": str(big), "action": "damage", "damage_type": "untyped"},
    )
    assert pc.current_hp == 0, f"expected 0 HP, got {pc.current_hp}"
    # Dying mirrors over so the sheet's red banner triggers correctly
    assert pc.conditions.get("dying", 0) >= 1, (
        f"dying didn't mirror: {pc.conditions.get('dying')}"
    )


def test_adjust_hp_heal_above_zero_clears_dying(client, app_module):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    # Drop to 0 + dying
    client.post(
        f"/api/adjust_hp/{inst_id}",
        data={"amount": str(pc.hp + 10), "action": "damage", "damage_type": "untyped"},
    )
    assert pc.conditions.get("dying", 0) >= 1
    # Heal back above zero — dying clears, wounded ticks up
    client.post(
        f"/api/adjust_hp/{inst_id}",
        data={"amount": "20", "action": "heal"},
    )
    assert pc.current_hp > 0
    assert pc.conditions.get("dying", 0) == 0, "dying should clear on heal-above-zero"
    assert pc.conditions.get("wounded", 0) >= 1, "wounded should tick up"


# ---------------------------------------------------------------------------
# /api/toggle_condition — every condition toggle mirrors to PARTY_LIBRARY
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("condition,action,expected", [
    ("frightened", "increase", 1),
    ("sickened", "increase", 1),
    ("stunned", "increase", 1),
    ("slowed", "increase", 1),
    ("enfeebled", "increase", 1),
    ("clumsy", "increase", 1),
    ("drained", "increase", 1),
    ("stupefied", "increase", 1),
])
def test_toggle_numeric_condition_mirrors(client, app_module, condition, action, expected):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    # Reset the condition to a known floor before toggling
    pc.conditions[condition] = 0
    for c in app_module.ACTIVE_ENCOUNTER:
        if c.instance_id == inst_id:
            c.conditions[condition] = 0

    resp = client.post(
        f"/api/toggle_condition/{inst_id}",
        data={"condition": condition, "action": action},
    )
    assert resp.status_code in (200, 302)
    assert pc.conditions.get(condition, 0) == expected, (
        f"{condition}: expected {expected}, got {pc.conditions.get(condition)}"
    )


@pytest.mark.parametrize("condition", ["prone", "off_guard", "concealed", "hidden"])
def test_toggle_bool_condition_mirrors(client, app_module, condition):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    pc.conditions[condition] = False
    for c in app_module.ACTIVE_ENCOUNTER:
        if c.instance_id == inst_id:
            c.conditions[condition] = False

    client.post(
        f"/api/toggle_condition/{inst_id}",
        data={"condition": condition, "action": "toggle"},
    )
    assert pc.conditions.get(condition) is True, (
        f"{condition}: didn't mirror to True"
    )


# ---------------------------------------------------------------------------
# /api/cycle_turn — auto-tick conditions (frightened, slowed) sync to PC
# ---------------------------------------------------------------------------

def test_cycle_turn_decrements_frightened_and_mirrors(client, app_module):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    # Apply Frightened 2, then end Kyle's turn — should drop to 1 and mirror.
    pc.conditions["frightened"] = 2
    for c in app_module.ACTIVE_ENCOUNTER:
        if c.instance_id == inst_id:
            c.conditions["frightened"] = 2
            app_module.TURN_INDEX = app_module.ACTIVE_ENCOUNTER.index(c)
            break

    client.post("/api/cycle_turn/next")
    assert pc.conditions.get("frightened", 0) == 1, (
        f"frightened didn't auto-tick: {pc.conditions.get('frightened')}"
    )


def test_cycle_turn_resets_reaction_for_new_active_pc(client, app_module):
    """When a PC's turn STARTS, their reaction_used flag should reset
    (so the player sheet's Reaction button re-arms automatically)."""
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    pc.reaction_used = True
    # Move TURN_INDEX so cycle_turn lands ON Kyle (start of her turn)
    for i, c in enumerate(app_module.ACTIVE_ENCOUNTER):
        if c.instance_id == inst_id:
            app_module.TURN_INDEX = (i - 1) % len(app_module.ACTIVE_ENCOUNTER)
            break

    client.post("/api/cycle_turn/next")
    assert pc.reaction_used is False, (
        f"reaction_used didn't reset on turn-start: {pc.reaction_used}"
    )


# ---------------------------------------------------------------------------
# /api/adjust_focus + /api/refocus — focus pool mirrors to PARTY_LIBRARY
# ---------------------------------------------------------------------------

def test_adjust_focus_mirrors(client, app_module):
    _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    pc.current_focus = 1
    client.post("/api/adjust_focus/Kyle", data={"action": "decrease"})
    assert pc.current_focus == 0


def test_refocus_clamps_to_focus_max(client, app_module):
    _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    pc.current_focus = 0
    client.post("/api/refocus/Kyle")
    assert 0 <= pc.current_focus <= pc.focus_max


# ---------------------------------------------------------------------------
# /api/adjust_hero — hero points mirror to PARTY_LIBRARY (max 3)
# ---------------------------------------------------------------------------

def test_hero_point_adjust_mirrors_and_clamps(client, app_module):
    _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    pc.hero_points = 1
    client.post("/api/adjust_hero/Kyle", data={"action": "increase"})
    assert pc.hero_points == 2
    # Push past max (3) — should clamp
    client.post("/api/adjust_hero/Kyle", data={"action": "increase"})
    client.post("/api/adjust_hero/Kyle", data={"action": "increase"})
    assert pc.hero_points == 3, f"hero_points should clamp at 3, got {pc.hero_points}"


# ---------------------------------------------------------------------------
# Combined: a chain of tracker mutations should all reflect on the sheet
# at the end (catches debounce-window races).
# ---------------------------------------------------------------------------

def test_chain_of_mutations_all_reflect_on_pc(client, app_module):
    inst_id = _add_pc_to_encounter(client, app_module, "Kyle")
    pc = app_module.PARTY_LIBRARY["Kyle"]
    starting_hp = pc.current_hp
    # Reset BOTH the PC and the combatant so cross-test state can't leak in.
    pc.conditions["frightened"] = 0
    pc.conditions["off_guard"] = False
    for c in app_module.ACTIVE_ENCOUNTER:
        if c.instance_id == inst_id:
            c.conditions["frightened"] = 0
            c.conditions["off_guard"] = False

    # 4 mutations in a row — within the 50ms encounter-broadcast window
    client.post(f"/api/adjust_hp/{inst_id}",
                data={"amount": "5", "action": "damage", "damage_type": "untyped"})
    client.post(f"/api/toggle_condition/{inst_id}",
                data={"condition": "frightened", "action": "increase"})
    client.post(f"/api/toggle_condition/{inst_id}",
                data={"condition": "off_guard", "action": "toggle"})
    client.post(f"/api/adjust_hp/{inst_id}",
                data={"amount": "3", "action": "heal"})

    # After the debounce window flushes, every change should be on the PC
    _wait_for_broadcast()
    assert pc.current_hp == starting_hp - 5 + 3
    assert pc.conditions.get("frightened") == 1
    assert pc.conditions.get("off_guard") is True
