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


# --- GM -> player: tracker damage writes HP/injuries back to the PC doc -------
def test_gm_tracker_damage_syncs_to_pc_doc(pc):
    """The GM dealing damage from the tracker must reach the player's saved
    play_state, so their own sheet repaints in place (it reads cosmere_player_state)
    -- not just the GM's tracker view."""
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    me.current_hp = 20
    me.deflect = {'value': 0}
    r = app.app.test_client().post('/api/adjust_hp/' + me.instance_id,
                                   data={'amount': '5', 'action': 'damage', 'damage_type': 'impact'})
    assert r.status_code in (200, 302)                    # form-POST redirects back to /tracker
    assert me.current_hp == 15                            # deflect 0 -> 5 impact taken
    assert app._load_cosmere_pc(pc)['play_state']['health'] == 15   # written back to the sheet's source


# --- the sheet wires up the GM->player live listener -------------------------
def test_sheet_listens_for_pushed_state(pc):
    body = app.app.test_client().get('/cosmere/pc/' + pc).data.decode()
    assert 'cosmere_player_state' in body                # listens for GM-pushed state
    assert 'applyRemoteState' in body and 'paintConds' in body


# --- concurrency: two writers, different fields, same doc -> both must stick ---
def test_concurrent_play_state_writes_do_not_clobber(pc, monkeypatch):
    """A player setting HP on their sheet while the GM sets injuries from the
    tracker both write the SAME PC doc (load->change->save). The per-file lock
    must serialize them so one full-doc save can't clobber the other's field."""
    import threading
    import time
    base = app.app.test_client()
    base.post('/cosmere/pc/' + pc + '/state', json={'health': 100, 'injuries': 0, 'focus': 4})

    # Widen the read-modify-write window so a MISSING lock would deterministically
    # clobber (both greenlets read the old doc, then both write their full doc back).
    real = app._load_cosmere_pc
    monkeypatch.setattr(app, '_load_cosmere_pc', lambda p: (real(p), time.sleep(0.05))[0])

    def setter(payload):
        app.app.test_client().post('/cosmere/pc/' + pc + '/state', json=payload)

    t1 = threading.Thread(target=setter, args=({'health': 42},))
    t2 = threading.Thread(target=setter, args=({'injuries': 5},))
    t1.start(); t2.start(); t1.join(); t2.join()

    ps = real(pc)['play_state']
    assert ps['health'] == 42 and ps['injuries'] == 5, ps   # neither update was lost


def test_path_lock_is_per_path_and_stable():
    """_path_lock returns the SAME lock for a path (so writers actually serialize)
    and DIFFERENT locks for different paths (so unrelated files don't contend)."""
    a1 = app._path_lock('/tmp/x/a.json')
    a2 = app._path_lock('/tmp/x/../x/a.json')   # same file, different spelling
    b = app._path_lock('/tmp/x/b.json')
    assert a1 is a2 and a1 is not b
