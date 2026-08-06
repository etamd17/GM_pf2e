"""Ground-truth tests against Pathbuilder L10 exports.

Where the snapshot tests catch *regressions* (the engine output changed
from what it used to produce), these tests catch *bugs* — places where
the engine disagrees with what Pathbuilder computes for the same build.

Pathbuilder's exported JSON includes its own derived numbers:
- ``build.acTotal.acTotal`` — the fully-computed AC
- ``build.proficiencies`` — the rank table for every save / weapon /
  armor / casting / skill, with PF2e ranks (0/2/4/6/8 = U/T/E/M/L)
- ``build.weapons[i].attack`` — first-strike attack bonus including
  proficiency, ability mod, item bonus
- ``build.attributes`` (ancestryhp / classhp / bonushp / bonushpPerLevel)
  — the inputs for HP; total is derivable

Comparing each of those against what our ``Character`` constructor
produces is a high-signal test: any mismatch means the rules engine,
class progression, or PB import is reading the build differently than
Pathbuilder does. That's a real bug to fix (or an explicit divergence
to document via ``EXPECTED_DRIFT`` below).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Reuse the L10 fixture map from the snapshot module so adding a new PC
# only touches one place.
from tests.test_pc_snapshots import L10_FIXTURES, _FIXTURES_DIR


@pytest.fixture(scope="module")
def Character():
    from app import Character as C
    return C


def _load_l10(filename: str) -> dict:
    path = _FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _compute_expected_hp(attributes: dict, con_mod: int, level: int) -> int:
    """Mirror the formula in ``Character.__init__`` so the assertion isn't
    just self-checking. ``bonushpPerLevel`` is how Pathbuilder encodes
    Toughness etc., so we include it per-level the way the live engine
    does."""
    anc = int(attributes.get("ancestryhp") or 0)
    cls = int(attributes.get("classhp") or 0)
    bonus = int(attributes.get("bonushp") or 0)
    per_lvl = int(attributes.get("bonushpPerLevel") or 0)
    return anc + bonus + (cls + con_mod + per_lvl) * level


# Skills that PB exports under their lowercase Pathbuilder keys but that
# our engine tracks under the same lowercase keys. Keys absent from the
# PB ``proficiencies`` dict default to 0 (Untrained) on both sides.
SKILL_KEYS = (
    "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
    "intimidation", "medicine", "nature", "occultism", "performance",
    "religion", "society", "stealth", "survival", "thievery",
)
ARMOR_KEYS = ("unarmored", "light", "medium", "heavy")
WEAPON_KEYS = ("unarmed", "simple", "martial", "advanced")
SAVE_KEYS = ("fortitude", "reflex", "will")


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_ac_matches_pathbuilder(Character, snap_key, filename):
    """Engine AC should match PB's pre-computed ``acTotal.acTotal``.
    A drift here means armor lookup, dex cap, or AC proficiency is off."""
    data = _load_l10(filename)
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    expected_ac = data["build"]["acTotal"]["acTotal"]
    assert pc.ac == expected_ac, (
        f"{snap_key}: engine AC {pc.ac} != Pathbuilder AC {expected_ac}. "
        f"Check armor proficiency rank, dex cap, or item bonus lookup."
    )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_hp_matches_formula(Character, snap_key, filename):
    """HP should follow the documented PF2e formula given PB's attribute
    inputs. A drift here usually means a Toughness / con-mod / per-level
    HP bug, or an over-eager rule-engine HP modifier."""
    data = _load_l10(filename)
    build = data["build"]
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    expected = _compute_expected_hp(
        build.get("attributes") or {},
        con_mod=pc.mods.get("con", 0),
        level=pc.level,
    )
    assert pc.hp == expected, (
        f"{snap_key}: engine HP {pc.hp} != formula {expected} "
        f"(ancestryhp + bonushp + (classhp + conMod + bonushpPerLevel) * level). "
        f"Likely a Toughness double-count or a stray rule-engine HP modifier."
    )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_save_proficiencies_match_pathbuilder(Character, snap_key, filename):
    """The save proficiency code path explicitly trusts PB's value verbatim
    (see TRUST_PB_PROF_KEYS in app.py). This test pins that contract — a
    failure means we accidentally reintroduced the buggy 'class progression
    overrides PB' behavior."""
    data = _load_l10(filename)
    pb_profs = data["build"]["proficiencies"]
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    for key in SAVE_KEYS:
        expected = int(pb_profs.get(key, 0) or 0)
        actual = int(pc.proficiencies.get(key, 0) or 0)
        assert actual == expected, (
            f"{snap_key}: {key} proficiency engine={actual} pb={expected}. "
            f"Saves should be trusted verbatim from PB."
        )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_perception_and_class_dc_match(Character, snap_key, filename):
    """Perception (proficiency only — bonuses tested elsewhere) and class
    DC are class-progression driven. A drift here means CLASS_PROGRESSION
    has a missing rank-bump."""
    data = _load_l10(filename)
    pb_profs = data["build"]["proficiencies"]
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    for pb_key, eng_key in (("perception", "perception"), ("classDC", "class_dc")):
        expected = int(pb_profs.get(pb_key, 0) or 0)
        actual = int(pc.proficiencies.get(eng_key, 0) or 0)
        # Engine takes max(PB, computed), so the engine value is allowed
        # to be HIGHER than PB (a class feature granting an extra bump
        # PB might not model). Strictly less-than is a real bug.
        assert actual >= expected, (
            f"{snap_key}: {eng_key} proficiency engine={actual} < pb={expected}. "
            f"Class progression is missing a bump."
        )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_armor_and_weapon_proficiencies_match(Character, snap_key, filename):
    """Armor + weapon proficiency ranks should match PB. Mismatches usually
    point at class-feature handling (e.g. Warpriest's armor doctrine, Druid
    weapon list, Beastmaster Dedication)."""
    data = _load_l10(filename)
    pb_profs = data["build"]["proficiencies"]
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    for key in ARMOR_KEYS + WEAPON_KEYS:
        expected = int(pb_profs.get(key, 0) or 0)
        actual = int(pc.proficiencies.get(key, 0) or 0)
        assert actual >= expected, (
            f"{snap_key}: {key} proficiency engine={actual} < pb={expected}."
        )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_skill_proficiencies_match(Character, snap_key, filename):
    """Each base skill should at least match PB's rank. A failure usually
    means a skill-increase from a feat (Additional Lore, Assurance) was
    misparsed, or ``mapLevelledBoosts`` skill increases aren't being read.
    (Lores are tested via the snapshot, not here, since they're keyed
    differently.)"""
    data = _load_l10(filename)
    pb_profs = data["build"]["proficiencies"]
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    for key in SKILL_KEYS:
        expected = int(pb_profs.get(key, 0) or 0)
        actual = int(pc.proficiencies.get(key, 0) or 0)
        assert actual >= expected, (
            f"{snap_key}: {key} proficiency engine={actual} < pb={expected}."
        )


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_l10_weapon_attack_bonuses_match(Character, snap_key, filename):
    """Each weapon's first-strike attack bonus should match PB's pre-computed
    ``weapons[i].attack``. This catches misapplied prof rank, ability mod,
    or item bonus on weapons. We compare by weapon name to survive
    ordering differences (the engine may sort attacks differently)."""
    data = _load_l10(filename)
    pb_weapons = {w["name"]: w for w in data["build"].get("weapons") or []}
    pc = Character(data, file_path=str(_FIXTURES_DIR / filename))
    eng_attacks = {a["name"]: a for a in (getattr(pc, "attacks", []) or [])}

    missing = [n for n in pb_weapons if n not in eng_attacks]
    assert not missing, (
        f"{snap_key}: engine attacks missing for {missing}. "
        f"Weapon ingestion didn't surface these PB weapons."
    )

    for name, pb_w in pb_weapons.items():
        expected = int(pb_w.get("attack", 0) or 0)
        eng = eng_attacks[name]
        first_strike = (eng.get("strikes") or [{}])[0].get("mod")
        assert first_strike == expected, (
            f"{snap_key}: {name} first-strike engine={first_strike} != pb={expected}."
        )
