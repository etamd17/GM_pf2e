"""Cosmere Fabrials (Ch.7): catalog + charge-tracking (RAW; no crafting).

The content packs hold a Fabrial device catalog (handbook-items) with tier +
charge counts in the description text, but NO crafting recipes/DCs — so this is
purchase/reward + charge-tracking, not crafting.

- items.fabrials() parses the catalog ({id, name, tier, charges, effect}).
- build.fabrials is a list of equipped device ids.
- The builder Gear step has a fabrial picker; the sheet shows each equipped
  fabrial with a charge tracker (spend/recharge), persisted per-id in play_state.

Verified live: a PC with 2 fabrials renders both with charge trackers; spending
a charge (5->4) persists per id.
"""
from __future__ import annotations

import pathlib

import pytest

import app
import systems.cosmere.items as I
import systems.cosmere.build as B

_REPO = pathlib.Path(__file__).resolve().parent.parent
_BUILDER = (_REPO / 'templates' / 'cosmere_builder.html').read_text()
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_fabrial_catalog_parsed():
    fab = I.fabrials()
    assert len(fab) >= 15
    f = fab[0]
    assert {'id', 'name', 'tier', 'charges', 'effect'} <= set(f)
    assert f['charges'] >= 1 and f['effect']
    # lookup by id
    assert I.fabrial(f['id'])['name'] == f['name']
    # a known fabrial exists
    assert any(x['name'] == 'Clock Fabrial' for x in fab)


def test_build_carries_fabrials():
    fid = I.fabrials()[0]['id']
    b = B.CosmereBuild({'fabrials': [fid]})
    assert b.fabrials == [fid]
    assert b.to_dict()['fabrials'] == [fid]


def test_builder_and_sheet_surfaces():
    assert 'fabrial-pick' in _BUILDER and 'function addFabrial' in _BUILDER and 'fabrials:FABRIALS' in _BUILDER
    assert 'window.cosFabrial' in _SHEET and 'data-fab-cur' in _SHEET and 'FAB_MAX' in _SHEET


@pytest.fixture
def fab_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    fid = I.fabrials()[0]['id']
    pid = 'fb' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Navani', 'owner_user_id': 'u1',
           'build': {'name': 'Navani', 'level': 5, 'path': 'scholar', 'attributes': {'int': 3},
                     'skills': {}, 'fabrials': [fid]}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid, fid


def test_sheet_renders_equipped_fabrials(fab_pc):
    pid, fid = fab_pc
    html = app.app.test_client().get('/cosmere/pc/' + pid).get_data(as_text=True)
    assert 'data-fab-cur="' + fid + '"' in html
    assert 'charges' in html


def test_state_persists_fabrial_charges(fab_pc):
    pid, fid = fab_pc
    r = app.app.test_client().post('/cosmere/pc/' + pid + '/state',
                                   json={'fabrials': {fid: 2}}).get_json()
    assert r['ok'] and r['play_state']['fabrials'][fid] == 2
