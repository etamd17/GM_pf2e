"""Strict, offline authoring contracts for PF2e class-review overlays."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
import re

from ..manifest import MAX_FILE_BYTES, digest, read_file, read_json
from ..validation import (
    RulesValidationError,
    bounded_json,
    choice,
    integer,
    require,
    rule_id,
    sequence,
    shape,
    strings,
    text,
    timestamp,
)
from .aon_capture import PENDING_REASON
from .evidence_inventory import (
    AON_AUTHORITY,
    evidence_metadata_text,
    normalize_aon_census,
    normalize_evidence_ledger,
)
from .evidence_snapshot import (
    _verify_evidence_snapshot_document,
    normalize_snapshot_manifest,
)


CLASS_REVIEW_SCHEMA_VERSION = 1
SHA256 = r"[0-9a-f]{64}"
OVERLAY_ID = r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*"
SNAPSHOT_ID = r"pf2e-aon-[a-z0-9]+(?:-[a-z0-9]+)*"
CLASS_SOURCE_ID = r"pf2e\.source\.[a-z0-9]+(?:-[a-z0-9]+)*"
MAX_CLASS_SOURCES = 8
MAX_CLASS_RECORDS = 29
PRODUCTION_SNAPSHOT_ID = "pf2e-aon-2026-10-07-player-build-v1"
PRODUCTION_SNAPSHOT_MANIFEST_SHA256 = (
    "c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de"
)
PRODUCTION_SOURCE_TITLE_COUNTS = {
    "Battlecry!": 2,
    "Dark Archive (Remastered)": 2,
    "Guns & Gears (Remastered)": 2,
    "Impossible Magic": 4,
    "Player Core": 8,
    "Player Core 2": 8,
    "Rage of Elements": 1,
    "War of Immortals": 2,
}


def _sha256(value, path: str) -> str:
    value = text(value, path)
    require(re.fullmatch(SHA256, value) is not None, "invalid_value", path)
    return value


def _review(value, path: str) -> dict:
    shape(value, "status reviewer reviewed_at evidence_sha256", path)
    status = choice(
        value["status"], {"pending", "approved", "rejected"}, path + ".status"
    )
    value["evidence_sha256"] = strings(
        value["evidence_sha256"], path + ".evidence_sha256", pattern=SHA256
    )
    if status == "pending":
        require(
            value["reviewer"] is None
            and value["reviewed_at"] is None
            and value["evidence_sha256"] == [],
            "invalid_review",
            path,
        )
        return value

    require(
        value["reviewer"] is not None
        and value["reviewed_at"] is not None
        and bool(value["evidence_sha256"]),
        "invalid_review",
        path,
    )
    text(value["reviewer"], path + ".reviewer")
    timestamp(value["reviewed_at"], path + ".reviewed_at")
    return value


def _manifest(value, path: str) -> dict:
    shape(
        value,
        "schema_version overlay_id created_at authority kind snapshot_id "
        "snapshot_manifest_sha256 base_category activation parent_overlay_id "
        "parent_overlay_hash",
        path,
    )
    require(type(value["schema_version"]) is int, "invalid_type", path + ".schema_version")
    require(
        value["schema_version"] == CLASS_REVIEW_SCHEMA_VERSION,
        "unsupported_version",
        path + ".schema_version",
    )
    text(value["overlay_id"], path + ".overlay_id", pattern=OVERLAY_ID)
    timestamp(value["created_at"], path + ".created_at")
    choice(value["authority"], {AON_AUTHORITY}, path + ".authority")
    choice(value["kind"], {"class"}, path + ".kind")
    text(value["snapshot_id"], path + ".snapshot_id", pattern=SNAPSHOT_ID)
    _sha256(
        value["snapshot_manifest_sha256"], path + ".snapshot_manifest_sha256"
    )
    choice(value["base_category"], {"class"}, path + ".base_category")
    choice(value["activation"], {"none"}, path + ".activation")
    require(
        value["parent_overlay_id"] is None
        and value["parent_overlay_hash"] is None,
        "unsupported_composition",
        path,
    )
    return value


def _source(value, path: str) -> dict:
    shape(value, "source_id title source_review license_review", path)
    text(value["source_id"], path + ".source_id", pattern=CLASS_SOURCE_ID)
    evidence_metadata_text(value["title"], path + ".title")
    _review(value["source_review"], path + ".source_review")
    _review(value["license_review"], path + ".license_review")
    return value


def _identity(value, path: str) -> dict:
    shape(value, "page_family numeric_id", path)
    choice(value["page_family"], {"Classes.aspx"}, path + ".page_family")
    integer(value["numeric_id"], 1, 99_999_999, path + ".numeric_id")
    return value


def _record(value, path: str) -> dict:
    shape(value, "identity rule_id source_id rules_review", path)
    _identity(value["identity"], path + ".identity")
    rule_id(value["rule_id"], path + ".rule_id", "class")
    text(value["source_id"], path + ".source_id", pattern=CLASS_SOURCE_ID)
    _review(value["rules_review"], path + ".rules_review")
    return value


def normalize_class_review_authoring(document: dict) -> dict:
    """Validate and detach one versioned class-review authoring document."""
    bounded_json(document)
    value = deepcopy(document)
    shape(value, "manifest sources records", "$")
    _manifest(value["manifest"], "$.manifest")

    source_ids = set()
    source_paths = {}
    sources = sequence(value["sources"], "$.sources")
    require(
        1 <= len(sources) <= MAX_CLASS_SOURCES,
        "limit_exceeded",
        "$.sources",
    )
    for index, source in enumerate(sources):
        path = f"$.sources[{index}]"
        _source(source, path)
        require(
            source["source_id"] not in source_ids,
            "duplicate_id",
            path + ".source_id",
        )
        source_ids.add(source["source_id"])
        source_paths[source["source_id"]] = path + ".source_id"

    identities = set()
    rule_ids = set()
    used_source_ids = set()
    records = sequence(value["records"], "$.records")
    require(
        1 <= len(records) <= MAX_CLASS_RECORDS,
        "limit_exceeded",
        "$.records",
    )
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        _record(record, path)
        require(
            record["source_id"] in source_ids,
            "dangling_reference",
            path + ".source_id",
        )
        used_source_ids.add(record["source_id"])
        identity = (
            record["identity"]["page_family"],
            record["identity"]["numeric_id"],
        )
        require(
            identity not in identities,
            "duplicate_identity",
            path + ".identity",
        )
        identities.add(identity)
        require(
            record["rule_id"] not in rule_ids,
            "duplicate_id",
            path + ".rule_id",
        )
        rule_ids.add(record["rule_id"])

    for source_id, source_path in source_paths.items():
        require(
            source_id in used_source_ids,
            "unused_source",
            source_path,
        )

    sources.sort(key=lambda item: item["source_id"])
    records.sort(
        key=lambda item: (
            item["identity"]["page_family"],
            item["identity"]["numeric_id"],
        )
    )
    return value


def _read_snapshot_document(root: Path, reference: dict, path: str):
    candidate = root.joinpath(*PurePosixPath(reference["path"]).parts)
    try:
        data = read_file(candidate)
    except RulesValidationError as error:
        if error.code == "invalid_package_layout":
            raise RulesValidationError(
                "invalid_package_layout", path + ".path"
            ) from None
        raise
    require(digest(data) == reference["sha256"], "hash_mismatch", path + ".sha256")
    return read_json(data)


def _identity_key(value: dict) -> tuple[str, int]:
    return value["identity"]["page_family"], value["identity"]["numeric_id"]


def _require_production_snapshot(
    snapshot: dict, manifest_sha256: str, census: dict
) -> None:
    require(
        snapshot["snapshot_id"] == PRODUCTION_SNAPSHOT_ID,
        "snapshot_mismatch",
        "$.manifest.snapshot_id",
    )
    require(
        manifest_sha256 == PRODUCTION_SNAPSHOT_MANIFEST_SHA256,
        "snapshot_mismatch",
        "$.manifest.snapshot_manifest_sha256",
    )
    records = census["records"]
    require(
        len(records) == MAX_CLASS_RECORDS,
        "count_mismatch",
        "$.snapshot.class",
    )
    source_counts = Counter()
    for index, record in enumerate(records):
        require(
            len(record["source_refs"]) == 1,
            "source_mismatch",
            f"$.snapshot.class.census.records[{index}].source_refs",
        )
        source_counts[record["source_refs"][0]["title"]] += 1
    require(
        dict(sorted(source_counts.items())) == PRODUCTION_SOURCE_TITLE_COUNTS,
        "source_mismatch",
        "$.snapshot.class.sources",
    )


def _resolve_class_snapshot(
    authoring: dict,
    snapshot_manifest_path: Path,
    *,
    fixture_mode: bool = False,
) -> dict:
    """Resolve verified class evidence into a detached compilation intermediate."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    value = normalize_class_review_authoring(authoring)
    if not fixture_mode:
        require(
            value["manifest"]["snapshot_id"] == PRODUCTION_SNAPSHOT_ID,
            "snapshot_mismatch",
            "$.manifest.snapshot_id",
        )
        require(
            value["manifest"]["snapshot_manifest_sha256"]
            == PRODUCTION_SNAPSHOT_MANIFEST_SHA256,
            "snapshot_mismatch",
            "$.manifest.snapshot_manifest_sha256",
        )
    manifest_path = Path(snapshot_manifest_path)
    manifest_bytes = read_file(manifest_path)
    manifest_sha256 = digest(manifest_bytes)
    require(
        manifest_sha256 == value["manifest"]["snapshot_manifest_sha256"],
        "hash_mismatch",
        "$.manifest.snapshot_manifest_sha256",
    )

    snapshot = normalize_snapshot_manifest(read_json(manifest_bytes))
    snapshot_summary = _verify_evidence_snapshot_document(
        snapshot, manifest_path.parent
    )
    require(
        snapshot["snapshot_id"] == value["manifest"]["snapshot_id"],
        "snapshot_mismatch",
        "$.manifest.snapshot_id",
    )
    require(
        snapshot["authority"] == value["manifest"]["authority"],
        "snapshot_mismatch",
        "$.manifest.authority",
    )

    class_shards = [
        (index, shard)
        for index, shard in enumerate(snapshot["shards"])
        if shard["category"] == value["manifest"]["base_category"]
    ]
    require(len(class_shards) == 1, "snapshot_mismatch", "$.snapshot.shards")
    shard_index, class_shard = class_shards[0]
    require(
        class_shard["kind"] == value["manifest"]["kind"],
        "snapshot_mismatch",
        f"$.snapshot.shards[{shard_index}].kind",
    )

    root = manifest_path.parent
    census = normalize_aon_census(
        _read_snapshot_document(
            root,
            class_shard["census"],
            f"$.snapshot.shards[{shard_index}].census",
        )
    )
    ledger = normalize_evidence_ledger(
        _read_snapshot_document(
            root,
            class_shard["ledger"],
            f"$.snapshot.shards[{shard_index}].ledger",
        )
    )
    if not fixture_mode:
        _require_production_snapshot(snapshot, manifest_sha256, census)

    census_by_identity = {
        _identity_key(record): (index, record)
        for index, record in enumerate(census["records"])
    }
    authoring_by_identity = {
        _identity_key(record): (index, record)
        for index, record in enumerate(value["records"])
    }
    for identity, (index, _) in authoring_by_identity.items():
        require(
            identity in census_by_identity,
            "unexpected_identity",
            f"$.records[{index}].identity",
        )
    require(
        authoring_by_identity.keys() == census_by_identity.keys(),
        "incomplete_inventory",
        "$.records",
    )

    ledger_by_identity = {
        _identity_key(entry): (index, entry)
        for index, entry in enumerate(ledger["entries"])
    }
    require(
        ledger_by_identity.keys() == census_by_identity.keys(),
        "incomplete_inventory",
        "$.snapshot.class.ledger",
    )
    pending_count = 0
    for _, entry in ledger_by_identity.values():
        require(
            entry["disposition"] == "pending"
            and entry["rule_id"] is None
            and entry["reason"] == PENDING_REASON
            and entry["review"]
            == {"status": "pending", "reviewer": None, "reviewed_at": None},
            "lifecycle_mismatch",
            "$.snapshot.class.ledger",
        )
        pending_count += 1

    sources_by_id = {source["source_id"]: source for source in value["sources"]}
    records = []
    for identity, (authoring_index, authored) in authoring_by_identity.items():
        census_index, evidence = census_by_identity[identity]
        source_refs = evidence["source_refs"]
        require(
            len(source_refs) == 1,
            "source_mismatch",
            f"$.snapshot.class.census.records[{census_index}].source_refs",
        )
        source_ref = source_refs[0]
        source = sources_by_id[authored["source_id"]]
        require(
            source["title"] == source_ref["title"],
            "source_mismatch",
            f"$.records[{authoring_index}].source_id",
        )
        records.append(
            {
                "identity": deepcopy(evidence["identity"]),
                "name": evidence["name"],
                "canonical_url": evidence["canonical_url"],
                "fingerprint": evidence["fingerprint"],
                "evidence_sha256": evidence["evidence_sha256"],
                "source_ref": deepcopy(source_ref),
                "rule_id": authored["rule_id"],
                "source_id": authored["source_id"],
                "rules_review": deepcopy(authored["rules_review"]),
            }
        )

    require(
        snapshot_summary["class_count"] == len(records)
        and class_shard["expected_records"] == len(records),
        "count_mismatch",
        "$.snapshot.class",
    )
    return {
        "manifest": value["manifest"],
        "sources": value["sources"],
        "records": records,
        "counts": {
            "snapshot_included_records": snapshot_summary["records"]["included"],
            "base_class_records": len(records),
            "base_pending_records": pending_count,
        },
    }


def repository_text_sha256(data: bytes, path: str = "$") -> str:
    """Hash normalized text; LF/CRLF parity requires both raw inputs fit 16 MiB."""
    require(type(data) is bytes, "invalid_type", path)
    require(len(data) <= MAX_FILE_BYTES, "limit_exceeded", path)
    normalized = data.replace(b"\r\n", b"\n")
    require(b"\r" not in normalized, "invalid_repository_text", path)
    return digest(normalized)
