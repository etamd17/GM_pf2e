"""The player-facing Cosmere combat / initiative view: a read-only snapshot of
the whole turn order, live via encounter_update. Hidden adversaries are masked
for players; adversary exact HP is withheld (PCs show health, the party already
sees each other).
"""
from __future__ import annotations

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _pc(name, hp=20):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': 3}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name
    a.system = 'cosmere'
    a.is_pc = True
    a.current_hp = hp
    a.health_max = hp
    return a


def _adv(name, visible=True):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': 2}}, 'tier': 2, 'role': 'brute'},
                                      'type': 'adversary', 'name': name}})
    a.instance_id = name
    a.system = 'cosmere'
    a.is_pc = False
    a.visible_to_players = visible
    return a


@pytest.fixture
def enc(monkeypatch):
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    app.ACTIVE_ENCOUNTER[:] = [_pc('Kaladin', 24), _adv('Brute', True), _adv('Lurker', False)]
    app.TURN_INDEX = 0
    app.ROUND_NUMBER = 3
    yield
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0
    app.ROUND_NUMBER = 1


def test_combat_state_order_and_active(enc):
    st = app.app.test_client().get('/api/cosmere/combat_state').get_json()
    assert st['ok'] and st['in_encounter'] and st['round'] == 3
    assert [c['name'] for c in st['order'][:2]] == ['Kaladin', 'Brute']
    kal = st['order'][0]
    assert kal['is_pc'] and kal['is_active'] and kal['health']['max'] == 24
    brute = st['order'][1]
    assert brute['is_pc'] is False and brute['health'] is None and brute['tier'] == 2


def test_combat_state_masks_hidden_for_players(enc, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    st = app.app.test_client().get('/api/cosmere/combat_state').get_json()
    lurker = st['order'][2]
    assert lurker['name'] == '???' and lurker.get('hidden') and 'tier' not in lurker


def test_combat_state_gm_sees_hidden(enc):
    # default test client is GM -> the hidden adversary is named, not masked
    st = app.app.test_client().get('/api/cosmere/combat_state').get_json()
    assert st['order'][2]['name'] == 'Lurker'


def test_combat_state_empty_when_no_encounter(monkeypatch):
    app.ACTIVE_ENCOUNTER[:] = []
    st = app.app.test_client().get('/api/cosmere/combat_state').get_json()
    assert st['ok'] and st['in_encounter'] is False and st['order'] == []


def test_combat_view_page_renders(enc):
    body = app.app.test_client().get('/cosmere/combat').data.decode()
    assert 'combat_state' in body and 'encounter_update' in body   # live wiring
    assert 'cc-list' in body                                       # the order list
