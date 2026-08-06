"""Cosmere Shardplate (RAW): living Plate at the Fourth Ideal.

From handbook-items.json "Shardplate (Radiant)": Deflect Value 5, deflects every
damage type (impact/keen/energy + spirit/vital — even the two that normally
bypass Deflect); "Radiants of the Fourth Ideal or higher can wield living
Shardplate." So a Don/Doff toggle appears at ideals_sworn >= 4; donning overrides
the Deflect facet to 5 against all types and persists. Forsaking doffs it.

Verified live on a 4th-Ideal Windrunner: Deflect 0 -> 5, types gain spirit+vital,
button flips to Doff, persists.
"""
from __future__ import annotations

import pathlib

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_sheet_has_shardplate_toggle_and_override():
    assert 'window.cosShardplate' in _SHEET
    assert 'btn-shardplate' in _SHEET
    assert 'cs-deflect-n' in _SHEET and 'cs-deflect-types' in _SHEET
    # donned -> Deflect 5 against every type
    assert "'impact','keen','energy','spirit','vital'" in _SHEET
    # forsaking doffs the plate too
    assert 'cur.forsaken && cur.shardplate' in _SHEET


@pytest.fixture
def plate_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'da' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Dalinar', 'owner_user_id': 'u1',
           'build': {'name': 'Dalinar', 'level': 13, 'path': 'warrior', 'radiant_order': 'windrunners',
                     'ideals_sworn': 4, 'attributes': {'str': 3, 'spd': 3, 'wil': 2}, 'skills': {'hwp': 3}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def test_fourth_ideal_grants_shardplate(plate_pc):
    html = app.app.test_client().get('/cosmere/pc/' + plate_pc).get_data(as_text=True)
    assert 'id="btn-shardplate"' in html and 'Shardplate' in html


def test_state_persists_shardplate(plate_pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + plate_pc + '/state', json={'shardplate': True}).get_json()
    assert r['ok'] and r['play_state']['shardplate'] is True


def test_below_fourth_ideal_has_no_shardplate(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'db' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kal', 'owner_user_id': 'u1',
           'build': {'name': 'Kal', 'level': 8, 'path': 'warrior', 'radiant_order': 'windrunners',
                     'ideals_sworn': 3, 'attributes': {'str': 3}, 'skills': {'hwp': 2}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    html = app.app.test_client().get('/cosmere/pc/' + pid).get_data(as_text=True)
    assert 'id="btn-shardplate"' not in html
