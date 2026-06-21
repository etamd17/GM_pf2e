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

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_TPL = (_REPO / 'templates' / 'cosmere_gm_vitals.html').read_text()
_HUB = (_REPO / 'templates' / 'gm_hub.html').read_text()


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


def test_board_listens_for_live_state():
    assert "appSSE('cosmere_player_state'" in _TPL
    assert 'data-bar="health"' in _TPL and 'data-bar="investiture"' in _TPL and 'data-bar="focus"' in _TPL
    assert "classList.toggle('crit'" in _TPL          # low-HP highlight


def test_hub_has_cosmere_vitals_tile():
    assert '/cosmere/gm/vitals' in _HUB
    assert "active_system == 'cosmere'" in _HUB


def test_route_requires_gm(monkeypatch):
    # non-GM is redirected/blocked by @gm_required
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    r = app.app.test_client().get('/cosmere/gm/vitals')
    assert r.status_code in (302, 401, 403)
