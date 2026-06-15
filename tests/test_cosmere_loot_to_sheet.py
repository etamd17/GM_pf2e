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


def test_credit_cosmere_loot_ignores_blank_items():
    doc = {'id': 'p2', 'name': 'Shallan'}
    w = app._credit_cosmere_loot(doc, [{'name': '', 'qty': 5}, {'name': '  ', 'qty': 1}], {})
    assert w['items'] == []
    assert w['spheres'] == {'chip': 0, 'mark': 0, 'broam': 0}
