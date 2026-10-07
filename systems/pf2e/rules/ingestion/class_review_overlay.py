"""Strict, offline authoring contracts for PF2e class-review overlays."""
from __future__ import annotations

from copy import deepcopy
import re

from ..manifest import MAX_FILE_BYTES, digest
from ..validation import (
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
from .evidence_inventory import AON_AUTHORITY, evidence_metadata_text


CLASS_REVIEW_SCHEMA_VERSION = 1
SHA256 = r"[0-9a-f]{64}"
OVERLAY_ID = r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*"
SNAPSHOT_ID = r"pf2e-aon-[a-z0-9]+(?:-[a-z0-9]+)*"
CLASS_SOURCE_ID = r"pf2e\.source\.[a-z0-9]+(?:-[a-z0-9]+)*"
MAX_CLASS_SOURCES = 8
MAX_CLASS_RECORDS = 29


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


def repository_text_sha256(data: bytes, path: str = "$") -> str:
    """Hash normalized text; LF/CRLF parity requires both raw inputs fit 16 MiB."""
    require(type(data) is bytes, "invalid_type", path)
    require(len(data) <= MAX_FILE_BYTES, "limit_exceeded", path)
    normalized = data.replace(b"\r\n", b"\n")
    require(b"\r" not in normalized, "invalid_repository_text", path)
    return digest(normalized)
