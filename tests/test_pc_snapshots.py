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

_PARTY_DIR = Path(__file__).resolve().parent.parent / "party_data"


@pytest.fixture(scope="module")
def Character():
    """Lazily import the Character class so test collection failures
    point at the right place if app.py module-load breaks."""
    from app import Character as C
    return C


@pytest.mark.parametrize("snap_key,filename", sorted(PARTY_FIXTURES.items()))
def test_pc_snapshot(Character, snap_key, filename):
    """For each PC: import the PB JSON, build a Character, snapshot."""
    path = _PARTY_DIR / filename
    assert path.exists(), f"missing party_data fixture: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    pc = Character(data, file_path=str(path))
    payload = serialize_character(pc)
    assert_matches_snapshot(snap_key, payload)
