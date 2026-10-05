"""Synthetic mechanics contracts; these do not assert Pathfinder rule content."""

from copy import deepcopy
from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest


V1_PACKAGE_HASH = "e573e78d00642c63ea1b9ee4fe5aa9436ab7c20ee593bf0e7dabe888f7aec002"
SYNTHETIC_SOURCE_SHA256 = sha256(
    (Path(__file__).parent / "fixtures" / "synthetic-source.bin").read_bytes()
).hexdigest()


def _compiler():
    return importlib.import_module("systems.pf2e.rules.ingestion.compile_pack")


def _registry():
    return importlib.import_module("systems.pf2e.rules.registry")


def _derive(package, class_id, level):
    module = importlib.import_module("systems.pf2e.rules.derivation.proficiencies")
    return module.derive_class_proficiencies(package, class_id, level)


def _v2_authoring(authoring):
    value = deepcopy(authoring)
    value["manifest"]["schema_version"] = 2
    value["manifest"]["ruleset_id"] = "pf2e-test-mechanics-0.1.0"
    value["sources"][0].update(
        artifact_sha256=SYNTHETIC_SOURCE_SHA256,
        printing={"status": "not_applicable", "designation": None},
        revision={"status": "not_applicable", "designation": None},
    )
    for record in value["records"]:
        record["mechanics"] = None
    fighter = next(record for record in value["records"] if record["kind"] == "class")
    fighter["automation"] = "derived"
    fighter["mechanics"] = {
        "supported_levels": [1],
        "proficiency_grants": [
            {"at_level": 1, "statistic": "perception", "rank": "expert"},
            {"at_level": 1, "statistic": "fortitude", "rank": "trained"},
        ],
    }
    return value


def _fighter(value):
    return next(record for record in value["records"] if record["kind"] == "class")


def _assert_rejected(value, code):
    with pytest.raises(ValueError) as error:
        _compiler().compile_package(value)
    assert error.value.code == code
    assert error.value.path.startswith("$.records[")


def test_v2_package_derives_synthetic_level_one_proficiencies(authoring, tmp_path):
    value = _v2_authoring(authoring)
    path = _compiler().write_package(value, tmp_path)
    package = _registry().load_package(path)

    assert package.manifest["schema_version"] == 2
    result = _derive(package, "pf2e.class.example", 1)
    assert dict(result) == {"perception": "expert", "fortitude": "trained"}
    with pytest.raises(TypeError):
        result["perception"] = "legendary"
    value["records"][0]["mechanics"]["proficiency_grants"][0]["rank"] = "legendary"
    assert dict(_derive(package, "pf2e.class.example", 1)) == {
        "perception": "expert", "fortitude": "trained",
    }


def test_v2_requires_mechanics_field_even_on_reference_records(authoring):
    value = _v2_authoring(authoring)
    del value["records"][1]["mechanics"]
    _assert_rejected(value, "missing_field")


def test_v2_rejects_unknown_mechanics_fields(authoring):
    value = _v2_authoring(authoring)
    _fighter(value)["mechanics"]["python"] = "run()"
    _assert_rejected(value, "unknown_field")


@pytest.mark.parametrize("mechanics,code", [
    ({"supported_levels": [], "proficiency_grants": []}, "invalid_value"),
    ({"supported_levels": ["1"], "proficiency_grants": []}, "invalid_type"),
    ({"supported_levels": [1], "proficiency_grants": [
        {"at_level": 1, "statistic": "perception", "rank": "superior"},
    ]}, "invalid_value"),
    ({"supported_levels": [1], "proficiency_grants": [
        {"at_level": 2, "statistic": "perception", "rank": "expert"},
    ]}, "invalid_value"),
])
def test_v2_rejects_invalid_proficiency_coverage(authoring, mechanics, code):
    value = _v2_authoring(authoring)
    _fighter(value)["mechanics"] = mechanics
    _assert_rejected(value, code)


def test_v2_rejects_duplicate_grants_for_one_level_and_statistic(authoring):
    value = _v2_authoring(authoring)
    grants = _fighter(value)["mechanics"]["proficiency_grants"]
    grants.append({"at_level": 1, "statistic": "perception", "rank": "trained"})
    _assert_rejected(value, "duplicate_id")


def test_v2_requires_a_level_one_baseline_grant(authoring):
    value = _v2_authoring(authoring)
    _fighter(value)["mechanics"]["proficiency_grants"] = []
    _assert_rejected(value, "invalid_value")


def test_v2_rejects_rank_downgrades_across_supported_levels(authoring):
    value = _v2_authoring(authoring)
    mechanics = _fighter(value)["mechanics"]
    mechanics["supported_levels"] = [1, 2]
    mechanics["proficiency_grants"].append(
        {"at_level": 2, "statistic": "perception", "rank": "trained"})
    _assert_rejected(value, "invalid_value")


def test_v2_rejects_derived_mechanics_on_enabled_nonclass(authoring):
    value = _v2_authoring(authoring)
    feat = next(record for record in value["records"] if record["kind"] == "feat")
    feat["automation"] = "derived"
    feat["mechanics"] = deepcopy(_fighter(value)["mechanics"])
    _assert_rejected(value, "unsupported_automation")


def test_v2_rejects_mechanics_on_reference_only_record(authoring):
    value = _v2_authoring(authoring)
    fighter = _fighter(value)
    fighter["automation"] = "reference_only"
    _assert_rejected(value, "unsupported_automation")


def test_derivation_rejects_quarantined_class(authoring, tmp_path):
    value = _v2_authoring(authoring)
    fighter = _fighter(value)
    fighter.update(state="quarantined", quarantine_reason="Synthetic quarantine",
                   automation="reference_only", mechanics=None)
    value["manifest"]["supported_classes"] = []
    value["manifest"]["exclusions"] = [{"id": fighter["id"], "reason": "Synthetic quarantine"}]
    feat = next(record for record in value["records"] if record["kind"] == "feat")
    feat["references"] = []
    feat["prerequisite"] = None
    package = _registry().load_package(_compiler().write_package(value, tmp_path))

    with pytest.raises(KeyError):
        _derive(package, fighter["id"], 1)


def test_derivation_rejects_level_outside_explicit_coverage(authoring, tmp_path):
    package = _registry().load_package(
        _compiler().write_package(_v2_authoring(authoring), tmp_path))
    with pytest.raises(ValueError) as error:
        _derive(package, "pf2e.class.example", 2)
    assert error.value.code == "unsupported_level"


def test_later_supported_level_keeps_prior_grants_and_applies_upgrades(authoring, tmp_path):
    value = _v2_authoring(authoring)
    mechanics = _fighter(value)["mechanics"]
    mechanics["supported_levels"] = [1, 2]
    mechanics["proficiency_grants"].extend([
        {"at_level": 2, "statistic": "perception", "rank": "master"},
        {"at_level": 2, "statistic": "will", "rank": "trained"},
    ])
    package = _registry().load_package(_compiler().write_package(value, tmp_path))

    assert dict(_derive(package, "pf2e.class.example", 1)) == {
        "perception": "expert", "fortitude": "trained",
    }
    assert dict(_derive(package, "pf2e.class.example", 2)) == {
        "perception": "master", "fortitude": "trained", "will": "trained",
    }


def test_v2_hash_is_independent_of_unordered_grant_and_record_input_order(authoring):
    value = _v2_authoring(authoring)
    first = _compiler().compile_package(value)
    reordered = deepcopy(value)
    _fighter(reordered)["mechanics"]["proficiency_grants"].reverse()
    reordered["records"].reverse()
    assert _compiler().compile_package(reordered) == first
    assert len(json.loads(first["manifest.json"])["package_hash"]) == 64


def test_v1_fixture_remains_byte_identical_and_loadable(authoring, tmp_path):
    files = _compiler().compile_package(authoring)
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema_version"] == 1
    assert manifest["package_hash"] == V1_PACKAGE_HASH
    assert all("mechanics" not in record for record in json.loads(files["records.json"]))

    package = _registry().load_package(_compiler().write_package(authoring, tmp_path))
    assert package.manifest["package_hash"] == V1_PACKAGE_HASH
    assert package.get("pf2e.class.example").name == "Example class"
