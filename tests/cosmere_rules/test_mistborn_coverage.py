"""Coverage and human-verification gates for compiled Mistborn mechanics."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from systems.cosmere.rules import mistborn_authoring as authoring
from systems.cosmere.rules.paths import registry_path


ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = registry_path().parent
MISTBORN_DIR = DATA_PATH / "mistborn"
COVERAGE_PATH = DATA_PATH / "mistborn_coverage.json"
pytestmark = pytest.mark.skipif(not registry_path().is_file(),
                                reason="private Cosmere rules data is not installed")


def load_bundle():
    # These contracts pin the original MB-00 transcription and its unresolved gaps.
    # test_mistborn_review.py separately checks the current reviewed release.
    return authoring.load_compiled_bundle(MISTBORN_DIR, COVERAGE_PATH, baseline=True)


def test_coverage_has_one_entry_per_record_and_no_silent_disposition():
    bundle = load_bundle()
    record_ids = {
        record["id"]
        for package in bundle["packages"]
        for record in package["records"]
    }
    coverage = bundle["coverage"]
    covered_ids = [entry["id"] for entry in coverage["records"]]
    assert len(covered_ids) == 918
    assert len(set(covered_ids)) == 918
    assert set(covered_ids) == record_ids
    assert coverage["summary"]["missing_citations"] == 0
    assert coverage["summary"]["missing_automation"] == 0
    assert coverage["summary"]["unresolved_exact_references"] == 0
    assert coverage["summary"]["unmatched_selectors"] == 0

    for entry in coverage["records"]:
        assert entry["citations"]
        assert entry["automation"] in {"calculated", "tracked", "reference", "gm_adjudicated"}
        assert entry["review_state"] in {"verified", "human_verification_required"}
        if entry["automation"] == "calculated":
            assert entry["test_refs"]


def test_all_human_verification_gaps_are_enumerated_and_never_claim_calculated():
    coverage = load_bundle()["coverage"]
    gaps = coverage["human_verification_gaps"]
    assert len(gaps) == 17
    assert {gap["source_audit"] for gap in gaps} == {
        "mistborn-content-audit", "mistborn-powers-audit"
    }
    assert all(gap["automation"] in {"tracked", "reference", "gm_adjudicated"} for gap in gaps)
    assert all(gap["review_state"] == "human_verification_required" for gap in gaps)

    by_id = {entry["id"]: entry for entry in coverage["records"]}
    affected = {
        record_id
        for gap in gaps
        for record_id in gap["affected_record_ids"]
    }
    assert affected
    for record_id in affected:
        assert by_id[record_id]["automation"] != "calculated"
        assert by_id[record_id]["review_state"] == "human_verification_required"


def test_adversary_action_transcription_gap_blocks_deterministic_automation():
    coverage = load_bundle()["coverage"]
    action_gap = next(
        gap for gap in coverage["human_verification_gaps"]
        if gap["id"] == "mistborn.gap.adversary-exact-action-transcription"
    )
    assert len(action_gap["affected_record_ids"]) == 188
    by_id = {entry["id"]: entry for entry in coverage["records"]}
    assert all(by_id[record_id]["automation"] in {"tracked", "reference", "gm_adjudicated"}
               for record_id in action_gap["affected_record_ids"])


def test_checked_in_data_contains_no_private_paths_or_source_payload_fields():
    files = sorted(MISTBORN_DIR.glob("*.json")) + [COVERAGE_PATH]
    assert files
    prohibited_keys = {
        "local_path", "absolute_path", "raw_text", "extracted_text",
        "extracted_prose", "pdf_text", "page_images", "watermark", "source_binary",
    }
    for path in files:
        raw = path.read_text(encoding="utf-8")
        assert "C:\\Users\\" not in raw
        assert "OneDrive" not in raw
        assert "%PDF-" not in raw
        data = json.loads(raw)
        stack = [data]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                assert prohibited_keys.isdisjoint(value)
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
            elif isinstance(value, str):
                assert not value.startswith(("C:\\", "C:/", "/Users/", "/home/"))


def test_bundle_reloads_to_identical_canonical_hashes():
    bundle = load_bundle()
    manifest = bundle["manifest"]
    assert manifest["bundle_hash"] == authoring.bundle_hash(bundle["packages"])
    for package in bundle["packages"]:
        assert package["package_hash"] == authoring.package_hash(package)
