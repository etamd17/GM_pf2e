"""Cosmere: Fabrial Workshop — Inventing Unique Fabrials (Ch.7), RAW.

The crafting rules aren't in the mined packs, so systems/cosmere/fabrial_crafting.py
encodes them from the rulebook: 14 craftable effects (each tier/charges + its own
upgrade/drawback), per-tier material cost + trap-spren Lore DC, the Crafting-test
result bands, and the general upgrade/drawback + advanced-feature tables. The
sheet's guided Workshop drives: choose effect -> materials -> trap a spren (Lore
test) -> Crafting test (raise the stakes) -> apply upgrades/drawbacks -> forge.
Crafted fabrials (with their chosen upgrades/drawbacks + charges) persist in
play_state.crafted_fabrials.

Verified live: a scholar trapped a spren (Lore vs DC 20), made the Crafting test
(Typical -> 1 upgrade/1 drawback), picked them, and forged a Liferial that
appears with a charge tracker and persists.
"""
from __future__ import annotations

import pathlib

import pytest

import app
import systems.cosmere.fabrial_crafting as FC

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_crafting_rules_data():
    assert len(FC.EFFECTS) >= 14
    e = FC.effect('accelerator')
    assert e and e['tier'] == 2 and e['charges'] == 3 and e['upgrade'] and e['drawback']
    assert FC.TIER_COST == {1: 100, 2: 200, 3: 400, 4: 800}
    assert FC.TRAP_DC == {1: 15, 2: 20, 3: 25, 4: 30}
    assert len(FC.GENERAL_UPGRADES) == 8 and len(FC.GENERAL_DRAWBACKS) == 8
    assert FC.ADVANCED_FEATURES


def test_craft_result_bands():
    assert FC.craft_result(4)['failed'] is True
    assert FC.craft_result(8) == {'total': 8, 'label': 'Shoddy Creation', 'upgrades': 0, 'drawbacks': 1, 'failed': False}
    assert FC.craft_result(16)['upgrades'] == 1 and FC.craft_result(16)['drawbacks'] == 1
    assert FC.craft_result(23)['upgrades'] == 2
    assert FC.craft_result(30) == {'total': 30, 'label': 'Exceptional Creation', 'upgrades': 3, 'drawbacks': 0, 'failed': False}


def test_sheet_workshop_present():
    for tok in ('cos-workshop', 'window.wsPick', 'window.wsTrap', 'window.wsCraft',
                'window.wsForge', 'cos-crafted-list', 'window.cosCrafted'):
        assert tok in _SHEET, tok
    # the rolls use the trap-spren Lore DC + the crafting-test band machinery
    assert 'trap_dc' in _SHEET and '_craftBand' in _SHEET


@pytest.fixture
def crafter_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'fc' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Navani', 'owner_user_id': 'u1',
           'build': {'name': 'Navani', 'level': 5, 'path': 'scholar',
                     'attributes': {'int': 3}, 'skills': {'cra': 3, 'lor': 2}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def test_sheet_exposes_workshop_for_radiant_or_anyone(crafter_pc):
    # the workshop is available to any interactive character (artifabrian path)
    html = app.app.test_client().get('/cosmere/pc/' + crafter_pc).get_data(as_text=True)
    assert 'Fabrial Workshop' in html and 'id="ws-effect"' in html


def test_state_persists_crafted_fabrials(crafter_pc):
    payload = {'crafted_fabrials': [{'key': 'liferial', 'name': 'Liferial', 'tier': 2,
                                     'charges': 3, 'current': 2, 'effect': 'Heals 1d6.',
                                     'upgrades': ['Amplified'], 'drawbacks': ['Delicate']}]}
    r = app.app.test_client().post('/cosmere/pc/' + crafter_pc + '/state', json=payload).get_json()
    assert r['ok']
    cf = r['play_state']['crafted_fabrials']
    assert len(cf) == 1 and cf[0]['name'] == 'Liferial' and cf[0]['current'] == 2
    assert cf[0]['upgrades'] == ['Amplified'] and cf[0]['drawbacks'] == ['Delicate']
