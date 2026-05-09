"""Snapshot tests for the level-up engine surfaces.

Three surfaces locked down here:

  1. ``get_required_slots_at_level(class, level)`` — the "what choices
     does the player owe at this level" lookup. The wizard uses it to
     decide which form panels to render. A class_matrix.py edit that
     drops a ``skill_feat`` slot at L4 silently hides the picker; this
     test catches that.

  2. ``_missing_progression_for_level(build, new_level)`` — the
     server-side validator the wizard hits before saving. We run it
     against the L10 fixtures at every plausible "submit" level so the
     mix of feats / divine-ally / skill-increase requirements gets
     exercised across class types.

  3. ``/api/levelup_validate/<pc_name>`` — the GET endpoint the level-up
     drawer polls to render its issue checklist. Hits the live HTTP
     surface to make sure the wiring (auth gate, JSON shape, integration
     with PARTY_LIBRARY) hasn't drifted.
"""

from __future__ import annotations

import copy
import json

import pytest

from class_matrix import (
    CLASS_PROGRESSION,
    SUBCLASS_PROGRESSION,
    get_required_slots_at_level,
)
from tests._snapshot import assert_matches_snapshot
from tests.test_pc_snapshots import L10_FIXTURES, _FIXTURES_DIR


_PREFIX = "levelup/"
_LEVELS = list(range(1, 21))

# Class names to exercise for the required-slots snapshot. We snapshot
# every base class registered in CLASS_PROGRESSION; subclasses share the
# slot table with their parent class so we don't snapshot those again.
_CLASSES_FOR_SLOTS = sorted(CLASS_PROGRESSION.keys())


@pytest.fixture(scope="module")
def app_module():
    import app
    return app


# ─────────────────────────────────────────────────────────────────────
# 1. Required-slot lookup per class+level
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("class_name", _CLASSES_FOR_SLOTS)
def test_required_slots_per_class(class_name):
    """One snapshot per class. ``{level_str: {slot: count}}``.
    Gold standard: every class registers a slot table that fills in
    the expected feat / skill / boost cadence."""
    payload = {}
    for lvl in _LEVELS:
        slots = get_required_slots_at_level(class_name, lvl)
        if slots:
            payload[str(lvl)] = dict(sorted(slots.items()))
        else:
            payload[str(lvl)] = {}
    assert_matches_snapshot(f"{_PREFIX}slots_{class_name}", payload)


# ─────────────────────────────────────────────────────────────────────
# 2. _missing_progression_for_level against L10 fixtures
# ─────────────────────────────────────────────────────────────────────
def _walk_missing_for_fixture(app_module, fixture_path) -> dict:
    """For a given L10 fixture, run ``_missing_progression_for_level``
    at every level the build could plausibly target. Returns a level→
    issues dict for snapshotting."""
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    build = raw["build"]
    out = {}
    # The validator inspects the build's feat list directly. Use the full
    # L10 build at every target level — that simulates "we have the L10
    # build in hand and we're checking whether each level's required
    # slots are filled." All slots up to L10 should report as filled.
    for lvl in _LEVELS:
        missing = app_module._missing_progression_for_level(build, lvl)
        out[str(lvl)] = list(missing)  # already a list of strings
    return out


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_missing_progression_walk(app_module, snap_key, filename):
    payload = _walk_missing_for_fixture(app_module, _FIXTURES_DIR / filename)
    assert_matches_snapshot(f"{_PREFIX}missing_{snap_key.replace('_l10','')}", payload)


# ─────────────────────────────────────────────────────────────────────
# 3. /api/levelup_validate end-to-end against live PARTY_LIBRARY
# ─────────────────────────────────────────────────────────────────────
def test_levelup_validate_endpoint_returns_clean_shape(app_module):
    """The /api/levelup_validate route is a thin wrapper but it's the
    surface the level-up drawer polls. Verify the JSON shape (success +
    issues + level keys) and that calling it for an unknown PC 404s
    cleanly. Pinning the issue *contents* would be too fragile (depends
    on live PC state) — we only assert the shape contract."""
    with app_module.app.test_client() as c:
        # Pick any PC currently in PARTY_LIBRARY. If it's empty the test
        # platform has no party_data fixtures; skip cleanly.
        if not app_module.PARTY_LIBRARY:
            pytest.skip("PARTY_LIBRARY is empty; no live PC to validate")
        pc_name = next(iter(app_module.PARTY_LIBRARY))

        resp = c.get(f"/api/levelup_validate/{pc_name}")
        assert resp.status_code == 200, resp.status_code
        data = resp.get_json()
        assert data is not None
        assert data.get("success") is True
        assert "issues" in data and isinstance(data["issues"], list)
        assert "level" in data and isinstance(data["level"], int)

        # Unknown PC must 404, not 200 with a stale payload.
        bad = c.get("/api/levelup_validate/_does_not_exist_")
        assert bad.status_code == 404


# ─────────────────────────────────────────────────────────────────────
# 4. Submit-levelup engine against the L10 fixtures
#    Drives the real /api/submit_levelup route, but pre-stages an
#    in-memory PC and stubs save_and_reload_character so nothing
#    touches party_data on disk.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_submit_levelup_engine(app_module, snap_key, filename, monkeypatch, tmp_path):
    """Reduce the L10 fixture to L4, then submit a level-up to L5.
    Snapshot the resulting build's proficiencies + class-level features
    (auto-bumps from CLASS_PROGRESSION) so a regression in the engine's
    auto-bump pipeline shows up as a snapshot diff.

    L5 is chosen because it triggers the ability-boost code path (one of
    the level-up engine's most rule-heavy branches) AND coincides with
    real CLASS_PROGRESSION bumps for both Cleric/Warpriest (Alertness)
    and Druid (Fortitude / Lightning Reflexes Expert)."""
    from tests.test_level_walk import _reduce_build_to_level

    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    build = raw["build"]

    # Reduce to L4 — the build state right before the L5 ability boosts
    # + new class feats arrive.
    pre_build = _reduce_build_to_level(build, 4)

    # Stage a fake PC file under tmp_path so the endpoint's file IO has
    # something to read. Use a synthetic name so we can't collide with
    # any real PC in PARTY_LIBRARY.
    pc_name = f"_TEST_{snap_key}"
    pc_file = tmp_path / f"{pc_name}.json"
    pc_file.write_text(json.dumps({"build": copy.deepcopy(pre_build)}), encoding="utf-8")

    # Plug into the engine: register the PC in PARTY_LIBRARY, point file
    # lookups at our temp file, and stub the persistence call so the
    # endpoint can run end-to-end without writing anywhere real.
    Character = app_module.Character
    pc = Character({"build": copy.deepcopy(pre_build)}, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, pc_name, pc)
    monkeypatch.setattr(app_module, "get_pc_file_path", lambda n: str(pc_file) if n == pc_name else None)
    saved = {}
    def _fake_save(name, pc_json, fp):
        saved["name"] = name
        saved["build"] = copy.deepcopy(pc_json.get("build", pc_json))
        # Mirror the real helper's behavior: rebuild Character + put back
        # in PARTY_LIBRARY so subsequent reads see the new state.
        app_module.PARTY_LIBRARY[name] = Character(pc_json, file_path=fp)
    monkeypatch.setattr(app_module, "save_and_reload_character", _fake_save)

    # Build a minimal level-up payload — feats are inherited from the
    # build, no ability boosts (L5 is even in PF2e but BOOST_LEVELS = {5,
    # 10, 15, 20}, so we DO need to send one), and force_save=True so
    # the validator doesn't reject for missing skill increases (the
    # reduced build doesn't carry the partial L4 skill-increase state).
    payload = {
        "new_level": 5,
        "force_save": True,
        # PB stores L5-applied abilities under abilities; pull them from
        # the original L10 build's mapLevelledBoosts structure to mimic
        # what the real wizard would send.
        "abilities": copy.deepcopy(build.get("abilities", {})),
        "feats": copy.deepcopy(pre_build.get("feats", [])),
    }

    with app_module.app.test_client() as c:
        resp = c.post(
            f"/api/submit_levelup/{pc_name}",
            json=payload,
        )

    # Snapshot the engine output. We don't snapshot the WHOLE build (it
    # has volatile bits like level_history timestamps, equipment ids,
    # etc.) — pin the proficiency table + level + feats list because
    # those are the engine's actual output.
    assert resp.status_code == 200, f"submit_levelup returned {resp.status_code}: {resp.get_json()}"
    out = saved.get("build") or {}
    snap = {
        "level": out.get("level"),
        "proficiencies": dict(sorted((out.get("proficiencies") or {}).items())),
        "feat_count": len(out.get("feats") or []),
        # spellCasters[i].perDay is the slot-table-correction surface; pin
        # just the lengths and per-day arrays.
        "spell_casters": [
            {
                "name": sc.get("name"),
                "perDay": sc.get("perDay"),
            }
            for sc in (out.get("spellCasters") or [])
        ],
    }
    assert_matches_snapshot(f"{_PREFIX}submit_l4_to_l5_{snap_key.replace('_l10','')}", snap)
