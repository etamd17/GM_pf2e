"""Turn-advance start-of-turn processing parity (readiness audit fix).

The PF2e start-of-new-turn block (action-economy reset, Slowed action
ceiling, Stunned action loss, reaction reset, monster persistent-damage
auto-roll) lived only inside cycle_turn('next'). delay_turn advanced the
pointer but skipped it, so the combatant that became active after a GM
clicked "Delay" behaved differently than one reached via "Next Turn" --
stale action pips, no Slowed/Stunned math, un-rolled persistent damage.
Both paths must now run the same extracted _apply_start_of_turn(new_c).
"""
from __future__ import annotations

import pytest

import app as app_module


class _Mon:
    """Monster-shaped combatant stub for the start-of-turn paths (no
    PARTY_LIBRARY / sheet coupling)."""

    def __init__(self, name, hp=30, conditions=None, persistent_damage='',
                 instance_id=None):
        self.instance_id = instance_id or (name + '-1')
        self.name = name
        self.is_pc = False
        self.system = 'pf2e'
        self.hp = hp
        self.current_hp = hp
        self.conditions = dict(conditions or {})
        self.condition_expiry = {}
        self.persistent_damage = persistent_damage
        self.delaying = False
        self.initiative = 10
        self.max_actions = 3
        self.actions_used = 0
        self.reaction_used = True   # start "used" so we can see it reset
        self.immunities = []
        self.resistances = []
        self.weaknesses = []


@pytest.fixture
def enc(monkeypatch):
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    a = _Mon('Aaa')
    b = _Mon('Bbb')
    app_module.ACTIVE_ENCOUNTER[:] = [a, b]
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module.ROUND_EVENTS[:] = []
    yield {'a': a, 'b': b}
    app_module.ACTIVE_ENCOUNTER[:] = []
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module.ROUND_EVENTS[:] = []


def _client():
    return app_module.app.test_client()


def test_delay_applies_slowed_action_ceiling_to_next(enc):
    """Bbb is Slowed 1. When Aaa delays into Bbb's turn, Bbb's action
    ceiling must drop to 2 (was silently 3)."""
    enc['b'].conditions['slowed'] = 1
    enc['b'].max_actions = 3
    _client().post('/api/delay_turn/' + enc['a'].instance_id)
    assert app_module.ACTIVE_ENCOUNTER[app_module.TURN_INDEX].name == 'Bbb'
    assert enc['b'].max_actions == 2
    assert enc['b'].actions_used == 0


def test_delay_auto_rolls_persistent_damage_on_next(enc):
    """Bbb has persistent 2d6 fire. Delaying into Bbb must auto-roll it
    (monster path), like Next Turn does."""
    enc['b'].persistent_damage = '2d6 fire'
    start_hp = enc['b'].current_hp
    _client().post('/api/delay_turn/' + enc['a'].instance_id)
    assert enc['b'].current_hp < start_hp, 'persistent damage never auto-rolled'
    assert start_hp - enc['b'].current_hp <= 12


def test_delay_spends_stunned_actions_on_next(enc):
    enc['b'].conditions['stunned'] = 2
    _client().post('/api/delay_turn/' + enc['a'].instance_id)
    assert enc['b'].actions_used == 2         # 2 actions pre-spent
    assert enc['b'].conditions['stunned'] == 0
    assert enc['b'].max_actions == 3


def test_delay_resets_next_monster_reaction(enc):
    enc['b'].reaction_used = True
    _client().post('/api/delay_turn/' + enc['a'].instance_id)
    assert enc['b'].reaction_used is False


def test_delay_and_next_reach_identical_start_state(enc):
    """Parity: whether Bbb becomes active via Delay or Next Turn, its
    start-of-turn state must be identical."""
    enc['b'].conditions['slowed'] = 1
    enc['b'].conditions['stunned'] = 1
    enc['b'].persistent_damage = ''   # keep HP deterministic for the compare
    # Path 1: Next Turn.
    _client().post('/api/cycle_turn/next')
    via_next = (enc['b'].max_actions, enc['b'].actions_used,
                enc['b'].conditions.get('stunned'), enc['b'].reaction_used)
    # Reset and take Path 2: Delay.
    enc['b'].conditions['slowed'] = 1
    enc['b'].conditions['stunned'] = 1
    enc['b'].max_actions = 3
    enc['b'].actions_used = 0
    enc['b'].reaction_used = True
    app_module.TURN_INDEX = 0
    app_module.ACTIVE_ENCOUNTER[0].delaying = False
    _client().post('/api/delay_turn/' + enc['a'].instance_id)
    via_delay = (enc['b'].max_actions, enc['b'].actions_used,
                 enc['b'].conditions.get('stunned'), enc['b'].reaction_used)
    assert via_next == via_delay, (via_next, via_delay)
