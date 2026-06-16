"""Cosmere loot crediting: awarding spheres/items to a named PC must update that
PC's sheet wallet, not just append a ledger note (PF2e send_loot already credits
the sheet; Cosmere was record-only, so player-visible wealth desynced).
"""
from __future__ import annotations

import app


def test_credit_cosmere_loot_accumulates_spheres_and_merges_items():
    doc = {'id': 'p1', 'name': 'Kal'}
    w = app._credit_cosmere_loot(doc, [{'name': 'Rope', 'qty': 1}], {'chip': 3, 'mark': 1})
    assert w['spheres'] == {'chip': 3, 'mark': 1, 'broam': 0}
    assert {'name': 'Rope', 'qty': 1} in w['items']

    # a second award accumulates spheres and merges the same item by name
    w2 = app._credit_cosmere_loot(doc, [{'name': 'Rope', 'qty': 2}, {'name': 'Knife', 'qty': 1}],
                                  {'chip': 2, 'broam': 1})
    assert w2['spheres'] == {'chip': 5, 'mark': 1, 'broam': 1}
    rope = next(i for i in w2['items'] if i['name'] == 'Rope')
    assert rope['qty'] == 3
    assert any(i['name'] == 'Knife' and i['qty'] == 1 for i in w2['items'])
    # persisted on the doc so the sheet can render it
    assert doc['wallet'] == w2


def test_builder_save_preserves_wallet_and_play_state(tmp_path, monkeypatch):
    """Leveling up / editing a Cosmere PC in the builder must NOT wipe the
    GM-awarded wallet (top-level doc['wallet']) or live play_state -- the builder
    rebuilds the doc from scratch, so it has to carry these forward."""
    import json
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


def test_credit_cosmere_loot_ignores_blank_items():
    doc = {'id': 'p2', 'name': 'Shallan'}
    w = app._credit_cosmere_loot(doc, [{'name': '', 'qty': 5}, {'name': '  ', 'qty': 1}], {})
    assert w['items'] == []
    assert w['spheres'] == {'chip': 0, 'mark': 0, 'broam': 0}
