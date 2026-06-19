"""Cosmere world-skin LORE data (cosmetic only — not rules).

The Stormlight engine is the one true ruleset; these tables drive the Mistborn
visual skin's reference panel + the optional per-character "house metal" crest.
Nothing here touches mechanics.
"""
from __future__ import annotations

# The four Allomantic metal families, in table order, each a muted tint that
# matches the glyph medallions in templates/_cosmere_glyphs.html.
METAL_FAMILIES = ('physical', 'mental', 'enhancement', 'temporal')
METAL_FAMILY_LABEL = {
    'physical': 'Physical', 'mental': 'Mental',
    'enhancement': 'Enhancement', 'temporal': 'Temporal',
}
METAL_FAMILY_TINT = {
    'physical': '#8fb3c9', 'mental': '#a99bc7',
    'enhancement': '#c2a36a', 'temporal': '#84b8a6',
}

# The sixteen Allomantic metals. `family` groups the table; `effect` is a terse
# one-liner. Keys match the cg-metal-* glyph ids.
METALS = {
    'iron':      {'name': 'Iron',      'family': 'physical',    'effect': 'Pull on nearby metals'},
    'steel':     {'name': 'Steel',     'family': 'physical',    'effect': 'Push on nearby metals'},
    'tin':       {'name': 'Tin',       'family': 'physical',    'effect': 'Heighten the senses'},
    'pewter':    {'name': 'Pewter',    'family': 'physical',    'effect': 'Boost physical ability'},
    'zinc':      {'name': 'Zinc',      'family': 'mental',      'effect': "Inflame others' emotions"},
    'brass':     {'name': 'Brass',     'family': 'mental',      'effect': "Soothe others' emotions"},
    'copper':    {'name': 'Copper',    'family': 'mental',      'effect': 'Hide Allomantic pulses'},
    'bronze':    {'name': 'Bronze',    'family': 'mental',      'effect': 'Sense Allomancy nearby'},
    'aluminum':  {'name': 'Aluminum',  'family': 'enhancement', 'effect': 'Wipe your own metals'},
    'duralumin': {'name': 'Duralumin', 'family': 'enhancement', 'effect': 'Flare a metal all at once'},
    'chromium':  {'name': 'Chromium',  'family': 'enhancement', 'effect': "Wipe another's metals"},
    'nicrosil':  {'name': 'Nicrosil',  'family': 'enhancement', 'effect': "Flare another's metal"},
    'gold':      {'name': 'Gold',      'family': 'temporal',    'effect': 'See your past self'},
    'electrum':  {'name': 'Electrum',  'family': 'temporal',    'effect': 'See your own future'},
    'cadmium':   {'name': 'Cadmium',   'family': 'temporal',    'effect': 'Slow time in a bubble'},
    'bendalloy': {'name': 'Bendalloy', 'family': 'temporal',    'effect': 'Speed time in a bubble'},
}


def metal_tint(key) -> str:
    """The family tint for a metal key (the Mistborn 'house metal' accent)."""
    m = METALS.get((key or '').lower())
    return METAL_FAMILY_TINT.get(m['family'], '#c8cdd5') if m else '#c8cdd5'


def metals_by_family():
    """[(family_key, label, tint, [metal dicts])] in table order — for the panel."""
    out = []
    for fam in METAL_FAMILIES:
        rows = [dict(v, key=k) for k, v in METALS.items() if v['family'] == fam]
        out.append((fam, METAL_FAMILY_LABEL[fam], METAL_FAMILY_TINT[fam], rows))
    return out
