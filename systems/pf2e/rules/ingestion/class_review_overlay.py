"""Strict, offline authoring contracts for PF2e class-review overlays."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import ctypes
import errno
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import sys
import tempfile
import unicodedata

from ..manifest import (
    MAX_FILE_BYTES,
    canonical_json,
    digest,
    read_file,
    read_json,
    reject_links,
)
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
CLASS_REVIEW_COMPILER_VERSION = "pf2e-class-review-1"
SHA256 = r"[0-9a-f]{64}"
OVERLAY_ID = r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*"
SNAPSHOT_ID = r"pf2e-aon-[a-z0-9]+(?:-[a-z0-9]+)*"
CLASS_SOURCE_ID = r"pf2e\.source\.[a-z0-9]+(?:-[a-z0-9]+)*"
MAX_CLASS_SOURCES = 8
MAX_CLASS_RECORDS = 29
MAX_LOCAL_CLASS_FILES = 27
MAX_CLASS_CORPUS_BYTES = 32 * 1024 * 1024
MAX_DISJOINT_PATH_COMPONENTS = 256
LOCK_NONCE_BYTES = 32
CLASS_REVIEW_FILES = frozenset(
    {"authoring.json", "sources.json", "records.json", "manifest.json"}
)
LOCAL_CLASS_FILE = r"[a-z0-9]+(?:-[a-z0-9]+)*\.json"
LOCAL_FOUNDRY_ID = r"[A-Za-z0-9]{16}"
WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
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
PRODUCTION_SOURCE_DRIFT_RULE_IDS = frozenset(
    {
        "pf2e.class.gunslinger",
        "pf2e.class.inventor",
        "pf2e.class.magus",
        "pf2e.class.psychic",
        "pf2e.class.summoner",
        "pf2e.class.thaumaturge",
    }
)
PRODUCTION_MISSING_LOCAL_RULE_IDS = frozenset(
    {"pf2e.class.necromancer", "pf2e.class.runesmith"}
)


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
    include_base_ledger: bool = False,
) -> dict:
    """Resolve verified class evidence into a detached compilation intermediate."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    require(
        type(include_base_ledger) is bool,
        "invalid_type",
        "$.include_base_ledger",
    )
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
    result = {
        "manifest": value["manifest"],
        "sources": value["sources"],
        "records": records,
        "counts": {
            "snapshot_included_records": snapshot_summary["records"]["included"],
            "base_class_records": len(records),
            "base_pending_records": pending_count,
            "base_dispositions": deepcopy(snapshot_summary["dispositions"]),
            "base_reviews": deepcopy(snapshot_summary["reviews"]),
        },
    }
    if include_base_ledger:
        result["_base_class_ledger"] = deepcopy(ledger)
    return result


def repository_text_sha256(data: bytes, path: str = "$") -> str:
    """Hash normalized text; LF/CRLF parity requires both raw inputs fit 16 MiB."""
    require(type(data) is bytes, "invalid_type", path)
    require(len(data) <= MAX_FILE_BYTES, "limit_exceeded", path)
    normalized = data.replace(b"\r\n", b"\n")
    require(b"\r" not in normalized, "invalid_repository_text", path)
    return digest(normalized)


def _local_class_path(name: str) -> str:
    return "$.corpus.classes." + name


def _scan_local_classes(corpus_root: Path) -> list[dict]:
    """Read only the bounded, flat local class directory."""
    root = Path(corpus_root).absolute()
    classes = root / "classes"
    try:
        reject_links(root)
        reject_links(classes)
        classes_info = classes.lstat()
    except (OSError, RulesValidationError):
        raise RulesValidationError(
            "invalid_package_layout", "$.corpus.classes"
        ) from None
    require(
        stat.S_ISDIR(classes_info.st_mode),
        "invalid_package_layout",
        "$.corpus.classes",
    )

    entries = []
    paths_seen = set()
    try:
        with os.scandir(classes) as iterator:
            for entry in iterator:
                name = entry.name
                entry_path = _local_class_path(name)
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    raise RulesValidationError("io_error", entry_path) from None
                require(
                    not stat.S_ISLNK(info.st_mode)
                    and not (
                        getattr(info, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                    ),
                    "invalid_package_layout",
                    entry_path,
                )
                require(
                    stat.S_ISREG(info.st_mode),
                    "invalid_package_layout",
                    entry_path,
                )
                require(
                    re.fullmatch(LOCAL_CLASS_FILE, name) is not None
                    and PurePosixPath(name).name == name
                    and name[:-5] not in WINDOWS_RESERVED_NAMES,
                    "invalid_path",
                    entry_path,
                )
                normalized_path = ("classes/" + name).casefold()
                require(
                    normalized_path not in paths_seen,
                    "duplicate_id",
                    entry_path,
                )
                paths_seen.add(normalized_path)
                entries.append((name, Path(entry.path)))
                require(
                    len(entries) <= MAX_LOCAL_CLASS_FILES,
                    "limit_exceeded",
                    "$.corpus.classes",
                )
    except RulesValidationError:
        raise
    except OSError:
        raise RulesValidationError("io_error", "$.corpus.classes") from None
    require(bool(entries), "limit_exceeded", "$.corpus.classes")

    records = []
    foundry_ids = set()
    names = set()
    local_keys = set()
    processed_bytes = 0
    for name, source_path in sorted(entries):
        record_path = _local_class_path(name)
        try:
            data = read_file(source_path)
        except RulesValidationError as error:
            raise RulesValidationError(error.code, record_path) from None
        processed_bytes += len(data)
        require(
            processed_bytes <= MAX_CLASS_CORPUS_BYTES,
            "limit_exceeded",
            record_path,
        )
        content_sha256 = repository_text_sha256(data, record_path)
        try:
            value = read_json(data)
        except RulesValidationError as error:
            raise RulesValidationError(error.code, record_path) from None

        require(type(value) is dict, "invalid_type", record_path)
        for field in ("_id", "name", "system", "type"):
            require(field in value, "missing_field", record_path + "." + field)
        foundry_id = text(value["_id"], record_path + "._id")
        require(
            re.fullmatch(LOCAL_FOUNDRY_ID, foundry_id) is not None,
            "invalid_id",
            record_path + "._id",
        )
        name_value = text(value["name"], record_path + ".name")
        choice(value["type"], {"class"}, record_path + ".type")
        system = value["system"]
        require(type(system) is dict, "invalid_type", record_path + ".system")
        require(
            "publication" in system,
            "missing_field",
            record_path + ".system.publication",
        )
        publication = system["publication"]
        require(
            type(publication) is dict,
            "invalid_type",
            record_path + ".system.publication",
        )
        require(
            "title" in publication,
            "missing_field",
            record_path + ".system.publication.title",
        )
        publication_title = text(
            publication["title"], record_path + ".system.publication.title"
        )
        local_key = name[:-5]
        require(
            local_key not in local_keys,
            "duplicate_id",
            record_path,
        )
        require(
            foundry_id not in foundry_ids,
            "duplicate_id",
            record_path + "._id",
        )
        require(
            name_value not in names,
            "duplicate_id",
            record_path + ".name",
        )
        local_keys.add(local_key)
        foundry_ids.add(foundry_id)
        names.add(name_value)
        records.append(
            {
                "local_key": local_key,
                "relative_path": "classes/" + name,
                "foundry_id": foundry_id,
                "name": name_value,
                "publication_title": publication_title,
                "content_sha256": content_sha256,
            }
        )
    return records


def _strip_local_pathfinder_prefix(title: str) -> str:
    prefix = "Pathfinder "
    return title[len(prefix) :] if title.startswith(prefix) else title


def _reconcile_class_corpus(
    resolved: dict,
    corpus_root: Path,
    *,
    fixture_mode: bool = False,
) -> dict:
    """Bind explicit class rule IDs to a detached local-corpus projection."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    value = deepcopy(resolved)
    local_records = _scan_local_classes(corpus_root)
    local_by_key = {record["local_key"]: record for record in local_records}
    expected_keys = {}
    for index, record in enumerate(value["records"]):
        rule_id_value = record["rule_id"]
        require(
            type(rule_id_value) is str
            and rule_id_value.startswith("pf2e.class."),
            "invalid_id",
            f"$.records[{index}].rule_id",
        )
        local_key = rule_id_value[len("pf2e.class.") :]
        require(
            re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", local_key) is not None,
            "invalid_id",
            f"$.records[{index}].rule_id",
        )
        require(
            local_key not in expected_keys,
            "duplicate_id",
            f"$.records[{index}].rule_id",
        )
        expected_keys[local_key] = index

    for local_key, local_record in local_by_key.items():
        require(
            local_key in expected_keys,
            "orphan_local_class",
            _local_class_path(Path(local_record["relative_path"]).name),
        )

    reconciliation_counts = Counter()
    reconciliation_rule_ids = {
        "aligned": set(),
        "source-drift": set(),
        "missing-local": set(),
    }
    for index, record in enumerate(value["records"]):
        local_key = record["rule_id"][len("pf2e.class.") :]
        local_record = local_by_key.get(local_key)
        if local_record is None:
            local = {
                "status": "missing",
                "relative_path": None,
                "foundry_id": None,
                "content_sha256": None,
                "local_key": None,
            }
            reconciliation = "missing-local"
        else:
            path = _local_class_path(Path(local_record["relative_path"]).name)
            require(
                local_record["name"] == record["name"],
                "name_mismatch",
                path + ".name",
            )
            local = {
                "status": "present",
                "relative_path": local_record["relative_path"],
                "foundry_id": local_record["foundry_id"],
                "content_sha256": local_record["content_sha256"],
                "local_key": local_key,
            }
            local_source_title = _strip_local_pathfinder_prefix(
                local_record["publication_title"]
            )
            reconciliation = (
                "aligned"
                if local_source_title == record["source_ref"]["title"]
                else "source-drift"
            )
        record["disposition"] = "mapped"
        record["local"] = local
        record["reconciliation"] = reconciliation
        reconciliation_counts[reconciliation] += 1
        reconciliation_rule_ids[reconciliation].add(record["rule_id"])

    if not fixture_mode:
        require(
            len(value["records"]) == MAX_CLASS_RECORDS
            and len(local_records) == MAX_LOCAL_CLASS_FILES
            and reconciliation_counts
            == {"aligned": 21, "source-drift": 6, "missing-local": 2},
            "count_mismatch",
            "$.corpus.classes",
        )
        require(
            reconciliation_rule_ids["source-drift"]
            == PRODUCTION_SOURCE_DRIFT_RULE_IDS
            and reconciliation_rule_ids["missing-local"]
            == PRODUCTION_MISSING_LOCAL_RULE_IDS,
            "reconciliation_mismatch",
            "$.corpus.classes",
        )
    value["counts"].update(
        {
            "local_class_records": len(local_records),
            "aligned_records": reconciliation_counts["aligned"],
            "source_drift_records": reconciliation_counts["source-drift"],
            "missing_local_records": reconciliation_counts["missing-local"],
        }
    )
    return value


def _apply_class_review_overlay(
    base_class_ledger: dict,
    reconciled: dict,
    *,
    fixture_mode: bool = False,
) -> dict:
    """Return a detached effective class ledger and zero-activation summary."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    bounded_json(reconciled)
    value = deepcopy(reconciled)
    effective_ledger = normalize_evidence_ledger(base_class_ledger)

    manifest = value.get("manifest")
    require(type(manifest) is dict, "invalid_type", "$.manifest")
    require(
        manifest.get("activation") == "none",
        "invalid_value",
        "$.manifest.activation",
    )
    records = sequence(value.get("records"), "$.records")
    sources = sequence(value.get("sources"), "$.sources")
    counts = value.get("counts")
    require(type(counts) is dict, "invalid_type", "$.counts")

    snapshot_records = integer(
        counts.get("snapshot_included_records"),
        1,
        100_000,
        "$.counts.snapshot_included_records",
    )
    base_dispositions = counts.get("base_dispositions")
    shape(
        base_dispositions,
        "excluded mapped pending",
        "$.counts.base_dispositions",
    )
    for field in ("excluded", "mapped", "pending"):
        integer(
            base_dispositions[field],
            0,
            100_000,
            "$.counts.base_dispositions." + field,
        )
    base_reviews = counts.get("base_reviews")
    shape(base_reviews, "pending reviewed", "$.counts.base_reviews")
    for field in ("pending", "reviewed"):
        integer(
            base_reviews[field],
            0,
            100_000,
            "$.counts.base_reviews." + field,
        )
    require(
        sum(base_dispositions.values()) == snapshot_records
        and sum(base_reviews.values()) == snapshot_records,
        "count_mismatch",
        "$.counts",
    )
    require(
        base_dispositions["excluded"] == 0
        and base_dispositions["mapped"] == 0
        and base_dispositions["pending"] == snapshot_records
        and base_reviews == {"pending": snapshot_records, "reviewed": 0},
        "lifecycle_mismatch",
        "$.counts",
    )
    require(
        counts.get("base_class_records") == len(records)
        and counts.get("base_pending_records") == len(records),
        "count_mismatch",
        "$.counts.base_class_records",
    )

    records_by_identity = {}
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        require(type(record) is dict, "invalid_type", path)
        require("identity" in record, "missing_field", path + ".identity")
        _identity(record["identity"], path + ".identity")
        identity = _identity_key(record)
        require(
            identity not in records_by_identity,
            "duplicate_identity",
            path + ".identity",
        )
        require(
            record.get("disposition") == "mapped",
            "lifecycle_mismatch",
            path + ".disposition",
        )
        rule_id(record.get("rule_id"), path + ".rule_id", "class")
        require("rules_review" in record, "missing_field", path + ".rules_review")
        _review(record["rules_review"], path + ".rules_review")
        records_by_identity[identity] = record

    ledger_identities = set()
    for index, entry in enumerate(effective_ledger["entries"]):
        path = f"$.base_class_ledger.entries[{index}]"
        identity = _identity_key(entry)
        require(
            identity in records_by_identity,
            "unexpected_identity",
            path + ".identity",
        )
        require(
            entry["disposition"] == "pending"
            and entry["rule_id"] is None
            and entry["reason"] == PENDING_REASON
            and entry["review"]
            == {"status": "pending", "reviewer": None, "reviewed_at": None},
            "lifecycle_mismatch",
            path,
        )
        ledger_identities.add(identity)
    require(
        ledger_identities == records_by_identity.keys(),
        "incomplete_inventory",
        "$.records",
    )

    for entry in effective_ledger["entries"]:
        record = records_by_identity[_identity_key(entry)]
        entry.update(
            disposition="mapped",
            rule_id=record["rule_id"],
            reason=None,
        )
    effective_ledger = normalize_evidence_ledger(effective_ledger)

    gate_statuses = Counter()
    for index, source in enumerate(sources):
        path = f"$.sources[{index}]"
        require(type(source) is dict, "invalid_type", path)
        for field in ("source_review", "license_review"):
            require(field in source, "missing_field", path + "." + field)
            review = _review(source[field], path + "." + field)
            gate_statuses[review["status"]] += 1
    for record in records:
        gate_statuses[record["rules_review"]["status"]] += 1

    mapped_records = len(records)
    require(
        base_dispositions["pending"] >= mapped_records,
        "count_mismatch",
        "$.counts.base_dispositions.pending",
    )
    summary = {
        "activation": "none",
        "enabled_mechanics": 0,
        "effective_dispositions": {
            "excluded": base_dispositions["excluded"],
            "mapped": base_dispositions["mapped"] + mapped_records,
            "pending": base_dispositions["pending"] - mapped_records,
        },
        "base_reviews": deepcopy(base_reviews),
        "overlay_gates": {
            "sources": len(sources),
            "licenses": len(sources),
            "rules": mapped_records,
            "total": (2 * len(sources)) + mapped_records,
            "by_status": {
                status: gate_statuses[status]
                for status in ("pending", "approved", "rejected")
            },
        },
    }

    if manifest.get("snapshot_id") == PRODUCTION_SNAPSHOT_ID and not fixture_mode:
        require(
            manifest.get("snapshot_manifest_sha256")
            == PRODUCTION_SNAPSHOT_MANIFEST_SHA256,
            "snapshot_mismatch",
            "$.manifest.snapshot_manifest_sha256",
        )
        require(
            summary["effective_dispositions"]
            == {"excluded": 0, "mapped": 29, "pending": 18_493}
            and summary["base_reviews"] == {"pending": 18_522, "reviewed": 0}
            and summary["overlay_gates"]
            == {
                "sources": 8,
                "licenses": 8,
                "rules": 29,
                "total": 45,
                "by_status": {"pending": 45, "approved": 0, "rejected": 0},
            },
            "count_mismatch",
            "$.counts",
        )
    return {
        "effective_class_ledger": effective_ledger,
        "summary": summary,
    }


def _require_pending_overlay_reviews(authoring: dict) -> None:
    for index, source in enumerate(authoring["sources"]):
        for field in ("source_review", "license_review"):
            require(
                source[field]["status"] == "pending",
                "lifecycle_mismatch",
                f"$.sources[{index}].{field}",
            )
    for index, record in enumerate(authoring["records"]):
        require(
            record["rules_review"]["status"] == "pending",
            "lifecycle_mismatch",
            f"$.records[{index}].rules_review",
        )


def _compile_class_review_overlay(
    authoring: dict,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    *,
    fixture_mode: bool = False,
) -> dict[str, bytes]:
    """Compile canonical sidecars from verified evidence without publishing."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    manifest_path = Path(snapshot_manifest_path).absolute()
    corpus = Path(corpus_root).absolute()
    _require_disjoint_trees(
        (
            ("$.paths.snapshot", manifest_path.parent),
            ("$.paths.corpus", corpus),
        )
    )
    resolved = _resolve_class_snapshot(
        authoring,
        manifest_path,
        fixture_mode=fixture_mode,
        include_base_ledger=True,
    )
    normalized_authoring = {
        "manifest": deepcopy(resolved["manifest"]),
        "sources": deepcopy(resolved["sources"]),
        "records": [
            {
                "identity": deepcopy(record["identity"]),
                "rule_id": record["rule_id"],
                "source_id": record["source_id"],
                "rules_review": deepcopy(record["rules_review"]),
            }
            for record in resolved["records"]
        ],
    }
    _require_pending_overlay_reviews(normalized_authoring)
    reconciled = _reconcile_class_corpus(
        resolved,
        corpus,
        fixture_mode=fixture_mode,
    )
    applied = _apply_class_review_overlay(
        reconciled["_base_class_ledger"],
        reconciled,
        fixture_mode=fixture_mode,
    )
    records = [
        {
            field: deepcopy(record[field])
            for field in (
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
            )
        }
        for record in reconciled["records"]
    ]
    files = {
        "authoring.json": canonical_json(normalized_authoring),
        "sources.json": canonical_json(normalized_authoring["sources"]),
        "records.json": canonical_json(records),
    }
    summary = applied["summary"]
    manifest = {
        **deepcopy(normalized_authoring["manifest"]),
        "compiler_version": CLASS_REVIEW_COMPILER_VERSION,
        "inputs": {"authoring.json": digest(files["authoring.json"])},
        "outputs": {
            name: digest(files[name])
            for name in ("sources.json", "records.json")
        },
        "counts": {
            "sources": len(normalized_authoring["sources"]),
            "records": len(records),
            "local_records": reconciled["counts"]["local_class_records"],
            "reconciliation": {
                "aligned": reconciled["counts"]["aligned_records"],
                "source-drift": reconciled["counts"]["source_drift_records"],
                "missing-local": reconciled["counts"]["missing_local_records"],
            },
            "effective_dispositions": deepcopy(
                summary["effective_dispositions"]
            ),
            "base_reviews": deepcopy(summary["base_reviews"]),
            "overlay_gates": deepcopy(summary["overlay_gates"]),
            "enabled_mechanics": summary["enabled_mechanics"],
        },
    }
    manifest["overlay_hash"] = digest(canonical_json(manifest))
    files["manifest.json"] = canonical_json(manifest)
    require(set(files) == CLASS_REVIEW_FILES, "invalid_package_layout", "$files")
    return files


def compile_class_review_overlay(
    authoring: dict,
    snapshot_manifest_path: Path,
    corpus_root: Path,
) -> dict[str, bytes]:
    """Compile the frozen production class-review overlay."""
    return _compile_class_review_overlay(
        authoring,
        snapshot_manifest_path,
        corpus_root,
    )


def _resolved_tree(path: Path, field: str) -> Path:
    candidate = Path(path).absolute()
    _portable_path_components(candidate)
    try:
        reject_links(candidate)
    except (OSError, RulesValidationError):
        raise RulesValidationError("invalid_package_layout", field) from None
    return candidate.resolve(strict=False)


def _portable_path_components(path: Path) -> tuple[str, ...]:
    parts = path.parts
    require(
        0 < len(parts) <= MAX_DISJOINT_PATH_COMPONENTS,
        "path_overlap",
        "$.paths",
    )
    return tuple(
        unicodedata.normalize(
            "NFC", unicodedata.normalize("NFC", part).casefold()
        )
        for part in parts
    )


def _component_paths_overlap(left: Path, right: Path) -> bool:
    left_parts = _portable_path_components(left)
    right_parts = _portable_path_components(right)
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def _existing_identity_suffixes(
    path: Path,
) -> dict[tuple[int, int], tuple[tuple[str, ...], ...]]:
    """Return bounded existing-object identities and normalized child suffixes."""
    _portable_path_components(path)
    current = Path(path)
    suffix: tuple[str, ...] = ()
    identities: dict[tuple[int, int], list[tuple[str, ...]]] = {}
    for _scan in range(MAX_DISJOINT_PATH_COMPONENTS):
        try:
            info = current.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise RulesValidationError("path_overlap", "$.paths") from None
        else:
            require(
                not stat.S_ISLNK(info.st_mode)
                and not (
                    getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                ),
                "path_overlap",
                "$.paths",
            )
            identities.setdefault(_file_identity(info), []).append(suffix)
        parent = current.parent
        if parent == current:
            return {
                identity: tuple(suffixes)
                for identity, suffixes in identities.items()
            }
        suffix = (
            unicodedata.normalize(
                "NFC",
                unicodedata.normalize("NFC", current.name).casefold(),
            ),
            *suffix,
        )
        current = parent
    raise RulesValidationError("path_overlap", "$.paths")


def _identity_paths_overlap(
    left: dict[tuple[int, int], tuple[tuple[str, ...], ...]],
    right: dict[tuple[int, int], tuple[tuple[str, ...], ...]],
) -> bool:
    for identity in left.keys() & right.keys():
        for left_suffix in left[identity]:
            for right_suffix in right[identity]:
                common = min(len(left_suffix), len(right_suffix))
                if left_suffix[:common] == right_suffix[:common]:
                    return True
    return False


def _require_disjoint_trees(paths: tuple[tuple[str, Path], ...]) -> None:
    resolved = [
        (
            field,
            Path(path).absolute(),
            _resolved_tree(path, field),
        )
        for field, path in paths
    ]
    identity_suffixes = [
        _existing_identity_suffixes(resolved_path)
        for _field, _absolute, resolved_path in resolved
    ]
    for index, (_left_field, left_absolute, left) in enumerate(resolved):
        for right_index in range(index + 1, len(resolved)):
            _right_field, right_absolute, right = resolved[right_index]
            require(
                left != right
                and left not in right.parents
                and right not in left.parents
                and left_absolute != right_absolute
                and left_absolute not in right_absolute.parents
                and right_absolute not in left_absolute.parents
                and not _component_paths_overlap(left_absolute, right_absolute)
                and not _component_paths_overlap(left, right)
                and not _identity_paths_overlap(
                    identity_suffixes[index], identity_suffixes[right_index]
                ),
                "path_overlap",
                "$.paths",
            )


def _open_overlay_file(path: Path):
    return path.open("xb")


def _write_overlay_bytes(handle, data: bytes) -> int:
    return handle.write(data)


def _flush_overlay_file(handle) -> None:
    handle.flush()


def _fsync_overlay_file(handle) -> None:
    os.fsync(handle.fileno())


def _write_lock_nonce(handle, nonce: bytes) -> None:
    """Write the complete ownership marker or leave cleanup fail-closed."""
    remaining = memoryview(nonce)
    while remaining:
        written = handle.write(remaining)
        require(
            type(written) is int and 0 < written <= len(remaining),
            "io_error",
            "$.paths.lock",
        )
        remaining = remaining[written:]


def _rename_path_no_replace(source: Path, target: Path) -> None:
    """Atomically move one path without replacing an existing target."""
    if os.name == "nt":
        os.rename(source, target)
        return

    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename_no_replace = getattr(library, "renameat2", None)
        require(
            rename_no_replace is not None,
            "unsupported_platform",
            "$.paths.store",
        )
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(
            -100,
            source_bytes,
            -100,
            target_bytes,
            1,
        )
    elif sys.platform == "darwin":
        rename_no_replace = getattr(library, "renamex_np", None)
        require(
            rename_no_replace is not None,
            "unsupported_platform",
            "$.paths.store",
        )
        rename_no_replace.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(source_bytes, target_bytes, 4)
    else:
        raise RulesValidationError("unsupported_platform", "$.paths.store")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), target)
    raise OSError(error_number, os.strerror(error_number), target)


def _rename_overlay_directory(staging: Path, target: Path) -> None:
    """Atomically publish a directory without replacing an existing target."""
    _rename_path_no_replace(staging, target)


def _file_identity(info) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _owned_path_info(path: Path, identity: tuple[int, int]):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        _file_identity(info) != identity
        or stat.S_ISLNK(info.st_mode)
        or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
        )
    ):
        return None
    return info


def _owned_file_matches(
    path: Path, identity: tuple[int, int], expected_payload: bytes
) -> bool:
    try:
        info = _owned_path_info(path, identity)
        if (
            info is None
            or not stat.S_ISREG(info.st_mode)
            or info.st_size != len(expected_payload)
        ):
            return False
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                _file_identity(opened) != identity
                or not stat.S_ISREG(opened.st_mode)
                or (
                    getattr(opened, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                )
            ):
                return False
            payload = handle.read(len(expected_payload) + 1)
    except OSError:
        return False
    return secrets.compare_digest(payload, expected_payload)


def _unlink_owned_file(
    path: Path, identity: tuple[int, int], expected_payload: bytes
) -> None:
    if not _owned_file_matches(path, identity, expected_payload):
        return
    claimed = _claim_owned_path(path, identity, stat.S_ISREG)
    if claimed is None:
        return
    if (
        _owned_file_matches(claimed, identity, expected_payload)
        and _owned_path_matches_for_cleanup(claimed, identity, stat.S_ISREG)
        and _owned_file_matches(claimed, identity, expected_payload)
    ):
        claimed.unlink()
        return
    _restore_unowned_claim(claimed, path)


def _remove_owned_tree(path: Path, identity: tuple[int, int]) -> None:
    claimed = _claim_owned_path(path, identity, stat.S_ISDIR)
    if claimed is not None:
        info = _owned_path_info(claimed, identity)
        if (
            info is not None
            and stat.S_ISDIR(info.st_mode)
            and _owned_path_matches_for_cleanup(
                claimed, identity, stat.S_ISDIR
            )
        ):
            shutil.rmtree(claimed)


def _private_sibling(path: Path, purpose: str) -> Path:
    return path.with_name(f".{purpose}-{secrets.token_hex(16)}")


def _owned_path_matches_for_cleanup(
    path: Path, identity: tuple[int, int], kind
) -> bool:
    """Make the final portable pathname check before best-effort cleanup.

    Python exposes no portable unlink/rmdir operation conditional on a prior
    ``(st_dev, st_ino)`` observation. Lock cleanup additionally checks its
    random nonce before and after the private claim so inode reuse cannot make
    a replacement look owned. The private claim and repeated checks protect
    normal writers and every swap observed before this check, but they cannot
    bind the later pathname deletion against an active same-account actor.
    Review stores are trusted against that actor.
    """
    try:
        current = _owned_path_info(path, identity)
        return bool(
            current is not None
            and kind(current.st_mode)
        )
    except OSError:
        return False


def _restore_unowned_claim(claimed: Path, original: Path) -> None:
    try:
        _rename_path_no_replace(claimed, original)
    except (FileExistsError, FileNotFoundError, OSError):
        return


def _claim_owned_path(path: Path, identity: tuple[int, int], kind) -> Path | None:
    """Privately claim a match and preserve every replacement observed here."""
    info = _owned_path_info(path, identity)
    if info is None or not kind(info.st_mode):
        return None
    for _attempt in range(16):
        claimed = _private_sibling(path, "cleanup")
        try:
            _rename_path_no_replace(path, claimed)
        except FileExistsError:
            continue
        except FileNotFoundError:
            return None
        moved = _owned_path_info(claimed, identity)
        if moved is not None and kind(moved.st_mode):
            return claimed
        _restore_unowned_claim(claimed, path)
        return None
    raise RulesValidationError("io_error", "$.paths.store")


def _quarantine_rejected_publication(target: Path) -> None:
    """Move an observed rejected publication off its reserved target name.

    This fail-closed quarantine assumes the review store is trusted against an
    active same-account actor that can replace random private paths between the
    final observation and a pathname operation.
    """
    for _attempt in range(16):
        rejected = _private_sibling(target, "rejected-publication")
        try:
            _rename_path_no_replace(target, rejected)
            require(
                not os.path.lexists(target),
                "quarantine_failed",
                "$.manifest.overlay_id",
            )
            return
        except FileExistsError:
            continue
        except FileNotFoundError:
            return
        except OSError:
            raise RulesValidationError(
                "quarantine_failed", "$.manifest.overlay_id"
            ) from None
    raise RulesValidationError("quarantine_failed", "$.manifest.overlay_id")


def _write_class_review_overlay(
    authoring: dict,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    store_root: Path,
    *,
    fixture_mode: bool = False,
) -> Path:
    """Create-only publish one overlay atomically to observing processes.

    File contents are flushed and fsynced before publication. Directory-entry
    durability across power loss remains platform-dependent because Python has
    no clean cross-platform directory-fsync contract.
    """
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    manifest_path = Path(snapshot_manifest_path).absolute()
    corpus = Path(corpus_root).absolute()
    store = Path(store_root).absolute()
    _require_disjoint_trees(
        (
            ("$.paths.snapshot", manifest_path.parent),
            ("$.paths.corpus", corpus),
            ("$.paths.store", store),
        )
    )
    files = _compile_class_review_overlay(
        authoring,
        manifest_path,
        corpus,
        fixture_mode=fixture_mode,
    )
    manifest = read_json(files["manifest.json"])
    overlay_id = manifest["overlay_id"]

    try:
        store.mkdir(parents=True, exist_ok=True)
        reject_links(store)
        require(store.is_dir(), "invalid_package_layout", "$.paths.store")
        _require_disjoint_trees(
            (
                ("$.paths.snapshot", manifest_path.parent),
                ("$.paths.corpus", corpus),
                ("$.paths.store", store),
            )
        )
    except RulesValidationError:
        raise
    except OSError:
        raise RulesValidationError(
            "invalid_package_layout", "$.paths.store"
        ) from None

    target = store / overlay_id
    require(
        not os.path.lexists(target),
        "overlay_exists",
        "$.manifest.overlay_id",
    )
    lock = store / f".{overlay_id}.lock"
    lock_nonce = secrets.token_bytes(LOCK_NONCE_BYTES)
    try:
        lock_handle = lock.open("xb", buffering=0)
    except FileExistsError:
        raise RulesValidationError(
            "publication_locked", "$.manifest.overlay_id"
        ) from None

    staging = None
    staging_identity = None
    lock_identity = _file_identity(os.fstat(lock_handle.fileno()))
    lock_initialized = False
    try:
        with lock_handle:
            _write_lock_nonce(lock_handle, lock_nonce)
            lock_initialized = True
            require(
                not os.path.lexists(target),
                "overlay_exists",
                "$.manifest.overlay_id",
            )
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{overlay_id}.",
                    suffix=".tmp",
                    dir=store,
                )
            )
            staging_identity = _file_identity(staging.lstat())
            for name in sorted(CLASS_REVIEW_FILES):
                data = files[name]
                with _open_overlay_file(staging / name) as output:
                    written = _write_overlay_bytes(output, data)
                    require(written == len(data), "io_error", f"$.files.{name}")
                    _flush_overlay_file(output)
                    _fsync_overlay_file(output)
            staged_files = _read_class_review_overlay_files(staging)
            for name in sorted(CLASS_REVIEW_FILES):
                require(
                    staged_files[name] == files[name],
                    "integrity_mismatch",
                    f"$.files.{name}",
                )
            require(
                not os.path.lexists(target),
                "overlay_exists",
                "$.manifest.overlay_id",
            )
            staged_info = _owned_path_info(staging, staging_identity)
            require(
                staged_info is not None and stat.S_ISDIR(staged_info.st_mode),
                "integrity_mismatch",
                "$.paths.staging",
            )
            try:
                _rename_overlay_directory(staging, target)
            except FileExistsError:
                raise RulesValidationError(
                    "overlay_exists", "$.manifest.overlay_id"
                ) from None
            try:
                published_info = _owned_path_info(target, staging_identity)
                require(
                    published_info is not None
                    and stat.S_ISDIR(published_info.st_mode),
                    "integrity_mismatch",
                    "$.paths.staging",
                )
                published_files = _read_class_review_overlay_files(target)
                for name in sorted(CLASS_REVIEW_FILES):
                    require(
                        published_files[name] == files[name],
                        "integrity_mismatch",
                        f"$.files.{name}",
                    )
                published_info = _owned_path_info(target, staging_identity)
                require(
                    published_info is not None
                    and stat.S_ISDIR(published_info.st_mode),
                    "integrity_mismatch",
                    "$.paths.staging",
                )
            except (OSError, RulesValidationError):
                _quarantine_rejected_publication(target)
                raise
            staging = None
    finally:
        try:
            if staging is not None and staging_identity is not None:
                _remove_owned_tree(staging, staging_identity)
        finally:
            if lock_initialized:
                _unlink_owned_file(lock, lock_identity, lock_nonce)
    return target


def write_class_review_overlay(
    authoring: dict,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    store_root: Path,
) -> Path:
    """Publish the frozen production class-review overlay exactly once."""
    return _write_class_review_overlay(
        authoring,
        snapshot_manifest_path,
        corpus_root,
        store_root,
    )


def _read_class_review_overlay_files(overlay_path: Path) -> dict[str, bytes]:
    root = Path(overlay_path).absolute()
    try:
        reject_links(root)
        root_info = root.lstat()
    except (OSError, RulesValidationError):
        raise RulesValidationError("invalid_package_layout", "$files") from None
    require(stat.S_ISDIR(root_info.st_mode), "invalid_package_layout", "$files")

    entries = {}
    casefolded = set()
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                require(len(entries) < len(CLASS_REVIEW_FILES),
                        "invalid_package_layout", "$files")
                name = entry.name
                folded = name.casefold()
                require(folded not in casefolded,
                        "invalid_package_layout", "$files")
                casefolded.add(folded)
                info = entry.stat(follow_symlinks=False)
                require(
                    not stat.S_ISLNK(info.st_mode)
                    and not (
                        getattr(info, "st_file_attributes", 0)
                        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)
                    )
                    and stat.S_ISREG(info.st_mode),
                    "invalid_package_layout",
                    "$files",
                )
                entries[name] = Path(entry.path)
    except RulesValidationError:
        raise
    except OSError:
        raise RulesValidationError("invalid_package_layout", "$files") from None
    require(set(entries) == CLASS_REVIEW_FILES,
            "invalid_package_layout", "$files")

    files = {}
    for name in sorted(CLASS_REVIEW_FILES):
        path = f"$.files.{name}"
        try:
            data = read_file(entries[name])
            document = read_json(data)
            canonical = canonical_json(document)
        except RulesValidationError as error:
            if error.code == "invalid_package_layout":
                raise RulesValidationError(error.code, "$files") from None
            raise RulesValidationError(error.code, path) from None
        require(data == canonical, "noncanonical_bytes", path)
        files[name] = data
    return files


def _verify_compiled_manifest_hashes(
    manifest: dict, files: dict[str, bytes]
) -> None:
    shape(
        manifest,
        "schema_version overlay_id created_at authority kind snapshot_id "
        "snapshot_manifest_sha256 base_category activation parent_overlay_id "
        "parent_overlay_hash compiler_version inputs outputs counts overlay_hash",
        "$.manifest",
    )
    shape(manifest["inputs"], "authoring.json", "$.manifest.inputs")
    shape(
        manifest["outputs"],
        "sources.json records.json",
        "$.manifest.outputs",
    )
    for name in ("authoring.json", "sources.json", "records.json"):
        field = (
            manifest["inputs"][name]
            if name == "authoring.json"
            else manifest["outputs"][name]
        )
        _sha256(field, f"$.manifest.files.{name}")
        require(
            digest(files[name]) == field,
            "integrity_mismatch",
            f"$.files.{name}",
        )
    _sha256(manifest["overlay_hash"], "$.manifest.overlay_hash")
    without_hash = deepcopy(manifest)
    overlay_hash = without_hash.pop("overlay_hash")
    require(
        digest(canonical_json(without_hash)) == overlay_hash,
        "integrity_mismatch",
        "$.files.manifest.json",
    )


def _verify_class_review_overlay(
    overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    expected_hash: str | None = None,
    *,
    fixture_mode: bool = False,
) -> dict:
    """Verify exact package bytes against freshly resolved frozen inputs."""
    require(type(fixture_mode) is bool, "invalid_type", "$.fixture_mode")
    root = Path(overlay_path).absolute()
    _require_disjoint_trees(
        (
            ("$.paths.snapshot", Path(snapshot_manifest_path).absolute().parent),
            ("$.paths.corpus", Path(corpus_root).absolute()),
            ("$.paths.store", root.parent),
        )
    )
    files = _read_class_review_overlay_files(root)
    authoring = read_json(files["authoring.json"])
    manifest = read_json(files["manifest.json"])
    require(type(manifest) is dict, "invalid_type", "$.manifest")
    _verify_compiled_manifest_hashes(manifest, files)
    require("overlay_id" in manifest,
            "missing_field", "$.manifest.overlay_id")
    text(manifest["overlay_id"], "$.manifest.overlay_id", pattern=OVERLAY_ID)
    require(root.name == manifest["overlay_id"],
            "invalid_package_layout", "$.manifest.overlay_id")

    expected_files = _compile_class_review_overlay(
        authoring,
        snapshot_manifest_path,
        corpus_root,
        fixture_mode=fixture_mode,
    )
    for name in ("authoring.json", "sources.json", "records.json", "manifest.json"):
        require(
            files[name] == expected_files[name],
            "integrity_mismatch",
            f"$.files.{name}",
        )
    manifest = read_json(expected_files["manifest.json"])
    if expected_hash is not None:
        text(
            expected_hash,
            "$.binding.overlay_hash",
            pattern=SHA256,
        )
        require(
            expected_hash == manifest["overlay_hash"],
            "binding_mismatch",
            "$.binding.overlay_hash",
        )
    return {
        "overlay_id": manifest["overlay_id"],
        "overlay_hash": manifest["overlay_hash"],
        "activation": manifest["activation"],
        "counts": deepcopy(manifest["counts"]),
    }


def verify_class_review_overlay(
    overlay_path: Path,
    snapshot_manifest_path: Path,
    corpus_root: Path,
    expected_hash: str | None = None,
) -> dict:
    """Verify one frozen production class-review overlay package."""
    return _verify_class_review_overlay(
        overlay_path,
        snapshot_manifest_path,
        corpus_root,
        expected_hash,
    )
