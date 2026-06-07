"""Cosmere player interactivity (Phase 1): the interactive sheet (tap-to-roll
skills/strikes + live resource steppers), the ownership-validated roll endpoint,
and the persisted play_state. Read-only for everyone but the owner / GM.
"""
from __future__ import annotations

import pytest

import app
import systems.cosmere.build as cb
import systems.cosmere.items as items
from systems.cosmere.actor import CosmereActor


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A Cosmere PC (owner u1) with an equipped weapon, in a throwaway store."""
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    axe = next(i for i in items.catalog() if i['name'] == 'Axe')
    pid = 'ab' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 3, 'path': 'warrior',
                     'attributes': {'str': 3, 'spd': 3, 'wil': 1},
                     'skills': {'hwp': 2, 'ath': 1},
                     'inventory': [{'id': axe['id'], 'qty': 1, 'equipped': True}]}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    return pid


# --- the interactive sheet (legacy mode => GM => interactive) --------------
def test_owner_sheet_is_interactive(pc):
    # cosRoll(/cosStrike(/cosAdjust( and the toolbar text only render when interactive
    # (the .cs-* CSS class names live in <style> on every sheet, so check the JS).
    body = app.app.test_client().get('/cosmere/pc/' + pc).data.decode()
    assert 'cosRoll(' in body                      # skills are tap-to-roll
    assert 'cosStrike(' in body                    # strikes are tap-to-roll
    assert 'cosAdjust(' in body                    # resource steppers
    assert 'Raise the stakes' in body              # the Plot Die toggle (toolbar markup)


def test_adversary_sheet_stays_readonly(pc):
    """An adversary sheet (bestiary) doesn't pass cur/interactive -> no rollers."""
    advs = __import__('systems.cosmere', fromlist=['adversary_docs']).adversary_docs()
    aid = advs[0]['_id']
    body = app.app.test_client().get('/cosmere/sheet/' + aid).data.decode()
    assert 'cosRoll(' not in body and 'cosAdjust(' not in body and 'Raise the stakes' not in body


def test_strike_carries_attack_mod(pc):
    doc = app._load_cosmere_pc(pc)
    a = CosmereActor(cb.CosmereBuild(doc['build']).to_actor_doc())
    axe = next(s for s in a.strikes if s['name'] == 'Axe')
    assert axe['skill'] == 'hwp'                   # Heavy Weaponry
    assert axe['mod'] == 2 + 3                     # ranks(2) + STR(3)


# --- the roll endpoint -----------------------------------------------------
def test_roll_endpoint_logs(pc):
    c = app.app.test_client()
    r = c.post('/api/cosmere/roll', json={'pid': pc, 'action': 'Athletics test',
                                          'result': '18', 'detail': 'd20(14) +4'})
    assert r.get_json()['ok']
    assert any(e.get('name') == 'Kaladin' and 'Athletics' in e.get('action', '') for e in app.COMBAT_LOGS)
    # unknown character -> 404
    assert c.post('/api/cosmere/roll', json={'pid': '00' * 16, 'action': 'x'}).status_code == 404


def test_roll_rejects_non_owner(pc, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'someone_else'})
    r = app.app.test_client().post('/api/cosmere/roll', json={'pid': pc, 'action': 'sneaky'})
    assert r.status_code == 403


# --- the play_state (current resources) ------------------------------------
def test_state_persists_and_reflects(pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + pc + '/state',
               json={'health': 12, 'focus': 1, 'investiture': 0, 'injuries': 2})
    assert r.get_json()['play_state']['health'] == 12
    # persisted to the doc
    doc = app._load_cosmere_pc(pc)
    assert doc['play_state']['health'] == 12 and doc['play_state']['injuries'] == 2
    # and the sheet renders the persisted current value, not the max
    body = c.get('/cosmere/pc/' + pc).data.decode()
    assert 'rv-health' in body and '>12<' in body.replace(' ', '')


def test_state_clamps_and_rejects_non_owner(pc, monkeypatch):
    c = app.app.test_client()
    # negative clamps to 0
    assert c.post('/cosmere/pc/' + pc + '/state', json={'health': -5}).get_json()['play_state']['health'] == 0
    # non-owner blocked
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    monkeypatch.setattr(app._auth, 'current_user', lambda: {'id': 'intruder'})
    assert c.post('/cosmere/pc/' + pc + '/state', json={'health': 99}).status_code == 403


# --- Phase 2: Radiant Stormlight actions + surge powers --------------------
@pytest.fixture
def radiant_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'ef' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 4, 'path': 'warrior',
                     'radiant_order': 'windrunners', 'ideals_sworn': 2, 'spren_name': 'Syl',
                     'attributes': {'str': 3, 'spd': 3, 'wil': 2, 'awa': 1, 'pre': 1},
                     'skills': {'hwp': 2, 'adh': 1, 'grv': 1}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    return pid


def test_radiant_pc_has_stormlight_actions(radiant_pc):
    body = app.app.test_client().get('/cosmere/pc/' + radiant_pc).data.decode()
    assert 'Stormlight Actions' in body
    assert 'cosBreathe()' in body and 'cosEnhance()' in body and 'cosRegenerate()' in body
    # the order's castable surge powers (Adhesion / Gravitation for Windrunners)
    assert 'Surge Powers' in body and 'cosSurge(' in body
    assert 'Adhesion' in body and 'Full Lashing' in body


def test_non_radiant_pc_has_no_radiant_panel(pc):
    body = app.app.test_client().get('/cosmere/pc/' + pc).data.decode()
    assert 'Stormlight Actions' not in body and 'cosEnhance()' not in body


def test_enhanced_toggle_persists(radiant_pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + radiant_pc + '/state', json={'enhanced': True, 'investiture': 3})
    assert r.get_json()['play_state']['enhanced'] is True
    body = c.get('/cosmere/pc/' + radiant_pc).data.decode()
    assert 'enhanced:true' in body                # the JS state reflects it on reload
    # clears
    assert c.post('/cosmere/pc/' + radiant_pc + '/state',
                  json={'enhanced': False}).get_json()['play_state']['enhanced'] is False
