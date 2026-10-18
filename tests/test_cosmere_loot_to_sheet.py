"""Cosmere loot crediting: awarding spheres/items to a named PC must update that
PC's sheet wallet, not just append a ledger note (PF2e send_loot already credits
the sheet; Cosmere was record-only, so player-visible wealth desynced).
"""
from __future__ import annotations

import copy
import json

import app
import pytest
from systems.cosmere.spheres import normalize_wallet


@pytest.fixture
def loot_routes(tmp_path, monkeypatch):
    ledger_path = tmp_path / 'loot.json'
    original = {
        'id': 'a' * 32,
        'name': 'Kal',
        'wallet': {
            'spheres': {'chip': 4, 'mark': 2, 'broam': 1},
            'items': [{'name': 'Rope', 'qty': 2}],
        },
    }
    store = {original['id']: copy.deepcopy(original)}
    saves = []

    def load(pid):
        return copy.deepcopy(store.get(pid))

    def save(doc, fsync=True):
        saves.append(copy.deepcopy(doc))
        store[doc['id']] = copy.deepcopy(doc)
        return doc['id']

    monkeypatch.setattr(app, 'LOOT_LEDGER_FILE', str(ledger_path))
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_list_cosmere_pcs', lambda: [copy.deepcopy(v) for v in store.values()])
    monkeypatch.setattr(app, '_load_cosmere_pc', load)
    monkeypatch.setattr(app, '_save_cosmere_pc', save)
    monkeypatch.setattr(app, 'sse_broadcast', lambda *_a, **_k: None)
    return app.app.test_client(), store, saves


def _award(client, recipient='Kal', gem='diamond'):
    return client.post('/api/cosmere/loot/add', json={
        'recipient': recipient,
        'gem': gem,
        'items': [{'name': 'Rope', 'qty': 3}, {'name': 'Knife', 'qty': 1}],
        'spheres': {'chip': 2, 'mark': 1, 'broam': 1},
    })


def _only_entry(client):
    entries = client.get('/api/cosmere/loot').get_json()['entries']
    assert len(entries) == 1
    return entries[0]


def test_credit_cosmere_loot_accumulates_spheres_and_merges_items():
    doc = {'id': 'p1', 'name': 'Kal'}
    w = app._credit_cosmere_loot(doc, [{'name': 'Rope', 'qty': 1}], {'chip': 3, 'mark': 1})
    assert w == {
        'version': 2,
        'spheres': {'diamond': {'chip': 3, 'mark': 1, 'broam': 0}},
        'items': [{'name': 'Rope', 'qty': 1}],
    }
    assert {'name': 'Rope', 'qty': 1} in w['items']

    # A second award accumulates only in its own gem and merges the same item.
    w2 = app._credit_cosmere_loot(doc, [{'name': 'Rope', 'qty': 2}, {'name': 'Knife', 'qty': 1}],
                                  {'chip': 2, 'broam': 1}, 'emerald')
    assert w2['spheres'] == {
        'diamond': {'chip': 3, 'mark': 1, 'broam': 0},
        'emerald': {'chip': 2, 'mark': 0, 'broam': 1},
    }
    rope = next(i for i in w2['items'] if i['name'] == 'Rope')
    assert rope['qty'] == 3
    assert any(i['name'] == 'Knife' and i['qty'] == 1 for i in w2['items'])
    # persisted on the doc so the sheet can render it
    assert doc['wallet'] == w2


def test_builder_save_preserves_wallet_and_play_state(tmp_path, monkeypatch):
    """Leveling up / editing a Cosmere PC in the builder must NOT wipe the
    GM-awarded wallet (top-level doc['wallet']) or live play_state -- the builder
    rebuilds the doc from scratch, so it has to carry these forward."""
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(tmp_path))
    pid = 'b' * 32
    build = {'name': 'Kal', 'level': 1, 'ancestry': 'Human', 'path': 'Warrior',
             'attributes': {'str': 1, 'spd': 1, 'int': 1, 'wil': 1, 'awa': 1, 'pre': 1}}
    existing = {'id': pid, 'system': 'cosmere', 'name': 'Kal', 'owner_user_id': None,
                'build': build,
                'wallet': {'spheres': {'chip': 10, 'mark': 2, 'broam': 1},
                           'items': [{'name': 'Sphere Pouch', 'qty': 1}]},
                'play_state': {'health': 5, 'conditions': {'slowed': True}}}
    with open(tmp_path / (pid + '.json'), 'w', encoding='utf-8') as f:
        json.dump(existing, f)
    r = app.app.test_client().post('/cosmere/builder', json={'id': pid, 'build': build})
    assert r.status_code == 200
    doc = app._load_cosmere_pc(pid)
    assert doc.get('wallet', {}).get('spheres', {}).get('chip') == 10   # wallet kept
    assert any(i['name'] == 'Sphere Pouch' for i in doc['wallet']['items'])
    assert doc.get('play_state', {}).get('health') == 5                 # play_state kept


def test_legacy_wallet_sheet_is_read_only_until_real_loot_change(tmp_path, monkeypatch):
    pc_dir = tmp_path / 'pcs'
    pc_dir.mkdir()
    pid = 'c' * 32
    path = pc_dir / f'{pid}.json'
    legacy = {
        'id': pid,
        'system': 'cosmere',
        'name': 'Legacy Holder',
        'owner_user_id': None,
        'build': {
            'name': 'Legacy Holder', 'level': 1, 'path': 'warrior',
            'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2},
        },
        'wallet': {
            'spheres': {'chip': 2, 'mark': 1, 'broam': 1},
            'items': [{'name': 'Old Rope', 'qty': 1}],
        },
    }
    path.write_text(json.dumps(legacy, indent=2), encoding='utf-8')
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(pc_dir))
    monkeypatch.setattr(app, 'LOOT_LEDGER_FILE', str(tmp_path / 'loot.json'))
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, '_cosmere_world', lambda: 'stormlight')
    monkeypatch.setattr(app, 'sse_broadcast', lambda *_a, **_k: None)

    before_bytes = path.read_bytes()
    before_mtime = path.stat().st_mtime_ns
    sheet = app.app.test_client().get(f'/cosmere/pc/{pid}')

    assert sheet.status_code == 200
    html = sheet.get_data(as_text=True)
    assert 'data-gem="diamond"' in html
    assert '5.4 mk' in html
    assert path.read_bytes() == before_bytes
    assert path.stat().st_mtime_ns == before_mtime

    changed = app.app.test_client().post('/api/cosmere/loot/add', json={
        'recipient': 'Legacy Holder',
        'gem': 'emerald',
        'items': [],
        'spheres': {'chip': 0, 'mark': 0, 'broam': 1},
    })
    assert changed.status_code == 200, changed.get_json()
    saved = json.loads(path.read_text(encoding='utf-8'))['wallet']
    assert saved == {
        'version': 2,
        'spheres': {
            'diamond': {'chip': 2, 'mark': 1, 'broam': 1},
            'emerald': {'chip': 0, 'mark': 0, 'broam': 1},
        },
        'items': [{'name': 'Old Rope', 'qty': 1}],
    }


def test_credit_cosmere_loot_ignores_blank_items():
    doc = {'id': 'p2', 'name': 'Shallan'}
    w = app._credit_cosmere_loot(doc, [{'name': '', 'qty': 5}, {'name': '  ', 'qty': 1}], {})
    assert w['items'] == []
    assert w == {'version': 2, 'spheres': {}, 'items': []}


def test_note_only_entry_does_not_rewrite_a_legacy_wallet(loot_routes, monkeypatch):
    client, store, saves = loot_routes
    before = copy.deepcopy(store['a' * 32]['wallet'])
    broadcasts = []
    monkeypatch.setattr(app, 'sse_broadcast', lambda *args, **kwargs: broadcasts.append((args, kwargs)))

    response = client.post('/api/cosmere/loot/add', json={
        'recipient': 'Kal',
        'gem': 'emerald',
        'items': [],
        'spheres': {'chip': 0, 'mark': 0, 'broam': 0},
        'note': 'The gems remain with the quartermaster.',
    })

    assert response.status_code == 200, response.get_json()
    assert store['a' * 32]['wallet'] == before
    assert saves == []
    assert broadcasts == []
    assert _only_entry(client)['recipient_pid'] == 'a' * 32


def test_loot_sse_uses_nested_wallet_and_exact_mark_total(loot_routes, monkeypatch):
    client, store, _saves = loot_routes
    broadcasts = []
    monkeypatch.setattr(
        app, 'sse_broadcast',
        lambda event, payload, **kwargs: broadcasts.append((event, copy.deepcopy(payload), kwargs)),
    )

    added = client.post('/api/cosmere/loot/add', json={
        'recipient': 'Kal',
        'gem': 'emerald',
        'items': [],
        'spheres': {'chip': 0, 'mark': 0, 'broam': 1},
    })
    assert added.status_code == 200, added.get_json()
    entry = _only_entry(client)

    assert len(broadcasts) == 1
    event, payload, kwargs = broadcasts[0]
    assert event == 'cosmere_loot' and kwargs == {}
    assert payload['gem'] == 'emerald'
    assert payload['wallet'] == store['a' * 32]['wallet']
    assert payload['wallet']['version'] == 2
    assert payload['wallet']['spheres']['emerald']['broam'] == 1
    assert payload['wallet_total_marks'] == '206.8'

    deleted = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})
    assert deleted.status_code == 200, deleted.get_json()
    assert len(broadcasts) == 2
    event, payload, kwargs = broadcasts[1]
    assert event == 'cosmere_loot' and kwargs == {}
    assert payload['wallet'] == store['a' * 32]['wallet']
    assert payload['wallet']['spheres'] == {
        'diamond': {'chip': 4, 'mark': 2, 'broam': 1},
    }
    assert payload['wallet_total_marks'] == '6.8'


def test_delete_reverses_a_named_award_from_the_wallet(loot_routes):
    client, store, _saves = loot_routes
    before = copy.deepcopy(store['a' * 32]['wallet'])

    added = _award(client)
    assert added.status_code == 200, added.get_json()
    entry = _only_entry(client)
    assert entry['recipient_pid'] == 'a' * 32

    deleted = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})
    assert deleted.status_code == 200, deleted.get_json()
    # A real credit/debit is the migration point for a legacy flat wallet.
    assert store['a' * 32]['wallet'] == normalize_wallet(before)
    assert client.get('/api/cosmere/loot').get_json()['entries'] == []


def test_emerald_award_delete_restores_exact_v2_wallet(loot_routes):
    client, store, _saves = loot_routes
    store['a' * 32]['wallet'] = normalize_wallet(store['a' * 32]['wallet'])
    before = copy.deepcopy(store['a' * 32]['wallet'])

    assert _award(client, gem='emerald').status_code == 200
    entry = _only_entry(client)
    assert entry['gem'] == 'emerald'
    assert store['a' * 32]['wallet']['spheres']['emerald'] == {
        'chip': 2, 'mark': 1, 'broam': 1,
    }

    deleted = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})
    assert deleted.status_code == 200, deleted.get_json()
    assert store['a' * 32]['wallet'] == before


def test_delete_refuses_when_the_awarded_wallet_value_was_spent(loot_routes):
    client, store, _saves = loot_routes
    assert _award(client).status_code == 200
    entry = _only_entry(client)
    store['a' * 32]['wallet']['spheres']['diamond']['broam'] = 0
    after_spend = copy.deepcopy(store['a' * 32]['wallet'])

    deleted = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})

    assert deleted.status_code == 409
    assert 'enough' in deleted.get_json()['error'].lower()
    assert store['a' * 32]['wallet'] == after_spend
    assert _only_entry(client)['id'] == entry['id']


def test_delete_refuses_when_only_another_gem_can_cover_the_award(loot_routes):
    client, store, _saves = loot_routes
    store['a' * 32]['wallet'] = {
        'version': 2,
        'spheres': {'diamond': {'chip': 0, 'mark': 0, 'broam': 100}},
        'items': [{'name': 'Rope', 'qty': 2}],
    }
    assert _award(client, gem='emerald').status_code == 200
    entry = _only_entry(client)
    # Spend the awarded emerald broam. Diamond broams are worth enough but are
    # a different holding and cannot reverse an emerald award.
    store['a' * 32]['wallet']['spheres']['emerald']['broam'] = 0
    after_spend = copy.deepcopy(store['a' * 32]['wallet'])

    deleted = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})

    assert deleted.status_code == 409
    assert 'emerald broams' in deleted.get_json()['error'].lower()
    assert store['a' * 32]['wallet'] == after_spend
    assert _only_entry(client)['id'] == entry['id']


def test_old_ledger_entry_without_gem_debits_diamond(loot_routes):
    client, store, _saves = loot_routes
    store['a' * 32]['wallet'] = normalize_wallet(store['a' * 32]['wallet'])
    before = copy.deepcopy(store['a' * 32]['wallet'])
    app._save_loot_ledger({'entries': [{
        'id': 'legacy-entry',
        'recipient': 'Kal',
        'recipient_pid': 'a' * 32,
        'items': [],
        'spheres': {'chip': 1, 'mark': 0, 'broam': 0},
    }]})
    store['a' * 32]['wallet']['spheres']['diamond']['chip'] += 1

    deleted = client.post('/api/cosmere/loot/delete', json={'id': 'legacy-entry'})

    assert deleted.status_code == 200, deleted.get_json()
    assert store['a' * 32]['wallet'] == before
    assert client.get('/api/cosmere/loot').get_json()['entries'] == []


def test_wallet_save_failure_leaves_no_ledger_entry(loot_routes, monkeypatch):
    client, store, _saves = loot_routes
    before = copy.deepcopy(store['a' * 32]['wallet'])

    def fail_save(_doc, fsync=True):
        raise OSError('injected wallet failure')

    monkeypatch.setattr(app, '_save_cosmere_pc', fail_save)
    response = _award(client)

    assert response.status_code == 503
    assert store['a' * 32]['wallet'] == before
    assert app._load_loot_ledger()['entries'] == []


def test_ledger_save_failure_rolls_the_wallet_back(loot_routes, monkeypatch):
    client, store, saves = loot_routes
    before = copy.deepcopy(store['a' * 32]['wallet'])

    def fail_ledger(_ledger):
        raise OSError('injected ledger failure')

    monkeypatch.setattr(app, '_save_loot_ledger', fail_ledger)
    response = _award(client)

    assert response.status_code == 503
    assert store['a' * 32]['wallet'] == before
    assert len(saves) == 2  # attempted credit, then compensating wallet restore
    assert app._load_loot_ledger()['entries'] == []


def test_delete_ledger_save_failure_restores_the_awarded_wallet(loot_routes, monkeypatch):
    client, store, saves = loot_routes
    assert _award(client, gem='emerald').status_code == 200
    entry = _only_entry(client)
    awarded_wallet = copy.deepcopy(store['a' * 32]['wallet'])
    saves_before_delete = len(saves)

    def fail_ledger(_ledger):
        raise OSError('injected delete ledger failure')

    monkeypatch.setattr(app, '_save_loot_ledger', fail_ledger)
    response = client.post('/api/cosmere/loot/delete', json={'id': entry['id']})

    assert response.status_code == 503
    assert store['a' * 32]['wallet'] == awarded_wallet
    assert len(saves) == saves_before_delete + 2  # attempted debit, then rollback
    assert _only_entry(client)['id'] == entry['id']


def test_party_award_is_ledger_only_and_can_be_removed(loot_routes):
    client, store, saves = loot_routes
    before = copy.deepcopy(store)

    assert _award(client, 'The Party').status_code == 200
    entry = _only_entry(client)
    assert 'recipient_pid' not in entry
    assert client.post('/api/cosmere/loot/delete', json={'id': entry['id']}).status_code == 200
    assert store == before
    assert saves == []
