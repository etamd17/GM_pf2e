"""Pins the Cosmere creation/advancement engine to the authoritative rulebook.

Ground truth = the Cosmere RPG Core Rulebook, Ch.1 "Character Advancement" table
(p29) and "Calculate Defenses" (p28). This guards the progression math against
drift the way the PF2e ground-truth tests guard the PF2e derivation:

  Tier L  AttrPts  HealthGain   MaxSkillRank  SkillRanks       TalentsGained
  1   1   12       10+STR       2             4 (+1 path)      1 path + ancestry bonus
      2   -        +5           2             +2               +1
      3   +1       +5           2             +2               +1
      ...
  2   6   +1       +4+STR       3             +2               +1 + ancestry bonus
  ...
  5   21+ -        +1           5             +1 skill OR talent (+ancestry bonus @21)

Defenses: Phy 10+STR+SPD, Cog 10+INT+WIL, Spi 10+AWA+PRE. Focus 2+WIL.
Investiture 2+max(AWA,PRE) (Radiant only). Expertises 2+INT.
"""
from __future__ import annotations

import pytest

import systems.cosmere.build as B
from systems.cosmere.actor import cosmere_max_health


# attribute score points: 12 at creation + 1 at each of L3/6/9/12/15/18.
@pytest.mark.parametrize("level,expected", [
    (1, 12), (2, 12), (3, 13), (5, 13), (6, 14), (9, 15),
    (11, 15), (12, 16), (15, 17), (18, 18), (20, 18), (21, 18),
])
def test_attribute_points(level, expected):
    assert B.attribute_points(level) == expected


# cumulative max health for STR=2, hand-computed from the advancement table
# (+5 tier1, +4+STR at 6, +3+STR at 11, +2+STR at 16, +1 at 21; STR folded in at L1).
@pytest.mark.parametrize("level,expected", [
    (1, 12), (5, 32), (6, 38), (10, 54), (11, 59),
    (15, 71), (16, 75), (20, 83), (21, 84),
])
def test_health_str2(level, expected):
    assert cosmere_max_health(level, 2) == expected


@pytest.mark.parametrize("level,tier,max_rank", [
    (1, 1, 2), (5, 1, 2), (6, 2, 3), (10, 2, 3), (11, 3, 4),
    (15, 3, 4), (16, 4, 5), (20, 4, 5), (21, 5, 5),
])
def test_tier_and_max_skill_rank(level, tier, max_rank):
    assert B.tier_of(level) == tier
    assert B.max_skill_rank(level) == max_rank


@pytest.mark.parametrize("level,expected", [
    (1, 5), (2, 7), (5, 13), (20, 43), (21, 43),  # 4 free +1 path, +2/level, capped at 20
])
def test_total_skill_ranks(level, expected):
    assert B.total_skill_ranks(level) == expected


@pytest.mark.parametrize("level,expected", [
    (1, 2), (2, 3), (5, 6), (6, 8), (11, 14), (16, 20), (20, 24), (21, 25),
])
def test_total_talents_base(level, expected):
    # base = path/level talents (1/level, capped 20) + ancestry bonus (L1/6/11/16/21)
    assert B.total_talents(level, 0) == expected


def test_level_21_is_skill_or_talent_choice():
    # L21+ grants EITHER a skill rank OR a talent, not both (advancement table, tier 5).
    assert B.total_talents(21, 1) == 26          # took the talent
    assert B.total_talents(21, 0) == 25          # took the skill rank instead
    assert B.total_skill_ranks(21, 1) == 44      # ... which adds a skill rank


def test_ancestry_bonus_talents_at_tier_starts():
    # ancestry bonus talent at L1/6/11/16/21 only.
    assert [B.ancestry_bonus_talents(L) for L in (1, 5, 6, 10, 11, 16, 21)] == [1, 1, 2, 2, 3, 4, 5]


def test_creation_caps():
    assert B.CREATION_ATTR_POINTS == 12
    assert B.CREATION_ATTR_MAX == 3     # per-attribute cap at creation
    assert B.ATTR_HARD_CAP == 5         # never above 5


def _build(level=1, attrs=None, ancestry="Human", **extra):
    return B.CosmereBuild({
        "name": "RB", "level": level, "path": "warrior", "ancestry": ancestry,
        "attributes": attrs or {"str": 2, "spd": 3, "int": 2, "wil": 2, "awa": 3, "pre": 0},
        "skills": {}, "talents": [], "expertises": [], **extra,
    })


def test_defenses_focus_from_rulebook_worked_example():
    # Rulebook p28 worked example: STR2/SPD3 -> Phy15, INT2/WIL2 -> Cog14, AWA3/PRE0 -> Spi13.
    d = _build().defenses()
    assert (d["phy"], d["cog"], d["spi"]) == (15, 14, 13)
    assert _build().focus_max() == 4            # 2 + WIL(2)
    assert _build().health_max() == 12          # L1: 10 + STR(2)


def test_expertises_two_plus_intellect():
    assert B.expertises_total(0) == 2
    assert B.expertises_total(3) == 5


def test_investiture_is_radiant_only():
    # Investiture 2 + max(AWA,PRE), and ONLY for Radiants (0 otherwise).
    non_radiant = _build()
    assert non_radiant.investiture_max() == 0   # not a sworn Radiant
    assert not non_radiant.is_radiant


def test_singer_gets_extra_l1_form_talent():
    # A Singer gains Change Form PLUS a starting-form talent at L1 (prior audit F1).
    assert _build(ancestry="Human").talents_available() == 2
    assert _build(ancestry="Singer", singer_form="workform").talents_available() == 3
