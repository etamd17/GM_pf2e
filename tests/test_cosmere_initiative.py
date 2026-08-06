"""Cosmere initiative house-rule toggle: 'phases' (rulebook 4-phase fast/slow,
default) vs 'traditional' (rolled d20+Speed order). The user's GM runs
traditional initiative, so the tracker must support switching off fast/slow.
"""
from __future__ import annotations

import json

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _mk(name, init, spd, fast):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': spd}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name
    a.initiative = init
    a.speed_choice = 'fast' if fast else 'slow'
    a.system = 'cosmere'
    a.is_pc = True
    return a


@pytest.fixture
def camp(tmp_path, monkeypatch):
    f = tmp_path / 'campaign.json'
    f.write_text('{"id":"c1","slug":"r","system":"cosmere","name":"Roshar","members":[]}')
    monkeypatch.setattr(app, 'CAMPAIGN_FILE', str(f))
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    return f


def test_phases_vs_traditional_sort(camp):
    # SlowHigh: high initiative but SLOW; FastLow: low initiative but FAST.
    app.ACTIVE_ENCOUNTER[:] = [_mk('SlowHigh', 18, 3, False), _mk('FastLow', 5, 1, True)]
    app.TURN_INDEX = 0
    try:
        app._save_campaign_config({'cosmere_initiative': 'phases'})
        app._sort_encounter()
        assert [c.instance_id for c in app.ACTIVE_ENCOUNTER][0] == 'FastLow'   # fast acts early
        app._save_campaign_config({'cosmere_initiative': 'traditional'})
        assert app._cosmere_initiative_mode() == 'traditional'
        app._sort_encounter()
        assert [c.instance_id for c in app.ACTIVE_ENCOUNTER][0] == 'SlowHigh'  # init 18 > 5
    finally:
        app.ACTIVE_ENCOUNTER[:] = []


def test_initiative_mode_route_preserves_campaign(camp):
    c = app.app.test_client()
    r = c.post('/api/cosmere/initiative_mode', json={'mode': 'traditional'})
    assert r.get_json()['mode'] == 'traditional'
    assert app._cosmere_initiative_mode() == 'traditional'
    # the config write keeps the campaign doc's identity (the _save_campaign_config fix)
    saved = json.loads(camp.read_text())
    assert saved['system'] == 'cosmere' and saved['id'] == 'c1'
    assert saved['cosmere_initiative'] == 'traditional'
    c.post('/api/cosmere/initiative_mode', json={'mode': 'phases'})


def test_tracker_exposes_the_toggle(monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    body = app.app.test_client().get('/tracker').data.decode()
    assert 'toggleCosmereInit()' in body          # the tool-strip toggle
    assert 'COSMERE_INIT_MODE' in body            # the client mode const (gates fast/slow)
