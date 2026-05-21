"""Chained from-scratch level-up walk + import equivalence.

``test_level_walk.py`` imports a Pathbuilder build *reduced* to each level
(the import path). This module instead drives the real ``/api/submit_levelup``
endpoint step by step from L1 upward — the actual "Level Up" engine the
wizard uses — and:

  * snapshots the engine's output at each chained level (proficiency table,
    feat count, spell slots), and
  * asserts the chained result matches importing the reduced PB build at the
    same level (HP, ability mods, and the auto-driven proficiency ranks).

Both paths derive ranks from CLASS_PROGRESSION, so a mismatch means the
level-up auto-bump pipeline and the import-time derivation disagree — a real
bug in one of them.

Only the two committed L10 fixtures (Go'el / Cleric-Warpriest, Kyle / Druid)
are exercised; adding Champion / Kineticist L10+ fixtures extends this for
free, the same way it does for the snapshot + ground-truth suites.
"""

from __future__ import annotations

import copy
import json

import pytest

from tests._snapshot import assert_matches_snapshot
from tests.test_pc_snapshots import L10_FIXTURES, _FIXTURES_DIR
from tests.test_level_walk import _reduce_build_to_level

_PREFIX = "levelup_chain/"
# We stage the PC at L1, then submit level-ups for 2..10.
_CHAIN_LEVELS = list(range(2, 11))

# Proficiency keys the class-progression engine drives automatically (saves,
# perception, class DC, casting, armor, weapons). Skills are excluded because
# skill increases aren't replayed in the chain (force_save bypasses them).
_AUTO_KEYS = (
    "fortitude", "reflex", "will", "perception", "class_dc",
    "spell_attack", "spell_dc",
    "unarmored", "light", "medium", "heavy",
    "unarmed", "simple", "martial", "advanced",
)


@pytest.fixture(scope="module")
def app_module():
    import app
    return app


def _chain_levelups(app_module, filename, monkeypatch, tmp_path):
    """Stage the L10 fixture reduced to L1, then submit level-ups 2..10 in
    sequence through the real endpoint. Returns {level: saved_build_dict}.

    The save stub writes the temp file so each level-up reads the prior
    level's state — that's what makes this a genuine *chain* rather than 9
    independent one-hop level-ups."""
    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    full = raw["build"]
    Character = app_module.Character

    start = _reduce_build_to_level(full, 1)
    pc_name = f"_CHAIN_{filename}"
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
        for lvl in _CHAIN_LEVELS:
            red = _reduce_build_to_level(full, lvl)
            payload = {
                "new_level": lvl,
                "force_save": True,  # skip the skill-increase validator
                "feats": copy.deepcopy(red.get("feats", [])),
                # Applied by the endpoint only at boost levels (5/10); the
                # reduced build carries the right scores for each level.
                "abilities": copy.deepcopy(red.get("abilities", {})),
            }
            resp = c.post(f"/api/submit_levelup/{pc_name}", json=payload)
            assert resp.status_code == 200, (
                f"{filename} L{lvl}: submit_levelup {resp.status_code} {resp.get_json()}"
            )
            saved = json.loads(pc_file.read_text(encoding="utf-8"))
            results[lvl] = saved.get("build", saved)
    return results


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_levelup_chain_snapshot(app_module, snap_key, filename, monkeypatch, tmp_path):
    """Snapshot the SHEET that results from leveling up step by step. We
    build a Character from each chained build and capture the computed
    numbers (HP, saves, DCs, the full proficiency table) plus the feat
    count and spell-slot arrays the endpoint persisted, so a regression in
    the level-up engine shows up as a diff."""
    Character = app_module.Character
    results = _chain_levelups(app_module, filename, monkeypatch, tmp_path)
    payload = {}
    for lvl, build in results.items():
        pc = Character({"build": build}, file_path="<chain>")
        payload[str(lvl)] = {
            "level": pc.level,
            "hp": pc.hp,
            "fort": pc.fort, "ref": pc.ref, "will": pc.will,
            "perception": pc.perception, "class_dc": pc.class_dc,
            "spell_dc": pc.spell_dc,
            "proficiencies": dict(sorted((pc.proficiencies or {}).items())),
            "feat_count": len(build.get("feats") or []),
            "spell_slots": [
                {"name": sc.get("name"), "perDay": sc.get("perDay")}
                for sc in (build.get("spellCasters") or [])
            ],
        }
    assert_matches_snapshot(f"{_PREFIX}{snap_key.replace('_l10', '')}", payload)


@pytest.mark.parametrize("snap_key,filename", sorted(L10_FIXTURES.items()))
def test_levelup_chain_matches_import(app_module, snap_key, filename, monkeypatch, tmp_path):
    """Equivalence: leveling from L1 up to N through the engine should produce
    the same HP, ability mods, and auto-driven proficiency ranks as importing
    the reduced PB build directly at N."""
    raw = json.loads((_FIXTURES_DIR / filename).read_text(encoding="utf-8"))
    full = raw["build"]
    Character = app_module.Character
    results = _chain_levelups(app_module, filename, monkeypatch, tmp_path)

    for lvl, chained_build in results.items():
        chained = Character({"build": chained_build}, file_path="<chain>")
        imported = Character({"build": _reduce_build_to_level(full, lvl)}, file_path="<import>")

        assert chained.hp == imported.hp, (
            f"{snap_key} L{lvl}: HP chain={chained.hp} != import={imported.hp}"
        )
        assert chained.mods == imported.mods, (
            f"{snap_key} L{lvl}: ability mods differ — chain={chained.mods} import={imported.mods}"
        )
        for k in _AUTO_KEYS:
            cv = int(chained.proficiencies.get(k, 0) or 0)
            iv = int(imported.proficiencies.get(k, 0) or 0)
            assert cv == iv, (
                f"{snap_key} L{lvl}: {k} rank chain={cv} != import={iv} "
                f"(level-up auto-bump disagrees with import derivation)"
            )
