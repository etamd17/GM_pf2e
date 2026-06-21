"""Cosmere sheet fuller redesign — "Stormlight Gauges" + Shardplate order-glow.

The flat card grid is replaced by:
- Three living SPHERE gauges (Health / Focus / Investiture) that fill with light
  from the bottom (height %) + a rim arc, go "dun" at 0 and ember-"low" at <=25%,
  with controls revealed on tap. Driven by paint()/_orb() so they animate live.
- Defenses become quiet hex FACETS so the spheres are the hero.
- The Radiant panel becomes the one deeply order-tinted PLATE (Shardplate graft),
  with Ideal dots; everything order-tinted is driven by --order-accent so each
  order reskins.
- A sticky quick-vitals BAR mirrors the three resources + active-condition count.
- Themed roll feedback: nat-20 flares the order color, nat-1 dims (honors
  prefers-reduced-motion).

Verified live on a Windrunner: spheres drain/refill, dun at 0, low at 4/22; the
sticky bar tracks; color-mix resolves the order tint on the plate + investiture
sphere.
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_sphere_bank_replaces_resource_cards():
    assert 'cs-orbs' in _SHEET
    for key in ('health', 'focus', 'investiture'):
        assert ('orb-' + key) in _SHEET            # the sphere
        assert ('fill-' + key) in _SHEET           # the light-fill element
        assert ('arc-' + key) in _SHEET            # the rim arc
        assert ('rv-' + key) in _SHEET             # value node (paint target)
    # defenses are now hex facets, not the old card grid
    assert 'cs-facet' in _SHEET
    assert 'cs-grid cs-cards' not in _SHEET


def test_spheres_are_driven_live():
    # _orb() sets fill height + arc dasharray + dun/low classes; paint() calls it.
    assert 'function _orb(' in _SHEET
    assert "classList.toggle('dun'" in _SHEET and "classList.toggle('low'" in _SHEET
    assert "_orb('health'" in _SHEET and "_orb('investiture'" in _SHEET
    # tap-to-reveal controls
    assert 'orb-ctrl' in _SHEET and "classList.toggle('open')" in _SHEET


def test_order_flooded_radiant_plate_and_ideal_dots():
    assert 'cs-rad-plate' in _SHEET
    assert 'cs-ideal-dots' in _SHEET
    # the tint is driven by the per-character order accent (color-mix on the var)
    assert 'color-mix' in _SHEET and 'var(--order-accent' in _SHEET


def test_sticky_vitals_bar():
    assert 'cs-vbar' in _SHEET
    assert 'vb-health' in _SHEET and 'vb-cond' in _SHEET
    assert 'function syncVbar(' in _SHEET


def test_themed_roll_flare_respects_reduced_motion():
    assert 'flare-opp' in _SHEET and 'flare-comp' in _SHEET
    assert "roll.nat===20?'opp'" in _SHEET           # cosRoll passes the flare
    assert 'prefers-reduced-motion' in _SHEET
