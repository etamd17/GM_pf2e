"""Closed legal-readiness evidence contracts against real frozen upstream inputs."""
from copy import deepcopy
from itertools import combinations
import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from systems.pf2e.rules.ingestion import class_legal_readiness as readiness
from systems.pf2e.rules.manifest import canonical_json, digest, read_json
from systems.pf2e.rules.validation import RulesValidationError

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "systems/pf2e/rules"
SOURCE = RULES / "evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json"
OVERLAY = RULES / "reviews/pf2e-class-source-review-2026-10-10.1"
PARENT = RULES / "reviews/pf2e-class-identities-2026-10-07.1"
SNAPSHOT = RULES / "evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json"
CORPUS = ROOT / "compendium_data"
SLUGS = ["battlecry", "dark-archive-remastered", "guns-and-gears-remastered",
         "impossible-magic", "player-core", "player-core-2", "rage-of-elements",
         "war-of-immortals"]
COUNTS = {
    "sources": 8, "artifact_hashes_observed": 1, "final_artifacts_missing": 7,
    "final_legal_notices_observed": 1, "final_legal_notices_missing": 7,
    "printings_unverified": 8, "revisions_unverified": 8,
    "errata_inventories_complete_as_captured": 1, "errata_inventories_unverified": 7,
    "license_reviews_ready": 0, "license_reviews_blocked": 8,
    "license_reviews_approved": 0, "enabled_mechanics": 0,
}
COMPLIANCE = ["attribution-review", "branding-review", "distribution-review",
              "notice-review", "reserved-material-review", "trademark-review"]
PC_BLOCKERS = ["downstream-compliance-missing", "errata-applicability-unresolved",
               "named-reviewer-missing", "printing-unverified", "revision-unverified"]
MISSING_BLOCKERS = ["downstream-compliance-missing", "errata-applicability-unresolved",
                    "errata-inventory-unverified", "final-artifact-missing",
                    "final-legal-notice-missing", "named-reviewer-missing",
                    "printing-unverified", "revision-unverified"]
MANIFEST_FIELDS = ("schema_version bundle_id scope authority created_at "
                   "class_review_overlay_id class_review_overlay_hash source_bundle_id "
                   "source_bundle_hash counts dossiers bundle_hash").split()
DOSSIER_FIELDS = ("schema_version evidence_id source_id source_evidence_id "
                  "source_dossier_sha256 product_ids artifact_observation "
                  "printing_observation revision_observation legal_notice_observation "
                  "errata_observation downstream_compliance review_readiness "
                  "supported_review_scopes").split()


@pytest.fixture(scope="session")
def frozen_upstream_results():
    """Exercise the real expensive chain once; copied/mutated inputs never use these doubles."""
    overlay = readiness.verify_class_review_overlay(
        OVERLAY, SNAPSHOT, CORPUS,
        expected_hash="e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb",
        parent_overlay_path=PARENT, evidence_manifest_path=SOURCE,
    )
    source = readiness.verify_class_source_evidence_bundle(
        SOURCE, expected_hash="4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3",
    )
    return overlay, source


@pytest.fixture(autouse=True)
def isolate_expensive_frozen_inputs(monkeypatch, frozen_upstream_results):
    """Cache only exact immutable inputs; preserve real verification for adversarial copies."""
    real_overlay = readiness.verify_class_review_overlay
    real_source = readiness.verify_class_source_evidence_bundle
    def overlay(path, snapshot, corpus, expected_hash=None, *, parent_overlay_path, evidence_manifest_path):
        if (path, snapshot, corpus, parent_overlay_path, evidence_manifest_path) == (OVERLAY, SNAPSHOT, CORPUS, PARENT, SOURCE):
            assert expected_hash == "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb"
            return deepcopy(frozen_upstream_results[0])
        return real_overlay(path, snapshot, corpus, expected_hash,
                            parent_overlay_path=parent_overlay_path, evidence_manifest_path=evidence_manifest_path)
    def source(path, expected_hash=None):
        if path == SOURCE:
            assert expected_hash == "4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3"
            return deepcopy(frozen_upstream_results[1])
        return real_source(path, expected_hash)
    monkeypatch.setattr(readiness, "verify_class_review_overlay", overlay)
    monkeypatch.setattr(readiness, "verify_class_source_evidence_bundle", source)


def write(path, value):
    path.write_bytes(canonical_json(value))


def deny_payload_reads(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("unsafe input reached a payload reader or upstream verifier")
    monkeypatch.setattr(readiness, "read_file", denied)
    monkeypatch.setattr(readiness, "verify_class_review_overlay", denied)
    monkeypatch.setattr(readiness, "verify_class_source_evidence_bundle", denied)


def rehash(root, relative=None):
    manifest = read_json((root / "manifest.json").read_bytes())
    if relative:
        for ref in manifest["dossiers"]:
            if ref["path"] == relative:
                ref["sha256"] = digest((root / relative).read_bytes())
    manifest.pop("bundle_hash", None)
    manifest["bundle_hash"] = digest(canonical_json(manifest))
    write(root / "manifest.json", manifest)
    return manifest


@pytest.fixture
def bundle(tmp_path):
    """Synthetic capture metadata only; all upstream facts are frozen real inputs."""
    root = tmp_path / "readiness"
    (root / "dossiers").mkdir(parents=True)
    upstream = read_json(SOURCE.read_bytes())
    refs = []
    for ref in upstream["dossiers"]:
        source = read_json((SOURCE.parent / ref["path"]).read_bytes())
        observed = source["source_id"] == "pf2e.source.player-core"
        artifact = deepcopy(source["artifact_observation"])
        locator = artifact.pop("notice_locator")
        artifact["status"] = "hash_observed" if observed else "missing"
        slug = source["source_id"].removeprefix("pf2e.source.")
        dossier = {
            "schema_version": 1, "evidence_id": "paizo.class-legal-readiness." + slug,
            "source_id": source["source_id"], "source_evidence_id": source["evidence_id"],
            "source_dossier_sha256": ref["sha256"], "product_ids": source["product_ids"],
            "artifact_observation": artifact,
            "printing_observation": source["printing_observation"],
            "revision_observation": source["revision_observation"],
            "legal_notice_observation": {"status": "observed" if observed else "missing",
                                         "family": "orc" if observed else None,
                                         "notice_locator": locator},
            "errata_observation": {
                "inventory_status": "complete_as_of_capture" if observed else "unverified",
                "applicability_status": "unresolved",
                "official_url": "https://paizo.com/pathfinder/faq",
                "captured_at": "2026-10-11T00:00:00Z" if observed else None,
                "response_sha256": "a" * 64 if observed else None,
                "locators": [{"release_label": "Synthetic October 2026",
                              "printing_label": "Synthetic first printing", "pages": [1, 2],
                              "entry_type": "clarification"}] if observed else [],
            },
            "downstream_compliance": {"status": "missing", "blockers": COMPLIANCE},
            "review_readiness": {"status": "blocked",
                                 "blockers": PC_BLOCKERS if observed else MISSING_BLOCKERS},
            "supported_review_scopes": ["license_readiness"],
        }
        write(root / ref["path"], dossier)
        refs.append({"evidence_id": dossier["evidence_id"], "source_id": source["source_id"],
                     "path": ref["path"], "sha256": digest(canonical_json(dossier))})
    manifest = {
        "schema_version": 1, "bundle_id": "pf2e-class-legal-readiness-2026-10-11.1",
        "scope": "class-license-readiness", "authority": "paizo",
        "created_at": "2026-10-11T00:00:00Z",
        "class_review_overlay_id": "pf2e-class-source-review-2026-10-10.1",
        "class_review_overlay_hash": "e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb",
        "source_bundle_id": upstream["bundle_id"], "source_bundle_hash": upstream["bundle_hash"],
        "counts": COUNTS, "dossiers": refs,
    }
    manifest["bundle_hash"] = digest(canonical_json(manifest))
    write(root / "manifest.json", manifest)
    return root


def verify(root, **kwargs):
    args = dict(manifest_path=root / "manifest.json", class_review_overlay_path=OVERLAY,
                snapshot_manifest_path=SNAPSHOT, corpus_root=CORPUS,
                parent_overlay_path=PARENT, source_evidence_manifest_path=SOURCE)
    args.update(kwargs)
    return readiness.verify_class_legal_readiness_bundle(**args)


DELETE = object()


def edit(root, field, value, *, manifest=False):
    relative = "manifest.json" if manifest else "dossiers/player-core.json"
    doc = read_json((root / relative).read_bytes())
    parts = field.split(".")
    target = doc
    for part in parts[:-1]:
        target = target[int(part)] if type(target) is list else target[part]
    key = int(parts[-1]) if type(target) is list else parts[-1]
    if value is DELETE:
        target.pop(key)
    else:
        target[key] = deepcopy(value)
    write(root / relative, doc)
    if not (manifest and field == "bundle_hash" and value is DELETE):
        rehash(root, None if manifest else relative)


def test_verifies_exact_contract_and_production_observations(bundle):
    result = verify(bundle)
    manifest = result["manifest"]
    assert set(manifest) == set(MANIFEST_FIELDS)
    assert manifest["counts"] == COUNTS
    assert manifest["dossiers"] == sorted(manifest["dossiers"], key=lambda r: r["source_id"])
    assert set(result["dossiers_by_source"]) == {"pf2e.source." + slug for slug in SLUGS}
    upstream = read_json(SOURCE.read_bytes())
    for ref, source_ref in zip(manifest["dossiers"], upstream["dossiers"]):
        dossier = result["dossiers_by_source"][ref["source_id"]]
        assert set(dossier) == set(DOSSIER_FIELDS)
        assert set(ref) == {"source_id", "evidence_id", "path", "sha256"}
        assert dossier["evidence_id"] == "paizo.class-legal-readiness." + ref["source_id"].removeprefix("pf2e.source.")
        assert dossier["source_evidence_id"] == source_ref["evidence_id"]
        assert dossier["source_dossier_sha256"] == source_ref["sha256"]
        assert ref["sha256"] == digest((bundle / ref["path"]).read_bytes())
        assert (bundle / ref["path"]).read_bytes() == canonical_json(dossier)
        assert dossier["downstream_compliance"] == {"status": "missing", "blockers": COMPLIANCE}
        assert dossier["printing_observation"] == {"status": "unverified", "designation": None}
        assert dossier["revision_observation"] == {"status": "unverified", "designation": None}
        assert dossier["supported_review_scopes"] == ["license_readiness"]
        pc = ref["source_id"] == "pf2e.source.player-core"
        assert dossier["review_readiness"] == {"status": "blocked", "blockers": PC_BLOCKERS if pc else MISSING_BLOCKERS}
        assert dossier["errata_observation"]["inventory_status"] == ("complete_as_of_capture" if pc else "unverified")
        assert dossier["errata_observation"]["applicability_status"] == "unresolved"
        if pc:
            assert dossier["artifact_observation"] == {"status": "hash_observed", "product_id": "PZO12001E", "sha256": "494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9", "retained": False}
            assert dossier["legal_notice_observation"] == {"status": "observed", "family": "orc", "notice_locator": {"printed_page": 463, "pdf_page": 464}}
        else:
            assert dossier["artifact_observation"] == {"status": "missing", "product_id": None, "sha256": None, "retained": False}
            assert dossier["legal_notice_observation"] == {"status": "missing", "family": None, "notice_locator": None}
            assert dossier["errata_observation"]["captured_at"] is None
            assert dossier["errata_observation"]["response_sha256"] is None
            assert dossier["errata_observation"]["locators"] == []
    unhashed = deepcopy(manifest)
    unhashed.pop("bundle_hash")
    assert digest(canonical_json(unhashed)) == manifest["bundle_hash"]
    assert (bundle / "manifest.json").read_bytes() == canonical_json(manifest)
    assert verify(bundle, expected_hash=manifest["bundle_hash"])["manifest"] == manifest


def test_detached_verifications_and_reports_with_stable_order(bundle):
    result = verify(bundle)
    report = readiness.class_legal_readiness_report(result)
    assert set(report) == {"bundle_id", "bundle_hash", "counts", "sources"}
    assert report["counts"] == COUNTS
    assert report["sources"] == [{"source_id": "pf2e.source." + slug,
                                  "blockers": PC_BLOCKERS if slug == "player-core" else MISSING_BLOCKERS}
                                 for slug in SLUGS]
    reverse = deepcopy(result)
    reverse["dossiers_by_source"] = dict(reversed(list(reverse["dossiers_by_source"].items())))
    reverse["manifest"] = dict(reversed(list(reverse["manifest"].items())))
    assert readiness.class_legal_readiness_report(reverse) == report
    repeated = readiness.class_legal_readiness_report(result)
    report["counts"]["sources"] = 0
    report["sources"][0]["blockers"].clear()
    assert readiness.class_legal_readiness_report(result) == repeated
    result["manifest"]["counts"].clear()
    result["dossiers_by_source"]["pf2e.source.player-core"]["review_readiness"]["blockers"].clear()
    pc_ref = next(ref for ref in result["manifest"]["dossiers"] if ref["source_id"] == "pf2e.source.player-core")
    assert result["dossiers_by_hash"][pc_ref["sha256"]]["review_readiness"]["blockers"] == PC_BLOCKERS
    fresh = verify(bundle)
    assert readiness.class_legal_readiness_report(fresh)["counts"] == COUNTS
    assert readiness.class_legal_readiness_report(fresh)["sources"][0]["blockers"] == MISSING_BLOCKERS
    assert fresh["dossiers_by_hash"][fresh["manifest"]["dossiers"][4]["sha256"]]["review_readiness"]["blockers"] == PC_BLOCKERS


def test_report_accepts_large_individually_bounded_verified_dossiers(bundle):
    """Catches counting both detached indexes toward one per-document JSON limit."""
    locators = [{"release_label": f"Synthetic {i:04}", "printing_label": "Synthetic",
                 "entry_type": "erratum", "pages": list(range(1, 81))} for i in range(4000)]
    edit(bundle, "errata_observation.locators", locators)
    verified = verify(bundle)
    report = readiness.class_legal_readiness_report(verified)
    assert report["counts"] == COUNTS
    assert next(row for row in report["sources"] if row["source_id"] == "pf2e.source.player-core")["blockers"] == PC_BLOCKERS


@pytest.mark.parametrize("key", ["manifest", "dossiers_by_hash", "dossiers_by_source", "extra"])
def test_report_requires_exact_bundle_shape(bundle, key):
    verified = verify(bundle)
    if key == "extra":
        verified["notes"] = "unsupported"
    else:
        verified.pop(key)
    with pytest.raises(RulesValidationError):
        readiness.class_legal_readiness_report(verified)


@pytest.mark.parametrize("mutation", ["hash_index_type", "hash_missing", "hash_extra", "hash_key_swap",
    "hash_content_swap", "source_content_swap", "divergent_dossier", "manifest_reference_hash", "manifest_self_hash"])
def test_report_rejects_inconsistent_index_identity_or_content(bundle, mutation):
    verified = verify(bundle)
    refs = verified["manifest"]["dossiers"]
    first, second = refs[0], refs[1]
    hashes = verified["dossiers_by_hash"]
    sources = verified["dossiers_by_source"]
    if mutation == "hash_index_type":
        verified["dossiers_by_hash"] = []
    elif mutation == "hash_missing":
        hashes.pop(first["sha256"])
    elif mutation == "hash_extra":
        hashes["0" * 64] = deepcopy(hashes[first["sha256"]])
    elif mutation == "hash_key_swap":
        hashes["0" * 64] = hashes.pop(first["sha256"])
    elif mutation == "hash_content_swap":
        hashes[first["sha256"]], hashes[second["sha256"]] = hashes[second["sha256"]], hashes[first["sha256"]]
    elif mutation == "source_content_swap":
        sources[first["source_id"]], sources[second["source_id"]] = sources[second["source_id"]], sources[first["source_id"]]
    elif mutation == "divergent_dossier":
        pc_hash = refs[4]["sha256"]
        hashes[pc_hash]["errata_observation"]["response_sha256"] = "b" * 64
    elif mutation == "manifest_reference_hash":
        first["sha256"] = "0" * 64
    else:
        verified["manifest"]["bundle_hash"] = "0" * 64
    with pytest.raises(RulesValidationError):
        readiness.class_legal_readiness_report(verified)


CLOSED_OBJECTS = {
    "": DOSSIER_FIELDS, "product_ids": ["hardcover", "pdf"],
    "artifact_observation": ["status", "product_id", "sha256", "retained"],
    "printing_observation": ["status", "designation"], "revision_observation": ["status", "designation"],
    "legal_notice_observation": ["status", "family", "notice_locator"],
    "legal_notice_observation.notice_locator": ["printed_page", "pdf_page"],
    "errata_observation": ["inventory_status", "applicability_status", "official_url", "captured_at", "response_sha256", "locators"],
    "errata_observation.locators.0": ["release_label", "printing_label", "pages", "entry_type"],
    "downstream_compliance": ["status", "blockers"], "review_readiness": ["status", "blockers"],
}


@pytest.mark.parametrize("field", [f"{p}.{f}".lstrip(".") for p, fields in CLOSED_OBJECTS.items() for f in fields])
def test_missing_dossier_keys_are_rejected(bundle, field):
    edit(bundle, field, DELETE)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("parent", list(CLOSED_OBJECTS))
def test_unknown_dossier_keys_are_rejected(bundle, parent):
    edit(bundle, (parent + ".notes").lstrip("."), "unbounded prose")
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", MANIFEST_FIELDS + ["counts." + f for f in COUNTS] + ["dossiers.0." + f for f in ["evidence_id", "source_id", "path", "sha256"]])
def test_missing_manifest_keys_are_rejected(bundle, field):
    edit(bundle, field, DELETE, manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", ["notes", "counts.notes", "dossiers.0.notes", "license_review", "activation", "rules", "reviewer", "description"])
def test_manifest_rejects_prose_review_identity_and_extra_fields(bundle, field):
    edit(bundle, field, "unsupported", manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


INVALID_DOSSIER = [
    ("schema_version", True), ("schema_version", 2), ("evidence_id", "paizo.class-legal-readiness.other"),
    ("source_id", "invalid"), ("source_evidence_id", "paizo.class-source.other"),
    ("source_dossier_sha256", "0" * 64), ("source_dossier_sha256", "A" * 64),
    ("product_ids.hardcover", "PZO12004-HC"), ("product_ids.pdf", "PZO12004E"),
    ("artifact_observation.status", "approved"), ("artifact_observation.product_id", "PZO12001-HC"),
    ("artifact_observation.sha256", "b" * 64), ("artifact_observation.retained", True),
    ("artifact_observation.retained", 0), ("artifact_observation", []),
    ("printing_observation.status", "verified"), ("printing_observation.designation", "first"),
    ("revision_observation.status", "verified"), ("revision_observation.designation", "first"),
    ("legal_notice_observation.status", "approved"), ("legal_notice_observation.family", "ogl"),
    ("legal_notice_observation.notice_locator.printed_page", 462),
    ("legal_notice_observation.notice_locator.pdf_page", True),
    ("legal_notice_observation.notice_locator.pdf_page", 0),
    ("errata_observation.inventory_status", "complete"), ("errata_observation.applicability_status", "resolved"),
    ("errata_observation.official_url", "http://paizo.com/pathfinder/faq"),
    ("errata_observation.official_url", "https://example.com/pathfinder/faq"),
    ("errata_observation.captured_at", "2026-02-30T00:00:00Z"),
    ("errata_observation.response_sha256", "bad"), ("errata_observation.locators", []),
    ("errata_observation.locators.0.release_label", "x" * 65),
    ("errata_observation.locators.0.printing_label", "first\nsecond"),
    ("errata_observation.locators.0.release_label", "first\u2028second"),
    ("errata_observation.locators.0.entry_type", "correction"),
    ("errata_observation.locators.0.pages", [2, 1]), ("errata_observation.locators.0.pages", [1, 1]),
    ("errata_observation.locators.0.pages", [0]), ("errata_observation.locators.0.pages", [True]),
    ("errata_observation.locators.0.pages", []), ("errata_observation.locators.0.pages", "1"),
    ("downstream_compliance.status", "complete"), ("downstream_compliance.blockers", []),
    ("downstream_compliance.blockers", COMPLIANCE[:-1]),
    ("downstream_compliance.blockers", COMPLIANCE[::-1]),
    ("downstream_compliance.blockers", COMPLIANCE + [COMPLIANCE[-1]]),
    ("downstream_compliance.blockers", ["other"]),
    ("review_readiness.status", "ready"), ("review_readiness.blockers", []),
    ("review_readiness.blockers", PC_BLOCKERS[:-1]),
    ("review_readiness.blockers", sorted(PC_BLOCKERS + ["final-artifact-missing"])),
    ("review_readiness.blockers", PC_BLOCKERS[::-1]),
    ("review_readiness.blockers", PC_BLOCKERS + [PC_BLOCKERS[-1]]),
    ("supported_review_scopes", ["license_review"]), ("supported_review_scopes", []),
    ("reviewer", "named person"), ("license_review", {}), ("activation", "none"),
    ("rules", []), ("notice_prose", "copied text"),
]


@pytest.mark.parametrize("field,value", INVALID_DOSSIER)
def test_invalid_dossier_values_cannot_be_made_valid_by_rehashing(bundle, field, value):
    edit(bundle, field, value)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 2), ("bundle_id", "bad"),
    ("bundle_id", "pf2e-class-legal-readiness-2026-02-30.1"),
    ("scope", "license_review"), ("authority", "other"), ("created_at", "bad"),
    ("class_review_overlay_id", "pf2e-class-identities-2026-10-07.1"),
    ("class_review_overlay_hash", "0" * 64), ("source_bundle_id", "other"),
    ("source_bundle_hash", "0" * 64), ("counts.sources", True), ("counts.sources", 7),
    ("counts.license_reviews_ready", 1), ("counts.enabled_mechanics", 1),
    ("dossiers", []), ("dossiers.0.evidence_id", "paizo.class-legal-readiness.other"),
])
def test_invalid_manifest_values_are_rejected(bundle, field, value):
    edit(bundle, field, value, manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", MANIFEST_FIELDS + ["counts." + field for field in COUNTS])
def test_manifest_fields_reject_wrong_types(bundle, field):
    if field == "bundle_hash":
        manifest = read_json((bundle / "manifest.json").read_bytes())
        manifest[field] = {"unsupported": True}
        write(bundle / "manifest.json", manifest)
    else:
        edit(bundle, field, {"unsupported": True}, manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", list(COUNTS))
def test_every_derived_manifest_count_is_enforced(bundle, field):
    edit(bundle, "counts." + field, COUNTS[field] + 1, manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", [f"{p}.{f}".lstrip(".") for p, fields in CLOSED_OBJECTS.items() for f in fields])
def test_dossier_fields_reject_wrong_types(bundle, field):
    # No field in the closed dossier contract permits a dict where this sentinel is used.
    edit(bundle, field, {"unsupported": True})
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("slug", [slug for slug in SLUGS if slug != "player-core"])
def test_missing_source_cannot_claim_observed_final_artifact_even_when_rehashed(bundle, slug):
    relative = f"dossiers/{slug}.json"
    dossier = read_json((bundle / relative).read_bytes())
    dossier["artifact_observation"] = {"status": "hash_observed", "product_id": dossier["product_ids"]["pdf"],
                                       "sha256": "a" * 64, "retained": False}
    dossier["legal_notice_observation"] = {"status": "observed", "family": "orc",
                                            "notice_locator": {"printed_page": 1, "pdf_page": 2}}
    dossier["review_readiness"]["blockers"] = [b for b in MISSING_BLOCKERS
                                                if b not in {"final-artifact-missing", "final-legal-notice-missing"}]
    write(bundle / relative, dossier)
    rehash(bundle, relative)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("path", ["../player-core.json", "dossiers/../player-core.json",
    "dossiers\\player-core.json", "C:/player-core.json", "dossiers/CON.json",
    "dossiers/player-core.json.", "dossiers/player-core.json ", "dossiers/playe\u0301r-core.json",
    "dossiers/Player-Core.json", "dossiers/player-core-2.json", "/dossiers/player-core.json"])
def test_unsafe_or_wrong_source_paths_are_rejected(bundle, path):
    edit(bundle, "dossiers.4.path", path, manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("field", ["source_id", "evidence_id", "path", "sha256"])
def test_duplicate_manifest_identity_is_rejected(bundle, field):
    manifest = read_json((bundle / "manifest.json").read_bytes())
    edit(bundle, "dossiers.1." + field, manifest["dossiers"][0][field], manifest=True)
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("mutation", ["extra", "extra_dossier", "missing", "wrong_case", "wrong_case_manifest", "directory", "noncanonical", "tampered"])
def test_exact_package_inventory_and_canonical_bytes(bundle, mutation):
    path = bundle / "dossiers/player-core.json"
    if mutation == "extra":
        (bundle / "extra.json").write_bytes(b"{}\n")
    elif mutation == "extra_dossier":
        (bundle / "dossiers/extra.json").write_bytes(b"{}\n")
    elif mutation == "missing":
        path.unlink()
    elif mutation == "wrong_case":
        path.rename(path.with_name("Player-Core.json"))
    elif mutation == "wrong_case_manifest":
        (bundle / "manifest.json").rename(bundle / "Manifest.json")
    elif mutation == "directory":
        path.unlink()
        path.mkdir()
    elif mutation == "noncanonical":
        path.write_text(json.dumps(read_json(path.read_bytes()), indent=2))
        rehash(bundle, "dossiers/player-core.json")
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(RulesValidationError):
        verify(bundle)


def test_rehashed_swapped_dossiers_are_rejected(bundle):
    left, right = bundle / "dossiers/battlecry.json", bundle / "dossiers/player-core.json"
    lbytes, rbytes = left.read_bytes(), right.read_bytes()
    left.write_bytes(rbytes)
    right.write_bytes(lbytes)
    rehash(bundle, "dossiers/battlecry.json")
    rehash(bundle, "dossiers/player-core.json")
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("mutation", ["duplicate", "unsorted"])
def test_errata_locators_require_unique_exact_tuple_order(bundle, mutation):
    first = read_json((bundle / "dossiers/player-core.json").read_bytes())["errata_observation"]["locators"][0]
    second = deepcopy(first)
    second["release_label"] = "Z synthetic release"
    edit(bundle, "errata_observation.locators", [first, first] if mutation == "duplicate" else [second, first])
    with pytest.raises(RulesValidationError):
        verify(bundle)


def test_capture_fields_are_bounded_fixture_data_not_production_pins(bundle):
    edit(bundle, "errata_observation.response_sha256", "b" * 64)
    edit(bundle, "errata_observation.captured_at", "2026-10-11T12:34:56Z")
    edit(bundle, "errata_observation.locators.0.pages", [10, 11])
    assert verify(bundle)["manifest"]["counts"] == COUNTS


@pytest.mark.parametrize("expected", ["bad", "0" * 64, True])
def test_optional_expected_hash_is_validated_at_binding(bundle, expected):
    with pytest.raises(RulesValidationError) as caught:
        verify(bundle, expected_hash=expected)
    assert caught.value.path == "$.binding.bundle_hash"


@pytest.mark.parametrize("leaf", ["manifest.json", "dossiers/player-core.json"])
def test_fresh_verification_rejects_disk_mutation(bundle, leaf):
    verify(bundle)
    path = bundle / leaf
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(RulesValidationError):
        verify(bundle)


def copy_inputs(tmp_path):
    """Independent real packages for adversarial mutations; never modify frozen originals."""
    args = {}
    for field, path in [("class_review_overlay_path", OVERLAY), ("parent_overlay_path", PARENT),
                        ("source_evidence_manifest_path", SOURCE), ("snapshot_manifest_path", SNAPSHOT)]:
        source_dir = path if path.is_dir() else path.parent
        target = tmp_path / field / source_dir.name
        shutil.copytree(source_dir, target)
        args[field] = target if path.is_dir() else target / path.name
    target = tmp_path / "corpus"
    shutil.copytree(CORPUS / "classes", target / "classes")
    args["corpus_root"] = target
    return args


@pytest.mark.parametrize("field", ["class_review_overlay_path", "parent_overlay_path", "source_evidence_manifest_path", "snapshot_manifest_path", "corpus_root"])
@pytest.mark.parametrize("mutation", ["missing", "drift", "replacement"])
def test_frozen_upstream_missing_drift_and_self_consistent_replacements(bundle, tmp_path, field, mutation):
    args = copy_inputs(tmp_path)
    root = args[field]
    leaf = root if root.is_file() else root / ("manifest.json" if field != "corpus_root" else "classes/fighter.json")
    if field == "corpus_root":
        leaf = next((root / "classes").glob("*.json"))
    if mutation == "missing":
        leaf.unlink()
    elif mutation == "drift":
        leaf.write_bytes(leaf.read_bytes() + b" ")
    else:
        doc = read_json(leaf.read_bytes())
        if field == "source_evidence_manifest_path":
            doc["created_at"] = "2026-10-11T00:00:00Z"
            doc.pop("bundle_hash")
            doc["bundle_hash"] = digest(canonical_json(doc))
        elif field in {"class_review_overlay_path", "parent_overlay_path"}:
            doc["created_at"] = "2026-10-11T00:00:00Z"
            doc.pop("overlay_hash")
            doc["overlay_hash"] = digest(canonical_json(doc))
        else:
            doc["synthetic_replacement"] = True
        write(leaf, doc)
    with pytest.raises(RulesValidationError):
        verify(bundle, **args)


def test_rehashed_source_dossier_replacement_is_rejected(bundle, tmp_path):
    args = copy_inputs(tmp_path)
    root = args["source_evidence_manifest_path"].parent
    leaf = root / "dossiers/player-core.json"
    doc = read_json(leaf.read_bytes())
    doc["official_product_title"] = "Synthetic replacement"
    write(leaf, doc)
    rehash(root, "dossiers/player-core.json")
    with pytest.raises(RulesValidationError):
        verify(bundle, **args)


def test_readiness_and_source_package_siblings_remain_valid(bundle, tmp_path):
    shared = tmp_path / "paizo"
    shared.mkdir()
    moved = shared / "readiness"
    bundle.rename(moved)
    source_copy = shared / "source"
    shutil.copytree(SOURCE.parent, source_copy)
    assert verify(moved, source_evidence_manifest_path=source_copy / "manifest.json")["manifest"]["counts"] == COUNTS


@pytest.mark.parametrize("left,right", [("class_review_overlay_path", "parent_overlay_path"),
    ("class_review_overlay_path", "source_evidence_manifest_path"),
    ("source_evidence_manifest_path", "manifest_path")])
def test_swapped_package_arguments_are_rejected(bundle, left, right):
    values = dict(class_review_overlay_path=OVERLAY, parent_overlay_path=PARENT,
                  source_evidence_manifest_path=SOURCE, manifest_path=bundle / "manifest.json")
    values[left], values[right] = values[right], values[left]
    with pytest.raises(RulesValidationError):
        verify(bundle, **values)


@pytest.mark.parametrize("kind", ["direct", "ancestor", "case", "unicode"])
def test_tree_overlap_is_rejected_before_first_read(bundle, monkeypatch, kind):
    corpus = bundle if kind == "direct" else bundle.parent
    if kind == "case":
        corpus = Path(str(bundle).upper())
    if kind == "unicode":
        renamed = bundle.with_name("readin\u00e9ss")
        bundle.rename(renamed)
        bundle = renamed
        corpus = renamed.with_name("readine\u0301ss")
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError) as caught:
        verify(bundle, corpus_root=corpus)
    assert caught.value.code == "path_overlap"


TREE_FIELDS = ["manifest_path", "class_review_overlay_path", "snapshot_manifest_path",
               "corpus_root", "parent_overlay_path", "source_evidence_manifest_path"]


@pytest.mark.parametrize("left,right", list(combinations(TREE_FIELDS, 2)))
def test_all_six_input_trees_must_be_pairwise_disjoint(bundle, monkeypatch, left, right):
    paths = dict(manifest_path=bundle / "manifest.json", class_review_overlay_path=OVERLAY,
                 snapshot_manifest_path=SNAPSHOT, corpus_root=CORPUS,
                 parent_overlay_path=PARENT, source_evidence_manifest_path=SOURCE)
    manifest_fields = {"manifest_path", "snapshot_manifest_path", "source_evidence_manifest_path"}
    root = paths[left].parent if left in manifest_fields else paths[left]
    paths[right] = root / paths[right].name if right in manifest_fields else root
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError) as caught:
        verify(bundle, **paths)
    assert caught.value.code == "path_overlap"


@pytest.mark.parametrize("kind", ["readiness", "upstream", "overlay", "parent", "snapshot", "cross_package"])
def test_real_hardlinks_rejected_before_first_payload_read(bundle, tmp_path, monkeypatch, kind):
    args = {}
    leaf = bundle / "dossiers/player-core.json"
    target = tmp_path / "hardlink-target.json"
    if kind == "upstream":
        source_copy = tmp_path / "source"
        shutil.copytree(SOURCE.parent, source_copy)
        args["source_evidence_manifest_path"] = source_copy / "manifest.json"
        leaf = source_copy / "dossiers/player-core.json"
    if kind in {"overlay", "parent"}:
        origin = OVERLAY if kind == "overlay" else PARENT
        copied = tmp_path / origin.name
        shutil.copytree(origin, copied)
        args["class_review_overlay_path" if kind == "overlay" else "parent_overlay_path"] = copied
        leaf = copied / "records.json"
    if kind == "snapshot":
        copied = tmp_path / "snapshot"
        copied.mkdir()
        leaf = copied / SNAPSHOT.name
        shutil.copy2(SNAPSHOT, leaf)
        args["snapshot_manifest_path"] = leaf
    if kind == "cross_package":
        source_copy = tmp_path / "source"
        shutil.copytree(SOURCE.parent, source_copy)
        args["source_evidence_manifest_path"] = source_copy / "manifest.json"
        target = source_copy / "dossiers/player-core.json"
        target.unlink()
    os.link(leaf, target)
    assert leaf.stat().st_nlink > 1
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError) as caught:
        verify(bundle, **args)
    assert caught.value.code == "path_overlap"


def test_unsafe_upstream_fixed_leaf_fails_before_any_payload_read(bundle, tmp_path, monkeypatch):
    source = tmp_path / "source"
    shutil.copytree(SOURCE.parent, source)
    (source / "dossiers/war-of-immortals.json").unlink()
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError):
        verify(bundle, source_evidence_manifest_path=source / "manifest.json")


@pytest.mark.parametrize("kind", ["file", "directory", "ancestor"])
def test_symlink_and_directory_reparse_capability(bundle, tmp_path, kind):
    leaf = bundle / "dossiers/player-core.json"
    target = tmp_path / "link-target"
    if kind == "file":
        shutil.copy2(leaf, target)
        leaf.unlink()
    elif kind == "directory":
        leaf = bundle / "dossiers"
        leaf.rename(target)
    else:
        leaf = bundle
        leaf.rename(target)
    try:
        os.symlink(target, leaf, target_is_directory=kind != "file")
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"links unavailable: {error}")
    with pytest.raises(RulesValidationError):
        verify(bundle)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_reparse_attribute_is_rejected_without_reading(bundle, monkeypatch, kind):
    leaf = bundle / ("manifest.json" if kind == "file" else "dossiers")
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path == leaf:
            class ReparseInfo:
                st_file_attributes = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                def __getattr__(self, key):
                    return getattr(info, key)
            return ReparseInfo()
        return info
    monkeypatch.setattr(Path, "lstat", lstat)
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError):
        verify(bundle)


def test_preflight_os_errors_are_stable_and_do_not_expose_host_paths(bundle, monkeypatch):
    def failed_scandir(path):
        raise OSError("host path and untrusted detail")
    monkeypatch.setattr(readiness.os, "scandir", failed_scandir)
    deny_payload_reads(monkeypatch)
    with pytest.raises(RulesValidationError) as caught:
        verify(bundle)
    assert str(caught.value) == "invalid_package_layout at $files"
