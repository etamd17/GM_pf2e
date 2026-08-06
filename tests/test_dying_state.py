"""Regression tests for the PF2e dying / wounded / doomed death-spiral.

These lock the life-or-death escalation that runs when a PC's HP is driven to 0
through the two server entry points that share the logic:

  * ``/api/adjust_party_hp/<pc_name>`` — the player-facing sheet button
    (app.py ~9740, the ``_mutate`` closure ~9770).
  * ``/api/adjust_hp/<instance_id>``  — the GM combat-tracker button, PC branch
    (app.py ~9610, the ``c.is_pc and c.name in PARTY_LIBRARY`` path ~9673).

PF2e Remaster rules being asserted:
  * Dropping to 0 HP from above 0 sets Dying = 1 + current Wounded value.
  * Taking damage while already at 0 increments Dying by 1.
  * The death threshold is ``max(1, 4 - doomed)``; reaching it = dead, and Dying
    is clamped there (never exceeds it).
  * Healing back above 0 clears Dying and adds 1 Wounded.

CI-safety: no live ``party_data`` / ``PARTY_LIBRARY`` is required. Each test
builds a PC from a committed fixture and injects it into ``PARTY_LIBRARY`` (and,
for the tracker path, ``ACTIVE_ENCOUNTER``) with save/restore in fixtures /
try-finally. ``get_pc_file_path`` is redirected at a tmp file so the route's
persistence step never touches a real character file.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX_DIR = pathlib.Path(__file__).parent / 'fixtures'
_KYLE_FIX = _FIX_DIR / 'kyle_l10.json'
_GOEL_FIX = _FIX_DIR / 'goel_l10.json'

_AJAX = {'X-Requested-With': 'XMLHttpRequest'}


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
def _load_pc(fixture_path, pc_file):
    raw = json.loads(fixture_path.read_text())
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    return Character(raw, file_path=str(pc_file))


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    """A fresh Kyle (l10, 138 HP, no W/R/I) registered in PARTY_LIBRARY.

    Returns the PC's registry name. The character object is reachable via
    ``app_module.PARTY_LIBRARY[name]`` so tests can inspect conditions directly.
    """
    pc_file = tmp_path / 'Kyle.json'
    pc = _load_pc(_KYLE_FIX, pc_file)
    name = pc.name
    monkeypatch.setitem(app_module.PARTY_LIBRARY, name, pc)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == name else None)
    # Sanity: the fixture starts clean and at full HP so the escalation begins
    # from a known baseline.
    assert pc.current_hp == pc.hp
    assert pc.conditions.get('dying', 0) == 0
    assert pc.conditions.get('wounded', 0) == 0
    assert pc.conditions.get('doomed', 0) == 0
    return name


@pytest.fixture
def goel(tmp_path, monkeypatch):
    """A fresh Go'el (l10) registered in PARTY_LIBRARY. Returns the registry
    name (note the name carries a class suffix, so the tracker path's
    ``c.name in PARTY_LIBRARY`` keys on the exact value)."""
    pc_file = tmp_path / 'Goel.json'
    pc = _load_pc(_GOEL_FIX, pc_file)
    name = pc.name
    monkeypatch.setitem(app_module.PARTY_LIBRARY, name, pc)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == name else None)
    return name


@pytest.fixture
def client():
    return app_module.app.test_client()


# --------------------------------------------------------------------------
# Helpers — player-facing /api/adjust_party_hp (returns JSON directly)
# --------------------------------------------------------------------------
def _party_damage(client, name, amount):
    r = client.post(f'/api/adjust_party_hp/{name}',
                    data={'amount': amount, 'action': 'damage'}, headers=_AJAX)
    assert r.status_code == 200, r.data
    return r.get_json()


def _party_heal(client, name, amount):
    r = client.post(f'/api/adjust_party_hp/{name}',
                    data={'amount': amount, 'action': 'heal'}, headers=_AJAX)
    assert r.status_code == 200, r.data
    return r.get_json()


# ==========================================================================
# PART 1 — /api/adjust_party_hp (player sheet path)
# ==========================================================================

def test_drop_to_zero_sets_dying_one(kyle, client):
    """A clean PC dropped to 0 HP from full enters Dying 1 (no Wounded yet)."""
    r = _party_damage(client, kyle, 9999)
    assert r['current_hp'] == 0
    assert r['dying'] == 1
    assert r['wounded'] == 0
    assert r['dead'] is False


def test_damage_at_zero_increments_dying(kyle, client):
    """Each hit while at 0 HP bumps Dying by exactly 1, up to the threshold."""
    _party_damage(client, kyle, 9999)            # -> dying 1
    assert _party_damage(client, kyle, 5)['dying'] == 2
    assert _party_damage(client, kyle, 5)['dying'] == 3
    r = _party_damage(client, kyle, 5)           # -> dying 4 == threshold
    assert r['dying'] == 4
    assert r['dead'] is True


def test_dead_at_dying_four_with_no_doomed(kyle, client):
    """With doomed 0 the death threshold is 4; dead flips true exactly there."""
    _party_damage(client, kyle, 9999)
    assert _party_damage(client, kyle, 1)['dead'] is False   # dying 2
    assert _party_damage(client, kyle, 1)['dead'] is False   # dying 3
    assert _party_damage(client, kyle, 1)['dead'] is True    # dying 4


def test_dying_clamps_at_threshold(kyle, client):
    """Dying never climbs above max(1, 4 - doomed): extra hits past death
    leave it pinned at 4 rather than 5, 6, ..."""
    _party_damage(client, kyle, 9999)
    for _ in range(6):
        r = _party_damage(client, kyle, 5)
    assert r['dying'] == 4
    assert r['dead'] is True


def test_drop_to_zero_with_existing_wounded(kyle, client):
    """Dropping to 0 sets Dying = 1 + Wounded. A PC already Wounded 2 enters
    the spiral at Dying 3 (PF2e Remaster Wounded interaction)."""
    app_module.PARTY_LIBRARY[kyle].conditions['wounded'] = 2
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 3
    assert r['wounded'] == 2          # the drop reads Wounded, doesn't change it
    assert r['dead'] is False         # 3 < threshold 4


def test_wounded_one_drops_in_at_dying_two(kyle, client):
    """Wounded 1 -> first drop to 0 is Dying 2."""
    app_module.PARTY_LIBRARY[kyle].conditions['wounded'] = 1
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 2
    assert r['dead'] is False


def test_doomed_one_dies_at_dying_three(kyle, client):
    """Doomed 1 lowers the death threshold to 3: the PC dies one Dying step
    earlier and Dying clamps at 3."""
    app_module.PARTY_LIBRARY[kyle].conditions['doomed'] = 1
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 1 and r['dead'] is False
    assert _party_damage(client, kyle, 5)['dying'] == 2          # not yet dead
    r = _party_damage(client, kyle, 5)
    assert r['dying'] == 3 and r['dead'] is True                 # threshold hit
    r = _party_damage(client, kyle, 5)
    assert r['dying'] == 3                                       # clamped at 3


def test_doomed_three_dies_on_first_drop(kyle, client):
    """Doomed 3 -> threshold max(1, 4-3) = 1: a single drop to 0 is lethal."""
    app_module.PARTY_LIBRARY[kyle].conditions['doomed'] = 3
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 1
    assert r['dead'] is True


def test_doomed_above_three_floors_threshold_at_one(kyle, client):
    """The max(1, ...) floor means doomed 5 still has threshold 1, not -1/0."""
    app_module.PARTY_LIBRARY[kyle].conditions['doomed'] = 5
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 1
    assert r['dead'] is True


def test_heal_above_zero_clears_dying_adds_wounded(kyle, client):
    """Healing back above 0 clears Dying and ticks Wounded up by one."""
    _party_damage(client, kyle, 9999)            # dying 1, wounded 0
    assert app_module.PARTY_LIBRARY[kyle].conditions['wounded'] == 0
    r = _party_heal(client, kyle, 10)
    assert r['current_hp'] == 10
    assert r['dying'] == 0
    assert r['wounded'] == 1


def test_heal_of_zero_does_not_clear_dying(kyle, client):
    """Healing 0 leaves HP at 0, so Dying must persist (the clear is gated on
    HP rising above 0)."""
    _party_damage(client, kyle, 9999)
    r = _party_heal(client, kyle, 0)
    assert r['current_hp'] == 0
    assert r['dying'] == 1
    assert r['wounded'] == 0


def test_heal_while_not_dying_does_not_add_wounded(kyle, client):
    """A heal on a PC that was never Dying must not invent a Wounded tick."""
    app_module.PARTY_LIBRARY[kyle].current_hp = 50
    r = _party_heal(client, kyle, 10)
    assert r['current_hp'] == 60
    assert r['wounded'] == 0
    assert r['dying'] == 0


def test_heal_caps_at_max_hp(kyle, client):
    """Overhealing is clamped to max HP (no overflow into temp HP here)."""
    pc = app_module.PARTY_LIBRARY[kyle]
    pc.current_hp = 50
    r = _party_heal(client, kyle, 10000)
    assert r['current_hp'] == pc.hp


def test_partial_damage_never_sets_dying(kyle, client):
    """Damage that leaves HP above 0 must not touch Dying."""
    pc = app_module.PARTY_LIBRARY[kyle]
    r = _party_damage(client, kyle, pc.hp - 1)
    assert r['current_hp'] == 1
    assert r['dying'] == 0
    assert r['dead'] is False


def test_zero_damage_is_a_noop(kyle, client):
    """A 0-damage hit changes nothing."""
    pc = app_module.PARTY_LIBRARY[kyle]
    r = _party_damage(client, kyle, 0)
    assert r['current_hp'] == pc.hp
    assert r['dying'] == 0


def test_damage_from_already_zero_without_dying_starts_dying(kyle, client):
    """A PC sitting at exactly 0 HP with Dying 0 (was_above_zero == False) takes
    the increment branch: Dying becomes 1."""
    pc = app_module.PARTY_LIBRARY[kyle]
    pc.current_hp = 0
    pc.conditions['dying'] = 0
    r = _party_damage(client, kyle, 5)
    assert r['dying'] == 1


def test_massive_overkill_single_hit_only_sets_dying_one(kyle, client):
    """One enormous hit (well over 2x max HP) still only sets Dying 1 — this
    app does NOT implement PF2e's massive-damage instant-death rule."""
    # POSSIBLE BUG: PF2e RAW says damage >= 2x max HP from a single source kills
    # outright (and >= 2x while at 0 HP from a hit can too). This path only sets
    # Dying 1, so a 1000-damage crit is survivable as Dying 1. Locking current
    # behavior; flagging as a rules gap.
    pc = app_module.PARTY_LIBRARY[kyle]
    r = _party_damage(client, kyle, pc.hp * 3)
    assert r['current_hp'] == 0
    assert r['dying'] == 1
    assert r['dead'] is False


def test_full_death_spiral_sequence(kyle, client):
    """End-to-end: full -> down -> recover (heal) -> down again starts higher
    because Wounded climbed."""
    # First knockout: dying 1, wounded still 0.
    assert _party_damage(client, kyle, 9999)['dying'] == 1
    # Recover via heal: dying clears, wounded -> 1.
    r = _party_heal(client, kyle, 5)
    assert r['dying'] == 0 and r['wounded'] == 1
    # Second knockout reads Wounded 1, so it enters at Dying 2.
    r = _party_damage(client, kyle, 9999)
    assert r['dying'] == 2 and r['wounded'] == 1


# ==========================================================================
# PART 2 — /api/adjust_hp (GM combat-tracker path, PC branch)
# ==========================================================================
#
# These exercise the same death-spiral through the tracker route, which routes
# a PC (one whose name is in PARTY_LIBRARY) through apply_pc_delta and then bumps
# Dying. ACTIVE_ENCOUNTER + TURN_INDEX are saved/restored and the tracker cache
# invalidated, per the established test pattern.

class _Encounter:
    """Context manager that puts ``pc`` (with a known instance_id) alone in the
    active encounter, then restores the prior encounter state on exit."""

    def __init__(self, pc, instance_id='dying-test-1'):
        self.pc = pc
        self.instance_id = instance_id

    def __enter__(self):
        self._saved = list(app_module.ACTIVE_ENCOUNTER)
        self._saved_idx = app_module.TURN_INDEX
        self.pc.instance_id = self.instance_id
        app_module.ACTIVE_ENCOUNTER[:] = [self.pc]
        app_module.TURN_INDEX = 0
        app_module._invalidate_tracker_cache()
        return self

    def __exit__(self, *exc):
        app_module.ACTIVE_ENCOUNTER[:] = self._saved
        app_module.TURN_INDEX = self._saved_idx
        app_module._invalidate_tracker_cache()
        return False


def _tracker_damage(client, instance_id, amount, damage_type='untyped'):
    r = client.post(f'/api/adjust_hp/{instance_id}',
                    data={'amount': amount, 'action': 'damage',
                          'damage_type': damage_type}, headers=_AJAX)
    assert r.status_code == 200, r.data
    return r


def _tracker_heal(client, instance_id, amount):
    r = client.post(f'/api/adjust_hp/{instance_id}',
                    data={'amount': amount, 'action': 'heal'}, headers=_AJAX)
    assert r.status_code == 200, r.data
    return r


def test_tracker_drop_to_zero_sets_dying_one(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        assert pc.current_hp == 0
        assert pc.conditions['dying'] == 1
        assert pc.conditions['wounded'] == 0


def test_tracker_damage_at_zero_increments_dying(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)   # dying 1
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 2
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 3
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 4


def test_tracker_dying_clamps_at_threshold(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        for _ in range(6):
            _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 4


def test_tracker_drop_with_existing_wounded(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    pc.conditions['wounded'] = 1
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        assert pc.conditions['dying'] == 2          # 1 + wounded
        assert pc.conditions['wounded'] == 1


def test_tracker_doomed_one_dies_at_dying_three(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    pc.conditions['doomed'] = 1
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        assert pc.conditions['dying'] == 1
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 2
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 3          # threshold hit
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.conditions['dying'] == 3          # clamped at 3, not 4


def test_tracker_heal_above_zero_clears_dying_adds_wounded(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        assert pc.conditions['dying'] == 1
        _tracker_heal(client, enc.instance_id, 20)
        assert pc.current_hp == 20
        assert pc.conditions['dying'] == 0
        assert pc.conditions['wounded'] == 1


def test_tracker_partial_damage_never_sets_dying(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 5)
        assert pc.current_hp == pc.hp - 5
        assert pc.conditions['dying'] == 0


def test_tracker_state_reports_dead_for_dying_pc(goel, client):
    """The tracker JSON state mirrors the PC's Dying value so the GM screen
    shows the correct death-spiral position."""
    pc = app_module.PARTY_LIBRARY[goel]
    pc.conditions['doomed'] = 1
    with _Encounter(pc) as enc:
        # Drive to the doomed-1 death threshold (dying 3).
        for amt in (9999, 5, 5):
            _tracker_damage(client, enc.instance_id, amt)
        state = app_module._get_tracker_state()
        ent = next(c for c in state['combatants']
                   if c['instance_id'] == enc.instance_id)
        assert ent['current_hp'] == 0
        assert ent['conditions'].get('dying') == 3
        assert ent['conditions'].get('doomed') == 1


def test_tracker_and_party_paths_agree(goel, client):
    """The tracker PC branch and the player-sheet path must produce the same
    Dying/Wounded outcome from the same damage sequence — they share the rule
    but live in two code sites, so this guards them against drifting."""
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)
        _tracker_damage(client, enc.instance_id, 5)
        tracker_dying = pc.conditions['dying']
        tracker_wounded = pc.conditions['wounded']

    # Reset the same PC and run the equivalent player-sheet sequence.
    pc.current_hp = pc.hp
    pc.conditions['dying'] = 0
    pc.conditions['wounded'] = 0
    _party_damage(client, goel, 9999)
    r = _party_damage(client, goel, 5)
    assert r['dying'] == tracker_dying == 2
    assert r['wounded'] == tracker_wounded == 0


# ==========================================================================
# PART 3 — /api/adjust_hp reports the NET hp delta applied
# ==========================================================================
#
# The damage/heal response carries an `applied` block {net, raw} so the client
# toast shows what actually landed (after resistances / weaknesses / temp HP /
# overkill + heal clamping), not the raw amount typed. `net` is the real
# hp-pool change, so it equals `raw` only when nothing reduced or clamped it.

def _applied(r):
    return (r.get_json() or {}).get('applied') or {}


def test_applied_net_equals_raw_for_partial_damage(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        a = _applied(_tracker_damage(client, enc.instance_id, 7))
        assert a.get('raw') == 7 and a.get('net') == 7      # nothing reduced it
        assert pc.current_hp == pc.hp - 7


def test_applied_net_caps_at_hp_lost_on_overkill(goel, client):
    """A 9999 hit on a PC reports net = the HP actually lost (capped at 0), so
    the toast reads "Took <hp>" rather than "Took 9999"."""
    pc = app_module.PARTY_LIBRARY[goel]
    hp_before = pc.hp
    with _Encounter(pc) as enc:
        a = _applied(_tracker_damage(client, enc.instance_id, 9999))
        assert a.get('raw') == 9999
        assert a.get('net') == hp_before
        assert pc.current_hp == 0


def test_applied_net_caps_at_healing_that_fit(goel, client):
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        _tracker_damage(client, enc.instance_id, 9999)       # -> 0
        a = _applied(_tracker_heal(client, enc.instance_id, 10000))
        assert a.get('raw') == 10000
        assert a.get('net') == pc.hp                          # only max HP fit
        assert pc.current_hp == pc.hp


def test_applied_net_zero_for_heal_at_full(goel, client):
    """Healing a full-HP PC moves nothing, so net is 0 (truthful "Healed 0")."""
    pc = app_module.PARTY_LIBRARY[goel]
    with _Encounter(pc) as enc:
        a = _applied(_tracker_heal(client, enc.instance_id, 25))
        assert a.get('net') == 0
        assert pc.current_hp == pc.hp
