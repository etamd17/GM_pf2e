"""Snapshot the entire class-progression matrix.

``get_class_proficiency_at_level(class_name, level)`` is the function
that drives every PC's proficiency ranks across all 20 levels. The L10
ground-truth tests check that the Druid + Cleric/Warpriest curves match
Pathbuilder, but the other 25+ classes have zero coverage. A typo in
class_matrix.py would silently propagate to anyone using that class.

This test pulls every (class_name, level) combination through the
function and writes one snapshot per class (or subclass) under
``tests/snapshots/class_prog/<key>.json``. First run captures the
baseline; subsequent runs lock it in.

Why one file per class instead of one giant file: when a class's
progression changes, only that class's snapshot moves — the diff is
focused and reviewable, not 580 lines of "everything shifted by one."

Adding a new class to class_matrix.py auto-extends coverage; the
parametrize loop reads CLASS_PROGRESSION + SUBCLASS_PROGRESSION at
import time.
"""

from __future__ import annotations

import pytest

from class_matrix import (
    CLASS_PROGRESSION,
    SUBCLASS_PROGRESSION,
    get_class_proficiency_at_level,
)
from tests._snapshot import assert_matches_snapshot


_PREFIX = "class_prog/"
_LEVELS = list(range(1, 21))


def _build_progression_snapshot(class_name: str, subclass: str | None = None) -> dict:
    """Return ``{level: {key: rank}}`` for every level 1-20.

    Empty dicts (levels with no bumps from this class) are represented as
    ``{}`` so the test still notices when a level *should* have produced
    nothing but starts producing something."""
    out = {}
    for lvl in _LEVELS:
        out[str(lvl)] = dict(sorted(
            get_class_proficiency_at_level(class_name, lvl, subclass=subclass).items()
        ))
    return out


_CLASS_KEYS = sorted(CLASS_PROGRESSION.keys())
_SUBCLASS_KEYS = sorted(SUBCLASS_PROGRESSION.keys())


@pytest.mark.parametrize("class_name", _CLASS_KEYS)
def test_class_progression_snapshot(class_name):
    """Every class in CLASS_PROGRESSION snapshots its full L1-L20 curve.
    A diff means class_matrix.py changed for that class — review the
    change, decide if it's intentional, and re-snapshot if so."""
    payload = _build_progression_snapshot(class_name)
    assert_matches_snapshot(f"{_PREFIX}{class_name}", payload)


@pytest.mark.parametrize("subclass", _SUBCLASS_KEYS)
def test_subclass_progression_snapshot(subclass):
    """Subclasses (Warpriest, Ruffian) replace the base class progression.
    A drift here would have hit Go'el at the table since he's a
    Warpriest — same coverage logic as the base classes."""
    payload = _build_progression_snapshot("", subclass=subclass)
    # snapshot key uses the subclass name as a slug (no spaces, lowercase)
    slug = subclass.lower().replace(" ", "_")
    assert_matches_snapshot(f"{_PREFIX}sub_{slug}", payload)


def test_progression_does_not_decrease_with_level():
    """Cumulative-bump invariant: a proficiency rank for a given key
    should never decrease as level goes up. If Druid Will is Master at
    L11, it should stay >= 6 at L12+. Catches the kind of bug where an
    edit to class_matrix.py accidentally clobbers a higher-level entry
    with a lower value (we caught this once where a typo set Druid Will
    to 4 at L13 instead of leaving it at 6)."""
    for class_name in _CLASS_KEYS:
        prev_ranks = {}
        for lvl in _LEVELS:
            ranks = get_class_proficiency_at_level(class_name, lvl)
            for key, val in ranks.items():
                prev = prev_ranks.get(key, 0)
                assert val >= prev, (
                    f"{class_name} {key}: rank decreased from {prev} (some "
                    f"earlier level) to {val} at L{lvl}. CLASS_PROGRESSION "
                    f"entries must be cumulative — never overwrite a higher "
                    f"rank with a lower one."
                )
                prev_ranks[key] = val
