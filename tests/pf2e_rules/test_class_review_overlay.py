"""Strict contracts for immutable PF2e class-review authoring data."""
from copy import deepcopy
import hashlib
import importlib

import pytest


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
