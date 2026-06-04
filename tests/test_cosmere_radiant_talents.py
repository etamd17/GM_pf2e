"""Radiant talent trees (Phase 4) — the rulebook-mined surge + spren-bond
talent dataset and its builder integration.
"""
from __future__ import annotations

import app
import systems.cosmere as cos
import systems.cosmere.radiant_talents as RT
from systems.cosmere.radiant import RADIANT_ORDERS


def test_surge_talent_trees_cover_all_ten_surges():
    assert set(RT.SURGE_TALENTS) == set(cos.SURGE_SKILLS)
    assert sum(len(v) for v in RT.SURGE_TALENTS.values()) >= 80
    for code, lst in RT.SURGE_TALENTS.items():
        assert lst, code
        for t in lst:
            assert t['name'] and isinstance(t['prereq'], dict)
        # each surge tree has at least one First-Ideal entry talent.
        assert any(t['prereq'].get('ideal') for t in lst), code


def test_spren_bond_trees_cover_nine_orders():
    assert set(RT.ORDER_TALENTS) == set(RADIANT_ORDERS)
    assert sum(len(v) for v in RT.ORDER_TALENTS.values()) >= 80


def test_known_canon_talents_present():
    adh = {t['name'] for t in RT.SURGE_TALENTS['adh']}
    assert 'Binding Strike' in adh
    grv = {t['name'] for t in RT.SURGE_TALENTS['grv']}
    assert 'Flying Ace' in grv


def test_prereq_chains_reference_real_talents():
    # A talent prereq named another talent in the same surge tree should
    # usually resolve to a real talent there (spot-check Adhesion's chain).
    adh = RT.SURGE_TALENTS['adh']
    names = {t['name'] for t in adh}
    chained = [t for t in adh if t['prereq'].get('talent') in names]
    assert chained                      # at least some real intra-tree chains


def test_builder_offers_radiant_talents():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Radiant talent' in body
    assert 'Binding Strike' in body     # the mined surge talents reached the page
