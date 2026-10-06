"""The local Foundry-derived corpus must be accounted for without inference."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def evidence():
    return importlib.import_module("systems.pf2e.rules.ingestion.evidence_inventory")


def write_json(root, relative, value, *, raw=None):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    path.write_text(raw, encoding="utf-8")
    return path


def document(foundry_id, name, declared_type, *, title="Synthetic Core",
             license_name="ORC", remaster=True, system=None):
    value = {
        "_id": foundry_id,
        "img": "synthetic.webp",
        "name": name,
        "system": {
            "publication": {
                "title": title,
                "license": license_name,
                "remaster": remaster,
            },
            "traits": {"value": []},
        },
        "type": declared_type,
    }
    if system:
        value["system"].update(system)
    return value


def synthetic_tree(root):
    paths = {}
    paths["class"] = write_json(
        root, "classes/fighter.json",
        document("AAAAAAAAAAAAAAAA", "Fighter", "class", system={"spellcasting": 0}),
    )
    paths["feature"] = write_json(
        root, "class-features/reactive-strike.json",
        document("BBBBBBBBBBBBBBBB", "Reactive Strike", "feat"),
    )
    paths["dedication"] = write_json(
        root, "feats/archetype/fighter-dedication.json",
        document(
            "CCCCCCCCCCCCCCCC", "Fighter Dedication", "feat",
            system={"category": "class", "level": {"value": 2},
                    "traits": {"value": ["archetype", "dedication", "multiclass"]}},
        ),
    )
    paths["skill_feat"] = write_json(
        root, "feats/skill/level-1/example.json",
        document(
            "DDDDDDDDDDDDDDDD", "Skill Example", "feat",
            system={"category": "skill", "traits": {"value": ["general", "skill"]}},
        ),
    )
    paths["weapon"] = write_json(
        root, "equipment/example-sword.json",
        document("EEEEEEEEEEEEEEEE", "Example Sword", "weapon", system={"weight": 1.5}),
    )
    paths["adventure"] = write_json(
        root, "adventure-specific-actions/study.json",
        document("FFFFFFFFFFFFFFFF", "Study", "action", license_name="OGL", remaster=False),
    )
    paths["campaign"] = write_json(
        root, "campaign-effects/effect.json",
        document("GGGGGGGGGGGGGGGG", "Campaign Effect", "effect"),
    )
    paths["iconic"] = write_json(
        root, "iconics/hero.json",
        {"_id": "HHHHHHHHHHHHHHHH", "name": "Example Hero", "type": "character",
         "system": {}},
    )
    paths["folders"] = write_json(
        root, "feats/_folders.json", [{"_id": "folder", "name": "UI Folder"}],
    )
    paths["projection"] = write_json(
        root, "spells/master_spells.json", [{"name": "Projection", "level": 1}],
    )
    return paths


def test_scan_accounts_for_records_structural_and_generated_files(tmp_path):
    paths = synthetic_tree(tmp_path)

    report = evidence().scan_corpus(tmp_path)

    assert report["coverage"] == {
        "files": 10,
        "records": 8,
        "structural_files": 1,
        "structural_entries": 1,
        "generated_files": 1,
        "generated_entries": 1,
        "by_kind": {
            "action": 1, "character": 1, "class": 1, "class-feature": 1,
            "effect": 1, "equipment": 1, "feat": 2,
        },
        "by_license": {"OGL": 1, "ORC": 6, "unknown": 1},
        "by_pack": {
            "adventure-specific-actions": 1, "campaign-effects": 1,
            "class-features": 1, "classes": 1, "equipment": 1, "feats": 2,
            "iconics": 1,
        },
        "by_rules_era": {"legacy": 1, "remaster": 6, "unknown": 1},
        "by_scope": {"adventure": 1, "campaign": 1, "example": 1, "global": 5},
    }
    assert report["structural"] == [{
        "relative_path": "feats/_folders.json",
        "kind": "folder-metadata",
        "entry_count": 1,
        "content_sha256": hashlib.sha256(paths["folders"].read_bytes()).hexdigest(),
    }]
    assert report["generated"] == [{
        "relative_path": "spells/master_spells.json",
        "kind": "spell-projection",
        "entry_count": 1,
        "content_sha256": hashlib.sha256(paths["projection"].read_bytes()).hexdigest(),
    }]


def test_scan_uses_pack_scoped_identity_and_semantic_subcategories(tmp_path):
    synthetic_tree(tmp_path)

    records = {record["relative_path"]: record for record in evidence().scan_corpus(tmp_path)["records"]}

    assert records["classes/fighter.json"]["local_identity"] == {
        "pack": "classes", "declared_type": "class", "foundry_id": "AAAAAAAAAAAAAAAA",
    }
    assert records["class-features/reactive-strike.json"]["kind"] == "class-feature"
    assert records["feats/archetype/fighter-dedication.json"]["subcategory"] == "dedication"
    assert records["feats/skill/level-1/example.json"]["subcategory"] == "skill"
    assert records["equipment/example-sword.json"]["kind"] == "equipment"
    assert records["equipment/example-sword.json"]["subcategory"] == "weapon"
    assert records["adventure-specific-actions/study.json"]["scope"] == "adventure"
    assert records["campaign-effects/effect.json"]["scope"] == "campaign"
    assert records["iconics/hero.json"]["scope"] == "example"
    assert records["iconics/hero.json"]["publication"] == {
        "title": None, "license": None, "remaster": None,
    }


def test_scan_hashes_raw_bytes_and_is_deterministic(tmp_path):
    paths = synthetic_tree(tmp_path)
    first = evidence().scan_corpus(tmp_path)
    second = evidence().scan_corpus(tmp_path)

    assert first == second
    fighter = next(record for record in first["records"]
                   if record["relative_path"] == "classes/fighter.json")
    assert fighter["content_sha256"] == hashlib.sha256(paths["class"].read_bytes()).hexdigest()
    assert [record["relative_path"] for record in first["records"]] == sorted(
        record["relative_path"] for record in first["records"]
    )


def test_same_foundry_id_in_different_packs_remains_distinct(tmp_path):
    shared = "AAAAAAAAAAAAAAAA"
    write_json(tmp_path, "classes/example.json", document(shared, "Example", "class"))
    write_json(tmp_path, "class-features/example.json", document(shared, "Example", "feat"))

    records = evidence().scan_corpus(tmp_path)["records"]

    assert len(records) == 2
    assert {record["local_identity"]["pack"] for record in records} == {
        "classes", "class-features",
    }


def test_scan_preserves_irregular_source_publication_title_as_evidence(tmp_path):
    write_json(
        tmp_path, "equipment/fortress-plate.json",
        document(
            "AAAAAAAAAAAAAAAA", "Fortress Plate", "armor",
            title=" Pathfinder Treasure Vault (Remastered)",
        ),
    )

    record = evidence().scan_corpus(tmp_path)["records"][0]

    assert record["publication"]["title"] == " Pathfinder Treasure Vault (Remastered)"


def test_duplicate_identity_inside_one_pack_is_rejected(tmp_path):
    shared = "AAAAAAAAAAAAAAAA"
    write_json(tmp_path, "classes/one.json", document(shared, "One", "class"))
    write_json(tmp_path, "classes/two.json", document(shared, "Two", "class"))

    with pytest.raises(ValueError) as error:
        evidence().scan_corpus(tmp_path)

    assert error.value.code == "duplicate_identity"
    assert error.value.path == "$files/classes/two.json"


@pytest.mark.parametrize(("relative", "raw", "code"), [
    ("classes/duplicate.json",
     '{"_id":"AAAAAAAAAAAAAAAA","_id":"BBBBBBBBBBBBBBBB","name":"X","type":"class","system":{}}',
     "duplicate_key"),
    ("classes/nan.json",
     '{"_id":"AAAAAAAAAAAAAAAA","name":"X","type":"class","system":{"x":NaN}}',
     "invalid_json"),
    ("classes/infinite.json",
     '{"_id":"AAAAAAAAAAAAAAAA","name":"X","type":"class","system":{"x":1e9999}}',
     "invalid_json"),
])
def test_scan_rejects_ambiguous_or_nonfinite_json(tmp_path, relative, raw, code):
    write_json(tmp_path, relative, None, raw=raw)

    with pytest.raises(ValueError) as error:
        evidence().scan_corpus(tmp_path)

    assert error.value.code == code
    assert error.value.path == f"$files/{relative}"


def test_scan_rejects_symlinked_json(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    outside.write_text(json.dumps(document("AAAAAAAAAAAAAAAA", "Outside", "class")),
                       encoding="utf-8")
    linked = tmp_path / "classes" / "linked.json"
    linked.parent.mkdir(parents=True)
    try:
        linked.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")

    with pytest.raises(ValueError) as error:
        evidence().scan_corpus(tmp_path)

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$files/classes/linked.json"
