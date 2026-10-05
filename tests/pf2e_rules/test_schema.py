"""Rejecting bad authoring here prevents invalid content reaching a future engine."""
from copy import deepcopy
from hashlib import sha256
import importlib
from pathlib import Path

import pytest


# SHA-256 of fixtures/synthetic-source.bin, a binary-marked evidence artifact.
SYNTHETIC_SOURCE_SHA256 = "cd480e2b76e30f4ec48ac410e0aef2bf1b56ae210618323703237aa4549e4d23"


def normalize(value):
    return importlib.import_module("systems.pf2e.rules.schema").normalize_authoring(value)


def v2_authoring(authoring):
    authoring["manifest"]["schema_version"] = 2
    authoring["sources"][0].update(
        artifact_sha256=SYNTHETIC_SOURCE_SHA256,
        printing={"status": "not_applicable", "designation": None},
        revision={"status": "not_applicable", "designation": None},
    )
    for record in authoring["records"]:
        record["mechanics"] = None
    return authoring


def test_normalizes_without_mutating_inputs(authoring):
    original = deepcopy(authoring)
    authoring["records"].reverse()
    result = normalize(authoring)
    assert [r["id"] for r in result["records"]] == ["pf2e.class.example", "pf2e.feat.example"]
    assert authoring["records"][0] == original["records"][1]
    result["records"][0]["traits"].append("new")
    assert authoring["records"][1]["traits"] == ["test"]


def test_synthetic_source_hash_matches_raw_artifact_bytes():
    artifact = Path(__file__).parent / "fixtures" / "synthetic-source.bin"

    assert sha256(artifact.read_bytes()).hexdigest() == SYNTHETIC_SOURCE_SHA256


def test_v2_preserves_source_artifact_evidence(authoring):
    value = v2_authoring(authoring)

    normalized = normalize(value)

    assert normalized["sources"][0]["artifact_sha256"] == SYNTHETIC_SOURCE_SHA256
    assert normalized["sources"][0]["printing"] == {
        "status": "not_applicable", "designation": None,
    }
    assert normalized["sources"][0]["revision"] == {
        "status": "not_applicable", "designation": None,
    }


@pytest.mark.parametrize("field", ["printing", "revision"])
@pytest.mark.parametrize("evidence,code", [
    ({"status": "not_applicable"}, "missing_field"),
    ({"status": "not_applicable", "designation": None, "guess": True}, "unknown_field"),
])
def test_v2_source_evidence_shape_is_closed(authoring, field, evidence, code):
    package = v2_authoring(authoring)
    package["sources"][0][field] = evidence

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == code
    assert error.value.path == f"$.sources[0].{field}"


@pytest.mark.parametrize("artifact_sha256,code", [
    ("abc", "invalid_value"),
    ("F5F3DE91F99E579F9F6ACFC4CF4E5D3988DEB63DDB42DF4B1537075D0CAE44C8",
     "invalid_value"),
    ("g" * 64, "invalid_value"),
    (True, "invalid_type"),
])
def test_v2_rejects_invalid_artifact_sha256(authoring, artifact_sha256, code):
    package = v2_authoring(authoring)
    package["sources"][0]["artifact_sha256"] = artifact_sha256

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == code
    assert error.value.path == "$.sources[0].artifact_sha256"


@pytest.mark.parametrize("evidence,path", [
    ({"status": "verified", "designation": None}, "designation"),
    ({"status": "unverified", "designation": "First printing"}, "designation"),
    ({"status": "not_applicable", "designation": "N/A"}, "designation"),
    ({"status": "assumed", "designation": None}, "status"),
])
def test_v2_rejects_inconsistent_source_status_designations(authoring, evidence, path):
    package = v2_authoring(authoring)
    package["sources"][0]["printing"] = evidence

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "invalid_value"
    assert error.value.path == f"$.sources[0].printing.{path}"


@pytest.mark.parametrize("field,value,path", [
    ("artifact_sha256", None, "$.sources[0].artifact_sha256"),
    ("printing", {"status": "unverified", "designation": None},
     "$.sources[0].printing.status"),
    ("revision", {"status": "unverified", "designation": None},
     "$.sources[0].revision.status"),
])
def test_v2_enabled_sources_require_complete_artifact_evidence(authoring, field, value, path):
    package = v2_authoring(authoring)
    package["sources"][0][field] = value

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "unverified_source_artifact"
    assert error.value.path == path


def test_v2_enabled_sources_accept_verified_artifact_evidence(authoring):
    package = v2_authoring(authoring)
    package["sources"][0].update(
        printing={"status": "verified", "designation": "Synthetic printing"},
        revision={"status": "verified", "designation": "Synthetic revision"},
    )

    assert normalize(package)["sources"][0]["printing"]["status"] == "verified"


@pytest.mark.parametrize("field", ["printing", "revision"])
def test_v2_base_sources_cannot_claim_artifact_status_not_applicable(authoring, field):
    package = v2_authoring(authoring)
    package["sources"][0].update(
        scope="base",
        printing={"status": "verified", "designation": "Synthetic printing"},
        revision={"status": "verified", "designation": "Synthetic revision"},
    )
    package["sources"][0][field] = {"status": "not_applicable", "designation": None}

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "unverified_source_artifact"
    assert error.value.path == f"$.sources[0].{field}.status"


@pytest.mark.parametrize("source_id,scope", [
    ("pf2e.errata.disguised-book", "optional_sourcebook"),
    ("pf2e.source.disguised-errata", "errata"),
])
def test_v2_requires_errata_id_and_scope_to_match(authoring, source_id, scope):
    package = v2_authoring(authoring)
    package["sources"][0].update(id=source_id, scope=scope)
    package["manifest"].update(
        source_ids=[source_id],
        errata_ids=[source_id] if source_id.startswith("pf2e.errata.") else [],
    )
    for record in package["records"]:
        for citation in record["sources"]:
            citation["source_id"] = source_id

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "invalid_source_scope"
    assert error.value.path == "$.sources[0].scope"


@pytest.mark.parametrize("field", ["artifact_sha256", "printing", "revision"])
def test_v2_requires_source_artifact_fields(authoring, field):
    package = v2_authoring(authoring)
    del package["sources"][0][field]

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "missing_field"
    assert error.value.path == "$.sources[0]"


@pytest.mark.parametrize("field,value", [
    ("artifact_sha256", SYNTHETIC_SOURCE_SHA256),
    ("printing", {"status": "not_applicable", "designation": None}),
    ("revision", {"status": "not_applicable", "designation": None}),
])
def test_v1_rejects_source_artifact_fields(authoring, field, value):
    authoring["sources"][0][field] = value

    with pytest.raises(ValueError) as error:
        normalize(authoring)

    assert error.value.code == "unknown_field"
    assert error.value.path == "$.sources[0]"


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
    authoring = v2_authoring(authoring)
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
    source.update(
        rights="reviewed_redistributable",
        scope="base",
        license_family="fixture-license",
        printing={"status": "verified", "designation": "Synthetic printing"},
        revision={"status": "verified", "designation": "Synthetic revision"},
    )
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


@pytest.mark.parametrize("cycle", [False, True])
@pytest.mark.parametrize("field,value,path", [
    ("artifact_sha256", None, "artifact_sha256"),
    ("printing", {"status": "unverified", "designation": None}, "printing.status"),
    ("revision", {"status": "unverified", "designation": None}, "revision.status"),
])
def test_v2_transitive_errata_require_complete_artifact_evidence(
        authoring, field, value, path, cycle):
    package = v2_authoring(authoring)
    source = package["sources"][0]
    first = deepcopy(source)
    first.update(
        id="pf2e.errata.first",
        scope="errata",
        rights="reviewed_redistributable",
        printing={"status": "not_applicable", "designation": None},
        revision={"status": "verified", "designation": "First errata revision"},
    )
    second = deepcopy(first)
    second.update(
        id="pf2e.errata.second",
        revision={"status": "verified", "designation": "Second errata revision"},
    )
    source["errata_ids"] = [first["id"]]
    first["errata_ids"] = [second["id"]]
    if cycle:
        second["errata_ids"] = [first["id"]]
    second[field] = value
    package["sources"].extend([first, second])
    package["manifest"].update(
        source_ids=[item["id"] for item in package["sources"]],
        errata_ids=[first["id"], second["id"]],
    )

    with pytest.raises(ValueError) as error:
        normalize(package)

    assert error.value.code == "unverified_source_artifact"
    assert error.value.path == f"$.sources[2].{path}"
