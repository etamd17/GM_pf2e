"""PF2e sheet full-state refetch on SSE reconnect/wake.

After a phone sleep / wifi blip (the most common in-person event) the sheet must
pull a COMPLETE fresh state (AC, saves, skills, strikes, shield, temp HP,
conditions) — the old fallback only patched HP+conditions after 45s of silence.
The new /api/pc_state/<name> route serves the same payload the live pc_update
frame carries, and the sheet refetches it on the hub's 'connected' event.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'
_SHEET = pathlib.Path(__file__).parent.parent / 'templates' / 'player_sheet.html'


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    raw = json.loads(_FIX.read_text())
    pc_file = tmp_path / 'Kyle.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    monkeypatch.setitem(app_module.PARTY_LIBRARY, 'Kyle', Character(raw, file_path=str(pc_file)))
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda n: str(pc_file) if n == 'Kyle' else None)
    return 'Kyle'


def test_pc_state_route_returns_full_derived_payload(kyle):
    r = app_module.app.test_client().get('/api/pc_state/Kyle')
    assert r.status_code == 200
    d = r.get_json()
    assert d['name'] == 'Kyle'
    assert 'derived' in d and 'skills' in d['derived'] and 'attacks' in d['derived']
    for key in ('ac', 'shield', 'conditions', 'max_hp', 'current_hp'):
        assert key in d
    assert d['max_hp'] == app_module.PARTY_LIBRARY['Kyle'].hp


def test_pc_state_unknown_is_404():
    r = app_module.app.test_client().get('/api/pc_state/NotARealPC')
    assert r.status_code == 404


def test_sheet_refetches_full_state_on_reconnect():
    html = _SHEET.read_text()
    assert 'window.refetchPcState' in html
    assert '/api/pc_state/' in html
    assert 'window.applyPcUpdate' in html
    # the hub's reconnect/wake event triggers the full refetch
    assert "appSSE('connected'" in html and 'refetchPcState()' in html
