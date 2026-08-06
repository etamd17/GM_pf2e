"""Snapshot tests for the four party PCs.

Each test loads a Pathbuilder JSON from `party_data/`, runs it through
the `Character` constructor (which is the full rules-engine pipeline:
ability mods, proficiencies, ActiveEffectLike rules, PB-mods item bonuses,
armor lookups, skill totals, attack computations), captures a stable
subset of the result, and compares against `tests/snapshots/<key>.json`.

This is the safety net for the level-up engine and PB import. The
2026-04-15 audit found 14 progression bugs in `class_matrix.py`; without
snapshot tests, every fix risks introducing a new silent regression.
With them, any number that changes shows up in the diff.

Updating a snapshot: delete the file under `tests/snapshots/` and re-run
pytest. Investigate every diff before accepting.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests._snapshot import assert_matches_snapshot, serialize_character

# Map snapshot key -> party_data filename. The key is also the name of
# the saved snapshot file.
PARTY_FIXTURES = {
    "amadeus_l3":  "amad.json",
    "gavin_l3":    "gavin.json",
    "goel_l3":     "goel.json",
    "kyle_l3":     "Kyle.json",
}

# Ground-truth fixtures — committed to the repo (NOT party_data, which is
# gitignored). These come from the players' Pathbuilder builds mapped to a
# high level so we can lock down the rules engine across the whole
# progression curve, not just where the live party currently sits. The map
# spans levels (L10 Go'el/Kyle, L11 Amadeus/Gavin); every consumer derives
# the actual level from the build (see ``_fixture_level``) rather than
# assuming 10, so dropping in an L12+ export extends coverage automatically.
# Historical name kept (``L10_FIXTURES``) because four test modules import it.
L10_FIXTURES = {
    "goel_l10": "goel_l10.json",
    "kyle_l10": "kyle_l10.json",
    "amadeus_l11": "amadeus_l11.json",
    "gavin_l11": "gavin_l11.json",
}

_PARTY_DIR = Path(__file__).resolve().parent.parent / "party_data"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _fixture_level(filename: str) -> int:
    """The build level baked into a ground-truth fixture. Lets the walk /
    chain suites size their level range per-fixture instead of assuming 10."""
    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    return int((raw.get("build") or {}).get("level") or 10)


def _snap_base(snap_key: str) -> str:
    """Strip a trailing ``_l<N>`` level tag from a fixture key so snapshot
    filenames stay level-agnostic (``gavin_l11`` -> ``gavin``). Existing L10
    snapshots keep their names since ``goel_l10`` -> ``goel`` as before."""
    return re.sub(r"_l\d+$", "", snap_key)


@pytest.fixture(scope="module")
def Character():
    """Lazily import the Character class so test collection failures
    point at the right place if app.py module-load breaks."""
    from app import Character as C
    return C


@pytest.mark.parametrize("snap_key,filename", sorted(PARTY_FIXTURES.items()))
def test_pc_snapshot(Character, snap_key, filename):
    """For each PC at the live-session level (~L3): import the PB JSON,
    build a Character, snapshot."""
    path = _PARTY_DIR / filename
    if not path.exists():
        # party_data is gitignored (local-only player builds), so these
        # live-level snapshots run on the GM's machine but skip in CI. The
        # committed L10 ground-truth fixtures keep the engine covered there.
        pytest.skip(f"party_data fixture not present (local-only): {filename}")
    data = json.loads(path.read_text(encoding="utf-8"))
    pc = Character(data, file_path=str(path))
    payload = serialize_character(pc)
    assert_matches_snapshot(snap_key, payload)


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_pc_snapshot_l10(Character, snap_key, filename):
    """Snapshot the L10 builds. These fixtures come from each player's
    full progression mapped through L10, so they exercise feats and
    proficiency-rank tracks the L3 fixtures don't reach."""
    path = _FIXTURES_DIR / filename
    assert path.exists(), f"missing L10 fixture: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    pc = Character(data, file_path=str(path))
    payload = serialize_character(pc)
    assert_matches_snapshot(snap_key, payload)
