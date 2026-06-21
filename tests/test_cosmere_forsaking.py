"""Cosmere forsaking-Ideals (GM-toggleable).

The packs encode no RAW penalty for breaking oaths (only the lore — the spren
dies/withdraws). Per the user's principle (Cosmere house-rules are GM-toggleable,
not hardcoded RAW), forsaking is a toggle, not an auto-punishment:

- A "forsaken" flag (play_state) seals the Stormlight Actions plate (surges,
  Breathe, Enhance, Regenerate, Mend Injury, squire) and dismisses a summoned
  Shardblade — the spren has withdrawn — until the bond is renewed.
- No Investiture drain (recommended default; the rules are silent).

Verified live on a 3rd-Ideal Windrunner: forsaking greys + locks the actions
plate, shows the banner, dismisses the Shardblade, and persists; renewing
restores it.
"""
from __future__ import annotations

import pathlib

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_sheet_has_forsake_toggle_and_lock():
    assert 'window.cosForsake' in _SHEET
    assert 'btn-forsake' in _SHEET
    assert 'cos-forsaken-banner' in _SHEET
    # the lock styling seals the actions plate
    assert '.cs-rad-plate.forsaken' in _SHEET
    assert 'pointer-events:none' in _SHEET
    # forsaking dismisses a summoned Shardblade
    assert 'cur.forsaken && cur.shardblade' in _SHEET


@pytest.fixture
def radiant_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'fa' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kal', 'owner_user_id': 'u1',
           'build': {'name': 'Kal', 'level': 8, 'path': 'warrior', 'radiant_order': 'windrunners',
                     'ideals_sworn': 3, 'attributes': {'str': 3, 'spd': 3, 'wil': 2},
                     'skills': {'hwp': 3}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def test_state_route_persists_forsaken(radiant_pc):
    c = app.app.test_client()
    r = c.post('/cosmere/pc/' + radiant_pc + '/state', json={'forsaken': True}).get_json()
    assert r['ok'] and r['play_state']['forsaken'] is True
    r2 = c.post('/cosmere/pc/' + radiant_pc + '/state', json={'forsaken': False}).get_json()
    assert r2['play_state']['forsaken'] is False


def test_forsaken_sheet_renders_renew_and_banner(radiant_pc):
    # seed forsaken state, then the sheet should show the locked/renew affordances
    app.app.test_client().post('/cosmere/pc/' + radiant_pc + '/state', json={'forsaken': True})
    html = app.app.test_client().get('/cosmere/pc/' + radiant_pc).get_data(as_text=True)
    assert 'Renew oaths' in html                    # toggle flips to renew when forsaken
    assert 'id="cos-sl-actions"' in html and 'forsaken' in html
