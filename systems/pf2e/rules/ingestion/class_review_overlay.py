"""Strict, offline authoring contracts for PF2e class-review overlays."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import os
from pathlib import Path, PurePosixPath
import re
import stat

from ..manifest import MAX_FILE_BYTES, digest, read_file, read_json, reject_links
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
MAX_LOCAL_CLASS_FILES = 27
MAX_CLASS_CORPUS_BYTES = 32 * 1024 * 1024
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
