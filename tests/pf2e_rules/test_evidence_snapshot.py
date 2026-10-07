"""Collection-level integrity for independently captured AoN evidence shards."""
from copy import deepcopy
import importlib
import json
from pathlib import Path

import pytest

from systems.pf2e.rules.ingestion.evidence_inventory import evidence_fingerprint
from systems.pf2e.rules.ingestion.aon_capture import (
    build_category_query_v1 as build_category_query,
    build_scope_query_v1 as build_scope_query,
)
from systems.pf2e.rules.manifest import canonical_json, digest


def snapshot_module():
    return importlib.import_module("systems.pf2e.rules.ingestion.evidence_snapshot")


def _record(page_family: str, numeric_id: int, name: str, kind: str) -> dict:
    value = {
        "identity": {"page_family": page_family, "numeric_id": numeric_id},
        "canonical_url": f"https://2e.aonprd.com/{page_family}?ID={numeric_id}",
        "name": name,
        "kind": kind,
        "rules_era": "mixed",
        "source_refs": [{"title": "Synthetic Core", "locator": "Synthetic Core pg. 1"}],
        "evidence_sha256": digest(f"{page_family}:{numeric_id}".encode("ascii")),
        "fingerprint": "0" * 64,
    }
    value["fingerprint"] = evidence_fingerprint(value)
    return value


def _census(records: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "authority": "archives-of-nethys",
        "captured_at": "2026-10-07T12:00:00Z",
        "site_update_date": "2026-10-07",
        "site_update_url": "https://2e.aonprd.com/",
        "records": records,
    }


def _ledger(category: str, records: list[dict]) -> dict:
    return {
        "schema_version": 1,
        "inventory_id": f"pf2e-aon-2026-10-07-{category}",
        "authority": "archives-of-nethys",
        "census_captured_at": "2026-10-07T12:00:00Z",
        "created_at": "2026-10-07T13:00:00Z",
        "entries": [{
            "identity": deepcopy(record["identity"]),
            "disposition": "pending",
            "rule_id": None,
            "reason": "Awaiting Paizo source, rules, and license review.",
            "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
        } for record in records],
    }


def _write_json(path: Path, value: dict) -> str:
    data = canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return digest(data)


def _snapshot_tree(tmp_path: Path) -> tuple[Path, dict]:
    root = tmp_path / "snapshot"
    categories = [
        ("class", "class", [_record("Classes.aspx", 35, "Synthetic Class", "class")]),
        ("feat", "feat", [_record("Feats.aspx", 7, "Synthetic Feat", "feat")]),
    ]
    shards = []
    for category, kind, records in categories:
        census_path = Path("census") / f"{category}.json"
        ledger_path = Path("ledger") / f"{category}.json"
        census_hash = _write_json(root / census_path, _census(records))
        ledger_hash = _write_json(root / ledger_path, _ledger(category, records))
        shards.append({
            "category": category,
            "kind": kind,
            "inventory_id": f"pf2e-aon-2026-10-07-{category}",
            "expected_records": len(records),
            "page_families": sorted({record["identity"]["page_family"] for record in records}),
            "census": {"path": census_path.as_posix(), "sha256": census_hash},
            "ledger": {"path": ledger_path.as_posix(), "sha256": ledger_hash},
        })

    policy = {
        "schema_version": 1,
        "scope_id": "pf2e-player-build-current-v1",
        "authority": "archives-of-nethys",
        "resolved_index": "aon-20261007-063536",
        "site_update_date": "2026-10-07",
        "total_records": 5,
        "categories": [
            {"name": "class", "observed_records": 1, "included_records": 1,
             "deferred_records": 0, "excluded_records": 0,
             "reason": "Required for character creation and advancement."},
            {"name": "creature", "observed_records": 2, "included_records": 0,
             "deferred_records": 2, "excluded_records": 0,
             "reason": "Deferred to the GM encounter-content census."},
            {"name": "feat", "observed_records": 1, "included_records": 1,
             "deferred_records": 0, "excluded_records": 0,
             "reason": "Required for character creation and advancement."},
            {"name": "skill-general-action", "observed_records": 1,
             "included_records": 0, "deferred_records": 0, "excluded_records": 1,
             "reason": "Alias of canonical action records."},
        ],
    }
    policy_path = root / "scope-policy.json"
    policy_hash = _write_json(policy_path, policy)
    def receipt(mode, run_id, captured_at, artifact_key):
        artifacts = []
        for shard in shards:
            query_sha = digest(canonical_json(
                build_category_query(shard["category"], mode)
            ))
            response_sha = digest(f"{mode}-response:{shard['category']}".encode("ascii"))
            artifacts.append({
                "category": shard["category"],
                "path": shard[artifact_key]["path"],
                "sha256": shard[artifact_key]["sha256"],
                "reported_records": shard["expected_records"],
                "returned_records": shard["expected_records"],
                "query_sha256": query_sha,
                "response_sha256": response_sha,
            })
        query_set = [{"category": item["category"], "sha256": item["query_sha256"]}
                     for item in artifacts]
        result_set = [{"category": item["category"], "sha256": item["response_sha256"]}
                      for item in artifacts]
        return {
            "schema_version": 1,
            "mode": mode,
            "run_id": run_id,
            "captured_at": captured_at,
            "scope_id": policy["scope_id"],
            "authority": "archives-of-nethys",
            "site_update_date": "2026-10-07",
            "search_endpoint": "https://elasticsearch.aonprd.com/aon/_search",
            "resolved_index": policy["resolved_index"],
            "query_set_sha256": digest(canonical_json(query_set)),
            "result_set_sha256": digest(canonical_json(result_set)),
            "artifacts": artifacts,
        }

    census_receipt = receipt(
        "census", "aon-census-20261007t120000z", "2026-10-07T12:00:00Z", "census"
    )
    ledger_receipt = receipt(
        "ledger", "aon-ledger-20261007t130000z", "2026-10-07T13:00:00Z", "ledger"
    )
    census_receipt_hash = _write_json(root / "census-receipt.json", census_receipt)
    ledger_receipt_hash = _write_json(root / "ledger-receipt.json", ledger_receipt)
    scope_receipt = {
        "schema_version": 1,
        "run_id": "aon-scope-20261007t113000z",
        "captured_at": "2026-10-07T11:30:00Z",
        "scope_id": policy["scope_id"],
        "authority": "archives-of-nethys",
        "site_update_date": "2026-10-07",
        "search_endpoint": "https://elasticsearch.aonprd.com/aon/_search",
        "resolved_index": policy["resolved_index"],
        "query_sha256": digest(canonical_json(build_scope_query())),
        "response_sha256": digest(b"synthetic-scope-response"),
        "reported_records": 5,
        "returned_records": 5,
        "categories": deepcopy(policy["categories"]),
    }
    scope_receipt_hash = _write_json(root / "scope-receipt.json", scope_receipt)

    manifest = {
        "schema_version": 1,
        "snapshot_id": "pf2e-aon-2026-10-07-player-build-v1",
        "lifecycle": "initial-pending",
        "scope_id": policy["scope_id"],
        "authority": "archives-of-nethys",
        "site_update_date": "2026-10-07",
        "site_update_url": "https://2e.aonprd.com/",
        "search_endpoint": "https://elasticsearch.aonprd.com/aon/_search",
        "resolved_index": policy["resolved_index"],
        "scope_policy": {"path": "scope-policy.json", "sha256": policy_hash},
        "scope_capture": {
            "run_id": scope_receipt["run_id"],
            "captured_at": scope_receipt["captured_at"],
            "query_sha256": scope_receipt["query_sha256"],
            "result_sha256": scope_receipt["response_sha256"],
            "receipt": {"path": "scope-receipt.json", "sha256": scope_receipt_hash},
        },
        "census_capture": {
            "run_id": "aon-census-20261007t120000z",
            "captured_at": "2026-10-07T12:00:00Z",
            "query_sha256": census_receipt["query_set_sha256"],
            "result_sha256": census_receipt["result_set_sha256"],
            "receipt": {"path": "census-receipt.json", "sha256": census_receipt_hash},
        },
        "ledger_enumeration": {
            "run_id": "aon-ledger-20261007t130000z",
            "captured_at": "2026-10-07T13:00:00Z",
            "query_sha256": ledger_receipt["query_set_sha256"],
            "result_sha256": ledger_receipt["result_set_sha256"],
            "receipt": {"path": "ledger-receipt.json", "sha256": ledger_receipt_hash},
        },
        "total_index_records": 5,
        "included_records": 2,
        "deferred_records": 2,
        "excluded_records": 1,
        "class_roster": ["Synthetic Class"],
        "shards": shards,
    }
    manifest_path = root / "snapshot-manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path, manifest


def _rebind_receipt(manifest_path: Path, manifest: dict, label: str,
                    category: str, artifact_key: str) -> None:
    receipt_ref = manifest[label]["receipt"]
    receipt_path = manifest_path.parent / receipt_ref["path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    shard = next(item for item in manifest["shards"] if item["category"] == category)
    artifact = next(item for item in receipt["artifacts"] if item["category"] == category)
    artifact.update(
        path=shard[artifact_key]["path"],
        sha256=shard[artifact_key]["sha256"],
        reported_records=shard["expected_records"],
        returned_records=shard["expected_records"],
    )
    receipt_ref["sha256"] = _write_json(receipt_path, receipt)


def test_snapshot_verification_is_compact_deterministic_and_nonmutating(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    before = {path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
              for path in manifest_path.parent.rglob("*.json")}

    first = snapshot_module().verify_evidence_snapshot(manifest_path)
    second = snapshot_module().verify_evidence_snapshot(manifest_path)

    assert first == second == {
        "schema_version": 1,
        "snapshot_id": manifest["snapshot_id"],
        "scope_id": manifest["scope_id"],
        "resolved_index": manifest["resolved_index"],
        "site_update_date": "2026-10-07",
        "complete": True,
        "census_schema_version": 1,
        "categories": {"deferred": 1, "excluded": 1, "included": 2},
        "records": {"deferred": 2, "excluded": 1, "included": 2, "total": 5},
        "dispositions": {"excluded": 0, "mapped": 0, "pending": 2},
        "reviews": {"pending": 2, "reviewed": 0},
        "kinds": {"class": 1, "feat": 1},
        "page_families": {"Classes.aspx": 1, "Feats.aspx": 1},
        "class_count": 1,
        "shard_count": 2,
    }
    assert {path.relative_to(manifest_path.parent).as_posix(): path.read_bytes()
            for path in manifest_path.parent.rglob("*.json")} == before


def test_snapshot_accepts_v2_census_with_v1_pending_ledger(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    for shard in manifest["shards"]:
        census_path = manifest_path.parent / shard["census"]["path"]
        census = json.loads(census_path.read_text(encoding="utf-8"))
        census["schema_version"] = 2
        if shard["category"] == "class":
            census["records"][0]["rules_era"] = "unverified"
        for record in census["records"]:
            record["fingerprint"] = evidence_fingerprint(
                record, schema_version=2
            )
        shard["census"]["sha256"] = _write_json(census_path, census)
        _rebind_receipt(
            manifest_path, manifest, "census_capture", shard["category"], "census"
        )
    _write_json(manifest_path, manifest)

    summary = snapshot_module().verify_evidence_snapshot(manifest_path)

    assert summary["complete"] is True
    assert summary["census_schema_version"] == 2
    assert summary["records"]["included"] == 2


def test_snapshot_rejects_mixed_census_schema_versions(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    census_path = manifest_path.parent / "census" / "class.json"
    census = json.loads(census_path.read_text(encoding="utf-8"))
    census["schema_version"] = 2
    census["records"][0]["rules_era"] = "unverified"
    census["records"][0]["fingerprint"] = evidence_fingerprint(
        census["records"][0], schema_version=2
    )
    manifest["shards"][0]["census"]["sha256"] = _write_json(census_path, census)
    _rebind_receipt(manifest_path, manifest, "census_capture", "class", "census")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.shards[1].census.schema_version"


def test_snapshot_binds_category_to_frozen_kind_contract(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    shard = manifest["shards"][0]
    census_path = manifest_path.parent / shard["census"]["path"]
    census = json.loads(census_path.read_text(encoding="utf-8"))
    census["records"][0]["kind"] = "feat"
    census["records"][0]["fingerprint"] = evidence_fingerprint(
        census["records"][0]
    )
    shard["kind"] = "feat"
    shard["census"]["sha256"] = _write_json(census_path, census)
    _rebind_receipt(manifest_path, manifest, "census_capture", "class", "census")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.shards[0].kind"


def test_snapshot_binds_category_to_frozen_page_family_contract(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    shard = manifest["shards"][0]
    census_path = manifest_path.parent / shard["census"]["path"]
    ledger_path = manifest_path.parent / shard["ledger"]["path"]
    census = json.loads(census_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    identity = {"page_family": "Feats.aspx", "numeric_id": 35}
    census["records"][0]["identity"] = identity
    census["records"][0]["canonical_url"] = (
        "https://2e.aonprd.com/Feats.aspx?ID=35"
    )
    census["records"][0]["fingerprint"] = evidence_fingerprint(
        census["records"][0]
    )
    ledger["entries"][0]["identity"] = identity
    shard["page_families"] = ["Feats.aspx"]
    shard["census"]["sha256"] = _write_json(census_path, census)
    shard["ledger"]["sha256"] = _write_json(ledger_path, ledger)
    _rebind_receipt(manifest_path, manifest, "census_capture", "class", "census")
    _rebind_receipt(manifest_path, manifest, "ledger_enumeration", "class", "ledger")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.shards[0].page_families"


def test_snapshot_rejects_tampered_shard_before_parsing(tmp_path):
    manifest_path, _ = _snapshot_tree(tmp_path)
    census = manifest_path.parent / "census" / "class.json"
    census.write_bytes(census.read_bytes() + b" ")

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "hash_mismatch"
    assert error.value.path == "$.shards[0].census.sha256"


def test_snapshot_rejects_nonindependent_capture_identity(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    receipt_path = manifest_path.parent / "ledger-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["run_id"] = manifest["census_capture"]["run_id"]
    receipt_hash = _write_json(receipt_path, receipt)
    manifest["ledger_enumeration"]["run_id"] = receipt["run_id"]
    manifest["ledger_enumeration"]["receipt"]["sha256"] = receipt_hash
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "capture_not_independent"
    assert error.value.path == "$.ledger_enumeration.run_id"


def test_snapshot_rejects_unaccounted_policy_totals(tmp_path):
    manifest_path, _ = _snapshot_tree(tmp_path)
    policy_path = manifest_path.parent / "scope-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["categories"][1]["observed_records"] = 1
    policy["categories"][1]["deferred_records"] = 1
    policy_hash = _write_json(policy_path, policy)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scope_policy"]["sha256"] = policy_hash
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "count_mismatch"
    assert error.value.path == "$.total_records"


def test_snapshot_rejects_cross_shard_identity_collision(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    feat_path = manifest_path.parent / "census" / "feat.json"
    feat = json.loads(feat_path.read_text(encoding="utf-8"))
    feat["records"] = [_record("Classes.aspx", 35, "Duplicate", "feat")]
    manifest["shards"][1]["page_families"] = ["Classes.aspx"]
    manifest["shards"][1]["census"]["sha256"] = _write_json(feat_path, feat)
    ledger_path = manifest_path.parent / "ledger" / "feat.json"
    manifest["shards"][1]["ledger"]["sha256"] = _write_json(
        ledger_path, _ledger("feat", feat["records"])
    )
    _rebind_receipt(manifest_path, manifest, "census_capture", "feat", "census")
    _rebind_receipt(manifest_path, manifest, "ledger_enumeration", "feat", "ledger")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.shards[1].page_families"


@pytest.mark.parametrize("bad_path", ["../outside.json", "/absolute.json", "census\\class.json"])
def test_snapshot_rejects_noncanonical_artifact_paths(tmp_path, bad_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    manifest["shards"][0]["census"]["path"] = bad_path
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$.shards[0].census.path"


@pytest.mark.parametrize("bad_path", ["C:class.json", "census/CON.json", "CENSUS/class.json"])
def test_snapshot_rejects_windows_alias_paths(tmp_path, bad_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    if bad_path == "CENSUS/class.json":
        manifest["shards"][0]["census"]["path"] = bad_path
        manifest["shards"][1]["census"]["path"] = "census/class.json"
        expected_path = "$.shards[1].census.path"
        expected_code = "duplicate_id"
    else:
        manifest["shards"][0]["census"]["path"] = bad_path
        expected_path = "$.shards[0].census.path"
        expected_code = "invalid_package_layout"
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == expected_code
    assert error.value.path == expected_path


def test_snapshot_rejects_tampered_capture_receipt(tmp_path):
    manifest_path, _ = _snapshot_tree(tmp_path)
    receipt = manifest_path.parent / "census-receipt.json"
    receipt.write_bytes(receipt.read_bytes() + b" ")

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "hash_mismatch"
    assert error.value.path == "$.census_capture.receipt.sha256"


def test_snapshot_rejects_receipt_that_does_not_bind_shard(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    receipt_path = manifest_path.parent / "census-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][0]["sha256"] = "f" * 64
    manifest["census_capture"]["receipt"]["sha256"] = _write_json(receipt_path, receipt)
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "capture_mismatch"
    assert error.value.path == "$.census_capture.receipt.artifacts[0].sha256"


def test_snapshot_recomputes_fixed_category_query_hash(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    receipt_path = manifest_path.parent / "census-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][0]["query_sha256"] = "f" * 64
    projection = [
        {"category": item["category"], "sha256": item["query_sha256"]}
        for item in receipt["artifacts"]
    ]
    receipt["query_set_sha256"] = digest(canonical_json(projection))
    manifest["census_capture"]["query_sha256"] = receipt["query_set_sha256"]
    manifest["census_capture"]["receipt"]["sha256"] = _write_json(
        receipt_path, receipt
    )
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "capture_mismatch"
    assert error.value.path == (
        "$.census_capture.receipt.artifacts[0].query_sha256"
    )


def test_snapshot_recomputes_fixed_scope_query_hash(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    receipt_path = manifest_path.parent / "scope-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["query_sha256"] = "e" * 64
    manifest["scope_capture"]["query_sha256"] = receipt["query_sha256"]
    manifest["scope_capture"]["receipt"]["sha256"] = _write_json(
        receipt_path, receipt
    )
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "capture_mismatch"
    assert error.value.path == "$.scope_capture.receipt.query_sha256"


def test_snapshot_rejects_receipt_with_partial_results(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    receipt_path = manifest_path.parent / "census-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][0]["returned_records"] = 0
    manifest["census_capture"]["receipt"]["sha256"] = _write_json(receipt_path, receipt)
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "count_mismatch"
    assert error.value.path == "$.census_capture.receipt.artifacts[0].returned_records"


def test_snapshot_rejects_scope_policy_not_bound_to_scope_receipt(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    policy_path = manifest_path.parent / "scope-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["categories"][1]["deferred_records"] = 1
    policy["categories"][1]["observed_records"] = 1
    policy["categories"][3]["excluded_records"] = 2
    policy["categories"][3]["observed_records"] = 2
    manifest["scope_policy"]["sha256"] = _write_json(policy_path, policy)
    manifest["deferred_records"] = 1
    manifest["excluded_records"] = 2
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "capture_mismatch"
    assert error.value.path == "$.scope_capture.receipt.categories"


def test_snapshot_rejects_unbound_ledger_inventory_id(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    ledger_path = manifest_path.parent / "ledger" / "feat.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["inventory_id"] = "pf2e-different-inventory"
    manifest["shards"][1]["ledger"]["sha256"] = _write_json(ledger_path, ledger)
    receipt_path = manifest_path.parent / "ledger-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][1]["sha256"] = manifest["shards"][1]["ledger"]["sha256"]
    manifest["ledger_enumeration"]["receipt"]["sha256"] = _write_json(
        receipt_path, receipt
    )
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.shards[1].ledger.inventory_id"


def test_initial_snapshot_rejects_nonpending_disposition(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    ledger_path = manifest_path.parent / "ledger" / "feat.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["entries"][0].update(
        disposition="mapped", rule_id="pf2e.feat.synthetic-feat", reason=None
    )
    manifest["shards"][1]["ledger"]["sha256"] = _write_json(ledger_path, ledger)
    receipt_path = manifest_path.parent / "ledger-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["artifacts"][1]["sha256"] = manifest["shards"][1]["ledger"]["sha256"]
    manifest["ledger_enumeration"]["receipt"]["sha256"] = _write_json(
        receipt_path, receipt
    )
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "lifecycle_mismatch"
    assert error.value.path == "$.shards[1].ledger.entries[0]"


def test_snapshot_rejects_incomplete_ledger(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    ledger_path = manifest_path.parent / "ledger" / "feat.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["entries"] = []
    manifest["shards"][1]["ledger"]["sha256"] = _write_json(ledger_path, ledger)
    _rebind_receipt(manifest_path, manifest, "ledger_enumeration", "feat", "ledger")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "incomplete_inventory"
    assert error.value.path == "$.shards[1].ledger"


def test_snapshot_rejects_class_roster_mismatch(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    manifest["class_roster"] = ["Different Class"]
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "roster_mismatch"
    assert error.value.path == "$.class_roster"


def test_snapshot_rejects_artifact_symlink(tmp_path):
    manifest_path, manifest = _snapshot_tree(tmp_path)
    original = manifest_path.parent / "census" / "class.json"
    linked = manifest_path.parent / "census" / "linked.json"
    try:
        linked.symlink_to(original)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")
    manifest["shards"][0]["census"]["path"] = "census/linked.json"
    _rebind_receipt(manifest_path, manifest, "census_capture", "class", "census")
    _write_json(manifest_path, manifest)

    with pytest.raises(ValueError) as error:
        snapshot_module().verify_evidence_snapshot(manifest_path)

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$.shards[0].census.path"
