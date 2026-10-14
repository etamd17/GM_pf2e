"""Portable character files and recoverable schema migration."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from systems.cosmere.rules.profiles import normalize_binding
from systems.cosmere.rules.schema import RulesValidationError, to_dict

PID = "3520ca4cb56243f095ec8a5209174a68"


def creation_build():
    from systems.cosmere.origins import path_key_talent
    return {
        "name": "Progression", "level": 1, "path": "warrior", "ancestry": "Human",
        "attributes": {"str": 2, "spd": 2, "int": 2, "wil": 2, "awa": 2, "pre": 2},
        "skills": {"ath": 1, "agi": 1, "hwp": 1, "prc": 1, "sur": 1},
        "talents": [path_key_talent("warrior"), {"id": "test.creation-bonus", "name": "Creation Bonus"}],
        "expertises": [], "epic_choices": [],
    }


def homebrew_store():
    return {
        "surge": [{"type": "surge", "name": "Sandmastery", "code": "snd",
                   "attribute": "wil"}],
        "radiant_path": [{"type": "radiant_path", "name": "Sandbearer",
                           "slug": "sandbearer", "spren": "Sandspren",
                           "surges": ["snd", "grv"], "philosophy": "Endure."}],
        "talent": [{"type": "talent", "id": "hb:sand-heart",
                    "name": "Sand Heart", "path": "any",
                    "effects": [{"target": "health", "value": 3}]}],
    }


def homebrew_build():
    build = creation_build()
    build.update(radiant_order="sandbearer", ideals_sworn=1, is_radiant=True)
    build["skills"].update(snd=1, grv=1)
    build["talents"][-1] = {"id": "hb:sand-heart", "name": "Sand Heart"}
    return build


def document(*, runtime=None):
    from systems.cosmere.documents import normalize_character_document
    return normalize_character_document({"id": PID, "system": "cosmere",
        "name": "Progression", "build": creation_build(),
        "runtime_state": runtime or {"health": 4, "focus": 1}}, actor_id=PID)


def level_two_candidate(build):
    result = deepcopy(build)
    result["level"] = 2
    result["skills"]["agi"] += 1
    result["skills"]["hwp"] += 1
    result["talents"].append({"id": "test.level-2-talent-0", "name": "Level 2 Talent"})
    return result


def advanced_document():
    from systems.cosmere.advancement import commit_advancement
    doc = document(runtime={"health": 4, "focus": 1})
    build = level_two_candidate(doc["build"])
    return commit_advancement(doc, {"expected_revision": 0, "target_level": 2,
        "build": build, "goal_ids": [], "request_id": "9f4f532c3dde4dbf84b070836750c3c5"})


def test_portable_round_trip_preserves_rules_state_and_strips_server_authority():
    from systems.cosmere.transfer import export_character, import_character

    source = advanced_document()
    source.update(campaign_id="campaign-secret", owner_user_id="user-secret",
                  editor_user_ids=["editor-secret"], viewer_user_ids=["viewer-secret"])
    source["runtime_state"]["server_context"] = {
        "campaign_id": "nested-campaign-secret",
        "owner_user_id": "nested-owner-secret",
    }
    source["advancement_history"][0]["audit_metadata"]["target_id"] = PID
    payload = export_character(source)
    encoded = json.dumps(payload, sort_keys=True)
    for secret in ("campaign-secret", "user-secret", "editor-secret", "viewer-secret",
                   "nested-campaign-secret", "nested-owner-secret", PID):
        assert secret not in encoded
    assert payload["format"] == "cosmere-character"

    new_id = "90f5436bcc294824979c497abeab36f6"
    imported = import_character(payload, actor_id=new_id, expected_binding=normalize_binding())
    assert imported["id"] == new_id
    assert imported["rules_binding"] == source["rules_binding"]
    assert imported["runtime_state"]["health"] == source["runtime_state"]["health"]
    assert imported["runtime_state"]["focus"] == source["runtime_state"]["focus"]
    assert imported["runtime_state"]["server_context"] == {}
    assert imported["revision"] == source["revision"]
    assert len(imported["advancement_history"]) == 1
    assert imported["advancement_history"][0]["actor_id"] == new_id
    assert imported["derived"] == source["derived"]
    assert imported["owner_user_id"] is None


def test_import_ignores_derived_and_authority_and_rejects_wrong_binding():
    from systems.cosmere.transfer import export_character, import_character

    payload = export_character(advanced_document())
    payload["character"]["derived"] = {"health_max": 999999}
    payload["character"]["owner_user_id"] = "attacker"
    payload["character"]["runtime_state"]["server_context"] = {
        "campaign_id": "attacker-campaign", "viewer_user_ids": ["attacker"]}
    imported = import_character(payload, actor_id="90f5436bcc294824979c497abeab36f6",
                                expected_binding=normalize_binding())
    assert imported["derived"]["health_max"] != 999999
    assert imported["owner_user_id"] is None
    assert imported["runtime_state"]["server_context"] == {}
    wrong = deepcopy(payload)
    wrong["character"]["rules_binding"]["core_package"]["package_hash"] = "0" * 64
    with pytest.raises(RulesValidationError):
        import_character(wrong, actor_id="90f5436bcc294824979c497abeab36f6",
                         expected_binding=normalize_binding())


def test_portable_runtime_self_targets_are_pseudonymized_then_rebound():
    from systems.cosmere.transfer import (PORTABLE_ACTOR_ID, export_character,
                                          import_character)

    source = document(runtime={"health": 4, "effects": [{
        "id": "cosmere.effect.test", "source_id": "cosmere.rule.test",
        "target_id": PID, "parameters": {},
    }]})
    payload = export_character(source)
    exported_effect = payload["character"]["runtime_state"]["effects"][0]
    assert exported_effect["target_id"] == PORTABLE_ACTOR_ID
    assert PID not in json.dumps(payload, sort_keys=True)

    new_id = "90f5436bcc294824979c497abeab36f6"
    imported = import_character(payload, actor_id=new_id,
                                expected_binding=normalize_binding())
    assert imported["runtime_state"]["effects"][0]["target_id"] == new_id


def test_portable_transfer_and_migration_preserve_homebrew_skills_and_derivation(tmp_path):
    from systems.cosmere.documents import normalize_character_document
    from systems.cosmere.migration import migrate_character_directory
    from systems.cosmere.transfer import export_character, import_character

    source = normalize_character_document(
        {"id": PID, "system": "cosmere", "build": homebrew_build()},
        actor_id=PID, homebrew=homebrew_store())
    payload = export_character(source, homebrew=homebrew_store())
    imported = import_character(
        payload, actor_id="90f5436bcc294824979c497abeab36f6",
        expected_binding=normalize_binding(), homebrew=homebrew_store())
    assert imported["build"]["skills"]["snd"] == 1
    assert imported["derived"] == source["derived"]

    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    path = root / f"{PID}.json"
    path.write_text(json.dumps({"id": PID, "system": "cosmere",
                                "build": homebrew_build()}), encoding="utf-8")
    report = migrate_character_directory(
        root, apply=True, run_id="homebrew", homebrew=homebrew_store())
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert report["changed"] == 1
    assert migrated["build"]["skills"]["snd"] == 1
    assert migrated["derived"] == source["derived"]


def test_migration_dry_run_apply_backup_idempotence_and_theme_is_not_mechanics(tmp_path):
    from systems.cosmere.migration import migrate_character_directory

    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    path = root / f"{PID}.json"
    legacy = {"id": PID, "system": "cosmere", "name": "Vin-themed Kaladin",
              "build": {**creation_build(), "cosmere_world": "mistborn"},
              "play_state": {"health": 3}}
    path.write_text(json.dumps(legacy), encoding="utf-8")
    before = path.read_bytes()
    dry = migrate_character_directory(root, apply=False, cosmere_theme="mistborn")
    assert dry["changed"] == 1 and dry["documents"][0]["changes"]
    assert path.read_bytes() == before and dry["backup"] is None

    applied = migrate_character_directory(root, apply=True, cosmere_theme="mistborn",
                                          run_id="test-run")
    assert applied["changed"] == 1
    assert applied["backup"] and (tmp_path / applied["backup"]).is_dir()
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["cosmere_theme"] == "mistborn"
    assert migrated["rules_binding"] == to_dict(normalize_binding())
    assert migrated["origin_world"] == "roshar" and migrated["era"] is None
    after = path.read_bytes()
    rerun = migrate_character_directory(root, apply=True, cosmere_theme="mistborn",
                                        run_id="must-not-be-used")
    assert rerun["changed"] == 0 and rerun["backup"] is None
    assert path.read_bytes() == after


@pytest.mark.parametrize("legacy", [
    {"cosmere_theme": "mistborn"},
    {"build_theme": "mistborn"},
], ids=["top-level-theme", "nested-legacy-world"])
def test_default_migration_preserves_existing_theme_without_an_override(tmp_path, legacy):
    from systems.cosmere.migration import migrate_character_directory

    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    build = creation_build()
    document = {"id": PID, "system": "cosmere", "name": "Themed", "build": build}
    if "cosmere_theme" in legacy:
        document["cosmere_theme"] = legacy["cosmere_theme"]
    else:
        build["cosmere_world"] = legacy["build_theme"]
    path = root / f"{PID}.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    migrate_character_directory(root, apply=True, run_id="preserve-theme")

    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["cosmere_theme"] == "mistborn"
    assert migrated["build"]["cosmere_theme"] == "mistborn"
    assert migrated["rules_binding"] == to_dict(normalize_binding())


def test_migration_rolls_back_every_rewrite_when_readback_validation_fails(tmp_path):
    from systems.cosmere.migration import MigrationError, migrate_character_directory

    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    originals = {}
    for index in range(2):
        actor_id = f"{index + 1:032x}"
        path = root / f"{actor_id}.json"
        path.write_text(json.dumps({"id": actor_id, "system": "cosmere",
                                    "name": f"Hero {index}", "build": creation_build()}),
                        encoding="utf-8")
        originals[path.name] = path.read_bytes()

    calls = []
    def corrupting_writer(path, value):
        calls.append(path.name)
        path.write_text(json.dumps(value), encoding="utf-8")
        if len(calls) == 2:
            path.write_text("{}", encoding="utf-8")

    with pytest.raises(MigrationError, match="rolled back"):
        migrate_character_directory(root, apply=True, run_id="rollback-test",
                                    writer=corrupting_writer)
    assert {path.name: path.read_bytes() for path in root.glob("*.json")} == originals


def test_migration_cli_dry_run_and_apply_are_machine_readable(tmp_path):
    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    path = root / f"{PID}.json"
    path.write_text(json.dumps({"id": PID, "system": "cosmere", "name": "Legacy",
                                "build": creation_build()}), encoding="utf-8")
    tool = Path(__file__).resolve().parents[2] / "tools" / "migrate_cosmere_characters.py"
    dry = subprocess.run([sys.executable, str(tool), str(root)], capture_output=True,
                         text=True, cwd=tool.parents[1])
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["mode"] == "dry-run"
    assert json.loads(path.read_text(encoding="utf-8")).get("schema_version") is None
    applied = subprocess.run([sys.executable, str(tool), str(root), "--apply",
                              "--run-id", "cli-test", "--theme", "mistborn"],
                             capture_output=True, text=True, cwd=tool.parents[1])
    assert applied.returncode == 0, applied.stderr
    report = json.loads(applied.stdout)
    assert report["mode"] == "apply" and report["changed"] == 1
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2 and migrated["cosmere_theme"] == "mistborn"
    assert migrated["origin_world"] == "roshar"


def test_migration_cli_omitted_theme_preserves_legacy_nested_theme(tmp_path):
    root = tmp_path / "cosmere_pcs"
    root.mkdir()
    path = root / f"{PID}.json"
    path.write_text(json.dumps({"id": PID, "system": "cosmere", "name": "Legacy",
                                "build": {**creation_build(),
                                          "cosmere_world": "mistborn"}}),
                    encoding="utf-8")
    tool = Path(__file__).resolve().parents[2] / "tools" / "migrate_cosmere_characters.py"

    applied = subprocess.run([sys.executable, str(tool), str(root), "--apply",
                              "--run-id", "cli-preserve-theme"],
                             capture_output=True, text=True, cwd=tool.parents[1])

    assert applied.returncode == 0, applied.stderr
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["cosmere_theme"] == "mistborn"
    assert migrated["rules_binding"] == to_dict(normalize_binding())


def test_migration_cli_uses_the_campaign_homebrew_store(tmp_path):
    campaign = tmp_path / "campaign"
    root = campaign / "cosmere_pcs"
    root.mkdir(parents=True)
    (campaign / "homebrew.json").write_text(
        json.dumps(homebrew_store()), encoding="utf-8")
    path = root / f"{PID}.json"
    path.write_text(json.dumps({"id": PID, "system": "cosmere",
                                "build": homebrew_build()}), encoding="utf-8")
    tool = Path(__file__).resolve().parents[2] / "tools" / "migrate_cosmere_characters.py"

    applied = subprocess.run([sys.executable, str(tool), str(root), "--apply",
                              "--run-id", "cli-homebrew"],
                             capture_output=True, text=True, cwd=tool.parents[1])

    assert applied.returncode == 0, applied.stderr
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["build"]["skills"]["snd"] == 1
    assert migrated["derived"]["health_max"] == 15
