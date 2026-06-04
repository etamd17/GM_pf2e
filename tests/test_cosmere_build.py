"""Cosmere builder + leveler engine (Phase 4) — rulebook ground truth.

The budget assertions encode the Stormlight core rulebook's Character
Advancement table directly; the derive/round-trip tests prove a build renders
the same stats CosmereActor computes; inventory tests cover Deflect + Strikes.
"""
from __future__ import annotations

import systems.cosmere as cos
from systems.cosmere import items as I
from systems.cosmere.actor import CosmereActor
from systems.cosmere.build import (
    CosmereBuild, attribute_points, total_skill_ranks, free_skill_ranks,
    total_talents, ancestry_bonus_talents, max_skill_rank, expertises_total,
    health_gain_at, level_grants,
)

_BREASTPLATE = 'xLer8raOT6EkLfWN'   # deflect 2
_AXE = 'C4o8jIXuVulD9qS9'           # 1d6 keen, hwp/str


# -- advancement table (rulebook Ch.1) --------------------------------------

def test_attribute_points_match_table():
    # 12 at L1, +1 at L3/6/9/12/15/18 only.
    assert [attribute_points(l) for l in range(1, 22)] == \
        [12, 12, 13, 13, 13, 14, 14, 14, 15, 15, 15, 16, 16, 16, 17, 17, 17, 18, 18, 18, 18]


def test_max_skill_rank_by_tier():
    assert [max_skill_rank(l) for l in (1, 5, 6, 10, 11, 15, 16, 20, 21)] == \
        [2, 2, 3, 3, 4, 4, 5, 5, 5]


def test_skill_rank_budget():
    # 4 free + 1 path at L1; +2/level through L20.
    assert total_skill_ranks(1) == 5
    assert free_skill_ranks(1) == 4
    assert total_skill_ranks(2) == 7
    assert total_skill_ranks(20) == 43           # 4 + 2*19 + 1
    # L21+ ranks come from rank-or-talent choices.
    assert total_skill_ranks(22, epic_skill_choices=2) == 45


def test_talent_budget():
    # 1 path key + ancestry bonus at each tier start; +1/level.
    assert total_talents(1) == 2                  # 1 path + 1 ancestry (L1)
    assert ancestry_bonus_talents(1) == 1
    assert ancestry_bonus_talents(6) == 2
    assert ancestry_bonus_talents(21) == 5
    assert total_talents(5) == 6                  # 5 + 1
    assert total_talents(6) == 8                  # 6 + 2
    assert total_talents(20) == 24                # 20 + 4


def test_expertises_and_health_gain():
    assert expertises_total(0) == 2 and expertises_total(3) == 5
    # Health gains: L1=10+STR, then per-tier with STR re-added at L6/11/16.
    assert health_gain_at(1, 2) == 12
    assert health_gain_at(2, 2) == 5
    assert health_gain_at(6, 2) == 6              # +4 + STR(2)
    assert health_gain_at(11, 3) == 6             # +3 + STR(3)
    assert health_gain_at(21, 5) == 1             # +1, no STR
    g = level_grants(6, 2)
    assert g['attribute_point'] is True and g['ancestry_bonus_talent'] is True
    assert g['max_skill_rank'] == 3 and g['health'] == 6


# -- a real build derives the rulebook stats --------------------------------

def _sample():
    # Rulebook example attributes (sum = 12, each <= 3).
    return CosmereBuild({
        'name': 'Test Hero', 'level': 1, 'path': 'warrior',
        'attributes': {'str': 2, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 3, 'pre': 0},
        'skills': {'ath': 2, 'hwp': 2, 'prc': 1},   # 5 ranks
        'expertises': ['Alethi', 'Soldiering'],
    })


def test_build_derives_defenses_and_resources():
    b = _sample()
    assert b.defenses() == {'phy': 15, 'cog': 14, 'spi': 13}   # 10 + governing pair
    assert b.health_max() == 12                                # 10 + STR(2)
    assert b.focus_max() == 4                                  # 2 + WIL(2)
    assert b.investiture_max() == 0                            # not Radiant
    assert b.skill_mods()['hwp'] == 4                          # rank 2 + STR 2
    assert b.skill_mods()['prc'] == 4                          # rank 1 + AWA 3
    assert b.is_valid, b.validate()


def test_validation_flags_violations():
    bad = CosmereBuild({
        'level': 1, 'path': 'warrior',
        'attributes': {'str': 4, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 1, 'pre': 0},  # str 4 > 3, sum 12
        'skills': {'ath': 3},                       # rank 3 > tier-1 max 2
    })
    issues = ' '.join(bad.validate())
    assert 'max 3 per attribute' in issues
    assert 'max rank' in issues
    # Surge skill without a Radiant path is flagged.
    surge = CosmereBuild({'level': 1, 'path': 'warrior',
                          'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2},
                          'skills': {'grv': 1}})
    assert any('Surge' in x for x in surge.validate())


# -- actor-doc round-trip: a build renders what CosmereActor computes --------

def test_to_actor_doc_round_trips_through_cosmere_actor():
    b = _sample()
    actor = CosmereActor(b.to_actor_doc())
    assert actor.is_pc is True and actor.name == 'Test Hero'
    assert actor.defenses == b.defenses()
    assert actor.health_max == b.health_max()
    assert actor.focus_max == b.focus_max()
    assert actor.skills['hwp']['mod'] == b.skill_mods()['hwp']
    # Surge skills stay locked for a non-Radiant.
    assert actor.skills['grv']['unlocked'] is False


def test_from_actor_doc_recovers_build():
    b = _sample()
    again = CosmereBuild.from_actor_doc(b.to_actor_doc())
    assert again.attributes == b.attributes
    assert again.skills == b.skills
    assert again.path == 'warrior'


# -- inventory: deflect + strikes -------------------------------------------

def test_inventory_deflect_and_strikes():
    b = CosmereBuild({
        'name': 'Armed', 'level': 1, 'path': 'warrior',
        'attributes': {'str': 3, 'spd': 2, 'int': 1, 'wil': 2, 'awa': 2, 'pre': 2},
        'inventory': [
            {'id': _BREASTPLATE, 'qty': 1, 'equipped': True},
            {'id': _AXE, 'qty': 1, 'equipped': True},
        ],
    })
    assert b.deflect_value() == 2
    actor = CosmereActor(b.to_actor_doc())
    assert actor.deflect['value'] == 2
    assert actor.deflect['types'].get('impact') is True
    names = [s['name'] for s in actor.strikes]
    assert 'Axe' in names


def test_item_catalog_shapes():
    ax = I.get(_AXE)
    assert ax['kind'] == 'weapon' and ax['damage']['formula'] == '1d6'
    assert ax['damage']['type'] == 'keen'
    bp = I.get(_BREASTPLATE)
    assert bp['kind'] == 'armor' and bp['deflect'] == 2
    assert len(I.weapons()) >= 10 and len(I.armor()) >= 5


def test_skill_attr_map_matches_bestiary_data():
    """Drift guard: the hardcoded SKILL_ATTR equals the Foundry actor data."""
    seen = {}
    for d in cos.load_pack('companions-and-adversaries'):
        for code, node in (d.get('system', {}).get('skills', {}) or {}).items():
            if isinstance(node, dict) and node.get('attribute'):
                seen.setdefault(code, node['attribute'])
    assert seen == dict(cos.SKILL_ATTR)
