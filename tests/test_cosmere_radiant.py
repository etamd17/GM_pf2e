"""Radiant / Surgebinding (Phase 4) — canon data integrity, engine, and routes.

Asserts the order -> spren / surges / philosophy table is internally consistent
with the surge skills, that becoming Radiant unlocks exactly the order's two
surges + Investiture + the Stormlight actions, and that the builder/sheet routes
surface it.
"""
from __future__ import annotations

import os

import app
import systems.cosmere as cos
import systems.cosmere.radiant as R
from systems.cosmere.build import CosmereBuild
from systems.cosmere.actor import CosmereActor


# -- canon data integrity ---------------------------------------------------

def test_nine_playable_orders_and_surge_consistency():
    assert len(R.RADIANT_ORDERS) == 9                  # Bondsmiths excluded
    assert 'bondsmiths' not in R.RADIANT_ORDERS
    for key, o in R.RADIANT_ORDERS.items():
        assert o['name'] and o['spren'] and o['philosophy']
        assert len(o['surges']) == 2
        for code in o['surges']:
            assert code in R.SURGES                     # a real surge
            assert code in cos.SURGE_SKILLS             # maps to a surge skill
            assert code in cos.SKILL_ATTR
    # Canon spot-checks straight from the rulebook table.
    assert R.RADIANT_ORDERS['windrunners']['surges'] == ('adh', 'grv')
    assert R.RADIANT_ORDERS['windrunners']['spren'] == 'Honorspren'
    assert R.RADIANT_ORDERS['edgedancers']['surges'] == ('abr', 'prg')
    assert R.RADIANT_ORDERS['lightweavers']['surges'] == ('ill', 'trs')
    assert R.FIRST_IDEAL.startswith('Life before death')


def test_every_surge_skill_belongs_to_some_order():
    covered = set()
    for o in R.RADIANT_ORDERS.values():
        covered.update(o['surges'])
    covered.update(R.BONDSMITHS['surges'])
    assert covered == set(cos.SURGE_SKILLS)             # all 10 surges are assigned


# -- engine -----------------------------------------------------------------

def _windrunner(level=2, ideals=1):
    return CosmereBuild({
        'name': 'Kal', 'level': level, 'path': 'warrior',
        'radiant_order': 'windrunners', 'ideals_sworn': ideals, 'spren_name': 'Syl',
        'attributes': {'str': 3, 'spd': 3, 'int': 1, 'wil': 2, 'awa': 2, 'pre': 1},
        'skills': {'adh': 1, 'grv': 1},
    })


def test_first_ideal_unlocks_surges_and_investiture():
    b = _windrunner()
    assert b.is_radiant is True
    assert set(b.surges_unlocked()) == {'adh', 'grv'}
    assert b.investiture_max() == 4                     # 2 + max(AWA 2, PRE 1)
    assert b.skill_ranks_available() == 9               # L2 base 7 + 2 free surge ranks
    assert b.validate() == []
    actor = CosmereActor(b.to_actor_doc())
    assert actor.skills['adh']['unlocked'] is True
    assert actor.skills['dvs']['unlocked'] is False     # not this order's surge
    assert actor.investiture_max == 4
    assert {'Breathe Stormlight', 'Enhance', 'Regenerate'} <= {a['name'] for a in actor.actions}


def test_order_without_first_ideal_grants_investiture_but_no_surges():
    b = _windrunner(ideals=0)
    assert b.is_radiant is True and b.investiture_max() == 4
    assert b.surges_unlocked() == ()                    # surges still locked
    assert any('Surge skills require' in i for i in b.validate())  # the adh/grv ranks are stray


def test_radiant_requires_level_2():
    b = _windrunner(level=1, ideals=1)
    assert any('requires level 2' in i for i in b.validate())


def test_round_trip_preserves_radiant():
    again = CosmereBuild.from_actor_doc(_windrunner().to_actor_doc())
    assert again.radiant_order == 'windrunners' and again.ideals_sworn == 1
    assert again.spren_name == 'Syl'


# -- routes -----------------------------------------------------------------

def test_builder_offers_orders():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Radiant Order' in body and 'Windrunners' in body and 'Surgebinding' in body


def test_radiant_pc_sheet_shows_order():
    c = app.app.test_client()
    d = c.post('/cosmere/builder', json={'build': _windrunner().to_dict()}).get_json()
    try:
        body = c.get(d['url']).data.decode()
        assert 'Windrunners' in body and 'Honorspren' in body or 'Syl' in body
        assert 'Adhesion' in body and 'Gravitation' in body   # the unlocked surges
        assert 'Life before death' in body                    # First Ideal
    finally:
        p = app._cosmere_pc_path(d['id'])
        if p and os.path.isfile(p):
            os.remove(p)
