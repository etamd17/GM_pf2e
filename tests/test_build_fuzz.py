"""Synthetic build fuzzer — every class, every level, no hand-built fixtures.

The ground-truth + chain + walk suites are powerful but only cover the four
PCs we have Pathbuilder exports for (Go'el / Kyle / Amadeus / Gavin). That
leaves ~23 classes in ``CLASS_MATRIX`` with *zero* progression coverage —
exactly where a typo'd rank or a missing save bump hides, because nobody at
the table plays a Wizard or a Fighter so no fixture ever exercised them.

This module closes that gap by *generating* a minimal, legal Pathbuilder-shaped
build for every class and running it through the same two pipelines the
fixture suites use:

  * **Layer 1 — import invariants** (``test_synth_progression_invariants``):
    reduce the synth build to each level 1..20 via the ``Character`` import
    path and assert the universal PF2e truths — legal ranks (0/2/4/6/8),
    core ranks never drop, saves/perception stay at least Trained, HP
    strictly increases, AC is a positive int. These hold for ANY correct
    PC, so a failure is a real bug, not a snapshot churn.

  * **Layer 2 — level-up vs. import equivalence**
    (``test_synth_levelup_matches_import``): chain the real
    ``/api/submit_levelup`` endpoint L1->20 and assert the result equals
    importing the reduced build at each level (HP, ability mods, and the
    auto-driven proficiency ranks). This is the synth-build analogue of
    ``test_levelup_chain.py`` — the same "do the two engines agree?" check,
    but across every class instead of two.

What this DOESN'T do: it's not ground truth. The builds are synthesized by us,
so there's no independent authority (the way Pathbuilder's own ``acTotal`` is
in ``test_pb_ground_truth.py``). Layer 1 checks the engine against PF2e
invariants; Layer 2 checks the engine against itself. Generating real
Pathbuilder exports across many classes (the Chrome-driven generator) is the
complementary follow-up that adds the third-party check.

The synth builds are deterministic per class (the abilities/feats are fixed,
not random) so a failure is reproducible from the class name alone. A seeded
randomization layer (varied ancestry / boost spread / legal feats) is the
obvious next extension — ``synth_build`` already takes a ``seed``.
"""

from __future__ import annotations

import copy
import json

import pytest

from class_matrix import CLASS_MATRIX

# Reuse the proven reducer + invariant constants from the fixture suites so the
# synth path tests the *same* code the fixtures do.
from tests.test_level_walk import (
    _reduce_build_to_level,
    _LEGAL_RANKS,
    _MONOTONIC_KEYS,
)
from tests.test_levelup_chain import _AUTO_KEYS


# Every base class the engine knows how to build. Subclass-specific
# progressions (Warpriest / Ruffian) are a deliberate follow-up — base
# classes first.
ALL_CLASSES = sorted(CLASS_MATRIX.keys())

# Floors that hold for every class at every level: PF2e gives all classes at
# least Trained (rank 2) in all three saves, Perception, and their class DC at
# level 1, and those never regress. A class whose save silently drops to
# Untrained trips this even though the monotonic check (which only compares
# adjacent levels) might not.
_TRAINED_FLOOR_KEYS = ("fortitude", "reflex", "will", "perception", "class_dc")


def synth_build(class_name: str, level: int = 20, *, seed=None) -> dict:
    """Return a minimal, legal Pathbuilder-shaped ``build`` dict for
    ``class_name`` at ``level``.

    Deterministic per class (so failures reproduce from the class name).
    ``seed`` is reserved for a future randomized-fuzz layer; it's accepted
    now so callers don't have to change when that lands.

    The shape mirrors what Pathbuilder exports and what ``Character`` /
    ``_reduce_build_to_level`` read:
      * ``abilities.breakdown`` drives the per-level score recompute, so the
        reducer can derive mods at any level.
      * ``attributes`` carries the HP inputs (ancestry/class/bonus HP).
      * ``proficiencies`` is left EMPTY so the engine derives every rank from
        ``CLASS_MATRIX`` base + ``CLASS_PROGRESSION`` — which is the surface
        we're actually testing.
      * ``feats`` is empty to isolate class progression from feat parsing
        (feat fuzzing is a separate layer).
    """
    breakdown = {
        "ancestryBoosts": ["str", "con"],
        "ancestryFlaws": [],
        "ancestryFree": ["dex"],
        "backgroundBoosts": ["con", "wis"],
        "classBoosts": ["con"],
        # Boosts land only at 5/10/15/20 in PF2e; same four stats each time.
        "mapLevelledBoosts": {
            "5": ["str", "dex", "con", "wis"],
            "10": ["str", "dex", "con", "wis"],
            "15": ["str", "dex", "con", "wis"],
            "20": ["str", "dex", "con", "wis"],
        },
    }
    abilities = {
        "str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10,
        "breakdown": breakdown,
    }
    return {
        "name": f"Synth {class_name.title()}",
        "class": class_name,
        "level": level,
        "ancestry": "Human",
        "heritage": "Skilled Heritage",
        "background": "Field Medic",
        "abilities": abilities,
        # HP inputs — any positive class HP keeps HP strictly increasing.
        "attributes": {
            "ancestryhp": 8, "classhp": 8, "bonushp": 0, "bonushpPerLevel": 0,
        },
        # Empty so ranks are derived, not trusted. See docstring.
        "proficiencies": {},
        "feats": [],
        "lores": [],
        "spellCasters": [],
        "equipment": [],
        "weapons": [],
        "armor": [],
    }


# ─────────────────────────────────────────────────────────────────────
# Layer 1 — import-path invariants across every class
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def Character():
    from app import Character as C
    return C


@pytest.mark.parametrize("class_name", ALL_CLASSES)
def test_synth_progression_invariants(Character, class_name):
    """For each class: reduce the synth build to every level 1..20, build a
    Character, and assert the universal PF2e invariants. No hand-authored
    fixture required, so this is the only coverage classes like Wizard /
    Cleric / Fighter currently get for their progression curve."""
    full = synth_build(class_name, level=20)
    prev_profs = None
    prev_hp = None
    for level in range(1, 21):
        reduced = _reduce_build_to_level(full, level)
        pc = Character({"build": reduced}, file_path="<synth>")

        # (1) Every rank is a legal PF2e value — catches a stray 3/5/7.
        for k, v in (pc.proficiencies or {}).items():
            assert v in _LEGAL_RANKS, (
                f"{class_name} L{level}: illegal proficiency rank {k}={v}"
            )

        # (2) Saves / Perception / class DC are at least Trained from L1 on.
        for k in _TRAINED_FLOOR_KEYS:
            assert int(pc.proficiencies.get(k, 0)) >= 2, (
                f"{class_name} L{level}: {k} below Trained "
                f"({pc.proficiencies.get(k, 0)}) — base proficiency lost?"
            )

        # (3) Core ranks never drop as level rises.
        if prev_profs is not None:
            for k in _MONOTONIC_KEYS:
                assert pc.proficiencies.get(k, 0) >= prev_profs.get(k, 0), (
                    f"{class_name}: {k} rank dropped "
                    f"{prev_profs.get(k, 0)} -> {pc.proficiencies.get(k, 0)} into L{level}"
                )

        # (4) HP strictly increases each level.
        if prev_hp is not None:
            assert pc.hp > prev_hp, (
                f"{class_name}: HP did not increase at L{level} ({prev_hp} -> {pc.hp})"
            )

        # (5) AC is a sane positive integer at every level.
        assert isinstance(pc.ac, int) and pc.ac > 0, (
            f"{class_name} L{level}: non-positive AC {pc.ac!r}"
        )

        prev_profs = dict(pc.proficiencies or {})
        prev_hp = pc.hp


# ─────────────────────────────────────────────────────────────────────
# Layer 2 — level-up engine vs. import equivalence across every class
# ─────────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def app_module():
    import app
    return app


def _chain_synth(app_module, full, monkeypatch, tmp_path):
    """Stage the synth build reduced to L1, then submit level-ups 2..20 in
    sequence through the real ``/api/submit_levelup`` endpoint. Returns
    ``{level: saved_build_dict}``.

    Mirrors ``test_levelup_chain._chain_levelups`` but takes a synth build
    dict directly instead of reading a fixture file. The save stub writes the
    temp file so each level-up reads the prior level's persisted state — a
    genuine chain, not independent one-hops."""
    Character = app_module.Character
    class_name = full["class"]
    chain_levels = list(range(2, int(full.get("level") or 20) + 1))

    start = _reduce_build_to_level(full, 1)
    pc_name = f"_FUZZ_{class_name}"
    pc_file = tmp_path / f"{pc_name}.json"
    pc_file.write_text(json.dumps({"build": copy.deepcopy(start)}), encoding="utf-8")

    monkeypatch.setitem(
        app_module.PARTY_LIBRARY, pc_name,
        Character({"build": copy.deepcopy(start)}, file_path=str(pc_file)),
    )
    monkeypatch.setattr(
        app_module, "get_pc_file_path",
        lambda n: str(pc_file) if n == pc_name else None,
    )

    def _fake_save(name, pc_json, fp):
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(pc_json, f)
        app_module.PARTY_LIBRARY[name] = Character(pc_json, file_path=fp)
    monkeypatch.setattr(app_module, "save_and_reload_character", _fake_save)

    results = {}
    with app_module.app.test_client() as c:
        for lvl in chain_levels:
            red = _reduce_build_to_level(full, lvl)
            payload = {
                "new_level": lvl,
                "force_save": True,  # skip the skill-increase validator
                "feats": copy.deepcopy(red.get("feats", [])),
                # Applied by the endpoint only at boost levels (5/10/15/20);
                # the reduced build carries the right scores for each level.
                "abilities": copy.deepcopy(red.get("abilities", {})),
            }
            resp = c.post(f"/api/submit_levelup/{pc_name}", json=payload)
            assert resp.status_code == 200, (
                f"{class_name} L{lvl}: submit_levelup {resp.status_code} {resp.get_json()}"
            )
            saved = json.loads(pc_file.read_text(encoding="utf-8"))
            results[lvl] = saved.get("build", saved)
    return results


@pytest.mark.parametrize("class_name", ALL_CLASSES)
def test_synth_levelup_matches_import(app_module, class_name, monkeypatch, tmp_path):
    """Equivalence: leveling a synth build from L1 to N through the real
    endpoint should produce the same HP, ability mods, and auto-driven
    proficiency ranks as importing the reduced build at N. A mismatch means
    the level-up auto-bump pipeline and the import-time derivation disagree —
    the exact bug class ``test_levelup_chain`` guards for the four fixtures,
    now extended to every class."""
    full = synth_build(class_name, level=20)
    Character = app_module.Character
    results = _chain_synth(app_module, full, monkeypatch, tmp_path)

    for lvl, chained_build in results.items():
        chained = Character({"build": chained_build}, file_path="<chain>")
        imported = Character({"build": _reduce_build_to_level(full, lvl)}, file_path="<import>")

        assert chained.hp == imported.hp, (
            f"{class_name} L{lvl}: HP chain={chained.hp} != import={imported.hp}"
        )
        assert chained.mods == imported.mods, (
            f"{class_name} L{lvl}: ability mods differ — chain={chained.mods} import={imported.mods}"
        )
        for k in _AUTO_KEYS:
            cv = int(chained.proficiencies.get(k, 0) or 0)
            iv = int(imported.proficiencies.get(k, 0) or 0)
            assert cv == iv, (
                f"{class_name} L{lvl}: {k} rank chain={cv} != import={iv} "
                f"(level-up auto-bump disagrees with import derivation)"
            )
