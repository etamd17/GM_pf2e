"""Cosmere Radiant ORDER VARIANTS (Stormlight Canon sub-paths), RAW from the packs.

Three orders have variants:
  - Dustbringers / Canon — wield only Abrasion (drop Division surge)
  - Skybreakers / Nale  — same surges, sterner philosophy (flavor)
  - Truthwatchers / Enlightened — gain the Enlightened Talents tree

The variant is a build field (radiant_variant). build.order() returns a
variant-adjusted copy, so surge_codes() and everything downstream reflect it
(Canon's lost Division propagates to surge skills/powers/trees). The builder gets
a variant selector; the sheet shows the variant name.

Verified live in the builder: Canon Dustbringer shows only Abrasion (surge
skills, powers, and the Abrasion tree — no Division); Enlightened Truthwatcher
gains the Enlightened Talents tree (default no longer shows it).
"""
from __future__ import annotations

import pathlib

import pytest

import app
import systems.cosmere.radiant as R
import systems.cosmere.build as B

_REPO = pathlib.Path(__file__).resolve().parent.parent
_BUILDER = (_REPO / 'templates' / 'cosmere_builder.html').read_text()


def test_variant_catalog():
    assert set(R.variants('dustbringers')) == {'canon'}
    assert set(R.variants('skybreakers')) == {'nale'}
    assert set(R.variants('truthwatchers')) == {'enlightened'}
    assert R.variants('windrunners') == {}
    assert R.RADIANT_VARIANTS['truthwatchers']['enlightened']['extra_tree'] == 'Enlightened Talents'


def test_order_with_variant_drops_and_marks():
    canon = R.order_with_variant('dustbringers', 'canon')
    assert canon['surges'] == ['abr'] and canon['variant'] == 'canon'
    base = R.order_with_variant('dustbringers', '')
    assert tuple(base['surges']) == ('abr', 'dvs') and 'variant' not in base
    # Nale keeps both surges (philosophy only)
    nale = R.order_with_variant('skybreakers', 'nale')
    assert set(nale['surges']) == set(R.RADIANT_ORDERS['skybreakers']['surges'])


def test_build_surge_codes_follow_variant():
    b = B.CosmereBuild({'radiant_order': 'dustbringers', 'radiant_variant': 'canon'})
    assert b.surge_codes() == ('abr',)
    assert b.to_dict()['radiant_variant'] == 'canon'
    # an invalid variant for the order is flagged
    bad = B.CosmereBuild({'radiant_order': 'windrunners', 'radiant_variant': 'canon'})
    assert any('variant' in w.lower() for w in bad.validate())


def test_builder_has_variant_selector():
    assert 'id="f-variant"' in _BUILDER
    assert 'function onVariant' in _BUILDER
    assert 'RADIANT_VARIANTS' in _BUILDER
    assert 'function effSurges' in _BUILDER and 'function _orderTrees' in _BUILDER
    assert 'radiant_variant:' in _BUILDER          # serialized into the saved build


def test_builder_context_and_sheet_expose_variants():
    # builder context carries the catalog
    import flask
    with app.app.test_request_context('/cosmere/builder'):
        ctx = app._cosmere_builder_context(B.CosmereBuild({'radiant_order': 'dustbringers'}))
    assert 'radiant_variants' in ctx and 'dustbringers' in ctx['radiant_variants']
