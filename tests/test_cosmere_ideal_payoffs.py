"""Cosmere Ideal payoffs (Full mechanical, RAW from the content packs).

- Third Ideal: a Radiant can summon their spren as a Shardblade — a real Strike
  (2d8, spirit damage so it bypasses physical Deflect, deadly) gated on
  ideals_sworn >= 3. Summon/dismiss toggles the Strike and persists.
- Take Squire (talent): a squire roster field on the sheet (persists).
- Wound Regeneration (talent): a "Mend Injury" Stormlight action — spend 1
  Investiture to clear an injury.

Verified live on a 3rd-Ideal Windrunner: summon shows "Shardblade attack +N ·
deadly / 2d8 spirit · bypasses Deflect"; Mend Injury drops Investiture + an
injury; the squire name persists.
"""
from __future__ import annotations

import pathlib

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_sheet_has_payoff_handlers():
    for tok in ('window.cosShardblade', 'window.cosSquire', 'window.cosMendInjury',
                'btn-shardblade', 'cos-squire', 'cos-shardblade-strike',
                'Mend Injury', 'Summon Shardblade'):
        assert tok in _SHEET, tok


@pytest.fixture
def radiant_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'ef' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kal', 'owner_user_id': 'u1',
           'build': {'name': 'Kal', 'level': 8, 'path': 'warrior', 'radiant_order': 'windrunners',
                     'ideals_sworn': 3, 'attributes': {'str': 3, 'spd': 3, 'wil': 2},
                     'skills': {'hwp': 3, 'ath': 3},
                     'talents': [{'id': 'radiant:Take Squire (Windrunner)', 'name': 'Take Squire (Windrunner)'},
                                 {'id': 'radiant:Wound Regeneration', 'name': 'Wound Regeneration'}]}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def test_third_ideal_grants_shardblade_strike(radiant_pc):
    html = app.app.test_client().get('/cosmere/pc/' + radiant_pc).get_data(as_text=True)
    assert 'cos-shardblade-strike' in html
    assert '2d8' in html and 'spirit' in html and 'bypasses Deflect' in html
    assert 'Summon Shardblade' in html
    # the two talents surface their actions
    assert 'cos-squire' in html and 'Mend Injury' in html


def test_state_route_persists_shardblade_and_squire(radiant_pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + radiant_pc + '/state', json={'shardblade': True, 'squire': 'Lopen'}).get_json()
    assert r['ok']
    assert r['play_state']['shardblade'] is True
    assert r['play_state']['squire'] == 'Lopen'
    # dismiss persists too
    r2 = c.post('/cosmere/pc/' + radiant_pc + '/state', json={'shardblade': False}).get_json()
    assert r2['play_state']['shardblade'] is False


def test_below_third_ideal_has_no_shardblade(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'ab' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'NewRad', 'owner_user_id': 'u1',
           'build': {'name': 'NewRad', 'level': 3, 'path': 'warrior', 'radiant_order': 'windrunners',
                     'ideals_sworn': 1, 'attributes': {'str': 2}, 'skills': {'hwp': 1}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    html = app.app.test_client().get('/cosmere/pc/' + pid).get_data(as_text=True)
    # the rendered elements (not the JS that references their ids) must be absent
    assert 'id="cos-shardblade-strike"' not in html
    assert 'id="btn-shardblade"' not in html
