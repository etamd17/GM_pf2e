"""Kineticist impulses must show authoritative action cost + full description.

Impulses are feats/actions, NOT spells — they're absent from master_spells.json,
so the spell action-cost path can't reach them, and the name 'Elemental Blast'
collides with a focus spell of the same name (2 actions) that is NOT the at-will
blast (1 or 2 actions). The impulse list is therefore enriched directly from the
Foundry compendium (compendium_data/{actions,feats}/.../kineticist/).

Regression guard for the bugs seen on Gavin's sheet: Scorching Column rendering
2 actions (the compendium feat is 3), and Elemental Blast rendering a stub
description with the wrong 2-action cost.
"""
from __future__ import annotations

import app


def _impulses(build):
    pc = app.Character(build)
    out = {}
    for sc in pc.spell_casters:
        for lvl in sc.get("levels", []):
            for sp in lvl.get("spells", []):
                out[sp["name"]] = sp
    return out


# Minimal kineticist; the core impulses (Elemental Blast, Base Kinesis, Channel
# Elements) come from class features, and Scorching Column is an impulse feat.
_KINETICIST = {
    "name": "ImpulseTest", "level": 3, "class": "Kineticist", "subclass": "Single Gate",
    "abilities": {"str": 0, "dex": 1, "con": 4, "int": 0, "wis": 1, "cha": 0},
    "proficiencies": {"fortitude": 4, "reflex": 2, "will": 2, "perception": 4,
                      "class_dc": 4, "unarmored": 2, "simple": 2, "martial": 2},
    "feats": [["Scorching Column", None, "Kineticist Feat", 1,
               "Single Gate Element Impulse Feat 1", "standardChoice", None]],
    "attributes": {"ancestryhp": 8, "classhp": 8, "speed": 25},
}


def test_feat_impulse_uses_compendium_action_cost():
    imp = _impulses(_KINETICIST)
    assert "Scorching Column" in imp, "impulse feat not in the impulse list"
    assert imp["Scorching Column"]["actions"] == "◆◆◆", \
        f"Scorching Column should be 3 actions, got {imp['Scorching Column']['actions']!r}"


def test_elemental_blast_resolves_name_collision_and_full_desc():
    imp = _impulses(_KINETICIST)
    eb = imp["Elemental Blast"]
    assert eb["actions"] == "◆-◆◆", \
        f"Elemental Blast (at-will) is 1 or 2 actions, got {eb['actions']!r}"
    assert len(eb.get("desc", "")) > 800, \
        f"Elemental Blast should carry the full compendium description, got {len(eb.get('desc',''))} chars"


def test_class_feature_impulses_get_full_descriptions():
    imp = _impulses(_KINETICIST)
    assert len(imp["Channel Elements"].get("desc", "")) > 500, "Channel Elements desc is a stub"
    assert len(imp["Base Kinesis"].get("desc", "")) > 800, "Base Kinesis desc is a stub"
