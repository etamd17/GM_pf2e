"""Strict contracts for immutable PF2e class-review authoring data."""
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CURRENT_SNAPSHOT = (
    ROOT
    / "systems"
    / "pf2e"
    / "rules"
    / "evidence"
    / "archives-of-nethys"
    / "2026-10-07-player-build-v1"
    / "snapshot-manifest.json"
)


def overlay():
    return importlib.import_module(
        "systems.pf2e.rules.ingestion.class_review_overlay"
    )


def _pending_review():
    return {
        "status": "pending",
        "reviewer": None,
        "reviewed_at": None,
        "evidence_sha256": [],
    }


def _authoring():
    return {
        "manifest": {
            "schema_version": 1,
            "overlay_id": "pf2e-class-identities-2026-10-07.1",
            "created_at": "2026-10-07T12:34:56Z",
            "authority": "archives-of-nethys",
            "kind": "class",
            "snapshot_id": "pf2e-aon-2026-10-07-player-build-v1",
            "snapshot_manifest_sha256": "a" * 64,
            "base_category": "class",
            "activation": "none",
            "parent_overlay_id": None,
            "parent_overlay_hash": None,
        },
        "sources": [
            {
                "source_id": "pf2e.source.player-core-2",
                "title": "Player Core 2",
                "source_review": _pending_review(),
                "license_review": _pending_review(),
            },
            {
                "source_id": "pf2e.source.player-core",
                "title": "Player Core",
                "source_review": _pending_review(),
                "license_review": _pending_review(),
            },
        ],
        "records": [
            {
                "identity": {"page_family": "Classes.aspx", "numeric_id": 20},
                "rule_id": "pf2e.class.wizard",
                "source_id": "pf2e.source.player-core",
                "rules_review": _pending_review(),
            },
            {
                "identity": {"page_family": "Classes.aspx", "numeric_id": 10},
                "rule_id": "pf2e.class.alchemist",
                "source_id": "pf2e.source.player-core-2",
                "rules_review": _pending_review(),
            },
        ],
    }


def _snapshot_support():
    return importlib.import_module("tests.pf2e_rules.test_evidence_snapshot")


def _class_snapshot_tree(tmp_path):
    support = _snapshot_support()
    manifest_path, manifest = support._snapshot_tree(tmp_path)
    root = manifest_path.parent
    class_shard = next(
        shard for shard in manifest["shards"] if shard["category"] == "class"
    )
    census_path = root / class_shard["census"]["path"]
    census = json.loads(census_path.read_text(encoding="utf-8"))
    census["records"].append(
        support._record("Classes.aspx", 36, "Second Class", "class")
    )
    class_shard["census"]["sha256"] = support._write_json(census_path, census)

    ledger_path = root / class_shard["ledger"]["path"]
    class_shard["ledger"]["sha256"] = support._write_json(
        ledger_path, support._ledger("class", census["records"])
    )
    class_shard["expected_records"] = 2

    policy_path = root / manifest["scope_policy"]["path"]
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    class_policy = next(
        category for category in policy["categories"] if category["name"] == "class"
    )
    class_policy.update(observed_records=2, included_records=2)
    policy["total_records"] = 6
    manifest["scope_policy"]["sha256"] = support._write_json(policy_path, policy)

    scope_receipt_path = root / manifest["scope_capture"]["receipt"]["path"]
    scope_receipt = json.loads(scope_receipt_path.read_text(encoding="utf-8"))
    scope_receipt.update(
        reported_records=6,
        returned_records=6,
        categories=deepcopy(policy["categories"]),
    )
    manifest["scope_capture"]["receipt"]["sha256"] = support._write_json(
        scope_receipt_path, scope_receipt
    )

    manifest.update(
        total_index_records=6,
        included_records=3,
        class_roster=["Second Class", "Synthetic Class"],
    )
    support._rebind_receipt(
        manifest_path, manifest, "census_capture", "class", "census"
    )
    support._rebind_receipt(
        manifest_path, manifest, "ledger_enumeration", "class", "ledger"
    )
    support._write_json(manifest_path, manifest)
    return manifest_path, manifest


def _bound_authoring(manifest_path):
    document = _authoring()
    document["manifest"]["snapshot_manifest_sha256"] = hashlib.sha256(
        manifest_path.read_bytes()
    ).hexdigest()
    document["sources"] = [
        {
            "source_id": "pf2e.source.synthetic-core",
            "title": "Synthetic Core",
            "source_review": _pending_review(),
            "license_review": _pending_review(),
        }
    ]
    document["records"] = [
        {
            "identity": {"page_family": "Classes.aspx", "numeric_id": 36},
            "rule_id": "pf2e.class.second-class",
            "source_id": "pf2e.source.synthetic-core",
            "rules_review": _pending_review(),
        },
        {
            "identity": {"page_family": "Classes.aspx", "numeric_id": 35},
            "rule_id": "pf2e.class.synthetic-class",
            "source_id": "pf2e.source.synthetic-core",
            "rules_review": _pending_review(),
        },
    ]
    return document


def _rewrite_class_census(manifest_path, manifest, mutate):
    support = _snapshot_support()
    class_shard = next(
        shard for shard in manifest["shards"] if shard["category"] == "class"
    )
    census_path = manifest_path.parent / class_shard["census"]["path"]
    census = json.loads(census_path.read_text(encoding="utf-8"))
    mutate(census)
    for record in census["records"]:
        record["fingerprint"] = support.evidence_fingerprint(record)
    class_shard["census"]["sha256"] = support._write_json(census_path, census)
    support._rebind_receipt(
        manifest_path, manifest, "census_capture", "class", "census"
    )
    support._write_json(manifest_path, manifest)


def _snapshot_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*.json")
    }


def _current_snapshot_contract_documents():
    manifest_bytes = CURRENT_SNAPSHOT.read_bytes()
    snapshot = json.loads(manifest_bytes)
    class_shard = next(
        shard for shard in snapshot["shards"] if shard["category"] == "class"
    )
    census = json.loads(
        (CURRENT_SNAPSHOT.parent / class_shard["census"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    return snapshot, hashlib.sha256(manifest_bytes).hexdigest(), census


def _assert_error(document, code, path):
    with pytest.raises(ValueError) as error:
        overlay().normalize_class_review_authoring(document)

    assert error.value.code == code
    assert error.value.path == path


def test_authoring_normalizes_order_and_detaches_without_mutating_input():
    document = _authoring()
    before = deepcopy(document)

    normalized = overlay().normalize_class_review_authoring(document)

    assert document == before
    assert normalized is not document
    assert normalized["manifest"] is not document["manifest"]
    assert [item["source_id"] for item in normalized["sources"]] == [
        "pf2e.source.player-core",
        "pf2e.source.player-core-2",
    ]
    assert [item["identity"]["numeric_id"] for item in normalized["records"]] == [
        10,
        20,
    ]


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (lambda value: value.update(extra=True), "unknown_field", "$"),
        (
            lambda value: value["manifest"].update(extra=True),
            "unknown_field",
            "$.manifest",
        ),
        (
            lambda value: value["sources"][0].update(description="rules prose"),
            "unknown_field",
            "$.sources[0]",
        ),
        (
            lambda value: value["records"][0].update(name="Wizard"),
            "unknown_field",
            "$.records[0]",
        ),
        (
            lambda value: value["records"][0]["identity"].update(extra=True),
            "unknown_field",
            "$.records[0].identity",
        ),
        (
            lambda value: value["records"][0]["rules_review"].update(notes="ok"),
            "unknown_field",
            "$.records[0].rules_review",
        ),
        (
            lambda value: value["manifest"].pop("activation"),
            "missing_field",
            "$.manifest",
        ),
    ],
)
def test_authoring_rejects_fields_outside_the_versioned_schema(mutation, code, path):
    document = _authoring()
    mutation(document)

    _assert_error(document, code, path)


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda value: value["manifest"].update(schema_version=2),
            "unsupported_version",
            "$.manifest.schema_version",
        ),
        (
            lambda value: value["manifest"].update(overlay_id="Class Review"),
            "invalid_id",
            "$.manifest.overlay_id",
        ),
        (
            lambda value: value["manifest"].update(snapshot_id="snapshot"),
            "invalid_id",
            "$.manifest.snapshot_id",
        ),
        (
            lambda value: value["sources"][0].update(source_id="player-core"),
            "invalid_id",
            "$.sources[0].source_id",
        ),
        (
            lambda value: value["records"][0].update(
                rule_id="pf2e.spell.wizard"
            ),
            "invalid_id",
            "$.records[0].rule_id",
        ),
        (
            lambda value: value["records"][0]["identity"].update(
                page_family="Feats.aspx"
            ),
            "invalid_value",
            "$.records[0].identity.page_family",
        ),
        (
            lambda value: value["records"][0]["identity"].update(numeric_id=True),
            "invalid_type",
            "$.records[0].identity.numeric_id",
        ),
    ],
)
def test_authoring_rejects_invalid_version_and_identifiers(mutation, code, path):
    document = _authoring()
    mutation(document)

    _assert_error(document, code, path)


def test_authoring_rejects_activation_other_than_none():
    document = _authoring()
    document["manifest"]["activation"] = "runtime"

    _assert_error(document, "invalid_value", "$.manifest.activation")


@pytest.mark.parametrize(
    ("field", "bad_value", "path"),
    [
        (
            "snapshot_manifest_sha256",
            "A" * 64,
            "$.manifest.snapshot_manifest_sha256",
        ),
        (
            "snapshot_manifest_sha256",
            "a" * 63,
            "$.manifest.snapshot_manifest_sha256",
        ),
    ],
)
def test_authoring_rejects_noncanonical_manifest_hashes(field, bad_value, path):
    document = _authoring()
    document["manifest"][field] = bad_value

    _assert_error(document, "invalid_value", path)


@pytest.mark.parametrize(
    ("target", "bad_timestamp", "path"),
    [
        ("manifest", "2026-10-07T12:34:56+00:00", "$.manifest.created_at"),
        ("manifest", "2026-02-30T12:34:56Z", "$.manifest.created_at"),
        ("review", "2026-10-07T12:34:56+00:00", "$.records[0].rules_review.reviewed_at"),
    ],
)
def test_authoring_requires_exact_utc_timestamps(target, bad_timestamp, path):
    document = _authoring()
    if target == "manifest":
        document["manifest"]["created_at"] = bad_timestamp
    else:
        document["records"][0]["rules_review"] = {
            "status": "approved",
            "reviewer": "Rules reviewer",
            "reviewed_at": bad_timestamp,
            "evidence_sha256": ["b" * 64],
        }

    _assert_error(document, "invalid_date", path)


@pytest.mark.parametrize(
    ("parent_overlay_id", "parent_overlay_hash"),
    [
        ("pf2e-class-identities-2026-10-06.1", None),
        (None, "b" * 64),
        ("pf2e-class-identities-2026-10-06.1", "b" * 64),
    ],
)
def test_schema_v1_rejects_parent_overlay_claims(parent_overlay_id, parent_overlay_hash):
    document = _authoring()
    document["manifest"]["parent_overlay_id"] = parent_overlay_id
    document["manifest"]["parent_overlay_hash"] = parent_overlay_hash

    _assert_error(document, "unsupported_composition", "$.manifest")


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda value: value["sources"].append(deepcopy(value["sources"][0])),
            "duplicate_id",
            "$.sources[2].source_id",
        ),
        (
            lambda value: value["records"].append(deepcopy(value["records"][0])),
            "duplicate_identity",
            "$.records[2].identity",
        ),
        (
            lambda value: value["records"].append(
                {
                    **deepcopy(value["records"][0]),
                    "identity": {"page_family": "Classes.aspx", "numeric_id": 99},
                }
            ),
            "duplicate_id",
            "$.records[2].rule_id",
        ),
    ],
)
def test_authoring_rejects_duplicate_source_identity_and_rule_ids(mutation, code, path):
    document = _authoring()
    mutation(document)

    _assert_error(document, code, path)


def test_authoring_rejects_record_source_that_is_not_declared():
    document = _authoring()
    document["records"][0]["source_id"] = "pf2e.source.missing-book"

    _assert_error(document, "dangling_reference", "$.records[0].source_id")


def test_authoring_rejects_declared_source_that_no_record_uses():
    document = _authoring()
    document["records"][1]["source_id"] = "pf2e.source.player-core"

    _assert_error(document, "unused_source", "$.sources[0].source_id")


@pytest.mark.parametrize(
    ("target", "path"),
    [
        ("source", "$.sources[0].source_id"),
        ("record", "$.records[0].source_id"),
    ],
)
def test_authoring_rejects_errata_ids_in_source_slots(target, path):
    document = _authoring()
    if target == "source":
        document["sources"][0]["source_id"] = "pf2e.errata.player-core-2"
    else:
        document["records"][0]["source_id"] = "pf2e.errata.player-core"

    _assert_error(document, "invalid_id", path)


@pytest.mark.parametrize(
    ("collection", "size", "path"),
    [
        ("sources", 0, "$.sources"),
        ("sources", 9, "$.sources"),
        ("records", 0, "$.records"),
        ("records", 30, "$.records"),
    ],
)
def test_authoring_enforces_bounded_nonempty_source_and_record_counts(
    collection, size, path
):
    document = _authoring()
    if collection == "sources":
        document["sources"] = [
            {
                "source_id": f"pf2e.source.synthetic-{index}",
                "title": f"Synthetic Source {index}",
                "source_review": _pending_review(),
                "license_review": _pending_review(),
            }
            for index in range(1, size + 1)
        ]
    else:
        document["records"] = [
            {
                "identity": {
                    "page_family": "Classes.aspx",
                    "numeric_id": index,
                },
                "rule_id": f"pf2e.class.synthetic-{index}",
                "source_id": "pf2e.source.player-core",
                "rules_review": _pending_review(),
            }
            for index in range(1, size + 1)
        ]

    _assert_error(document, "limit_exceeded", path)


def test_pending_reviews_require_empty_attribution_and_evidence():
    for field, value in (
        ("reviewer", "Rules reviewer"),
        ("reviewed_at", "2026-10-07T12:34:56Z"),
        ("evidence_sha256", ["b" * 64]),
    ):
        document = _authoring()
        document["records"][0]["rules_review"][field] = value

        _assert_error(document, "invalid_review", "$.records[0].rules_review")


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_completed_reviews_require_named_reviewer_timestamp_and_evidence(status):
    invalid_reviews = [
        {
            "status": status,
            "reviewer": None,
            "reviewed_at": "2026-10-07T12:34:56Z",
            "evidence_sha256": ["b" * 64],
        },
        {
            "status": status,
            "reviewer": "Rules reviewer",
            "reviewed_at": None,
            "evidence_sha256": ["b" * 64],
        },
        {
            "status": status,
            "reviewer": "Rules reviewer",
            "reviewed_at": "2026-10-07T12:34:56Z",
            "evidence_sha256": [],
        },
    ]
    for review in invalid_reviews:
        document = _authoring()
        document["records"][0]["rules_review"] = review

        _assert_error(document, "invalid_review", "$.records[0].rules_review")


@pytest.mark.parametrize("status", ["approved", "rejected"])
def test_completed_reviews_accept_and_sort_unique_evidence_hashes(status):
    document = _authoring()
    document["records"][0]["rules_review"] = {
        "status": status,
        "reviewer": "Rules reviewer",
        "reviewed_at": "2026-10-07T12:34:56Z",
        "evidence_sha256": ["c" * 64, "b" * 64],
    }

    normalized = overlay().normalize_class_review_authoring(document)

    review = next(
        record["rules_review"]
        for record in normalized["records"]
        if record["rule_id"] == "pf2e.class.wizard"
    )
    assert review["evidence_sha256"] == ["b" * 64, "c" * 64]


def test_review_rejects_invalid_or_duplicate_evidence_hashes():
    for evidence, code in ((["B" * 64], "invalid_id"), (["b" * 64] * 2, "duplicate_id")):
        document = _authoring()
        document["records"][0]["rules_review"] = {
            "status": "approved",
            "reviewer": "Rules reviewer",
            "reviewed_at": "2026-10-07T12:34:56Z",
            "evidence_sha256": evidence,
        }

        _assert_error(
            document,
            code,
            "$.records[0].rules_review.evidence_sha256"
            + ("[0]" if code == "invalid_id" else ""),
        )


def test_source_and_license_reviews_use_the_same_review_gate():
    for field in ("source_review", "license_review"):
        document = _authoring()
        document["sources"][0][field] = {
            "status": "rejected",
            "reviewer": "Source reviewer",
            "reviewed_at": "2026-10-07T12:34:56Z",
            "evidence_sha256": ["d" * 64],
        }

        normalized = overlay().normalize_class_review_authoring(document)

        source = next(
            item
            for item in normalized["sources"]
            if item["source_id"] == "pf2e.source.player-core-2"
        )
        assert source[field]["status"] == "rejected"


def test_repository_text_hash_is_identical_for_lf_and_crlf_checkouts():
    lf = b'{"name":"Fighter"}\n{"level":1}\n'
    crlf = lf.replace(b"\n", b"\r\n")
    expected = hashlib.sha256(lf).hexdigest()

    assert overlay().repository_text_sha256(lf) == expected
    assert overlay().repository_text_sha256(crlf) == expected


@pytest.mark.parametrize("data", [b"one\rtwo", b"one\r\ntwo\rthree"])
def test_repository_text_hash_rejects_lone_carriage_returns(data):
    with pytest.raises(ValueError) as error:
        overlay().repository_text_sha256(data, "$.local.content")

    assert error.value.code == "invalid_repository_text"
    assert error.value.path == "$.local.content"


def test_snapshot_resolution_binds_verified_evidence_without_mutation(tmp_path):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    before_document = deepcopy(document)
    before_snapshot = _snapshot_bytes(manifest_path.parent)

    resolved = overlay()._resolve_class_snapshot(
        document, manifest_path, fixture_mode=True
    )

    assert document == before_document
    assert _snapshot_bytes(manifest_path.parent) == before_snapshot
    assert resolved["manifest"] == before_document["manifest"]
    assert resolved["sources"] == before_document["sources"]
    assert resolved["counts"] == {
        "snapshot_included_records": 3,
        "base_class_records": 2,
        "base_pending_records": 2,
    }
    assert [record["identity"]["numeric_id"] for record in resolved["records"]] == [
        35,
        36,
    ]
    assert resolved["records"][0] == {
        "identity": {"page_family": "Classes.aspx", "numeric_id": 35},
        "name": "Synthetic Class",
        "canonical_url": "https://2e.aonprd.com/Classes.aspx?ID=35",
        "fingerprint": "db2aad7fe6f3870a3ecd56c2acbccce9af8db8feb93721eafa10f11a1332323a",
        "evidence_sha256": (
            "b5f2bee492272729cb2bebae8f4caf242e33db435afdfb8cefae5aecddae1a3c"
        ),
        "source_ref": {
            "title": "Synthetic Core",
            "locator": "Synthetic Core pg. 1",
        },
        "rule_id": "pf2e.class.synthetic-class",
        "source_id": "pf2e.source.synthetic-core",
        "rules_review": _pending_review(),
    }


def test_snapshot_resolution_is_deterministic_across_authoring_order(tmp_path):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    first = _bound_authoring(manifest_path)
    second = deepcopy(first)
    second["records"].reverse()

    assert overlay()._resolve_class_snapshot(
        first, manifest_path, fixture_mode=True
    ) == overlay()._resolve_class_snapshot(
        second, manifest_path, fixture_mode=True
    )


@pytest.mark.parametrize(
    ("field", "value", "code", "path"),
    [
        (
            "snapshot_manifest_sha256",
            "f" * 64,
            "hash_mismatch",
            "$.manifest.snapshot_manifest_sha256",
        ),
        (
            "snapshot_id",
            "pf2e-aon-2026-10-07-other-v1",
            "snapshot_mismatch",
            "$.manifest.snapshot_id",
        ),
    ],
)
def test_snapshot_resolution_rejects_wrong_manifest_binding(
    tmp_path, field, value, code, path
):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    document["manifest"][field] = value

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == code
    assert error.value.path == path


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda records: records[0]["identity"].update(numeric_id=37),
            "unexpected_identity",
            "$.records[1].identity",
        ),
        (
            lambda records: records.pop(0),
            "incomplete_inventory",
            "$.records",
        ),
        (
            lambda records: records.append(
                {
                    "identity": {"page_family": "Classes.aspx", "numeric_id": 99},
                    "rule_id": "pf2e.class.extra-class",
                    "source_id": "pf2e.source.synthetic-core",
                    "rules_review": _pending_review(),
                }
            ),
            "unexpected_identity",
            "$.records[2].identity",
        ),
    ],
)
def test_snapshot_resolution_requires_exact_authoring_identity_bijection(
    tmp_path, mutation, code, path
):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    mutation(document["records"])

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == code
    assert error.value.path == path


def test_snapshot_resolution_requires_authoring_source_title_mapping(tmp_path):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    document["sources"][0]["title"] = "Different Core"

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == "source_mismatch"
    assert error.value.path == "$.records[0].source_id"


def test_snapshot_resolution_requires_exactly_one_class_source_reference(tmp_path):
    manifest_path, manifest = _class_snapshot_tree(tmp_path)

    def add_second_source(census):
        census["records"][0]["source_refs"].append(
            {"title": "Other Book", "locator": "Other Book pg. 2"}
        )

    _rewrite_class_census(manifest_path, manifest, add_second_source)
    document = _bound_authoring(manifest_path)

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == "source_mismatch"
    assert error.value.path == "$.snapshot.class.census.records[0].source_refs"


@pytest.mark.parametrize("target", ["manifest", "evidence"])
def test_snapshot_resolution_rejects_manifest_and_evidence_tampering(
    tmp_path, target
):
    manifest_path, manifest = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    if target == "manifest":
        manifest_path.write_bytes(manifest_path.read_bytes() + b" ")
        expected_path = "$.manifest.snapshot_manifest_sha256"
    else:
        class_shard = next(
            shard for shard in manifest["shards"] if shard["category"] == "class"
        )
        census_path = manifest_path.parent / class_shard["census"]["path"]
        census_path.write_bytes(census_path.read_bytes() + b" ")
        expected_path = "$.shards[0].census.sha256"

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == "hash_mismatch"
    assert error.value.path == expected_path


def test_snapshot_resolution_requires_pending_unmapped_base_ledger(tmp_path):
    support = _snapshot_support()
    manifest_path, manifest = _class_snapshot_tree(tmp_path)
    class_shard = next(
        shard for shard in manifest["shards"] if shard["category"] == "class"
    )
    ledger_path = manifest_path.parent / class_shard["ledger"]["path"]
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["entries"][0].update(
        disposition="mapped",
        rule_id="pf2e.class.synthetic-class",
        reason=None,
        review={
            "status": "reviewed",
            "reviewer": "Synthetic reviewer",
            "reviewed_at": "2026-10-07T14:00:00Z",
        },
    )
    class_shard["ledger"]["sha256"] = support._write_json(ledger_path, ledger)
    support._rebind_receipt(
        manifest_path, manifest, "ledger_enumeration", "class", "ledger"
    )
    support._write_json(manifest_path, manifest)
    document = _bound_authoring(manifest_path)

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert error.value.code == "lifecycle_mismatch"
    assert error.value.path == "$.shards[0].ledger.entries[0]"


def test_snapshot_resolution_defaults_to_the_frozen_production_snapshot(tmp_path):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)

    with pytest.raises(ValueError) as error:
        overlay()._resolve_class_snapshot(document, manifest_path)

    assert error.value.code == "snapshot_mismatch"
    assert error.value.path == "$.manifest.snapshot_manifest_sha256"


def test_production_snapshot_contract_accepts_only_the_frozen_class_census():
    snapshot, manifest_sha256, census = _current_snapshot_contract_documents()

    overlay()._require_production_snapshot(snapshot, manifest_sha256, census)


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda snapshot, manifest_hash, census: snapshot.update(
                snapshot_id="pf2e-aon-2026-10-07-other-v1"
            ),
            "snapshot_mismatch",
            "$.manifest.snapshot_id",
        ),
        (
            lambda snapshot, manifest_hash, census: manifest_hash.update(
                value="f" * 64
            ),
            "snapshot_mismatch",
            "$.manifest.snapshot_manifest_sha256",
        ),
        (
            lambda snapshot, manifest_hash, census: census["records"].pop(),
            "count_mismatch",
            "$.snapshot.class",
        ),
        (
            lambda snapshot, manifest_hash, census: census["records"][0][
                "source_refs"
            ][0].update(title="Player Core"),
            "source_mismatch",
            "$.snapshot.class.sources",
        ),
    ],
)
def test_production_snapshot_contract_rejects_identity_and_source_drift(
    mutation, code, path
):
    snapshot, manifest_sha256, census = _current_snapshot_contract_documents()
    mutable_hash = {"value": manifest_sha256}
    mutation(snapshot, mutable_hash, census)

    with pytest.raises(ValueError) as error:
        overlay()._require_production_snapshot(
            snapshot, mutable_hash["value"], census
        )

    assert error.value.code == code
    assert error.value.path == path


def test_snapshot_resolution_verifies_the_initial_manifest_document_once(
    tmp_path, monkeypatch
):
    manifest_path, _ = _class_snapshot_tree(tmp_path)
    document = _bound_authoring(manifest_path)
    module = overlay()
    real_read_file = module.read_file
    manifest_reads = 0

    def read_once(path):
        nonlocal manifest_reads
        if Path(path).absolute() == manifest_path.absolute():
            manifest_reads += 1
            if manifest_reads > 1:
                raise AssertionError("snapshot manifest was re-read")
        return real_read_file(path)

    monkeypatch.setattr(module, "read_file", read_once)

    module._resolve_class_snapshot(document, manifest_path, fixture_mode=True)

    assert manifest_reads == 1


def test_snapshot_document_reader_reports_the_referenced_artifact_path(tmp_path):
    artifact = tmp_path / "class-census.json"
    artifact.mkdir()

    with pytest.raises(ValueError) as error:
        overlay()._read_snapshot_document(
            tmp_path,
            {"path": artifact.name, "sha256": "a" * 64},
            "$.snapshot.class.census",
        )

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$.snapshot.class.census.path"
