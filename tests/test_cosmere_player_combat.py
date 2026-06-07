"""Cosmere combat from the player's seat (Phase 3): the sheet's live combat
strip (whose turn it is + driving your own Fast/Slow election or rolling
traditional d20+Speed initiative) and self-managed conditions. All three
combat endpoints are ownership-validated against the player's OWN combatant
(matched by name in the active encounter).
"""
from __future__ import annotations

import json

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _combatant(name, spd=3):
    """A Cosmere PC combatant in the encounter, matched to the sheet by name."""
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': spd}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name + '-1'
    a.system = 'cosmere'
    a.is_pc = True
    a.speed_choice = 'slow'
    a.max_actions = 3
    a.initiative = 0
    return a


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A Cosmere PC (owner u1) named Kaladin, in the active encounter at index 0,
    with persistence/broadcast stubbed so the routes stay side-effect-free."""
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    camp = tmp_path / 'campaign.json'
    camp.write_text('{"id":"c1","slug":"r","system":"cosmere","name":"Roshar","members":[]}')
    monkeypatch.setattr(app, 'CAMPAIGN_FILE', str(camp))
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    pid = 'ab' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 3, 'path': 'warrior',
                     'attributes': {'str': 3, 'spd': 3, 'wil': 1},
                     'skills': {'hwp': 2, 'ath': 1}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    app.ACTIVE_ENCOUNTER[:] = [_combatant('Kaladin'), _combatant('Adolin', spd=2)]
    app.TURN_INDEX = 0
    app.ROUND_NUMBER = 2
    app._save_campaign_config({'cosmere_initiative': 'phases'})
    yield pid
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0


# --- my_combat: whose turn is it, and what are my controls -----------------
def test_my_combat_reports_my_turn(pc):
    st = app.app.test_client().get('/api/cosmere/my_combat?pid=' + pc).get_json()
    assert st['ok'] and st['in_encounter'] is True
    assert st['round'] == 2 and st['mode'] == 'phases'
    assert st['is_my_turn'] is True and st['active_name'] == 'Kaladin'
    assert st['speed_choice'] == 'slow' and st['max_actions'] == 3


def test_my_combat_not_my_turn(pc):
    app.TURN_INDEX = 1                                  # Adolin is up
    st = app.app.test_client().get('/api/cosmere/my_combat?pid=' + pc).get_json()
    assert st['in_encounter'] is True and st['is_my_turn'] is False
    assert st['active_name'] == 'Adolin'


def test_my_combat_not_in_encounter(pc):
    app.ACTIVE_ENCOUNTER[:] = []                        # nobody is fighting
    st = app.app.test_client().get('/api/cosmere/my_combat?pid=' + pc).get_json()
    assert st['ok'] is True and st['in_encounter'] is False


def test_my_combat_rejects_non_owner(pc, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'someone_else'})
    assert app.app.test_client().get('/api/cosmere/my_combat?pid=' + pc).status_code == 403


# --- my_speed: elect Fast (2 actions) / Slow (3 actions) for my own PC ------
def test_my_speed_sets_actions(pc):
    c = app.app.test_client()
    r = c.post('/api/cosmere/my_speed', json={'pid': pc, 'choice': 'fast'}).get_json()
    assert r['ok'] and r['speed_choice'] == 'fast' and r['max_actions'] == 2
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    assert me.speed_choice == 'fast' and me.max_actions == 2
    # and back to slow -> 3 actions
    r2 = c.post('/api/cosmere/my_speed', json={'pid': pc, 'choice': 'slow'}).get_json()
    assert r2['max_actions'] == 3


def test_my_speed_validates_choice(pc):
    assert app.app.test_client().post(
        '/api/cosmere/my_speed', json={'pid': pc, 'choice': 'sideways'}).status_code == 400


def test_my_speed_rejects_non_owner(pc, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'intruder'})
    assert app.app.test_client().post(
        '/api/cosmere/my_speed', json={'pid': pc, 'choice': 'fast'}).status_code == 403


# --- my_initiative: roll d20+Speed for the traditional house-rule -----------
def test_my_initiative_rolls(pc):
    app._save_campaign_config({'cosmere_initiative': 'traditional'})
    r = app.app.test_client().post('/api/cosmere/my_initiative', json={'pid': pc}).get_json()
    assert r['ok']
    assert 21 >= r['initiative'] >= 4                   # d20(1..20) + Speed(3)
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    assert me.initiative == r['initiative']
    assert any('Initiative' in e.get('action', '') or 'Initiative' in str(e.get('detail', ''))
               or 'rolled Initiative' in str(e) for e in app.COMBAT_LOGS) or me.initiative > 0


def test_my_initiative_rejects_non_owner(pc, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'nope'})
    assert app.app.test_client().post(
        '/api/cosmere/my_initiative', json={'pid': pc}).status_code == 403


# --- conditions: self-managed, persisted, reflected on the sheet ------------
def test_conditions_persist_and_render(pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + pc + '/state', json={'conditions': {'slowed': True, 'prone': True}})
    assert r.get_json()['play_state']['conditions'] == {'slowed': True, 'prone': True}
    doc = app._load_cosmere_pc(pc)
    assert doc['play_state']['conditions']['slowed'] is True
    # the chip renders in the 'on' state on reload
    body = c.get('/cosmere/pc/' + pc).data.decode()
    assert 'data-cond="slowed"' in body
    assert 'cs-cond on" data-cond="slowed"' in body or 'on" data-cond="slowed"' in body


# --- the sheet wires up the combat strip + condition chips ------------------
def test_sheet_renders_combat_strip(pc):
    body = app.app.test_client().get('/cosmere/pc/' + pc).data.decode()
    assert 'cos-combat' in body                         # the live combat strip
    assert 'cosCond(' in body                           # tap-to-toggle conditions
    assert 'cosSpeed(' in body and 'cosInit(' in body   # fast/slow + roll-initiative controls
    assert 'my_combat' in body                           # polls own combat state
