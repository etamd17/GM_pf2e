"""Loot ledger -> sheet award (queue #4).

Spec: docs/superpowers/specs/2026-07-09-loot-award-design.md

The GM's loot ledger can AWARD a logged entry to a specific PC, depositing
its items into the character's inventory and its coins into their wallet,
then notifying the sheet -- rather than only logging it. Built on the
existing send_loot deposit path (now a shared _deposit_loot_to_pc core).
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'
_AJAX = {'X-Requested-With': 'XMLHttpRequest'}


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    raw = json.loads(_FIX.read_text())
    pc_file = tmp_path / 'Kyle.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    pc = Character(raw, file_path=str(pc_file))
    name = pc.name
    monkeypatch.setitem(app_module.PARTY_LIBRARY, name, pc)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == name else None)
    # In-memory loot ledger so tests don't touch the campaign store.
    ledger = {'entries': []}
    monkeypatch.setattr(app_module, '_load_loot_ledger', lambda: ledger)
    monkeypatch.setattr(app_module, '_save_loot_ledger', lambda l: None)
    monkeypatch.setattr(app_module, '_mutate_loot_ledger',
                        lambda fn: fn(ledger))
    broadcasts = []
    monkeypatch.setattr(app_module, 'sse_broadcast',
                        lambda ev, data, **k: broadcasts.append((ev, data)))
    return {'name': name, 'file': pc_file, 'pc': pc, 'ledger': ledger,
            'broadcasts': broadcasts}


def _equip_names(pc_file):
    build = json.loads(pathlib.Path(pc_file).read_text()).get('build', {})
    out = {}
    for eq in build.get('equipment', []):
        if isinstance(eq, list) and eq:
            out[str(eq[0]).lower()] = int(eq[1]) if len(eq) > 1 else 1
        elif isinstance(eq, dict) and eq.get('name'):
            out[eq['name'].lower()] = int(eq.get('qty', 1))
    return out


def _client():
    return app_module.app.test_client()


# ---------------------------------------------------------------------------
# Deposit core
# ---------------------------------------------------------------------------

def test_deposit_adds_items_and_coins(kyle):
    gp0 = int(getattr(kyle['pc'], 'gp', 0) or 0)
    app_module._deposit_loot_to_pc(
        kyle['name'],
        [{'name': 'Vialglass Elixir', 'qty': 2}],
        {'gp': 50, 'sp': 3})
    eq = _equip_names(kyle['file'])
    assert eq.get('vialglass elixir') == 2
    assert int(app_module.PARTY_LIBRARY[kyle['name']].gp) == gp0 + 50
    assert int(app_module.PARTY_LIBRARY[kyle['name']].sp) == 3


def test_deposit_dedups_existing_item(kyle):
    # A name Kyle definitely doesn't start with, so the count is unambiguous.
    app_module._deposit_loot_to_pc(kyle['name'], [{'name': 'Zorblax Widget', 'qty': 1}], {})
    app_module._deposit_loot_to_pc(kyle['name'], [{'name': 'zorblax widget', 'qty': 2}], {})
    eq = _equip_names(kyle['file'])
    assert eq.get('zorblax widget') == 3, 'must stack (case-insensitive), not duplicate'
    # exactly one row for it
    assert sum(1 for k in eq if k == 'zorblax widget') == 1


# ---------------------------------------------------------------------------
# send_loot still deposits through the shared core
# ---------------------------------------------------------------------------

def test_send_loot_route_still_deposits(kyle):
    gp0 = int(getattr(app_module.PARTY_LIBRARY[kyle['name']], 'gp', 0) or 0)
    r = _client().post('/api/send_loot', json={
        'target': kyle['name'], 'items': [{'name': 'Glowing Zorb', 'qty': 4}],
        'coins': {'gp': 5}})
    assert r.status_code == 200 and r.get_json()['success']
    assert _equip_names(kyle['file']).get('glowing zorb') == 4
    assert int(app_module.PARTY_LIBRARY[kyle['name']].gp) == gp0 + 5
    assert any(ev == 'loot_received' for ev, _ in kyle['broadcasts'])


# ---------------------------------------------------------------------------
# Award a ledger entry to a sheet
# ---------------------------------------------------------------------------

def _add_entry(kyle, **over):
    entry = {'id': 'e1', 'recipient': kyle['name'],
             'items': [{'name': 'Runeblade Prototype', 'qty': 1}],
             'coins': {'gp': 10}, 'note': ''}
    entry.update(over)
    kyle['ledger']['entries'].append(entry)
    return entry


def test_award_entry_deposits_and_marks(kyle):
    _add_entry(kyle)
    r = _client().post('/api/loot_ledger/e1/award', json={})
    assert r.status_code == 200, r.data
    assert _equip_names(kyle['file']).get('runeblade prototype') == 1
    entry = kyle['ledger']['entries'][0]
    assert entry.get('awarded_to') == kyle['name']
    assert entry.get('awarded_at')
    assert any(ev == 'loot_received' for ev, _ in kyle['broadcasts'])


def test_award_explicit_target_overrides_recipient(kyle):
    _add_entry(kyle, recipient='The Party')     # recipient not a PC
    r = _client().post('/api/loot_ledger/e1/award', json={'target': kyle['name']})
    assert r.status_code == 200
    assert _equip_names(kyle['file']).get('runeblade prototype') == 1
    assert kyle['ledger']['entries'][0].get('awarded_to') == kyle['name']


def test_award_unknown_entry_404(kyle):
    r = _client().post('/api/loot_ledger/nope/award', json={})
    assert r.status_code == 404


def test_award_non_pc_target_400(kyle):
    _add_entry(kyle, recipient='The Party')     # no PC to default to
    r = _client().post('/api/loot_ledger/e1/award', json={})
    assert r.status_code == 400


def test_award_double_rejected_without_force(kyle):
    _add_entry(kyle)
    assert _client().post('/api/loot_ledger/e1/award', json={}).status_code == 200
    r = _client().post('/api/loot_ledger/e1/award', json={})
    assert r.status_code == 409
    # not deposited twice
    assert _equip_names(kyle['file']).get('runeblade prototype') == 1


def test_award_double_allowed_with_force(kyle):
    _add_entry(kyle)
    assert _client().post('/api/loot_ledger/e1/award', json={}).status_code == 200
    r = _client().post('/api/loot_ledger/e1/award', json={'force': True})
    assert r.status_code == 200
    assert _equip_names(kyle['file']).get('runeblade prototype') == 2
