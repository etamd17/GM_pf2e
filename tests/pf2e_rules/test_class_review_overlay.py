"""Strict contracts for immutable PF2e class-review authoring data."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
PRODUCTION_SOURCES = [
    ("pf2e.source.battlecry", "Battlecry!"),
    ("pf2e.source.dark-archive-remastered", "Dark Archive (Remastered)"),
    ("pf2e.source.guns-and-gears-remastered", "Guns & Gears (Remastered)"),
    ("pf2e.source.impossible-magic", "Impossible Magic"),
    ("pf2e.source.player-core", "Player Core"),
    ("pf2e.source.player-core-2", "Player Core 2"),
    ("pf2e.source.rage-of-elements", "Rage of Elements"),
    ("pf2e.source.war-of-immortals", "War of Immortals"),
]
PRODUCTION_CLASS_MAP = [
    (19, "pf2e.class.inventor", "pf2e.source.guns-and-gears-remastered"),
    (20, "pf2e.class.gunslinger", "pf2e.source.guns-and-gears-remastered"),
    (23, "pf2e.class.kineticist", "pf2e.source.rage-of-elements"),
    (32, "pf2e.class.bard", "pf2e.source.player-core"),
    (33, "pf2e.class.cleric", "pf2e.source.player-core"),
    (34, "pf2e.class.druid", "pf2e.source.player-core"),
    (35, "pf2e.class.fighter", "pf2e.source.player-core"),
    (36, "pf2e.class.ranger", "pf2e.source.player-core"),
    (37, "pf2e.class.rogue", "pf2e.source.player-core"),
    (38, "pf2e.class.witch", "pf2e.source.player-core"),
    (39, "pf2e.class.wizard", "pf2e.source.player-core"),
    (56, "pf2e.class.alchemist", "pf2e.source.player-core-2"),
    (57, "pf2e.class.barbarian", "pf2e.source.player-core-2"),
    (58, "pf2e.class.champion", "pf2e.source.player-core-2"),
    (59, "pf2e.class.investigator", "pf2e.source.player-core-2"),
    (60, "pf2e.class.monk", "pf2e.source.player-core-2"),
    (61, "pf2e.class.oracle", "pf2e.source.player-core-2"),
    (62, "pf2e.class.sorcerer", "pf2e.source.player-core-2"),
    (63, "pf2e.class.swashbuckler", "pf2e.source.player-core-2"),
    (64, "pf2e.class.animist", "pf2e.source.war-of-immortals"),
    (65, "pf2e.class.exemplar", "pf2e.source.war-of-immortals"),
    (66, "pf2e.class.commander", "pf2e.source.battlecry"),
    (67, "pf2e.class.guardian", "pf2e.source.battlecry"),
    (68, "pf2e.class.psychic", "pf2e.source.dark-archive-remastered"),
    (69, "pf2e.class.thaumaturge", "pf2e.source.dark-archive-remastered"),
    (74, "pf2e.class.magus", "pf2e.source.impossible-magic"),
    (75, "pf2e.class.necromancer", "pf2e.source.impossible-magic"),
    (76, "pf2e.class.runesmith", "pf2e.source.impossible-magic"),
    (77, "pf2e.class.summoner", "pf2e.source.impossible-magic"),
]


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


def _production_authoring():
    document = _authoring()
    document["manifest"]["snapshot_manifest_sha256"] = hashlib.sha256(
        CURRENT_SNAPSHOT.read_bytes()
    ).hexdigest()
    document["sources"] = [
        {
            "source_id": source_id,
            "title": title,
            "source_review": _pending_review(),
            "license_review": _pending_review(),
        }
        for source_id, title in PRODUCTION_SOURCES
    ]
    document["records"] = [
        {
            "identity": {"page_family": "Classes.aspx", "numeric_id": numeric_id},
            "rule_id": rule_id,
            "source_id": source_id,
            "rules_review": _pending_review(),
        }
        for numeric_id, rule_id, source_id in PRODUCTION_CLASS_MAP
    ]
    return document


def _write_local_class(
    corpus_root,
    local_key,
    *,
    foundry_id,
    name,
    publication="Pathfinder Synthetic Core",
    line_ending=b"\n",
):
    classes = corpus_root / "classes"
    classes.mkdir(parents=True, exist_ok=True)
    document = {
        "_id": foundry_id,
        "name": name,
        "system": {"publication": {"title": publication}},
        "type": "class",
    }
    data = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    data = data.replace(b"\n", line_ending)
    path = classes / f"{local_key}.json"
    path.write_bytes(data)
    return path


def _synthetic_resolved(records):
    return {
        "manifest": {"activation": "none"},
        "sources": [],
        "records": records,
        "counts": {
            "snapshot_included_records": len(records),
            "base_class_records": len(records),
            "base_pending_records": len(records),
        },
    }


def _resolved_record(
    numeric_id,
    name,
    rule_id,
    source_title="Synthetic Core",
):
    return {
        "identity": {"page_family": "Classes.aspx", "numeric_id": numeric_id},
        "name": name,
        "canonical_url": f"https://2e.aonprd.com/Classes.aspx?ID={numeric_id}",
        "fingerprint": "a" * 64,
        "evidence_sha256": "b" * 64,
        "source_ref": {"title": source_title, "locator": f"{source_title} pg. 1"},
        "rule_id": rule_id,
        "source_id": "pf2e.source.synthetic-core",
        "rules_review": _pending_review(),
    }


def _overlay_review(status, marker):
    if status == "pending":
        return _pending_review()
    return {
        "status": status,
        "reviewer": "Synthetic reviewer",
        "reviewed_at": "2026-10-07T15:00:00Z",
        "evidence_sha256": [marker * 64],
    }


def _overlay_source(source_id, source_status="pending", license_status="pending"):
    return {
        "source_id": source_id,
        "title": source_id.rsplit(".", 1)[-1],
        "source_review": _overlay_review(source_status, "c"),
        "license_review": _overlay_review(license_status, "d"),
    }


def _reconciled_record(
    numeric_id,
    name,
    rule_id,
    *,
    source_id="pf2e.source.synthetic-core",
    rules_status="pending",
):
    record = _resolved_record(numeric_id, name, rule_id)
    record["source_id"] = source_id
    record["rules_review"] = _overlay_review(rules_status, "e")
    record.update(
        disposition="mapped",
        local={
            "status": "missing",
            "relative_path": None,
            "foundry_id": None,
            "content_sha256": None,
            "local_key": None,
        },
        reconciliation="missing-local",
    )
    return record


def _synthetic_application(records, sources, *, snapshot_records=None):
    total = len(records) if snapshot_records is None else snapshot_records
    value = _synthetic_resolved(records)
    value["sources"] = sources
    value["counts"].update(
        {
            "snapshot_included_records": total,
            "base_dispositions": {
                "excluded": 0,
                "mapped": 0,
                "pending": total,
            },
            "base_reviews": {"pending": total, "reviewed": 0},
        }
    )
    return value


def _base_class_ledger(records):
    return {
        "schema_version": 1,
        "inventory_id": "pf2e-synthetic.class",
        "authority": "archives-of-nethys",
        "census_captured_at": "2026-10-07T12:00:00Z",
        "created_at": "2026-10-07T13:00:00Z",
        "entries": [
            {
                "identity": deepcopy(record["identity"]),
                "disposition": "pending",
                "rule_id": None,
                "reason": (
                    "Awaiting Paizo source, rules, and license review."
                ),
                "review": {
                    "status": "pending",
                    "reviewer": None,
                    "reviewed_at": None,
                },
            }
            for record in records
        ],
    }


def _current_class_ledger():
    manifest = json.loads(CURRENT_SNAPSHOT.read_text(encoding="utf-8"))
    class_shard = next(
        shard for shard in manifest["shards"] if shard["category"] == "class"
    )
    return json.loads(
        (CURRENT_SNAPSHOT.parent / class_shard["ledger"]["path"]).read_text(
            encoding="utf-8"
        )
    )


def _class_review_compile_fixture(tmp_path):
    manifest_path, _ = _class_snapshot_tree(tmp_path / "evidence")
    authoring = _bound_authoring(manifest_path)
    corpus_root = tmp_path / "corpus"
    _write_local_class(
        corpus_root,
        "second-class",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Second Class",
    )
    _write_local_class(
        corpus_root,
        "synthetic-class",
        foundry_id="BBBBBBBBBBBBBBBB",
        name="Synthetic Class",
    )
    return authoring, manifest_path, corpus_root


def _compile_fixture(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    files = overlay()._compile_class_review_overlay(
        authoring,
        manifest_path,
        corpus_root,
        fixture_mode=True,
    )
    return files, authoring, manifest_path, corpus_root


def _write_fixture(tmp_path, *, store_name="store"):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    target = overlay()._write_class_review_overlay(
        authoring,
        manifest_path,
        corpus_root,
        tmp_path / store_name,
        fixture_mode=True,
    )
    return target, authoring, manifest_path, corpus_root


def _test_canonical_json(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


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
        "base_dispositions": {"excluded": 0, "mapped": 0, "pending": 3},
        "base_reviews": {"pending": 3, "reviewed": 0},
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


def test_local_class_scanner_is_flat_deterministic_and_normalizes_crlf_hashes(
    tmp_path,
):
    lf_root = tmp_path / "lf"
    crlf_root = tmp_path / "crlf"
    _write_local_class(
        lf_root,
        "zeta",
        foundry_id="ZZZZZZZZZZZZZZZZ",
        name="Zeta",
    )
    _write_local_class(
        lf_root,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    _write_local_class(
        crlf_root,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
        line_ending=b"\r\n",
    )

    lf_records = overlay()._scan_local_classes(lf_root)
    crlf_records = overlay()._scan_local_classes(crlf_root)

    assert [record["local_key"] for record in lf_records] == ["alpha", "zeta"]
    assert lf_records[0] == {
        "local_key": "alpha",
        "relative_path": "classes/alpha.json",
        "foundry_id": "AAAAAAAAAAAAAAAA",
        "name": "Alpha",
        "publication_title": "Pathfinder Synthetic Core",
        "content_sha256": crlf_records[0]["content_sha256"],
    }


def test_local_class_scanner_hash_keeps_non_line_ending_bytes_significant(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = _write_local_class(
        first_root,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    second = _write_local_class(
        second_root,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    second.write_bytes(second.read_bytes().replace(b'"name":', b'"name" :', 1))

    first_hash = overlay()._scan_local_classes(first_root)[0]["content_sha256"]
    second_hash = overlay()._scan_local_classes(second_root)[0]["content_sha256"]

    assert first_hash != second_hash


@pytest.mark.parametrize(
    ("setup", "code", "path"),
    [
        (
            lambda root: None,
            "invalid_package_layout",
            "$.corpus.classes",
        ),
        (
            lambda root: (root / "classes").write_text("not a directory"),
            "invalid_package_layout",
            "$.corpus.classes",
        ),
        (
            lambda root: (
                (root / "classes").mkdir(),
                (root / "classes" / "nested").mkdir(),
            ),
            "invalid_package_layout",
            "$.corpus.classes.nested",
        ),
        (
            lambda root: (
                (root / "classes").mkdir(),
                (root / "classes" / "README.md").write_text("unexpected"),
            ),
            "invalid_path",
            "$.corpus.classes.README.md",
        ),
        (
            lambda root: _write_local_class(
                root,
                "Fighter",
                foundry_id="AAAAAAAAAAAAAAAA",
                name="Fighter",
            ),
            "invalid_path",
            "$.corpus.classes.Fighter.json",
        ),
        (
            lambda root: _write_local_class(
                root,
                "con",
                foundry_id="AAAAAAAAAAAAAAAA",
                name="Con",
            ),
            "invalid_path",
            "$.corpus.classes.con.json",
        ),
    ],
)
def test_local_class_scanner_rejects_missing_nonflat_and_unsafe_layouts(
    tmp_path, setup, code, path
):
    setup(tmp_path)

    with pytest.raises(ValueError) as error:
        overlay()._scan_local_classes(tmp_path)

    assert error.value.code == code
    assert error.value.path == path


def test_local_class_scanner_rejects_links(tmp_path):
    classes = tmp_path / "classes"
    classes.mkdir()
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    try:
        (classes / "linked.json").symlink_to(target)
    except OSError as error:
        pytest.skip(f"host cannot create a test symlink: {error}")

    with pytest.raises(ValueError) as caught:
        overlay()._scan_local_classes(tmp_path)

    assert caught.value.code == "invalid_package_layout"
    assert caught.value.path == "$.corpus.classes.linked.json"


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda document: document.update(type="feat"),
            "invalid_value",
            "$.corpus.classes.alpha.json.type",
        ),
        (
            lambda document: document.update(name=""),
            "invalid_value",
            "$.corpus.classes.alpha.json.name",
        ),
        (
            lambda document: document.update(_id="short"),
            "invalid_id",
            "$.corpus.classes.alpha.json._id",
        ),
        (
            lambda document: document["system"].pop("publication"),
            "missing_field",
            "$.corpus.classes.alpha.json.system.publication",
        ),
        (
            lambda document: document["system"]["publication"].update(title=""),
            "invalid_value",
            "$.corpus.classes.alpha.json.system.publication.title",
        ),
    ],
)
def test_local_class_scanner_validates_required_class_fields(
    tmp_path, mutation, code, path
):
    local = _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    document = json.loads(local.read_text(encoding="utf-8"))
    mutation(document)
    local.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError) as error:
        overlay()._scan_local_classes(tmp_path)

    assert error.value.code == code
    assert error.value.path == path


def test_local_class_scanner_rejects_duplicate_json_keys(tmp_path):
    classes = tmp_path / "classes"
    classes.mkdir()
    (classes / "alpha.json").write_bytes(
        b'{"_id":"AAAAAAAAAAAAAAAA","name":"Alpha","name":"Other",'
        b'"system":{"publication":{"title":"Pathfinder Synthetic Core"}},'
        b'"type":"class"}'
    )

    with pytest.raises(ValueError) as error:
        overlay()._scan_local_classes(tmp_path)

    assert error.value.code == "duplicate_key"
    assert error.value.path == "$.corpus.classes.alpha.json"


@pytest.mark.parametrize(
    ("duplicate", "path"),
    [
        ("foundry_id", "$.corpus.classes.beta.json._id"),
        ("name", "$.corpus.classes.beta.json.name"),
    ],
)
def test_local_class_scanner_rejects_duplicate_ids_and_names(
    tmp_path, duplicate, path
):
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    _write_local_class(
        tmp_path,
        "beta",
        foundry_id=(
            "AAAAAAAAAAAAAAAA" if duplicate == "foundry_id" else "BBBBBBBBBBBBBBBB"
        ),
        name="Alpha" if duplicate == "name" else "Beta",
    )

    with pytest.raises(ValueError) as error:
        overlay()._scan_local_classes(tmp_path)

    assert error.value.code == "duplicate_id"
    assert error.value.path == path


def test_local_class_scanner_enforces_file_count_and_aggregate_byte_budgets(
    tmp_path, monkeypatch
):
    for index in range(28):
        _write_local_class(
            tmp_path / "too-many",
            f"class-{index}",
            foundry_id=f"A{index:015d}",
            name=f"Class {index}",
        )

    with pytest.raises(ValueError) as count_error:
        overlay()._scan_local_classes(tmp_path / "too-many")

    assert count_error.value.code == "limit_exceeded"
    assert count_error.value.path == "$.corpus.classes"

    _write_local_class(
        tmp_path / "too-large",
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    monkeypatch.setattr(overlay(), "MAX_CLASS_CORPUS_BYTES", 1)

    with pytest.raises(ValueError) as byte_error:
        overlay()._scan_local_classes(tmp_path / "too-large")

    assert byte_error.value.code == "limit_exceeded"
    assert byte_error.value.path == "$.corpus.classes.alpha.json"


def test_local_class_scanner_streams_only_the_class_directory(tmp_path, monkeypatch):
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "ignored.json").write_text("not json")

    def unbounded_iterdir(_path):
        raise AssertionError("Path.iterdir must not materialize directory entries")

    monkeypatch.setattr(Path, "iterdir", unbounded_iterdir)

    assert len(overlay()._scan_local_classes(tmp_path)) == 1


def test_class_reconciliation_uses_explicit_rule_suffix_and_does_not_mutate_input(
    tmp_path,
):
    records = [
        _resolved_record(1, "Display Name", "pf2e.class.operator-key"),
        _resolved_record(
            2,
            "Drifted",
            "pf2e.class.drifted",
            source_title="Synthetic Core (Remastered)",
        ),
        _resolved_record(3, "Future", "pf2e.class.future"),
    ]
    resolved = _synthetic_resolved(records)
    before = deepcopy(resolved)
    operator_path = _write_local_class(
        tmp_path,
        "operator-key",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Display Name",
    )
    _write_local_class(
        tmp_path,
        "drifted",
        foundry_id="BBBBBBBBBBBBBBBB",
        name="Drifted",
        publication="Pathfinder Synthetic Core",
    )

    reconciled = overlay()._reconcile_class_corpus(
        resolved, tmp_path, fixture_mode=True
    )

    assert resolved == before
    assert reconciled["records"][0]["disposition"] == "mapped"
    assert reconciled["records"][0]["reconciliation"] == "aligned"
    assert reconciled["records"][0]["local"] == {
        "status": "present",
        "relative_path": "classes/operator-key.json",
        "foundry_id": "AAAAAAAAAAAAAAAA",
        "content_sha256": overlay().repository_text_sha256(
            operator_path.read_bytes()
        ),
        "local_key": "operator-key",
    }
    assert reconciled["records"][1]["reconciliation"] == "source-drift"
    assert reconciled["records"][2]["reconciliation"] == "missing-local"
    assert reconciled["records"][2]["local"] == {
        "status": "missing",
        "relative_path": None,
        "foundry_id": None,
        "content_sha256": None,
        "local_key": None,
    }
    assert reconciled["counts"] == {
        **before["counts"],
        "local_class_records": 2,
        "aligned_records": 1,
        "source_drift_records": 1,
        "missing_local_records": 1,
    }


def test_class_reconciliation_strips_only_one_exact_pathfinder_prefix(tmp_path):
    records = [
        _resolved_record(1, "Alpha", "pf2e.class.alpha"),
        _resolved_record(2, "Beta", "pf2e.class.beta"),
    ]
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    _write_local_class(
        tmp_path,
        "beta",
        foundry_id="BBBBBBBBBBBBBBBB",
        name="Beta",
        publication="Pathfinder Pathfinder Synthetic Core",
    )

    reconciled = overlay()._reconcile_class_corpus(
        _synthetic_resolved(records), tmp_path, fixture_mode=True
    )

    assert [record["reconciliation"] for record in reconciled["records"]] == [
        "aligned",
        "source-drift",
    ]


def test_class_reconciliation_rejects_local_name_mismatch(tmp_path):
    resolved = _synthetic_resolved(
        [_resolved_record(1, "Expected", "pf2e.class.alpha")]
    )
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Different",
    )

    with pytest.raises(ValueError) as error:
        overlay()._reconcile_class_corpus(resolved, tmp_path, fixture_mode=True)

    assert error.value.code == "name_mismatch"
    assert error.value.path == "$.corpus.classes.alpha.json.name"


def test_class_reconciliation_rejects_orphan_local_class(tmp_path):
    resolved = _synthetic_resolved(
        [_resolved_record(1, "Alpha", "pf2e.class.alpha")]
    )
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )
    _write_local_class(
        tmp_path,
        "orphan",
        foundry_id="BBBBBBBBBBBBBBBB",
        name="Orphan",
    )

    with pytest.raises(ValueError) as error:
        overlay()._reconcile_class_corpus(resolved, tmp_path, fixture_mode=True)

    assert error.value.code == "orphan_local_class"
    assert error.value.path == "$.corpus.classes.orphan.json"


def test_class_reconciliation_keeps_production_cardinality_strict(tmp_path):
    resolved = _synthetic_resolved(
        [_resolved_record(1, "Alpha", "pf2e.class.alpha")]
    )
    _write_local_class(
        tmp_path,
        "alpha",
        foundry_id="AAAAAAAAAAAAAAAA",
        name="Alpha",
    )

    with pytest.raises(ValueError) as error:
        overlay()._reconcile_class_corpus(resolved, tmp_path)

    assert error.value.code == "count_mismatch"
    assert error.value.path == "$.corpus.classes"


def test_class_reconciliation_rejects_count_preserving_identity_swap(tmp_path):
    corpus_root = tmp_path / "corpus"
    shutil.copytree(ROOT / "compendium_data" / "classes", corpus_root / "classes")
    gunslinger_path = corpus_root / "classes" / "gunslinger.json"
    fighter_path = corpus_root / "classes" / "fighter.json"
    gunslinger = json.loads(gunslinger_path.read_text(encoding="utf-8"))
    fighter = json.loads(fighter_path.read_text(encoding="utf-8"))
    gunslinger["system"]["publication"]["title"] = (
        "Pathfinder Guns & Gears (Remastered)"
    )
    fighter["system"]["publication"]["title"] = "Pathfinder Different Core"
    gunslinger_path.write_text(json.dumps(gunslinger), encoding="utf-8")
    fighter_path.write_text(json.dumps(fighter), encoding="utf-8")
    resolved = overlay()._resolve_class_snapshot(
        _production_authoring(), CURRENT_SNAPSHOT
    )

    with pytest.raises(ValueError) as error:
        overlay()._reconcile_class_corpus(resolved, corpus_root)

    assert error.value.code == "reconciliation_mismatch"
    assert error.value.path == "$.corpus.classes"


def test_current_class_corpus_reconciles_exact_frozen_production_inventory():
    module = overlay()
    resolved = module._resolve_class_snapshot(_production_authoring(), CURRENT_SNAPSHOT)

    reconciled = module._reconcile_class_corpus(resolved, ROOT / "compendium_data")

    by_reconciliation = Counter(
        record["reconciliation"] for record in reconciled["records"]
    )
    assert by_reconciliation == {
        "aligned": 21,
        "source-drift": 6,
        "missing-local": 2,
    }
    assert {
        record["name"]
        for record in reconciled["records"]
        if record["reconciliation"] == "source-drift"
    } == {
        "Gunslinger",
        "Inventor",
        "Magus",
        "Psychic",
        "Summoner",
        "Thaumaturge",
    }
    assert {
        record["name"]
        for record in reconciled["records"]
        if record["reconciliation"] == "missing-local"
    } == {"Necromancer", "Runesmith"}
    present = [
        record["local"]
        for record in reconciled["records"]
        if record["local"]["status"] == "present"
    ]
    assert len(present) == 27
    assert len({local["foundry_id"] for local in present}) == 27
    assert len({local["relative_path"] for local in present}) == 27
    assert len({local["content_sha256"] for local in present}) == 27
    assert all(
        len(local["content_sha256"]) == 64
        and set(local["content_sha256"]) <= set("0123456789abcdef")
        for local in present
    )
    assert len(module._scan_local_classes(ROOT / "compendium_data")) == 27


def test_overlay_application_is_detached_and_maps_only_effective_dispositions():
    records = [
        _reconciled_record(
            1,
            "Alpha",
            "pf2e.class.alpha",
            source_id="pf2e.source.alpha",
            rules_status="approved",
        ),
        _reconciled_record(
            2,
            "Beta",
            "pf2e.class.beta",
            source_id="pf2e.source.beta",
            rules_status="rejected",
        ),
    ]
    sources = [
        _overlay_source("pf2e.source.alpha", "pending", "approved"),
        _overlay_source("pf2e.source.beta", "rejected", "pending"),
    ]
    reconciled = _synthetic_application(records, sources, snapshot_records=5)
    base_ledger = _base_class_ledger(records)
    reconciled_before = deepcopy(reconciled)
    ledger_before = deepcopy(base_ledger)

    result = overlay()._apply_class_review_overlay(base_ledger, reconciled)

    assert base_ledger == ledger_before
    assert reconciled == reconciled_before
    assert result["effective_class_ledger"] is not base_ledger
    assert result["effective_class_ledger"]["entries"] == [
        {
            "identity": {"page_family": "Classes.aspx", "numeric_id": 1},
            "disposition": "mapped",
            "rule_id": "pf2e.class.alpha",
            "reason": None,
            "review": {
                "status": "pending",
                "reviewer": None,
                "reviewed_at": None,
            },
        },
        {
            "identity": {"page_family": "Classes.aspx", "numeric_id": 2},
            "disposition": "mapped",
            "rule_id": "pf2e.class.beta",
            "reason": None,
            "review": {
                "status": "pending",
                "reviewer": None,
                "reviewed_at": None,
            },
        },
    ]
    assert result["summary"] == {
        "activation": "none",
        "enabled_mechanics": 0,
        "effective_dispositions": {
            "excluded": 0,
            "mapped": 2,
            "pending": 3,
        },
        "base_reviews": {"pending": 5, "reviewed": 0},
        "overlay_gates": {
            "sources": 2,
            "licenses": 2,
            "rules": 2,
            "total": 6,
            "by_status": {"pending": 2, "approved": 2, "rejected": 2},
        },
    }


@pytest.mark.parametrize(
    ("mutation", "code", "path"),
    [
        (
            lambda ledger, reconciled: ledger["entries"].pop(),
            "incomplete_inventory",
            "$.records",
        ),
        (
            lambda ledger, reconciled: (
                reconciled["records"].pop(),
                reconciled["counts"].update(
                    base_class_records=1,
                    base_pending_records=1,
                ),
            ),
            "unexpected_identity",
            "$.base_class_ledger.entries[1].identity",
        ),
    ],
)
def test_overlay_application_requires_exact_identity_bijection(
    mutation, code, path
):
    records = [
        _reconciled_record(1, "Alpha", "pf2e.class.alpha"),
        _reconciled_record(2, "Beta", "pf2e.class.beta"),
    ]
    reconciled = _synthetic_application(
        records, [_overlay_source("pf2e.source.synthetic-core")]
    )
    base_ledger = _base_class_ledger(records)
    mutation(base_ledger, reconciled)

    with pytest.raises(ValueError) as error:
        overlay()._apply_class_review_overlay(base_ledger, reconciled)

    assert error.value.code == code
    assert error.value.path == path


def test_overlay_application_rejects_malformed_reconciled_identity():
    records = [_reconciled_record(1, "Alpha", "pf2e.class.alpha")]
    reconciled = _synthetic_application(
        records, [_overlay_source("pf2e.source.synthetic-core")]
    )
    base_ledger = _base_class_ledger(records)
    reconciled["records"][0]["identity"] = None

    with pytest.raises(ValueError) as error:
        overlay()._apply_class_review_overlay(base_ledger, reconciled)

    assert error.value.code == "invalid_type"
    assert error.value.path == "$.records[0].identity"


@pytest.mark.parametrize("lifecycle", ["mapped", "reviewed"])
def test_overlay_application_requires_original_pending_base_lifecycle(lifecycle):
    records = [_reconciled_record(1, "Alpha", "pf2e.class.alpha")]
    reconciled = _synthetic_application(
        records, [_overlay_source("pf2e.source.synthetic-core")]
    )
    base_ledger = _base_class_ledger(records)
    if lifecycle == "mapped":
        base_ledger["entries"][0].update(
            disposition="mapped",
            rule_id="pf2e.class.alpha",
            reason=None,
        )
    else:
        base_ledger["entries"][0]["review"] = {
            "status": "reviewed",
            "reviewer": "Synthetic reviewer",
            "reviewed_at": "2026-10-07T15:00:00Z",
        }

    with pytest.raises(ValueError) as error:
        overlay()._apply_class_review_overlay(base_ledger, reconciled)

    assert error.value.code == "lifecycle_mismatch"
    assert error.value.path == "$.base_class_ledger.entries[0]"


def test_overlay_application_rejects_any_activation_mode():
    records = [_reconciled_record(1, "Alpha", "pf2e.class.alpha")]
    reconciled = _synthetic_application(
        records, [_overlay_source("pf2e.source.synthetic-core")]
    )
    reconciled["manifest"]["activation"] = "rules"

    with pytest.raises(ValueError) as error:
        overlay()._apply_class_review_overlay(
            _base_class_ledger(records), reconciled
        )

    assert error.value.code == "invalid_value"
    assert error.value.path == "$.manifest.activation"


def test_current_overlay_application_preserves_snapshot_bytes_and_zero_activation():
    snapshot_root = CURRENT_SNAPSHOT.parent
    before = _snapshot_bytes(snapshot_root)
    module = overlay()
    resolved = module._resolve_class_snapshot(_production_authoring(), CURRENT_SNAPSHOT)
    reconciled = module._reconcile_class_corpus(resolved, ROOT / "compendium_data")

    result = module._apply_class_review_overlay(
        _current_class_ledger(), reconciled
    )

    assert len(before) == 137
    assert _snapshot_bytes(snapshot_root) == before
    assert resolved["counts"]["base_dispositions"] == {
        "excluded": 0,
        "mapped": 0,
        "pending": 18_522,
    }
    assert resolved["counts"]["base_reviews"] == {
        "pending": 18_522,
        "reviewed": 0,
    }
    assert result["summary"] == {
        "activation": "none",
        "enabled_mechanics": 0,
        "effective_dispositions": {
            "excluded": 0,
            "mapped": 29,
            "pending": 18_493,
        },
        "base_reviews": {"pending": 18_522, "reviewed": 0},
        "overlay_gates": {
            "sources": 8,
            "licenses": 8,
            "rules": 29,
            "total": 45,
            "by_status": {"pending": 45, "approved": 0, "rejected": 0},
        },
    }
    effective_entries = result["effective_class_ledger"]["entries"]
    assert len(effective_entries) == 29
    assert {entry["disposition"] for entry in effective_entries} == {"mapped"}
    assert {entry["review"]["status"] for entry in effective_entries} == {
        "pending"
    }


def test_class_review_compiler_is_deterministic_canonical_and_nonmutating(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    authoring_before = deepcopy(authoring)
    snapshot_before = _snapshot_bytes(manifest_path.parent)
    corpus_before = _snapshot_bytes(corpus_root)

    first = overlay()._compile_class_review_overlay(
        authoring, manifest_path, corpus_root, fixture_mode=True
    )
    reordered = deepcopy(authoring)
    reordered["records"].reverse()
    second = overlay()._compile_class_review_overlay(
        reordered, manifest_path, corpus_root, fixture_mode=True
    )

    assert first == second
    assert authoring == authoring_before
    assert _snapshot_bytes(manifest_path.parent) == snapshot_before
    assert _snapshot_bytes(corpus_root) == corpus_before
    assert set(first) == {
        "authoring.json",
        "sources.json",
        "records.json",
        "manifest.json",
    }
    for data in first.values():
        assert data.endswith(b"\n")
        assert b"\r" not in data
        assert _test_canonical_json(json.loads(data)) == data


def test_class_review_compiler_emits_exact_public_schemas_and_counts(tmp_path):
    files, _authoring, _manifest_path, _corpus_root = _compile_fixture(tmp_path)
    authoring = json.loads(files["authoring.json"])
    sources = json.loads(files["sources.json"])
    records = json.loads(files["records.json"])
    manifest = json.loads(files["manifest.json"])

    assert set(authoring) == {"manifest", "sources", "records"}
    assert sources == authoring["sources"]
    assert set(sources[0]) == {
        "source_id",
        "title",
        "source_review",
        "license_review",
    }
    assert [record["identity"]["numeric_id"] for record in records] == [35, 36]
    assert all(
        set(record)
        == {
            "identity",
            "name",
            "canonical_url",
            "fingerprint",
            "evidence_sha256",
            "disposition",
            "rule_id",
            "source_id",
            "source_ref",
            "local",
            "reconciliation",
            "rules_review",
        }
        for record in records
    )
    assert all(
        set(record["local"])
        == {
            "status",
            "relative_path",
            "foundry_id",
            "content_sha256",
            "local_key",
        }
        for record in records
    )
    assert not any("publication_title" in record["local"] for record in records)
    assert set(manifest) == {
        "schema_version",
        "overlay_id",
        "created_at",
        "authority",
        "kind",
        "snapshot_id",
        "snapshot_manifest_sha256",
        "base_category",
        "activation",
        "parent_overlay_id",
        "parent_overlay_hash",
        "compiler_version",
        "inputs",
        "outputs",
        "counts",
        "overlay_hash",
    }
    assert manifest["compiler_version"] == "pf2e-class-review-1"
    assert manifest["counts"] == {
        "sources": 1,
        "records": 2,
        "local_records": 2,
        "reconciliation": {
            "aligned": 2,
            "source-drift": 0,
            "missing-local": 0,
        },
        "effective_dispositions": {
            "excluded": 0,
            "mapped": 2,
            "pending": 1,
        },
        "base_reviews": {"pending": 3, "reviewed": 0},
        "overlay_gates": {
            "sources": 1,
            "licenses": 1,
            "rules": 2,
            "total": 4,
            "by_status": {"pending": 4, "approved": 0, "rejected": 0},
        },
        "enabled_mechanics": 0,
    }


def test_class_review_manifest_hash_scopes_exclude_only_the_manifest_itself(tmp_path):
    files, _authoring, _manifest_path, _corpus_root = _compile_fixture(tmp_path)
    manifest = json.loads(files["manifest.json"])

    assert manifest["inputs"] == {
        "authoring.json": hashlib.sha256(files["authoring.json"]).hexdigest()
    }
    assert manifest["outputs"] == {
        name: hashlib.sha256(files[name]).hexdigest()
        for name in ("sources.json", "records.json")
    }
    without_hash = deepcopy(manifest)
    overlay_hash = without_hash.pop("overlay_hash")
    assert overlay_hash == hashlib.sha256(
        _test_canonical_json(without_hash)
    ).hexdigest()
    assert "manifest.json" not in manifest["inputs"] | manifest["outputs"]


@pytest.mark.parametrize(
    ("gate", "path"),
    [
        ("source", "$.sources[0].source_review"),
        ("license", "$.sources[0].license_review"),
        ("rules", "$.records[0].rules_review"),
    ],
)
def test_class_review_compiler_requires_every_v1_gate_pending(tmp_path, gate, path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    review = _overlay_review("approved", "f")
    if gate == "rules":
        next(
            record
            for record in authoring["records"]
            if record["identity"]["numeric_id"] == 35
        )["rules_review"] = review
    else:
        authoring["sources"][0][f"{gate}_review"] = review

    with pytest.raises(ValueError) as error:
        overlay()._compile_class_review_overlay(
            authoring, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "lifecycle_mismatch"
    assert error.value.path == path


def test_class_review_writer_publishes_exact_create_only_package(tmp_path):
    target, _authoring, _manifest_path, _corpus_root = _write_fixture(tmp_path)

    assert target.name == "pf2e-class-identities-2026-10-07.1"
    assert {path.name for path in target.iterdir()} == {
        "authoring.json",
        "sources.json",
        "records.json",
        "manifest.json",
    }
    assert not list(target.parent.glob(".*.lock"))
    assert not list(target.parent.glob(".*.tmp"))


def test_class_review_writer_never_overwrites_existing_target(tmp_path):
    target, authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    before = {path.name: path.read_bytes() for path in target.iterdir()}

    with pytest.raises(ValueError) as error:
        overlay()._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            target.parent,
            fixture_mode=True,
        )

    assert error.value.code == "overlay_exists"
    assert error.value.path == "$.manifest.overlay_id"
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_class_review_writer_preserves_stale_lock_for_operator_review(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    lock = store / ".pf2e-class-identities-2026-10-07.1.lock"
    lock.write_bytes(b"stale operator evidence")

    with pytest.raises(ValueError) as error:
        overlay()._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert error.value.code == "publication_locked"
    assert error.value.path == "$.manifest.overlay_id"
    assert lock.read_bytes() == b"stale operator evidence"


def test_class_review_writer_has_one_concurrent_winner(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"

    def publish():
        try:
            return overlay()._write_class_review_overlay(
                authoring,
                manifest_path,
                corpus_root,
                store,
                fixture_mode=True,
            )
        except ValueError as error:
            assert error.code in {"overlay_exists", "publication_locked"}
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: publish(), range(2)))

    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize("relationship", ["store-under-snapshot", "store-over-snapshot", "store-under-corpus", "store-over-corpus", "snapshot-over-corpus"])
def test_class_review_writer_requires_pairwise_disjoint_trees(tmp_path, relationship):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    if relationship == "store-under-snapshot":
        store = manifest_path.parent / "store"
    elif relationship == "store-over-snapshot":
        store = manifest_path.parent.parent
    elif relationship == "store-under-corpus":
        store = corpus_root / "store"
    elif relationship == "store-over-corpus":
        store = corpus_root.parent
    else:
        corpus_root = manifest_path.parent

    with pytest.raises(ValueError) as error:
        overlay()._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert error.value.code == "path_overlap"
    assert error.value.path == "$.paths"


def test_class_review_writer_treats_broken_link_target_as_existing(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    target = store / authoring["manifest"]["overlay_id"]
    try:
        target.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")

    with pytest.raises(ValueError) as error:
        overlay()._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert error.value.code == "overlay_exists"
    assert os.path.lexists(target)


@pytest.mark.parametrize(
    "failure_stage", ["open", "write", "flush", "fsync", "rename"]
)
def test_class_review_writer_cleans_its_partial_state_after_io_failure(
    tmp_path, monkeypatch, failure_stage
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    store.mkdir()
    unrelated = store / ".unrelated.tmp"
    unrelated.mkdir()
    marker = unrelated / "operator-evidence"
    marker.write_bytes(b"preserve")
    module = overlay()

    def fail(*_args, **_kwargs):
        raise OSError(f"injected {failure_stage} failure")

    monkeypatch.setattr(
        module,
        {
            "open": "_open_overlay_file",
            "write": "_write_overlay_bytes",
            "flush": "_flush_overlay_file",
            "fsync": "_fsync_overlay_file",
            "rename": "_rename_overlay_directory",
        }[failure_stage],
        fail,
    )

    with pytest.raises(OSError, match=f"injected {failure_stage} failure"):
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    overlay_id = authoring["manifest"]["overlay_id"]
    assert not os.path.lexists(store / overlay_id)
    assert not os.path.lexists(store / f".{overlay_id}.lock")
    assert not list(store.glob(f".{overlay_id}.*.tmp"))
    assert marker.read_bytes() == b"preserve"


def test_class_review_writer_rejects_short_write_and_cleans_partial_state(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    module = overlay()

    def short_write(handle, data):
        return handle.write(data[:-1])

    monkeypatch.setattr(module, "_write_overlay_bytes", short_write)

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    overlay_id = authoring["manifest"]["overlay_id"]
    assert error.value.code == "io_error"
    assert not os.path.lexists(store / overlay_id)
    assert not os.path.lexists(store / f".{overlay_id}.lock")
    assert not list(store.glob(f".{overlay_id}.*.tmp"))


def test_class_review_writer_preserves_replacement_staging_object(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    replacement = {}
    module = overlay()

    def replace_then_fail(staging, _target):
        moved = staging.with_name(staging.name + ".owned-moved")
        staging.rename(moved)
        staging.mkdir()
        marker = staging / "replacement-marker"
        marker.write_bytes(b"not owned")
        replacement.update(path=staging, marker=marker, moved=moved)
        raise OSError("injected replacement race")

    monkeypatch.setattr(module, "_rename_overlay_directory", replace_then_fail)

    with pytest.raises(OSError, match="injected replacement race"):
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert replacement["marker"].read_bytes() == b"not owned"
    assert replacement["moved"].is_dir()


def test_class_review_writer_never_publishes_staging_replaced_after_readback(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    replacement = {}
    module = overlay()
    real_publish = module._rename_overlay_directory

    def replace_then_publish(staging, target):
        moved = staging.with_name(staging.name + ".validated-moved")
        staging.rename(moved)
        staging.mkdir()
        marker = staging / "unverified-marker"
        marker.write_bytes(b"not validated")
        replacement.update(marker=marker, moved=moved)
        real_publish(staging, target)

    monkeypatch.setattr(module, "_rename_overlay_directory", replace_then_publish)

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    target = store / authoring["manifest"]["overlay_id"]
    assert error.value.code == "integrity_mismatch"
    assert error.value.path == "$.paths.staging"
    assert not os.path.lexists(target)
    assert replacement["moved"].is_dir()
    assert any(
        path.read_bytes() == b"not validated"
        for path in store.rglob("unverified-marker")
    )


def test_class_review_writer_reports_rejected_publication_quarantine_failure(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    module = overlay()
    real_publish = module._rename_overlay_directory
    real_no_replace = module._rename_path_no_replace

    def replace_then_publish(staging, target):
        staging.rename(staging.with_name(staging.name + ".validated-moved"))
        staging.mkdir()
        (staging / "unverified-marker").write_bytes(b"not validated")
        real_publish(staging, target)

    def fail_rejected_quarantine(source, target):
        if target.name.startswith(".rejected-publication-"):
            raise OSError("injected quarantine failure")
        return real_no_replace(source, target)

    monkeypatch.setattr(module, "_rename_overlay_directory", replace_then_publish)
    monkeypatch.setattr(module, "_rename_path_no_replace", fail_rejected_quarantine)

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    target = store / authoring["manifest"]["overlay_id"]
    assert error.value.code == "quarantine_failed"
    assert error.value.path == "$.manifest.overlay_id"
    assert (target / "unverified-marker").read_bytes() == b"not validated"


def test_owned_lock_cleanup_preserves_replacement_object(tmp_path):
    lock = tmp_path / ".overlay.lock"
    lock.write_bytes(b"owned")
    info = lock.lstat()
    identity = (info.st_dev, info.st_ino)
    lock.unlink()
    lock.write_bytes(b"replacement")

    overlay()._unlink_owned_file(lock, identity)

    assert lock.read_bytes() == b"replacement"


def test_owned_lock_cleanup_preserves_swap_after_identity_check(
    tmp_path, monkeypatch
):
    lock = tmp_path / ".overlay.lock"
    lock.write_bytes(b"owned")
    info = lock.lstat()
    identity = (info.st_dev, info.st_ino)
    moved = tmp_path / ".overlay.owned-moved"
    module = overlay()
    real_owned_path_info = module._owned_path_info

    def swap_after_check(path, expected_identity):
        result = real_owned_path_info(path, expected_identity)
        if path == lock and result is not None:
            path.rename(moved)
            path.write_bytes(b"replacement")
        return result

    monkeypatch.setattr(module, "_owned_path_info", swap_after_check)

    module._unlink_owned_file(lock, identity)

    assert lock.read_bytes() == b"replacement"
    assert moved.read_bytes() == b"owned"


def test_owned_tree_cleanup_preserves_swap_after_identity_check(
    tmp_path, monkeypatch
):
    staging = tmp_path / ".overlay.tmp"
    staging.mkdir()
    (staging / "owned-marker").write_bytes(b"owned")
    info = staging.lstat()
    identity = (info.st_dev, info.st_ino)
    moved = tmp_path / ".overlay.owned-moved"
    module = overlay()
    real_owned_path_info = module._owned_path_info

    def swap_after_check(path, expected_identity):
        result = real_owned_path_info(path, expected_identity)
        if path == staging and result is not None:
            path.rename(moved)
            path.mkdir()
            (path / "replacement-marker").write_bytes(b"replacement")
        return result

    monkeypatch.setattr(module, "_owned_path_info", swap_after_check)

    module._remove_owned_tree(staging, identity)

    assert (staging / "replacement-marker").read_bytes() == b"replacement"
    assert (moved / "owned-marker").read_bytes() == b"owned"


def test_owned_lock_cleanup_preserves_claimed_path_swap_after_verification(
    tmp_path, monkeypatch
):
    lock = tmp_path / ".overlay.lock"
    lock.write_bytes(b"owned")
    info = lock.lstat()
    identity = (info.st_dev, info.st_ino)
    state = {"claimed_checks": 0}
    module = overlay()
    real_owned_path_info = module._owned_path_info

    def swap_claimed_after_final_check(path, expected_identity):
        result = real_owned_path_info(path, expected_identity)
        if path.name.startswith(".cleanup-") and result is not None:
            state["claimed_checks"] += 1
            if state["claimed_checks"] == 2:
                moved = path.with_name(path.name + ".owned-moved")
                path.rename(moved)
                path.write_bytes(b"replacement")
                state.update(moved=moved, replacement=path)
        return result

    monkeypatch.setattr(module, "_owned_path_info", swap_claimed_after_final_check)

    module._unlink_owned_file(lock, identity)

    assert state["replacement"].read_bytes() == b"replacement"
    assert state["moved"].read_bytes() == b"owned"


def test_owned_tree_cleanup_preserves_claimed_path_swap_after_verification(
    tmp_path, monkeypatch
):
    staging = tmp_path / ".overlay.tmp"
    staging.mkdir()
    (staging / "owned-marker").write_bytes(b"owned")
    info = staging.lstat()
    identity = (info.st_dev, info.st_ino)
    state = {"claimed_checks": 0}
    module = overlay()
    real_owned_path_info = module._owned_path_info

    def swap_claimed_after_final_check(path, expected_identity):
        result = real_owned_path_info(path, expected_identity)
        if path.name.startswith(".cleanup-") and result is not None:
            state["claimed_checks"] += 1
            if state["claimed_checks"] == 2:
                moved = path.with_name(path.name + ".owned-moved")
                path.rename(moved)
                path.mkdir()
                (path / "replacement-marker").write_bytes(b"replacement")
                state.update(moved=moved, replacement=path)
        return result

    monkeypatch.setattr(module, "_owned_path_info", swap_claimed_after_final_check)

    module._remove_owned_tree(staging, identity)

    assert (state["replacement"] / "replacement-marker").read_bytes() == b"replacement"
    assert (state["moved"] / "owned-marker").read_bytes() == b"owned"


def test_class_review_writer_preserves_replacement_lock_object(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    overlay_id = authoring["manifest"]["overlay_id"]
    lock = store / f".{overlay_id}.lock"
    module = overlay()
    real_remove = module._remove_owned_tree

    def fail_publish(_staging, _target):
        raise OSError("injected publication failure")

    def replace_lock_after_staging_cleanup(path, identity):
        real_remove(path, identity)
        lock.unlink()
        lock.write_bytes(b"replacement lock")

    monkeypatch.setattr(module, "_rename_overlay_directory", fail_publish)
    monkeypatch.setattr(
        module, "_remove_owned_tree", replace_lock_after_staging_cleanup
    )

    with pytest.raises(OSError, match="injected publication failure"):
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert lock.read_bytes() == b"replacement lock"


def test_class_review_writer_refuses_injected_staging_layout(tmp_path, monkeypatch):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    module = overlay()
    real_reader = module._read_class_review_overlay_files

    def inject_extra(staging):
        (staging / "extra.json").write_bytes(b"{}\n")
        return real_reader(staging)

    monkeypatch.setattr(module, "_read_class_review_overlay_files", inject_extra)

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    overlay_id = authoring["manifest"]["overlay_id"]
    assert error.value.code == "invalid_package_layout"
    assert not os.path.lexists(store / overlay_id)
    assert not list(store.glob(f".{overlay_id}.*.tmp"))


def test_class_review_writer_refuses_injected_staging_link(tmp_path, monkeypatch):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"operator evidence")
    module = overlay()
    real_reader = module._read_class_review_overlay_files

    def inject_link(staging):
        try:
            (staging / "extra.json").symlink_to(outside)
        except OSError as error:
            pytest.skip(f"Host does not permit symlink creation: {error}")
        return real_reader(staging)

    monkeypatch.setattr(module, "_read_class_review_overlay_files", inject_link)

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    assert error.value.code == "invalid_package_layout"
    assert outside.read_bytes() == b"operator evidence"


def test_class_review_writer_atomic_publish_never_replaces_racing_target(
    tmp_path, monkeypatch
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    store = tmp_path / "store"
    module = overlay()
    real_rename = module._rename_overlay_directory
    raced = {}

    def create_target_then_publish(staging, target):
        target.mkdir()
        info = target.lstat()
        raced["identity"] = (info.st_dev, info.st_ino)
        real_rename(staging, target)

    monkeypatch.setattr(
        module, "_rename_overlay_directory", create_target_then_publish
    )

    with pytest.raises(ValueError) as error:
        module._write_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            store,
            fixture_mode=True,
        )

    target = store / authoring["manifest"]["overlay_id"]
    current = target.lstat()
    assert error.value.code == "overlay_exists"
    assert (current.st_dev, current.st_ino) == raced["identity"]


@pytest.mark.parametrize("relationship", ["equal", "corpus-under-snapshot", "snapshot-under-corpus"])
def test_class_review_compiler_requires_disjoint_snapshot_and_corpus(
    tmp_path, relationship
):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    if relationship == "equal":
        corpus_root = manifest_path.parent
    elif relationship == "corpus-under-snapshot":
        corpus_root = manifest_path.parent / "nested-corpus"
    else:
        corpus_root = manifest_path.parent.parent

    with pytest.raises(ValueError) as error:
        overlay()._compile_class_review_overlay(
            authoring,
            manifest_path,
            corpus_root,
            fixture_mode=True,
        )

    assert error.value.code == "path_overlap"
    assert error.value.path == "$.paths"


def test_class_review_verifier_returns_compact_verified_summary(tmp_path):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    manifest = json.loads((target / "manifest.json").read_bytes())

    result = overlay()._verify_class_review_overlay(
        target,
        manifest_path,
        corpus_root,
        expected_hash=manifest["overlay_hash"],
        fixture_mode=True,
    )

    assert result == {
        "overlay_id": "pf2e-class-identities-2026-10-07.1",
        "overlay_hash": manifest["overlay_hash"],
        "activation": "none",
        "counts": manifest["counts"],
    }


@pytest.mark.parametrize("mutation", ["extra", "missing", "directory", "wrong-case"])
def test_class_review_verifier_requires_exact_four_file_layout(tmp_path, mutation):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    if mutation == "extra":
        (target / "extra.json").write_bytes(b"{}\n")
    elif mutation == "missing":
        (target / "sources.json").unlink()
    elif mutation == "directory":
        (target / "sources.json").unlink()
        (target / "sources.json").mkdir()
    else:
        (target / "records.json").rename(target / "Records.json")

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$files"


def test_class_review_verifier_refuses_linked_file(tmp_path):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    record_path = target / "records.json"
    outside = tmp_path / "outside.json"
    outside.write_bytes(record_path.read_bytes())
    record_path.unlink()
    try:
        record_path.symlink_to(outside)
    except OSError as error:
        pytest.skip(f"Host does not permit symlink creation: {error}")

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "invalid_package_layout"
    assert outside.exists()


@pytest.mark.parametrize(
    "filename",
    ["authoring.json", "sources.json", "records.json", "manifest.json"],
)
def test_class_review_verifier_rejects_noncanonical_bytes(tmp_path, filename):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    path = target / filename
    path.write_bytes(json.dumps(json.loads(path.read_bytes()), indent=2).encode())

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "noncanonical_bytes"
    assert error.value.path == f"$.files.{filename}"


@pytest.mark.parametrize(
    ("filename", "field"),
    [
        ("authoring.json", "created_at"),
        ("sources.json", "title"),
        ("records.json", "name"),
        ("manifest.json", "compiler_version"),
    ],
)
def test_class_review_verifier_rejects_canonical_tampering(
    tmp_path, filename, field
):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    path = target / filename
    document = json.loads(path.read_bytes())
    if filename == "authoring.json":
        document["manifest"][field] = "2026-10-07T12:34:57Z"
    elif filename == "sources.json":
        document[0][field] = "Altered Source"
    elif filename == "records.json":
        document[0][field] = "Altered Class"
    else:
        document[field] = "altered-compiler"
    path.write_bytes(_test_canonical_json(document))

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "integrity_mismatch"
    assert error.value.path == f"$.files.{filename}"


def test_class_review_verifier_requires_directory_name_to_match_overlay_id(tmp_path):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    renamed = target.with_name("wrong-overlay-name")
    target.rename(renamed)

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            renamed, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "invalid_package_layout"
    assert error.value.path == "$.manifest.overlay_id"


def test_class_review_verifier_rejects_snapshot_drift(tmp_path):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    manifest_path.write_bytes(manifest_path.read_bytes() + b" ")

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "hash_mismatch"
    assert error.value.path == "$.manifest.snapshot_manifest_sha256"


def test_class_review_verifier_rejects_local_corpus_drift(tmp_path):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    local_path = corpus_root / "classes" / "synthetic-class.json"
    local_path.write_bytes(local_path.read_bytes().replace(b'  "name"', b'   "name"'))

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "integrity_mismatch"
    assert error.value.path == "$.files.records.json"


@pytest.mark.parametrize(
    ("expected_hash", "code"),
    [("0" * 64, "binding_mismatch"), ("not-a-hash", "invalid_id")],
)
def test_class_review_verifier_enforces_optional_trusted_hash(
    tmp_path, expected_hash, code
):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            target,
            manifest_path,
            corpus_root,
            expected_hash=expected_hash,
            fixture_mode=True,
        )

    assert error.value.code == code
    assert error.value.path == "$.binding.overlay_hash"


def test_class_review_verifier_trusted_hash_rejects_self_consistent_replacement(
    tmp_path,
):
    first, authoring, manifest_path, corpus_root = _write_fixture(
        tmp_path / "first", store_name="store"
    )
    trusted = json.loads((first / "manifest.json").read_bytes())["overlay_hash"]
    changed = deepcopy(authoring)
    changed["manifest"]["created_at"] = "2026-10-07T12:34:57Z"
    second_store = tmp_path / "replacement-store"
    second = overlay()._write_class_review_overlay(
        changed,
        manifest_path,
        corpus_root,
        second_store,
        fixture_mode=True,
    )

    overlay()._verify_class_review_overlay(
        second, manifest_path, corpus_root, fixture_mode=True
    )
    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            second,
            manifest_path,
            corpus_root,
            expected_hash=trusted,
            fixture_mode=True,
        )

    assert error.value.code == "binding_mismatch"
    assert error.value.path == "$.binding.overlay_hash"


def test_class_review_verifier_stops_layout_scan_at_fifth_entry(
    tmp_path, monkeypatch
):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    for index in range(20):
        (target / f"extra-{index:02d}.json").write_bytes(b"{}\n")
    module = overlay()
    real_scandir = os.scandir
    calls = 0

    class BoundedScandir:
        def __init__(self, path):
            self._iterator = real_scandir(path)

        def __enter__(self):
            self._iterator.__enter__()
            return self

        def __exit__(self, *args):
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            nonlocal calls
            calls += 1
            if calls > 5:
                raise AssertionError("overlay layout scan exceeded five entries")
            return next(self._iterator)

    monkeypatch.setattr(module.os, "scandir", BoundedScandir)

    with pytest.raises(ValueError) as error:
        module._verify_class_review_overlay(
            target, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "invalid_package_layout"
    assert calls == 5


@pytest.mark.parametrize("relationship", ["store-under-snapshot", "store-over-corpus"])
def test_class_review_verifier_requires_disjoint_store_snapshot_and_corpus(
    tmp_path, relationship
):
    target, _authoring, manifest_path, corpus_root = _write_fixture(tmp_path)
    if relationship == "store-under-snapshot":
        nested = manifest_path.parent / "nested-store" / target.name
        nested.parent.mkdir()
    else:
        nested = corpus_root.parent / "nested-store" / target.name
        nested.parent.mkdir()
        corpus_root = nested.parent / "corpus"
        shutil.copytree(tmp_path / "corpus", corpus_root)
    shutil.copytree(target, nested)

    with pytest.raises(ValueError) as error:
        overlay()._verify_class_review_overlay(
            nested, manifest_path, corpus_root, fixture_mode=True
        )

    assert error.value.code == "path_overlap"
    assert error.value.path == "$.paths"


def test_class_review_compiler_bytes_ignore_python_hash_seed(tmp_path):
    authoring, manifest_path, corpus_root = _class_review_compile_fixture(tmp_path)
    authoring_path = tmp_path / "authoring-input.json"
    authoring_path.write_bytes(_test_canonical_json(authoring))
    script = """
import json
import sys
from pathlib import Path
from systems.pf2e.rules.ingestion.class_review_overlay import _compile_class_review_overlay

authoring = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
files = _compile_class_review_overlay(
    authoring, Path(sys.argv[2]), Path(sys.argv[3]), fixture_mode=True
)
print(json.dumps({name: data.hex() for name, data in sorted(files.items())}, sort_keys=True))
"""
    outputs = []
    for seed in ("1", "417"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(authoring_path),
                str(manifest_path),
                str(corpus_root),
            ],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
