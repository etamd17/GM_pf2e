"""Cosmere GM party-vitals overview.

A GM-only at-a-glance board (/cosmere/gm/vitals) showing every PC's LIVE Health /
Focus / Investiture + injuries + conditions, fed by _cosmere_status_party and
updated in real time from the cosmere_player_state SSE (the same event the
/state route broadcasts when a player adjusts their sheet). Linked from the GM
hub via a cosmere-only tile.

Verified live: 4 seeded PCs render as vital cards; POSTing a /state change
updated the matching card with no reload (HP/bar/crit + injuries + condition
chips), driven by the real SSE round-trip.
"""
from __future__ import annotations

import pathlib
import re
import copy

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TPL = (_REPO / 'templates' / 'cosmere_gm_vitals.html').read_text(encoding='utf-8')
_HUB = (_REPO / 'templates' / 'gm_hub.html').read_text(encoding='utf-8')


def test_route_is_gm_gated_and_renders(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'ce' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Shallan', 'owner_user_id': 'u1',
           'build': {'name': 'Shallan', 'level': 5, 'path': 'scholar', 'radiant_order': 'lightweavers',
                     'ideals_sworn': 1, 'attributes': {'int': 2}, 'skills': {}},
           'play_state': {'health': 9, 'conditions': {'exhausted': 1}, 'injuries': 1}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    html = app.app.test_client().get('/cosmere/gm/vitals').get_data(as_text=True)
    assert 'Party Vitals' in html
    assert 'Shallan' in html
    assert 'data-name="Shallan"' in html and 'data-hpmax=' in html
    assert f'data-pid="{pid}"' in html


def test_board_listens_for_live_state():
    assert "appSSE('cosmere_player_state'" in _TPL
    assert 'data-bar="health"' in _TPL and 'data-bar="investiture"' in _TPL and 'data-bar="focus"' in _TPL
    assert "classList.toggle('crit'" in _TPL          # low-HP highlight


def test_board_keys_live_updates_by_pid_with_name_only_as_legacy_fallback():
    lookup = _TPL[_TPL.index('function _card'): _TPL.index('function _setBar')]

    assert re.search(r'function\s+_card\s*\(\s*pid\s*,\s*name\s*\)', lookup)
    assert 'data-pid' in lookup
    assert 'data-name' in lookup
    assert lookup.index('data-pid') < lookup.index('data-name')
    assert re.search(r'if\s*\(\s*pid\s*\)\s*return', lookup), (
        'the name lookup must be used only when an event has no stable pid')
    assert re.search(r'_apply\s*\(\s*d\.pid\s*,\s*d\.name\s*,', _TPL)


def test_board_repaints_live_resource_maxima_and_labels():
    for key in ('health_max', 'focus_max', 'investiture_max'):
        assert key in _TPL
    assert '.setAttribute(' in _TPL
    assert '[data-m="' in _TPL


def test_hub_has_cosmere_vitals_tile():
    assert '/cosmere/gm/vitals' in _HUB
    assert "active_system == 'cosmere'" in _HUB


def test_route_requires_gm(monkeypatch):
    # non-GM is redirected/blocked by @gm_required
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    r = app.app.test_client().get('/cosmere/gm/vitals')
    assert r.status_code in (302, 401, 403)


def test_missing_mistborn_bundle_keeps_roster_vitals_and_broadcast_available(
        tmp_path, monkeypatch, request):
    from systems.cosmere.party import project_member
    from systems.cosmere.rules.profiles import default_registry, package_pin
    from systems.cosmere.rules.schema import CosmereRulesBinding, to_dict
    from systems.cosmere.rules.schema import RulesValidationError

    monkeypatch.setenv('COSMERE_DATA_DIR', str(tmp_path / 'missing-rules'))
    default_registry.cache_clear()
    request.addfinalizer(default_registry.cache_clear)
    core_pin = package_pin(default_registry().packs['cosmere.core'])
    mistborn_binding = to_dict(CosmereRulesBinding(
        core_pin, (), 'scadrial', ('era-1',), ('scadrial',)))
    build = {
        'level': 2, 'ancestry': 'Human', 'path': 'Warrior',
        'attributes': {'str': 2, 'spd': 2, 'int': 1, 'wil': 1, 'awa': 1, 'pre': 1},
    }
    stormlight = {
        'id': 'a' * 32, 'system': 'cosmere', 'name': 'Teft',
        'build': {'name': 'Teft', **build},
        'play_state': {'health': 8, 'focus': 2},
    }
    mistborn = {
        'id': 'b' * 32, 'system': 'cosmere', 'name': 'Vin',
        'origin_world': 'scadrial', 'era': 'era-1',
        'rules_binding': mistborn_binding,
        'build': {'name': 'Vin', **build},
        'play_state': {'health': 7, 'focus': 1, 'investiture': 3},
    }
    with pytest.raises(RulesValidationError, match='not installed'):
        project_member(mistborn, homebrew={})

    monkeypatch.setattr(app, '_list_cosmere_pcs',
                        lambda: [copy.deepcopy(stormlight), copy.deepcopy(mistborn)])
    monkeypatch.setattr(app, '_cosmere_homebrew_store', lambda: {})
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    client = app.app.test_client()
    for route in ('/cosmere/pcs', '/cosmere/gm/vitals'):
        response = client.get(route)
        assert response.status_code == 200, (route, response.get_data(as_text=True)[:300])
        html = response.get_data(as_text=True)
        assert 'Teft' in html and 'Vin' in html
        assert 'data-pid="' + stormlight['id'] + '"' in html
        assert 'data-pid="' + mistborn['id'] + '"' in html

    published = []
    monkeypatch.setattr(app, 'sse_broadcast',
                        lambda event, payload: published.append((event, payload)))
    app._broadcast_cosmere_player_state(mistborn)
    assert published[0][0] == 'cosmere_player_state'
    assert published[0][1]['pid'] == mistborn['id']
