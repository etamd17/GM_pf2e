"""Cosmere iconography (theming PR-B + PR-C).

Per-character identity + reference panels for the two world skins:
  - PR-B: a name crest (Radiant order glyph, or a Mistborn cosmetic house-metal
    glyph) in the character's secondary accent, plus surge medallion icons.
  - PR-C: the ten-order Double-Eye ring (Stormlight) and the 16-metal Allomantic
    table (Mistborn) on the player hub.

All glyphs are original monoline medallions in templates/_cosmere_glyphs.html.
Structural + data guards; the visuals were verified live in a browser on a
seeded Cosmere PC in both world skins (Windrunner crest #38bdf8 / Steel
house-metal crest; order ring + metal table).
"""
from __future__ import annotations

import os
import pathlib

import app as A
import systems.cosmere.lore as L

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    return pathlib.Path(_REPO, rel).read_text()


# ── Lore data ───────────────────────────────────────────────────────────────

def test_sixteen_metals_in_four_families():
    assert len(L.METALS) == 16
    fams = {m['family'] for m in L.METALS.values()}
    assert fams == {'physical', 'mental', 'enhancement', 'temporal'}
    grouped = L.metals_by_family()
    assert [g[0] for g in grouped] == list(L.METAL_FAMILIES)
    assert sum(len(g[3]) for g in grouped) == 16


def test_metal_tint_resolves():
    assert L.metal_tint('steel') == L.METAL_FAMILY_TINT['physical']
    assert L.metal_tint('gold') == L.METAL_FAMILY_TINT['temporal']


# ── Glyph sprite ─────────────────────────────────────────────────────────────

def test_glyph_sprite_has_every_symbol():
    import systems.cosmere.radiant as R
    sprite = _read('templates/_cosmere_glyphs.html')
    for k in list(R.RADIANT_ORDERS) + ['bondsmiths']:
        assert ('id="cg-order-%s"' % k) in sprite, 'missing order glyph %s' % k
    for code in R.SURGES:
        assert ('id="cg-surge-%s"' % code) in sprite, 'missing surge glyph %s' % code
    for k in L.METALS:
        assert ('id="cg-metal-%s"' % k) in sprite, 'missing metal glyph %s' % k


# ── PR-B: sheet crest + surge icons ─────────────────────────────────────────

def test_sheet_route_computes_crest():
    src = _read('app.py')
    assert "crest_glyph, crest_color = 'cg-metal-' + _hm" in src      # Mistborn house metal
    assert "crest_glyph, crest_color = 'cg-order-' + build.radiant_order" in src  # Stormlight order
    assert 'crest_glyph=crest_glyph, crest_color=crest_color' in src


def test_sheet_template_renders_crest_and_surges():
    h = _read('templates/cosmere_sheet.html')
    assert "include '_cosmere_glyphs.html'" in h
    assert 'cs-crest' in h and 'href="#{{ crest_glyph }}"' in h
    assert '--order-accent:{{ crest_color }}' in h
    assert 'href="#cg-surge-{{ s }}"' in h


def test_house_metal_persisted_by_builder():
    src = _read('app.py')
    assert "doc['house_metal'] = _hm" in src


# ── PR-C: hub reference panels ───────────────────────────────────────────────

def test_hub_route_passes_reference_data():
    src = _read('app.py')
    assert 'order_ref=order_ref' in src and 'metal_families=_lore.metals_by_family()' in src


def test_hub_template_has_both_references():
    h = _read('templates/cosmere_player.html')
    # Mistborn metal table + Stormlight order ring, branched on the world.
    assert "cosmere_world == 'mistborn'" in h
    assert 'class="mtab"' in h and 'href="#cg-metal-' in h
    assert 'class="ring"' in h and 'href="#cg-order-' in h
