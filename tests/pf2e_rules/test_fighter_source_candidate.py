"""Keep the unverified Player Core/Fighter evidence fail-closed."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = (
    ROOT / "systems" / "pf2e" / "rules" / "candidates"
    / "pf2e-player-core-fighter-unverified-0.1.0.json"
)
EXPECTED_PACKAGE_HASH = "cfb825822f9cb3e19e092f39e2d418319a1cc5a21855f8daff3d45b8f41b36d0"
FIGHTER_ID = "pf2e.class.fighter"
SOURCE_ID = "pf2e.source.player-core"
ARTIFACT_SHA256 = "494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9"
PRODUCT_ID = "PZO12001E"
PRODUCT_URL = "https://store.paizo.com/pathfinder-player-core-pdf/"
FIGHTER_PAGE_LOCATOR = "printed 137; PDF 138"
ORC_NOTICE_LOCATOR = "ORC Notice: printed 463; PDF 464; project-specific review pending."
FIGHTER_LEVEL_ONE_GRANTS = [
    {"at_level": 1, "statistic": "perception", "rank": "expert"},
    {"at_level": 1, "statistic": "fortitude", "rank": "expert"},
    {"at_level": 1, "statistic": "reflex", "rank": "expert"},
    {"at_level": 1, "statistic": "simple", "rank": "expert"},
    {"at_level": 1, "statistic": "martial", "rank": "expert"},
    {"at_level": 1, "statistic": "unarmed", "rank": "expert"},
    {"at_level": 1, "statistic": "will", "rank": "trained"},
    {"at_level": 1, "statistic": "advanced", "rank": "trained"},
    {"at_level": 1, "statistic": "light", "rank": "trained"},
    {"at_level": 1, "statistic": "medium", "rank": "trained"},
    {"at_level": 1, "statistic": "heavy", "rank": "trained"},
    {"at_level": 1, "statistic": "unarmored", "rank": "trained"},
    {"at_level": 1, "statistic": "class_dc", "rank": "trained"},
]


def _compiler():
    return importlib.import_module("systems.pf2e.rules.ingestion.compile_pack")


def _registry():
    return importlib.import_module("systems.pf2e.rules.registry")


def _derive(package):
    module = importlib.import_module(
        "systems.pf2e.rules.derivation.proficiencies")
    return module.derive_class_proficiencies(package, FIGHTER_ID, 1)


def _candidate():
    return json.loads(CANDIDATE.read_text(encoding="utf-8"))


def _attempt_activation(authoring):
    value = deepcopy(authoring)
    value["manifest"]["supported_classes"] = [FIGHTER_ID]
    value["manifest"]["exclusions"] = []
    fighter = value["records"][0]
    fighter.update(
        automation="derived",
        state="enabled",
        quarantine_reason=None,
        mechanics={
            "supported_levels": [1],
            "proficiency_grants": deepcopy(FIGHTER_LEVEL_ONE_GRANTS),
        },
    )
    return value


def test_unverified_fighter_candidate_round_trips_as_inaccessible_evidence(tmp_path):
    authoring = _candidate()
    assert authoring["records"][0]["sources"] == [
        {"source_id": SOURCE_ID, "page": FIGHTER_PAGE_LOCATOR}
    ]
    files = _compiler().compile_package(authoring)
    manifest = json.loads(files["manifest.json"])

    assert manifest["ruleset_id"] == "pf2e-player-core-fighter-unverified-0.1.0"
    assert manifest["package_hash"] == EXPECTED_PACKAGE_HASH
    assert manifest["supported_classes"] == []

    path = _compiler().write_package(authoring, tmp_path)
    package = _registry().load_package(path, expected_hash=EXPECTED_PACKAGE_HASH)
    source = package.sources[SOURCE_ID]
    assert source["product_id"] == PRODUCT_ID
    assert source["url"] == PRODUCT_URL
    assert source["artifact_sha256"] == ARTIFACT_SHA256
    assert source["notices"] == (ORC_NOTICE_LOCATOR,)
    assert source["printing"] == {"status": "unverified", "designation": None}
    assert source["revision"] == {"status": "unverified", "designation": None}
    assert package.records[FIGHTER_ID].state == "quarantined"
    assert package.records[FIGHTER_ID].mechanics is None
    with pytest.raises(KeyError):
        package.get(FIGHTER_ID)
    with pytest.raises(KeyError):
        _derive(package)


def test_fighter_candidate_cannot_enable_with_unresolved_rights_and_review_date():
    value = _attempt_activation(_candidate())

    with pytest.raises(ValueError) as error:
        _compiler().compile_package(value)

    assert error.value.code == "unverified_source"


def test_fighter_candidate_cannot_enable_with_unverified_printing_and_revision():
    value = _attempt_activation(_candidate())
    value["sources"][0].update(
        rights="reviewed_redistributable",
        verification_date="2026-10-04",
    )

    with pytest.raises(ValueError) as error:
        _compiler().compile_package(value)

    assert error.value.code == "unverified_source_artifact"


def test_quarantined_fighter_candidate_citations_resolve_in_its_inventory():
    value = _candidate()
    source_ids = {source["id"] for source in value["sources"]}

    assert source_ids == {SOURCE_ID}
    assert all(
        citation["source_id"] in source_ids
        for record in value["records"]
        for citation in record["sources"]
    )
