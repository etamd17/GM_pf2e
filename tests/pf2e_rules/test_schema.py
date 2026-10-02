"""Rejecting bad authoring here prevents invalid content reaching a future engine."""
from copy import deepcopy
import importlib

import pytest


def normalize(value):
    return importlib.import_module("systems.pf2e.rules.schema").normalize_authoring(value)


def test_normalizes_without_mutating_inputs(authoring):
    original = deepcopy(authoring)
    authoring["records"].reverse()
    result = normalize(authoring)
    assert [r["id"] for r in result["records"]] == ["pf2e.class.example", "pf2e.feat.example"]
    assert authoring["records"][0] == original["records"][1]
    result["records"][0]["traits"].append("new")
    assert authoring["records"][1]["traits"] == ["test"]


@pytest.mark.parametrize("path,value,code", [
    (("manifest", "schema_version"), True, "invalid_type"),
    (("manifest", "schema_version"), 3, "unsupported_version"),
    (("manifest", "system"), "cosmere", "invalid_value"),
    (("manifest", "ruleset_id"), "../escape", "invalid_id"),
    (("manifest", "content_version"), "current", "invalid_value"),
    (("manifest", "created_at"), "yesterday", "invalid_date"),
    (("manifest", "effective_date"), "2026-02-30", "invalid_date"),
    (("manifest", "overlays"), ["unimplemented"], "unsupported_composition"),
    (("manifest", "base_package"), "unimplemented", "unsupported_composition"),
    (("manifest", "supported_classes"), [], "roster_mismatch"),
    (("manifest", "source_ids"), [], "source_inventory_mismatch"),
    (("sources", 0, "rights"), "unknown", "unverified_source"),
    (("sources", 0, "verification_date"), None, "unverified_source"),
    (("sources", 0, "url"), "javascript:alert(1)", "invalid_url"),
    (("records", 0, "level"), True, "invalid_type"),
    (("records", 0, "level"), 21, "invalid_value"),
    (("records", 0, "id"), "pf2e.feat.example", "invalid_id"),
    (("records", 0, "sources"), [], "missing_provenance"),
    (("records", 0, "sources", 0, "page"), "", "invalid_value"),
    (("records", 0, "references"), ["pf2e.feat.missing"], "dangling_reference"),
    (("records", 0, "automation"), "derived", "unsupported_automation"),
    (("records", 1, "prerequisite"), {"op": "eval", "code": "raise RuntimeError()"}, "invalid_predicate"),
    (("records", 1, "prerequisite"), {"op": "class", "id": "pf2e.feat.example"}, "invalid_id"),
    (("records", 1, "prerequisite"), {"op": "level", "min": True}, "invalid_type"),
])
def test_rejects_invalid_authoring(authoring, path, value, code):
    target = authoring
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == code
    assert error.value.path


def test_unknown_fields_and_duplicate_ids_fail(authoring):
    authoring["records"][0]["script"] = "__import__('os')"
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "unknown_field"
    del authoring["records"][0]["script"]
    authoring["records"].append(deepcopy(authoring["records"][0]))
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "duplicate_id"


def test_quarantine_retains_unverified_content_but_blocks_references(authoring):
    record = authoring["records"][0]
    record.update(state="quarantined", quarantine_reason="Awaiting provenance", sources=[])
    authoring["manifest"]["supported_classes"] = []
    authoring["manifest"]["exclusions"] = [{"id": record["id"], "reason": "Awaiting provenance"}]
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "quarantined_reference"
    authoring["records"][1]["references"] = []
    authoring["records"][1]["prerequisite"] = None
    assert normalize(authoring)["records"][0]["state"] == "quarantined"


def test_quarantine_requires_explicit_exclusion(authoring):
    authoring["records"][1].update(state="quarantined", quarantine_reason="Needs review")
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "missing_exclusion"


def test_publication_requires_real_reviewed_sources_and_named_reviews(authoring):
    manifest = authoring["manifest"]
    manifest.update(publication_status="published", publication_date="2026-10-01")
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "missing_review"
    manifest["reviews"] = {
        key: {"reviewer": "Synthetic test reviewer", "reviewed_at": "2026-10-01T00:00:00Z"}
        for key in ("rules", "license")
    }
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "test_source_publication"
    source = authoring["sources"][0]
    source.update(rights="reviewed_redistributable", scope="base", license_family="fixture-license")
    manifest["license_family"] = "fixture-license"
    assert normalize(authoring)["manifest"]["publication_status"] == "published"


@pytest.mark.parametrize("prerequisite", [
    {"op": "any", "children": []},
    {"op": "not", "child": None},
    {"op": "trait", "value": "test", "script": "run"},
    {"op": "tradition", "value": "unknown"},
    {"op": "rarity", "value": "impossible"},
])
def test_closed_predicate_language(authoring, prerequisite):
    authoring["records"][1]["prerequisite"] = prerequisite
    with pytest.raises(ValueError):
        normalize(authoring)


def test_deep_predicates_fail_with_domain_error(authoring):
    node = {"op": "level", "min": 1}
    for _ in range(80):
        node = {"op": "not", "child": node}
    authoring["records"][1]["prerequisite"] = node
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code == "limit_exceeded"


@pytest.mark.parametrize("cycle", [False, True])
@pytest.mark.parametrize("changed", [
    {"rights": "unknown"},
    {"verification_date": None},
    {"notices": []},
    {"license_family": "unreviewed"},
    {"rights": "test_only", "scope": "test"},
])
def test_transitive_errata_cannot_bypass_publication_provenance(authoring, changed, cycle):
    source = authoring["sources"][0]
    source.update(rights="reviewed_redistributable", scope="base", license_family="fixture-license")
    first = deepcopy(source)
    first["id"] = "pf2e.errata.first"
    second = deepcopy(source)
    second["id"] = "pf2e.errata.second"
    source["errata_ids"] = [first["id"]]
    first["errata_ids"] = [second["id"]]
    if cycle:
        second["errata_ids"] = [first["id"]]
    second.update(changed)
    authoring["sources"].extend([first, second])
    manifest = authoring["manifest"]
    manifest.update(publication_status="published", publication_date="2026-10-01",
                    license_family="fixture-license",
                    source_ids=[s["id"] for s in authoring["sources"]],
                    errata_ids=[first["id"], second["id"]])
    manifest["reviews"] = {
        key: {"reviewer": "Fixture reviewer", "reviewed_at": "2026-10-01T00:00:00Z"}
        for key in ("rules", "license")
    }
    with pytest.raises(ValueError) as error:
        normalize(authoring)
    assert error.value.code in {"unverified_source", "missing_notice", "license_mismatch",
                                "test_source_publication"}
