"""Cosmere rest & recovery (Ch.9). Covers the pure recovery-die table and the
rest helper that restores health/focus and clears the right conditions, writing
through to a PC's play_state. (PF2e had a rest wizard; Cosmere had none, so a
Stormlight session could not resolve a rest in-app.)
"""
from __future__ import annotations

import copy

import app
import systems.cosmere.build as cb
import systems.cosmere.combat as cc

_BUILD = {
    'name': 'Kal', 'level': 3, 'ancestry': 'Human', 'path': 'Warrior',
    'attributes': {'str': 3, 'spd': 2, 'int': 1, 'wil': 3, 'awa': 1, 'pre': 1},
}
_DOC = {
    'id': 'p1', 'name': 'Kal', 'build': _BUILD,
    'play_state': {'health': 1, 'focus': 0,
                   'conditions': {'slowed': True, 'afflicted': True, 'exhausted': 2},
                   'injuries': 1},
}


def test_recovery_die_table():
    assert cc.recovery_die(0) == 4
    assert cc.recovery_die(1) == 6 and cc.recovery_die(2) == 6
    assert cc.recovery_die(3) == 8 and cc.recovery_die(4) == 8
    assert cc.recovery_die(5) == 10 and cc.recovery_die(6) == 10
    assert cc.recovery_die(7) == 12 and cc.recovery_die(8) == 12
    assert cc.recovery_die(9) == 20 and cc.recovery_die(99) == 20


def test_long_rest_restores_maxes_and_clears_short_lived_conditions():
    b = cb.CosmereBuild(_BUILD)
    ps = app._cosmere_apply_rest(copy.deepcopy(_DOC), 'long')
    assert ps['health'] == b.health_max()        # full health
    assert ps['focus'] == b.focus_max()           # full focus
    assert ps['conditions'].get('exhausted') == 1  # reduced by 1 (2 -> 1)
    assert ps['conditions'].get('afflicted') is True   # ongoing affliction kept
    assert not ps['conditions'].get('slowed')     # short-lived condition cleared


def test_short_rest_heals_some_health_and_refills_focus():
    b = cb.CosmereBuild(_BUILD)
    doc = copy.deepcopy(_DOC)
    ps = app._cosmere_apply_rest(doc, 'short')
    assert 1 < ps['health'] <= b.health_max()     # healed by a recovery die, capped
    assert ps['focus'] == b.focus_max()           # focus refilled
    assert ps['conditions'].get('slowed') is True  # short rest does NOT clear conditions
