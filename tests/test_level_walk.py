"""L1 → L10 level-up walkthrough test.

Where ``test_pb_ground_truth.py`` validates the *endpoint* (the L10 build
matches what Pathbuilder reports for L10), this module validates the
*progression* — at every level from 1 to 10, the engine produces the
expected HP, ability scores, save proficiencies, and class DC.

The test reduces an L10 Pathbuilder build to an arbitrary level by:

  - Trimming feats to those with ``level <= target``.
  - Re-running the ability-score breakdown forward from the base 10s,
    applying only the boosts that occur at or before ``target``.
  - Setting ``build['level']`` to ``target`` and clearing the
    ``proficiencies`` block so the engine drives proficiency ranks
    purely from CLASS_PROGRESSION at that level (otherwise PB's L10
    ranks would override and we'd never test the progression curve).

Each (PC, level) pair gets its own snapshot under
``tests/snapshots/walk/<pc>_l<n>.json``. Adding a new fixture (e.g.
Amadeus L10 when his player has it mapped) automatically extends the
walk to that PC's curve.

Why snapshots instead of pin-the-numbers assertions: PF2e progression
has a lot of class-by-level edges, and pinning every value would mean
re-encoding the entire CLASS_PROGRESSION table inside the test. The
snapshot approach catches *changes* — if a class_matrix.py edit shifts
when Druid hits Expert Reflex from L5 to L7, the L5/L6 snapshots fail
and the diff makes the regression obvious.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests._snapshot import assert_matches_snapshot, serialize_character
from tests.test_pc_snapshots import L10_FIXTURES, _FIXTURES_DIR


# Levels we walk through. Stops at 10 since that's where ground truth ends;
# can be extended to 20 once L20 fixtures exist.
WALK_LEVELS = list(range(1, 11))

# Snapshot key prefix → put walk snapshots in their own subdir to keep the
# main snapshots/ readable.
_WALK_PREFIX = "walk/"


@pytest.fixture(scope="module")
def Character():
    from app import Character as C
    return C


def _ability_scores_at_level(build: dict, target_level: int) -> dict:
    """Compute STR/DEX/CON/INT/WIS/CHA scores at ``target_level`` from the
    PB build's breakdown. Mirrors PF2e's "boost gives +2 below 18, +1 at or
    above 18, flaw gives -2" rule."""
    abilities = build.get("abilities") or {}
    breakdown = abilities.get("breakdown") or {}
    scores = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10}

    def boost(stat_list, sign=1):
        for s in (stat_list or []):
            k = s.lower()
            if k not in scores:
                continue
            # +2 below 18, +1 at or above 18 (the "key ability cap" rule).
            # Flaws (sign=-1) always subtract 2.
            if sign < 0:
                scores[k] -= 2
            else:
                scores[k] += 2 if scores[k] < 18 else 1

    boost(breakdown.get("ancestryFree"))
    boost(breakdown.get("ancestryBoosts"))
    boost(breakdown.get("ancestryFlaws"), sign=-1)
    boost(breakdown.get("backgroundBoosts"))
    boost(breakdown.get("classBoosts"))

    for level_str, level_boosts in (breakdown.get("mapLevelledBoosts") or {}).items():
        try:
            lv = int(level_str)
        except (TypeError, ValueError):
            continue
        if lv <= target_level:
            boost(level_boosts)

    return scores


def _reduce_build_to_level(build: dict, target_level: int) -> dict:
    """Return a deep-copy of the build trimmed to ``target_level``."""
    new = copy.deepcopy(build)
    new["level"] = target_level

    # Trim feats to those granted at or before this level. PB feat tuples
    # carry the granted level at index 3 (or none, for awarded/heritage).
    feats = new.get("feats")
    if isinstance(feats, list):
        new["feats"] = [
            f for f in feats
            if not (isinstance(f, list) and len(f) >= 4 and isinstance(f[3], int) and f[3] > target_level)
        ]

    # Trim PB-stated proficiency ranks. PB only exports the snapshot at the
    # build's level; at L1 we shouldn't see L10 ranks. Clearing forces the
    # engine to drive ranks from CLASS_PROGRESSION + base_proficiencies at
    # the new target level — which is exactly what we want to test.
    new["proficiencies"] = {}

    # Recompute ability scores at the new level so downstream HP / save /
    # skill math operates on the right modifiers.
    scores = _ability_scores_at_level(build, target_level)
    abilities = dict(new.get("abilities") or {})
    abilities.update(scores)
    new["abilities"] = abilities

    # Lores: PB exports the rank at build-time. Strip them — at lower
    # levels the lore ranks differ. (The engine will pick up a 0 default.)
    new["lores"] = []

    return new


@pytest.mark.parametrize(
    "pc_key,filename,level",
    [(k, f, lv) for k, f in sorted(L10_FIXTURES.items()) for lv in WALK_LEVELS],
)
def test_level_walk(Character, pc_key, filename, level):
    """For each (PC, level) pair: reduce the L10 build to that level, run it
    through Character, and snapshot the FULL sheet. We use the same
    serialize_character() the L3/L10 snapshots use, so every level locks
    not just HP/AC/saves/DCs but per-skill totals, the feats granted by
    that level, weapon attacks, and every typed rule modifier — i.e. the
    whole "do feats / skills / bonuses rank up correctly each level" picture.
    The first run creates the snapshot; subsequent runs lock it in."""
    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    build = raw["build"]
    reduced = _reduce_build_to_level(build, level)

    # ``Character`` accepts the wrapper dict OR the inner build dict; pass
    # a wrapper so the import path matches what /api routes use.
    pc = Character({"build": reduced}, file_path=str(_FIXTURES_DIR / filename))
    payload = serialize_character(pc)
    assert_matches_snapshot(f"{_WALK_PREFIX}{pc_key.replace('_l10','')}_l{level}", payload)


_LEGAL_RANKS = {0, 2, 4, 6, 8}  # PF2e: Untrained/Trained/Expert/Master/Legendary
_MONOTONIC_KEYS = (
    "fortitude", "reflex", "will", "perception", "class_dc",
    "spell_attack", "spell_dc",
    "unarmored", "light", "medium", "heavy",
    "unarmed", "simple", "martial", "advanced",
)


@pytest.mark.parametrize("pc_key,filename", sorted(L10_FIXTURES.items()))
def test_progression_invariants(Character, pc_key, filename):
    """Correctness invariants true for ANY PF2e PC, checked at every level
    1-10. Unlike the snapshots (which lock whatever the engine produces),
    these catch real bugs even on a freshly-seeded run:
      * every proficiency rank is a legal PF2e value (0/2/4/6/8) — never a 3,
      * core ranks never DROP as level rises (an un-bump regression trips this),
      * HP strictly increases each level."""
    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    build = raw["build"]
    prev_profs = None
    prev_hp = None
    for level in WALK_LEVELS:
        reduced = _reduce_build_to_level(build, level)
        pc = Character({"build": reduced}, file_path=str(_FIXTURES_DIR / filename))

        for k, v in (pc.proficiencies or {}).items():
            assert v in _LEGAL_RANKS, f"{pc_key} L{level}: illegal proficiency rank {k}={v}"

        if prev_profs is not None:
            for k in _MONOTONIC_KEYS:
                assert pc.proficiencies.get(k, 0) >= prev_profs.get(k, 0), (
                    f"{pc_key}: {k} rank dropped "
                    f"{prev_profs.get(k, 0)} -> {pc.proficiencies.get(k, 0)} going into L{level}"
                )

        if prev_hp is not None:
            assert pc.hp > prev_hp, (
                f"{pc_key}: HP did not increase at L{level} ({prev_hp} -> {pc.hp})"
            )

        prev_profs = dict(pc.proficiencies or {})
        prev_hp = pc.hp


def test_l10_walk_endpoint_matches_full_build(Character):
    """Sanity check on the reducer: walking up to L10 should produce
    numbers that line up with the full PB build's L10 stats. If this drifts,
    the level walk is testing a different reality than the ground-truth
    test, which is misleading."""
    for pc_key, filename in L10_FIXTURES.items():
        raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
        build = raw["build"]

        # Full build, untouched
        full_pc = Character(raw, file_path=str(_FIXTURES_DIR / filename))

        # Reduced-then-rebuilt at L10 — should match the full build's HP
        # and ability scores. AC and prof ranks may diverge because our
        # reducer wipes the PB proficiencies block, so we don't compare
        # those here — the test_pb_ground_truth suite covers them.
        reduced = _reduce_build_to_level(build, 10)
        walked_pc = Character({"build": reduced}, file_path=str(_FIXTURES_DIR / filename))

        assert walked_pc.hp == full_pc.hp, (
            f"{pc_key}: walked HP {walked_pc.hp} != full HP {full_pc.hp}"
        )
        for stat in ("str", "dex", "con", "int", "wis", "cha"):
            assert walked_pc.mods.get(stat) == full_pc.mods.get(stat), (
                f"{pc_key}: walked {stat} mod {walked_pc.mods.get(stat)} "
                f"!= full {stat} mod {full_pc.mods.get(stat)}"
            )
