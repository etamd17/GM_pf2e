"""Cosmere two-way GM<->player sync.

Fixes two gaps found in the sync audit:
  - player -> GM: a player's sheet save (HP / injuries / conditions) never
    reached the live tracker combatant, so the GM screen showed stale values.
  - GM -> player: the GM had no way to push a condition onto a player's PC, and
    the sheet didn't listen for pushed state.
"""
from __future__ import annotations

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _combatant(name, spd=3):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': spd}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name + '-1'
    a.system = 'cosmere'
    a.is_pc = True
    return a


@pytest.fixture
def pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    pid = 'ab' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 3, 'path': 'warrior',
                     'attributes': {'str': 3, 'spd': 3, 'wil': 1}, 'skills': {'hwp': 2}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    app.ACTIVE_ENCOUNTER[:] = [_combatant('Kaladin')]
    app.TURN_INDEX = 0
    yield pid
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0


# --- player -> GM: the sheet save mirrors onto the live tracker combatant ----
def test_player_state_syncs_to_live_combatant(pc):
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    me.current_hp = 30
    r = app.app.test_client().post(
        '/cosmere/pc/' + pc + '/state',
        json={'health': 17, 'injuries': 2, 'conditions': {'slowed': True, 'gone': False}})
    assert r.get_json()['ok']
    assert me.current_hp == 17 and me.injuries == 2
    assert me.conditions == {'slowed': True}             # falsy entries dropped on the combatant
    assert me.tracker_block()['health']['value'] == 17   # exactly what the GM screen renders


def test_player_state_without_combatant_still_persists(pc):
    app.ACTIVE_ENCOUNTER[:] = []                          # PC not in any encounter
    r = app.app.test_client().post('/cosmere/pc/' + pc + '/state', json={'health': 5})
    assert r.get_json()['play_state']['health'] == 5     # doc save path unaffected


# --- GM -> player: condition push updates combatant AND the player's doc ------
def test_gm_condition_push_persists_to_pc_doc(pc):
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    c = app.app.test_client()
    c.post('/api/cosmere/combatant/' + me.instance_id + '/condition',
           json={'condition': 'stunned', 'action': 'add'})
    assert me.conditions.get('stunned') is True
    # written through to the player's saved play_state so their sheet (+reload) shows it
    assert app._load_cosmere_pc(pc)['play_state']['conditions'].get('stunned') is True
    # remove clears it from both the combatant and the doc
    c.post('/api/cosmere/combatant/' + me.instance_id + '/condition',
           json={'condition': 'stunned', 'action': 'remove'})
    assert 'stunned' not in me.conditions
    assert 'stunned' not in app._load_cosmere_pc(pc)['play_state']['conditions']


def test_gm_condition_toggle(pc):
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    c = app.app.test_client()
    c.post('/api/cosmere/combatant/' + me.instance_id + '/condition', json={'condition': 'prone'})
    assert me.conditions.get('prone') is True            # default action toggles on
    c.post('/api/cosmere/combatant/' + me.instance_id + '/condition', json={'condition': 'prone'})
    assert 'prone' not in me.conditions                  # toggles off


def test_gm_condition_validates(pc):
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    c = app.app.test_client()
    assert c.post('/api/cosmere/combatant/' + me.instance_id + '/condition',
                  json={'condition': 'bogus', 'action': 'add'}).status_code == 400
    assert c.post('/api/cosmere/combatant/nope/condition',
                  json={'condition': 'stunned', 'action': 'add'}).status_code == 404


def test_gm_condition_requires_gm(pc, monkeypatch):
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    r = app.app.test_client().post('/api/cosmere/combatant/' + me.instance_id + '/condition',
                                   json={'condition': 'stunned', 'action': 'add'})
    assert r.status_code == 403


# --- the sheet wires up the GM->player live listener -------------------------
def test_sheet_listens_for_pushed_state(pc):
    body = app.app.test_client().get('/cosmere/pc/' + pc).data.decode()
    assert 'cosmere_player_state' in body                # listens for GM-pushed state
    assert 'applyRemoteState' in body and 'paintConds' in body
