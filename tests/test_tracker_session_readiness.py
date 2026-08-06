"""Tracker session-readiness batch (2026-07-14):

1. A prominent 'End Encounter' button that works regardless of initiative state.
2. A non-PC combatant reduced to 0 HP is auto-removed from the encounter, with a
   short Undo (restore) path. PCs are never auto-removed (they enter dying).
3. Monster ability rows on the tracker inspector show their action cost.
"""
from __future__ import annotations

import os

import pytest

import app as app_module

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Mon:
    """Minimal combatant stub for the HP/damage path (no sheet coupling)."""

    def __init__(self, name, hp=30, is_pc=False, instance_id=None, actions=None):
        self.instance_id = instance_id or (name + '-1')
        self.name = name
        self.is_pc = is_pc
        self.system = 'pf2e'
        self.hp = hp
        self.current_hp = hp
        self.conditions = {}
        self.condition_expiry = {}
        self.persistent_damage = ''
        self.delaying = False
        self.initiative = 10
        self.level = 1
        self.ac = 15
        self.fort = self.ref = self.will = 5
        self.perception = 5
        self.max_actions = 3
        self.actions_used = 0
        self.reaction_used = False
        self.immunities = []
        self.resistances = []
        self.weaknesses = []
        self.strikes = []
        self.actions = actions if actions is not None else []
        self.elite_weak = 0
        self.traits = []


@pytest.fixture
def enc(monkeypatch):
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    # Keep the tracker-state cache from masking mutations between calls.
    monkeypatch.setattr(app_module, '_TRACKER_STATE_TTL', 0, raising=False)
    app_module.ACTIVE_ENCOUNTER[:] = []
    app_module._RECENT_DEFEATED[:] = []
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    yield
    app_module.ACTIVE_ENCOUNTER[:] = []
    app_module._RECENT_DEFEATED[:] = []
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1


def _client():
    return app_module.app.test_client()


def _dmg(instance_id, amount, action='damage'):
    return _client().post('/api/adjust_hp/' + instance_id,
                          data={'amount': str(amount), 'action': action, 'damage_type': 'untyped'},
                          headers={'X-Requested-With': 'XMLHttpRequest'})


# ---------------------------------------------------------------------------
# (1) End encounter works with no initiative rolled
# ---------------------------------------------------------------------------

def test_clear_encounter_works_without_initiative(enc):
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin'), _Mon('Orc')]
    app_module.TURN_INDEX = 0          # never rolled initiative / never advanced a turn
    app_module.ROUND_NUMBER = 1
    r = _client().post('/api/clear_encounter', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 200
    assert len(app_module.ACTIVE_ENCOUNTER) == 0


def test_tracker_header_has_prominent_end_button():
    """The End/Clear action must live in the always-visible header, not only in
    the Tools overflow menu."""
    html = open(os.path.join(_REPO, 'templates', 'tracker.html')).read()
    header = html[html.index('class="hb-right"'):html.index('id="trk-tools-menu"')]
    assert 'clear_encounter' in header, 'no header-level End Encounter button (only in Tools menu)'


# ---------------------------------------------------------------------------
# (2) Auto-remove non-PC at 0 HP, with Undo
# ---------------------------------------------------------------------------

def test_monster_at_zero_hp_is_removed(enc):
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin', hp=5), _Mon('Bystander', hp=20)]
    r = _dmg('Goblin-1', 5)
    assert r.status_code == 200
    ids = [c.instance_id for c in app_module.ACTIVE_ENCOUNTER]
    assert 'Goblin-1' not in ids, 'monster at 0 HP was not removed'
    body = r.get_json()
    assert body.get('defeated', {}).get('instance_id') == 'Goblin-1', 'response did not flag the defeat for the Undo toast'


def test_pc_at_zero_hp_is_not_removed(enc):
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Hero', hp=5, is_pc=True)]
    _dmg('Hero-1', 5)
    ids = [c.instance_id for c in app_module.ACTIVE_ENCOUNTER]
    assert 'Hero-1' in ids, 'a PC must never be auto-removed (dying track), only monsters'


def test_heal_never_removes(enc):
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin', hp=5)]
    app_module.ACTIVE_ENCOUNTER[0].current_hp = 0
    _dmg('Goblin-1', 3, action='heal')
    assert any(c.instance_id == 'Goblin-1' for c in app_module.ACTIVE_ENCOUNTER)


def test_removing_earlier_combatant_keeps_active(enc):
    a, b, cc = _Mon('A', hp=5), _Mon('B'), _Mon('C')
    app_module.ACTIVE_ENCOUNTER[:] = [a, b, cc]
    app_module.TURN_INDEX = 2          # C is acting
    _dmg('A-1', 5)                     # kill the first combatant
    assert app_module.ACTIVE_ENCOUNTER[app_module.TURN_INDEX].instance_id == 'C-1', \
        'removing an earlier combatant shifted the active turn'


def test_restore_defeated_readds_at_prior_hp(enc):
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin', hp=8), _Mon('Bystander')]
    _dmg('Goblin-1', 8)
    assert not any(c.instance_id == 'Goblin-1' for c in app_module.ACTIVE_ENCOUNTER)
    r = _client().post('/api/restore_defeated/Goblin-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 200
    restored = next((c for c in app_module.ACTIVE_ENCOUNTER if c.instance_id == 'Goblin-1'), None)
    assert restored is not None, 'Undo did not restore the monster'
    assert restored.current_hp == 8, 'restore should undo the killing blow (back to prior HP)'


def test_restore_clears_death_dying_artifact(enc):
    """A monster killed at 0 HP gets a 'dying' stamp from the damage path; Undo
    must bring it back clean, not Dying at full HP."""
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin', hp=6)]
    _dmg('Goblin-1', 6)
    _client().post('/api/restore_defeated/Goblin-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    restored = next(c for c in app_module.ACTIVE_ENCOUNTER if c.instance_id == 'Goblin-1')
    assert not restored.conditions.get('dying'), 'restored monster is still flagged Dying'


def test_restore_unknown_is_404(enc):
    r = _client().post('/api/restore_defeated/nope-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 404


def test_cosmere_adversary_not_auto_removed(enc):
    """A Cosmere adversary at 0 HP enters the unconscious/injury death-spiral and
    must STAY on the tracker -- auto-remove is PF2e-only."""
    m = _Mon('Voidbringer', hp=10)
    m.system = 'cosmere'
    m.current_hp = 0
    app_module.ACTIVE_ENCOUNTER[:] = [m]
    res = app_module._maybe_auto_remove_defeated('Voidbringer-1', 10, 'damage')
    assert res is None
    assert any(c.instance_id == 'Voidbringer-1' for c in app_module.ACTIVE_ENCOUNTER), \
        'a Cosmere adversary was wrongly auto-removed at 0 HP'


def test_clear_encounter_drops_pending_undo(enc):
    """Ending the encounter must clear the Undo stash so a lingering toast can't
    resurrect a monster into the ended/next encounter."""
    app_module.ACTIVE_ENCOUNTER[:] = [_Mon('Goblin', hp=5)]
    _dmg('Goblin-1', 5)
    assert app_module._RECENT_DEFEATED, 'precondition: a defeat should be stashed'
    _client().post('/api/clear_encounter', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert not app_module._RECENT_DEFEATED, 'End did not clear the Undo stash'
    r = _client().post('/api/restore_defeated/Goblin-1', headers={'X-Requested-With': 'XMLHttpRequest'})
    assert r.status_code == 404, 'a stale Undo still resurrects into the ended encounter'


def test_costpips_handles_variable_action_range():
    """foundry_action_cost emits range glyphs (e.g. '◆-◆◆◆' = 1 to 3 actions) for
    dozens of real monster abilities; costPips must render them, not drop them."""
    html = open(os.path.join(_REPO, 'templates', 'tracker.html')).read()
    fn = html[html.index('function costPips('):]
    fn = fn[:fn.index('\n}') + 2]
    assert "indexOf('-')" in fn or 'split(/[-' in fn, \
        'costPips does not handle a variable/range action cost'


# ---------------------------------------------------------------------------
# (3) Monster ability action cost on the tracker
# ---------------------------------------------------------------------------

def test_tracker_payload_carries_ability_action_cost(enc):
    mon = _Mon('Dragon', actions=[{'name': 'Breath Weapon', 'description': 'Cone of fire.', 'actions': '2'}])
    app_module.ACTIVE_ENCOUNTER[:] = [mon]
    app_module._TRACKER_STATE_CACHE = None
    state = app_module._get_tracker_state()
    dragon = state['combatants'][0]
    ability = dragon['actions'][0]
    assert ability.get('actions') == '2', 'ability action cost dropped from the tracker payload'


def test_monster_parse_extracts_action_cost():
    """The Monster action parser must read the Foundry action cost (not just
    name + description)."""
    src = open(os.path.join(_REPO, 'app.py')).read()
    line = src[src.index("self.actions.append({'name': name"):]
    line = line[:line.index('\n')]
    assert 'foundry_action_cost' in line, 'monster action parse does not capture the action cost'


def test_closed_modal_cannot_swallow_clicks():
    """The invisible-dead-zone bug: the closed stat side-panel (#stat-modal) hid
    via opacity+pointer-events:none, but its INNER .trk-modal-panel set an
    unconditional pointer-events:auto -- and a descendant's `auto` overrides an
    ancestor's `none`. The invisible 440px panel swallowed every click on the
    right edge of the tracker (End button, inspector +/- HP, condition inputs).

    Invariants: (1) the closed .trk-modal base state uses visibility:hidden so NO
    descendant can hit-test; (2) the side-panel's inner pointer-events:auto is
    scoped to the .open state only."""
    css = open(os.path.join(_REPO, 'templates', 'tracker.html')).read()

    def block(selector):
        i = css.index(selector + ' {')
        return css[i:css.index('}', i)]

    base = block('.trk-modal')
    assert 'visibility:hidden' in base.replace(' ', ''), \
        'closed .trk-modal is not visibility:hidden -- a child with pointer-events:auto can eat clicks'
    assert 'visibility:visible' in block('.trk-modal.open').replace(' ', '')
    side_inner = block('.trk-modal.side-panel .trk-modal-panel')
    assert 'pointer-events: auto' not in side_inner and 'pointer-events:auto' not in side_inner.replace(' ', ''), \
        'closed side-panel inner still forces pointer-events:auto (the dead-zone bug)'
    assert 'pointer-events: auto' in block('.trk-modal.side-panel.open .trk-modal-panel'), \
        'open side-panel lost its pointer-events -- the stat panel would be uninteractable'


def test_tracker_ability_render_shows_cost():
    html = open(os.path.join(_REPO, 'templates', 'tracker.html')).read()
    # The NPC "Abilities" render must pip the cost like strikes/spells already do.
    seg = html[html.index('// NPC abilities'):]
    seg = seg[:seg.index('// PC Feats')]
    assert 'costPips(a.actions)' in seg, 'NPC ability rows do not show the action cost'
