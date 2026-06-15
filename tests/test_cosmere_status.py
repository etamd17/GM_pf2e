"""The GM party-status board (/status) and session export must show live
Cosmere party state, not an empty PF2e roster. These cover the pure assembly
helper (no account-mode setup needed) that the route/export call.
"""
from __future__ import annotations

import app

_DOC = {
    'id': 'p1', 'name': 'Kal',
    'build': {
        'name': 'Kal', 'level': 2, 'ancestry': 'Human', 'path': 'Warrior',
        'attributes': {'str': 3, 'spd': 2, 'int': 1, 'wil': 1, 'awa': 1, 'pre': 1},
    },
    'play_state': {'health': 5, 'conditions': {'slowed': True}, 'focus': 1, 'injuries': 1},
}


def test_cosmere_status_party_assembles_live_state():
    rows = app._cosmere_status_party([_DOC])
    assert len(rows) == 1
    r = rows[0]
    assert r['name'] == 'Kal'
    assert r['level'] == 2
    assert r['current_hp'] == 5                 # from play_state, not max
    assert r['max_hp'] > 0                       # derived from the build
    assert 0 <= r['hp_pct'] <= 100
    assert r['conditions'].get('slowed') is True
    assert r['hero_points'] is None              # Cosmere has no hero points
    assert r['class_name']                       # path/order shown where PF2e shows class
    assert 'investiture_max' in r and 'focus_max' in r


def test_cosmere_status_party_handles_missing_play_state():
    doc = {'id': 'p2', 'name': 'NoState', 'build': dict(_DOC['build'])}
    rows = app._cosmere_status_party([doc])
    assert len(rows) == 1
    r = rows[0]
    assert r['current_hp'] == r['max_hp']        # full health when no play_state
    assert r['conditions'] == {}
