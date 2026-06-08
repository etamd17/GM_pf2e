"""Radiant talent trees + surge powers (builder stage 2b) -- sourced from the
ingested Stormlight Handbook Foundry data (``handbook-radiant-paths`` +
``handbook-surges``), mapped onto radiant.py's canonical order keys / surge
codes. Supersedes the earlier rulebook-PDF-mined dataset.
"""
from __future__ import annotations

import re

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
    # An intra-tree talent prerequisite should resolve to a real talent there.
    adh = RT.SURGE_TALENTS['adh']
    names = {t['name'] for t in adh}
    chained = [t for t in adh if t['prereq'].get('talent') in names]
    assert chained                      # at least some real intra-tree chains


def test_no_pdf_mining_artifacts():
    """The handbook data must be clean: no two-column OCR breaks ("T rue
    Stoneshaping"), no "Activation:" lead-in leaking into the effect text."""
    for lst in list(RT.SURGE_TALENTS.values()) + list(RT.ORDER_TALENTS.values()):
        for t in lst:
            assert not re.search(r'\b[A-Z] [a-z]', t['name']), t['name']     # "T rue", "G ravitational"
            assert not t['effect'].startswith(('Activation', 'Radiant Orders')), t


def test_order_talents_have_no_cross_order_bleed():
    """An order's talent prerequisites must never name a DIFFERENT order's
    parenthetical (the shared bond-talent slugs once contaminated this)."""
    others = {RADIANT_ORDERS[k]['name'].rstrip('s') for k in RADIANT_ORDERS}
    for plural, lst in RT.ORDER_TALENTS.items():
        own = RADIANT_ORDERS[plural]['name'].rstrip('s')
        for t in lst:
            blob = (t['prereq'].get('talent', '') + ' ' + t['prereq'].get('text', ''))
            for other in re.findall(r'\(([A-Za-z]+)\)', blob):
                assert other == own or other not in others, (plural, t['name'], other)


def test_prereq_reducer_accepts_list_shaped_talents():
    """A talent doc carries ``prerequisites[*].talents`` as a LIST, whereas a
    talent-tree node keys them by slug (a DICT). _prereq_from_node must read
    either -- the doc-level fallback in _build_order_talents depends on it."""
    list_shaped = {'prerequisites': {
        'g0': {'type': 'talent', 'talents': [{'label': 'Invested'}]}}}
    dict_shaped = {'prerequisites': {
        'g0': {'type': 'talent', 'talents': {'invested': {'label': 'Invested'}}}}}
    assert RT._prereq_from_node(list_shaped) == {'talent': 'Invested'}
    assert RT._prereq_from_node(dict_shaped) == {'talent': 'Invested'}


def test_bond_talents_keep_talent_prereqs_for_all_orders():
    """The generic Radiant bond talent Wound Regeneration requires the Invested
    talent in EVERY order. The Lightweaver (Cryptic) and Windrunner (Honorspren)
    bond trees omit the wound-regeneration node, so for those two the prereq has
    to come from the talent doc's own ``system.prerequisites`` -- they used to
    collapse to a no-prereq {'ideal': 1} entry, the bug this guards against."""
    for plural in RADIANT_ORDERS:
        wr = next((t for t in RT.ORDER_TALENTS[plural]
                   if t['name'] == 'Wound Regeneration'), None)
        assert wr is not None, plural
        assert wr['prereq'] == {'talent': 'Invested'}, (plural, wr['prereq'])
    # the two regression cases, named explicitly (no tree node -> doc fallback).
    assert {t['name']: t['prereq'] for t in RT.ORDER_TALENTS['lightweavers']}.get(
        'Wound Regeneration') == {'talent': 'Invested'}
    assert {t['name']: t['prereq'] for t in RT.ORDER_TALENTS['windrunners']}.get(
        'Wound Regeneration') == {'talent': 'Invested'}


def test_surge_powers_are_the_ten_real_powers():
    assert set(RT.SURGE_POWERS) == set(cos.SURGE_SKILLS)
    adh = RT.SURGE_POWERS['adh']
    assert adh['name'] == 'Adhesion' and adh['cost'] and adh['effect']
    # Division is a 2-action surge per the handbook power doc.
    assert RT.SURGE_POWERS['dvs']['cost'] == 2


def test_builder_offers_radiant_talents_and_powers():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Radiant talent' in body
    assert 'Binding Strike' in body          # a handbook surge talent reached the page
    assert 'RADIANT_SURGE_POWERS' in body    # the castable surges are wired in
