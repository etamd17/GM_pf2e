"""Offline integrity verification for sharded PF2e evidence snapshots."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
import re

from .evidence_inventory import (
    AON_AUTHORITY,
    AON_HOST,
    EVIDENCE_KINDS,
    PAGE_FAMILY,
    audit_evidence_inventory,
    normalize_aon_census,
    normalize_evidence_ledger,
)
from ..manifest import canonical_json, digest, read_file, read_json
from ..validation import (
    RulesValidationError,
    bounded_json,
    choice,
    integer,
    iso_date,
    require,
    sequence,
    shape,
    strings,
    text,
    timestamp,
)


SNAPSHOT_SCHEMA_VERSION = 1
SHA256 = re.compile(r"[0-9a-f]{64}")
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SNAPSHOT_ID = r"pf2e-aon-[a-z0-9]+(?:-[a-z0-9]+)*"
SCOPE_ID = r"pf2e-[a-z0-9]+(?:-[a-z0-9]+)*"
INDEX_ID = r"aon-[0-9]{8}-[0-9]{6}"
MAX_SNAPSHOT_CATEGORIES = 200
MAX_CATEGORY_RECORDS = 9_999
WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
})


def _sha256(value, path: str) -> str:
    text(value, path)
    require(SHA256.fullmatch(value) is not None, "invalid_value", path)
    return value


def _relative_json_path(value, path: str) -> str:
    text(value, path)
    require("\\" not in value and ":" not in value,
            "invalid_package_layout", path)
    relative = PurePosixPath(value)
    safe_parts = all(
        part not in {"", ".", ".."}
        and not part.endswith((" ", "."))
        and part.split(".", 1)[0].upper() not in WINDOWS_RESERVED
        for part in relative.parts
    )
    require(
        not relative.is_absolute()
        and value == relative.as_posix()
        and bool(relative.parts)
        and safe_parts
        and relative.suffix == ".json",
        "invalid_package_layout",
        path,
    )
    return value


def _artifact_ref(value, path: str) -> dict:
    shape(value, "path sha256", path)
    _relative_json_path(value["path"], path + ".path")
    _sha256(value["sha256"], path + ".sha256")
    return value


def _capture(value, path: str) -> dict:
    shape(value, "run_id captured_at query_sha256 result_sha256 receipt", path)
    text(value["run_id"], path + ".run_id", pattern=SLUG)
    timestamp(value["captured_at"], path + ".captured_at")
    _sha256(value["query_sha256"], path + ".query_sha256")
    _sha256(value["result_sha256"], path + ".result_sha256")
    _artifact_ref(value["receipt"], path + ".receipt")
    return value


def _snapshot_shard(value, path: str) -> dict:
    shape(
        value,
        "category kind inventory_id expected_records page_families census ledger",
        path,
    )
    text(value["category"], path + ".category", pattern=SLUG)
    choice(value["kind"], EVIDENCE_KINDS, path + ".kind")
    text(value["inventory_id"], path + ".inventory_id",
         pattern=r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*")
    integer(value["expected_records"], 1, MAX_CATEGORY_RECORDS,
            path + ".expected_records")
    families = strings(value["page_families"], path + ".page_families",
                       pattern=PAGE_FAMILY)
    require(bool(families), "invalid_value", path + ".page_families")
    value["page_families"] = families
    _artifact_ref(value["census"], path + ".census")
    _artifact_ref(value["ledger"], path + ".ledger")
    return value


def normalize_snapshot_manifest(document: dict) -> dict:
    """Validate and detach one sharded evidence-snapshot manifest."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version snapshot_id lifecycle scope_id authority site_update_date "
        "site_update_url search_endpoint resolved_index scope_policy scope_capture "
        "census_capture ledger_enumeration total_index_records "
        "included_records deferred_records excluded_records class_roster shards",
        "$",
    )
    require(type(value["schema_version"]) is int, "invalid_type", "$.schema_version")
    require(value["schema_version"] == SNAPSHOT_SCHEMA_VERSION,
            "unsupported_version", "$.schema_version")
    text(value["snapshot_id"], "$.snapshot_id", pattern=SNAPSHOT_ID)
    choice(value["lifecycle"], {"initial-pending"}, "$.lifecycle")
    text(value["scope_id"], "$.scope_id", pattern=SCOPE_ID)
    choice(value["authority"], {AON_AUTHORITY}, "$.authority")
    iso_date(value["site_update_date"], "$.site_update_date")
    require(value["site_update_url"] == f"https://{AON_HOST}/",
            "invalid_url", "$.site_update_url")
    require(value["search_endpoint"] ==
            "https://elasticsearch.aonprd.com/aon/_search",
            "invalid_url", "$.search_endpoint")
    text(value["resolved_index"], "$.resolved_index", pattern=INDEX_ID)
    _artifact_ref(value["scope_policy"], "$.scope_policy")
    _capture(value["scope_capture"], "$.scope_capture")
    _capture(value["census_capture"], "$.census_capture")
    _capture(value["ledger_enumeration"], "$.ledger_enumeration")
    captures = ("scope_capture", "census_capture", "ledger_enumeration")
    for field in ("run_id", "query_sha256", "result_sha256", "captured_at"):
        seen = set()
        for label in captures:
            require(value[label][field] not in seen,
                    "capture_not_independent", f"$.{label}.{field}")
            seen.add(value[label][field])
    integer(value["total_index_records"], 1, 100_000, "$.total_index_records")
    integer(value["included_records"], 1, 100_000, "$.included_records")
    integer(value["deferred_records"], 0, 100_000, "$.deferred_records")
    integer(value["excluded_records"], 0, 100_000, "$.excluded_records")
    require(value["included_records"] + value["deferred_records"]
            + value["excluded_records"]
            == value["total_index_records"], "count_mismatch", "$.total_index_records")
    value["class_roster"] = strings(value["class_roster"], "$.class_roster")
    require(bool(value["class_roster"]), "invalid_value", "$.class_roster")

    shards = sequence(value["shards"], "$.shards")
    require(0 < len(shards) <= MAX_SNAPSHOT_CATEGORIES,
            "limit_exceeded", "$.shards")
    categories, inventory_ids = set(), set()
    paths = {
        value["scope_policy"]["path"].casefold(),
        value["scope_capture"]["receipt"]["path"].casefold(),
        value["census_capture"]["receipt"]["path"].casefold(),
        value["ledger_enumeration"]["receipt"]["path"].casefold(),
    }
    require(len(paths) == 4, "duplicate_id", "$.ledger_enumeration.receipt.path")
    for index, shard in enumerate(shards):
        shard_path = f"$.shards[{index}]"
        _snapshot_shard(shard, shard_path)
        require(shard["category"] not in categories,
                "duplicate_id", shard_path + ".category")
        categories.add(shard["category"])
        require(shard["inventory_id"] not in inventory_ids,
                "duplicate_id", shard_path + ".inventory_id")
        inventory_ids.add(shard["inventory_id"])
        for artifact in ("census", "ledger"):
            artifact_path = shard[artifact]["path"].casefold()
            require(artifact_path not in paths, "duplicate_id",
                    shard_path + f".{artifact}.path")
            paths.add(artifact_path)
    shards.sort(key=lambda item: item["category"])
    return value


def _normalize_scope_categories(categories, total_records: int, path: str) -> list[dict]:
    categories = sequence(categories, path + ".categories")
    require(0 < len(categories) <= MAX_SNAPSHOT_CATEGORIES,
            "limit_exceeded", path + ".categories")
    seen = set()
    for index, category in enumerate(categories):
        category_path = f"{path}.categories[{index}]"
        shape(
            category,
            "name observed_records included_records deferred_records "
            "excluded_records reason",
            category_path,
        )
        text(category["name"], category_path + ".name", pattern=SLUG)
        integer(category["observed_records"], 1, MAX_CATEGORY_RECORDS,
                category_path + ".observed_records")
        for field in ("included_records", "deferred_records", "excluded_records"):
            integer(category[field], 0, MAX_CATEGORY_RECORDS,
                    category_path + "." + field)
        require(
            category["included_records"] + category["deferred_records"]
            + category["excluded_records"] == category["observed_records"],
            "count_mismatch",
            category_path + ".observed_records",
        )
        text(category["reason"], category_path + ".reason")
        require(category["name"] not in seen,
                "duplicate_id", category_path + ".name")
        seen.add(category["name"])
    categories.sort(key=lambda item: item["name"])
    require(sum(item["observed_records"] for item in categories)
            == total_records, "count_mismatch", path + ".total_records")
    return categories


def normalize_scope_policy(document: dict) -> dict:
    """Validate and detach an exhaustive AoN category scope policy."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version scope_id authority resolved_index site_update_date "
        "total_records categories",
        "$",
    )
    require(type(value["schema_version"]) is int, "invalid_type", "$.schema_version")
    require(value["schema_version"] == SNAPSHOT_SCHEMA_VERSION,
            "unsupported_version", "$.schema_version")
    text(value["scope_id"], "$.scope_id", pattern=SCOPE_ID)
    choice(value["authority"], {AON_AUTHORITY}, "$.authority")
    text(value["resolved_index"], "$.resolved_index", pattern=INDEX_ID)
    iso_date(value["site_update_date"], "$.site_update_date")
    integer(value["total_records"], 1, 100_000, "$.total_records")
    value["categories"] = _normalize_scope_categories(
        value["categories"], value["total_records"], "$"
    )
    return value


def normalize_scope_receipt(document: dict, path: str = "$") -> dict:
    """Validate the independently captured full-index category facet."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version run_id captured_at scope_id authority site_update_date "
        "search_endpoint resolved_index query_sha256 response_sha256 "
        "reported_records returned_records categories",
        path,
    )
    require(type(value["schema_version"]) is int,
            "invalid_type", path + ".schema_version")
    require(value["schema_version"] == SNAPSHOT_SCHEMA_VERSION,
            "unsupported_version", path + ".schema_version")
    text(value["run_id"], path + ".run_id", pattern=SLUG)
    timestamp(value["captured_at"], path + ".captured_at")
    text(value["scope_id"], path + ".scope_id", pattern=SCOPE_ID)
    choice(value["authority"], {AON_AUTHORITY}, path + ".authority")
    iso_date(value["site_update_date"], path + ".site_update_date")
    require(value["search_endpoint"] ==
            "https://elasticsearch.aonprd.com/aon/_search",
            "invalid_url", path + ".search_endpoint")
    text(value["resolved_index"], path + ".resolved_index", pattern=INDEX_ID)
    _sha256(value["query_sha256"], path + ".query_sha256")
    _sha256(value["response_sha256"], path + ".response_sha256")
    integer(value["reported_records"], 1, 100_000, path + ".reported_records")
    integer(value["returned_records"], 1, 100_000, path + ".returned_records")
    require(value["returned_records"] == value["reported_records"],
            "count_mismatch", path + ".returned_records")
    value["categories"] = _normalize_scope_categories(
        value["categories"], value["returned_records"], path
    )
    return value


def normalize_capture_receipt(document: dict, path: str = "$") -> dict:
    """Validate one capture receipt that binds a run to its shard artifacts."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version mode run_id captured_at scope_id authority "
        "site_update_date search_endpoint resolved_index query_set_sha256 "
        "result_set_sha256 artifacts",
        path,
    )
    require(type(value["schema_version"]) is int,
            "invalid_type", path + ".schema_version")
    require(value["schema_version"] == SNAPSHOT_SCHEMA_VERSION,
            "unsupported_version", path + ".schema_version")
    choice(value["mode"], {"census", "ledger"}, path + ".mode")
    text(value["run_id"], path + ".run_id", pattern=SLUG)
    timestamp(value["captured_at"], path + ".captured_at")
    text(value["scope_id"], path + ".scope_id", pattern=SCOPE_ID)
    choice(value["authority"], {AON_AUTHORITY}, path + ".authority")
    iso_date(value["site_update_date"], path + ".site_update_date")
    require(value["search_endpoint"] ==
            "https://elasticsearch.aonprd.com/aon/_search",
            "invalid_url", path + ".search_endpoint")
    text(value["resolved_index"], path + ".resolved_index", pattern=INDEX_ID)
    _sha256(value["query_set_sha256"], path + ".query_set_sha256")
    _sha256(value["result_set_sha256"], path + ".result_set_sha256")
    artifacts = sequence(value["artifacts"], path + ".artifacts")
    require(0 < len(artifacts) <= MAX_SNAPSHOT_CATEGORIES,
            "limit_exceeded", path + ".artifacts")
    categories, paths = set(), set()
    for index, artifact in enumerate(artifacts):
        artifact_path = f"{path}.artifacts[{index}]"
        shape(
            artifact,
            "category path sha256 reported_records returned_records "
            "query_sha256 response_sha256",
            artifact_path,
        )
        text(artifact["category"], artifact_path + ".category", pattern=SLUG)
        _relative_json_path(artifact["path"], artifact_path + ".path")
        _sha256(artifact["sha256"], artifact_path + ".sha256")
        integer(artifact["reported_records"], 1, MAX_CATEGORY_RECORDS,
                artifact_path + ".reported_records")
        integer(artifact["returned_records"], 0, MAX_CATEGORY_RECORDS,
                artifact_path + ".returned_records")
        require(artifact["returned_records"] == artifact["reported_records"],
                "count_mismatch", artifact_path + ".returned_records")
        _sha256(artifact["query_sha256"], artifact_path + ".query_sha256")
        _sha256(artifact["response_sha256"], artifact_path + ".response_sha256")
        require(artifact["category"] not in categories,
                "duplicate_id", artifact_path + ".category")
        categories.add(artifact["category"])
        normalized_path = artifact["path"].casefold()
        require(normalized_path not in paths,
                "duplicate_id", artifact_path + ".path")
        paths.add(normalized_path)
    artifacts.sort(key=lambda item: item["category"])
    query_projection = [
        {"category": item["category"], "sha256": item["query_sha256"]}
        for item in artifacts
    ]
    result_projection = [
        {"category": item["category"], "sha256": item["response_sha256"]}
        for item in artifacts
    ]
    require(digest(canonical_json(query_projection)) == value["query_set_sha256"],
            "hash_mismatch", path + ".query_set_sha256")
    require(digest(canonical_json(result_projection)) == value["result_set_sha256"],
            "hash_mismatch", path + ".result_set_sha256")
    return value


def _read_artifact(root: Path, reference: dict, path: str) -> bytes:
    candidate = root.joinpath(*PurePosixPath(reference["path"]).parts)
    try:
        data = read_file(candidate)
    except RulesValidationError as error:
        if error.code == "invalid_package_layout":
            raise RulesValidationError("invalid_package_layout", path + ".path") from None
        raise
    require(digest(data) == reference["sha256"], "hash_mismatch", path + ".sha256")
    return data


def _verify_capture_receipt(root: Path, label: str, mode: str, capture: dict,
                            manifest: dict, shards: list[dict]) -> dict:
    path = f"$.{label}.receipt"
    receipt = normalize_capture_receipt(
        read_json(_read_artifact(root, capture["receipt"], path)), path
    )
    require(receipt["mode"] == mode, "capture_mismatch", path + ".mode")
    for field in ("run_id", "captured_at"):
        require(receipt[field] == capture[field], "capture_mismatch", path + "." + field)
    require(receipt["query_set_sha256"] == capture["query_sha256"],
            "capture_mismatch", path + ".query_set_sha256")
    require(receipt["result_set_sha256"] == capture["result_sha256"],
            "capture_mismatch", path + ".result_set_sha256")
    for field in (
        "scope_id", "authority", "site_update_date", "search_endpoint", "resolved_index"
    ):
        require(receipt[field] == manifest[field],
                "capture_mismatch", path + "." + field)

    receipt_by_category = {
        artifact["category"]: (index, artifact)
        for index, artifact in enumerate(receipt["artifacts"])
    }
    require(receipt_by_category.keys() == {shard["category"] for shard in shards},
            "capture_mismatch", path + ".artifacts")
    artifact_key = "census" if mode == "census" else "ledger"
    for shard in shards:
        index, artifact = receipt_by_category[shard["category"]]
        artifact_path = f"{path}.artifacts[{index}]"
        expected = shard[artifact_key]
        require(artifact["path"] == expected["path"],
                "capture_mismatch", artifact_path + ".path")
        require(artifact["sha256"] == expected["sha256"],
                "capture_mismatch", artifact_path + ".sha256")
        require(artifact["reported_records"] == shard["expected_records"],
                "capture_mismatch", artifact_path + ".reported_records")
        require(artifact["returned_records"] == shard["expected_records"],
                "capture_mismatch", artifact_path + ".returned_records")
    return receipt


def _verify_scope_receipt(root: Path, capture: dict, manifest: dict) -> dict:
    path = "$.scope_capture.receipt"
    receipt = normalize_scope_receipt(
        read_json(_read_artifact(root, capture["receipt"], path)), path
    )
    for field in ("run_id", "captured_at"):
        require(receipt[field] == capture[field], "capture_mismatch", path + "." + field)
    require(receipt["query_sha256"] == capture["query_sha256"],
            "capture_mismatch", path + ".query_sha256")
    require(receipt["response_sha256"] == capture["result_sha256"],
            "capture_mismatch", path + ".response_sha256")
    for field in (
        "scope_id", "authority", "site_update_date", "search_endpoint", "resolved_index"
    ):
        require(receipt[field] == manifest[field],
                "capture_mismatch", path + "." + field)
    require(receipt["reported_records"] == manifest["total_index_records"],
            "capture_mismatch", path + ".reported_records")
    return receipt


def verify_evidence_snapshot(manifest_path: Path) -> dict:
    """Verify a complete sharded snapshot without network or application imports."""
    manifest_path = Path(manifest_path)
    manifest = normalize_snapshot_manifest(read_json(read_file(manifest_path)))
    root = manifest_path.parent
    scope_receipt = _verify_scope_receipt(root, manifest["scope_capture"], manifest)
    _verify_capture_receipt(
        root, "census_capture", "census", manifest["census_capture"],
        manifest, manifest["shards"],
    )
    _verify_capture_receipt(
        root, "ledger_enumeration", "ledger", manifest["ledger_enumeration"],
        manifest, manifest["shards"],
    )
    policy = normalize_scope_policy(read_json(_read_artifact(
        root, manifest["scope_policy"], "$.scope_policy"
    )))

    for field in ("scope_id", "authority", "resolved_index", "site_update_date"):
        require(policy[field] == manifest[field], "snapshot_mismatch",
                "$.scope_policy." + field)
    require(policy["total_records"] == manifest["total_index_records"],
            "count_mismatch", "$.total_index_records")
    require(policy["categories"] == scope_receipt["categories"],
            "capture_mismatch", "$.scope_capture.receipt.categories")

    policy_by_name = {item["name"]: item for item in policy["categories"]}
    included_policy = {
        name: item for name, item in policy_by_name.items()
        if item["included_records"] > 0
    }
    deferred_policy = {
        name: item for name, item in policy_by_name.items()
        if item["deferred_records"] > 0
    }
    excluded_policy = {
        name: item for name, item in policy_by_name.items()
        if item["excluded_records"] > 0
    }
    shard_by_category = {item["category"]: item for item in manifest["shards"]}
    require(shard_by_category.keys() == included_policy.keys(),
            "scope_mismatch", "$.shards")
    require(sum(item["included_records"] for item in policy_by_name.values())
            == manifest["included_records"], "count_mismatch", "$.included_records")
    require(sum(item["deferred_records"] for item in policy_by_name.values())
            == manifest["deferred_records"], "count_mismatch", "$.deferred_records")
    require(sum(item["excluded_records"] for item in policy_by_name.values())
            == manifest["excluded_records"], "count_mismatch", "$.excluded_records")

    disposition_counts = Counter()
    review_counts = Counter()
    kind_counts = Counter()
    page_family_counts = Counter()
    identities: set[tuple[str, int]] = set()
    class_names: list[str] = []
    included_records = 0

    for shard_index, shard in enumerate(manifest["shards"]):
        shard_path = f"$.shards[{shard_index}]"
        expected = included_policy[shard["category"]]["included_records"]
        require(shard["expected_records"] == expected,
                "count_mismatch", shard_path + ".expected_records")
        census = normalize_aon_census(read_json(_read_artifact(
            root, shard["census"], shard_path + ".census"
        )))
        ledger = normalize_evidence_ledger(read_json(_read_artifact(
            root, shard["ledger"], shard_path + ".ledger"
        )))
        require(census["captured_at"] == manifest["census_capture"]["captured_at"],
                "snapshot_mismatch", shard_path + ".census.captured_at")
        require(census["site_update_date"] == manifest["site_update_date"],
                "snapshot_mismatch", shard_path + ".census.site_update_date")
        require(ledger["census_captured_at"]
                == manifest["census_capture"]["captured_at"],
                "snapshot_mismatch", shard_path + ".ledger.census_captured_at")
        require(ledger["created_at"]
                == manifest["ledger_enumeration"]["captured_at"],
                "snapshot_mismatch", shard_path + ".ledger.created_at")
        require(ledger["inventory_id"] == shard["inventory_id"],
                "snapshot_mismatch", shard_path + ".ledger.inventory_id")
        report = audit_evidence_inventory(census, ledger)
        require(report["complete"], "incomplete_inventory", shard_path + ".ledger")
        require(len(census["records"]) == shard["expected_records"],
                "count_mismatch", shard_path + ".expected_records")
        families = sorted({record["identity"]["page_family"]
                           for record in census["records"]})
        require(families == shard["page_families"],
                "count_mismatch", shard_path + ".page_families")

        ledger_by_identity = {
            (entry["identity"]["page_family"], entry["identity"]["numeric_id"]):
                (index, entry)
            for index, entry in enumerate(ledger["entries"])
        }
        for record_index, record in enumerate(census["records"]):
            record_path = f"{shard_path}.census.records[{record_index}]"
            require(record["kind"] == shard["kind"],
                    "snapshot_mismatch", record_path + ".kind")
            identity = (record["identity"]["page_family"],
                        record["identity"]["numeric_id"])
            require(identity not in identities, "duplicate_identity",
                    record_path + ".identity")
            identities.add(identity)
            kind_counts[record["kind"]] += 1
            page_family_counts[identity[0]] += 1
            ledger_index, entry = ledger_by_identity[identity]
            require(
                entry["disposition"] == "pending"
                and entry["review"]["status"] == "pending",
                "lifecycle_mismatch",
                f"{shard_path}.ledger.entries[{ledger_index}]",
            )
            disposition_counts[entry["disposition"]] += 1
            review_counts[entry["review"]["status"]] += 1
            if shard["category"] == "class":
                class_names.append(record["name"])
        included_records += len(census["records"])

    require(included_records == manifest["included_records"],
            "count_mismatch", "$.included_records")
    require(sorted(class_names) == manifest["class_roster"],
            "roster_mismatch", "$.class_roster")
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": manifest["snapshot_id"],
        "scope_id": manifest["scope_id"],
        "resolved_index": manifest["resolved_index"],
        "site_update_date": manifest["site_update_date"],
        "complete": True,
        "categories": {
            "deferred": len(deferred_policy),
            "excluded": len(excluded_policy),
            "included": len(included_policy),
        },
        "records": {
            "deferred": manifest["deferred_records"],
            "excluded": manifest["excluded_records"],
            "included": included_records,
            "total": manifest["total_index_records"],
        },
        "dispositions": {
            key: disposition_counts[key] for key in ("excluded", "mapped", "pending")
        },
        "reviews": {key: review_counts[key] for key in ("pending", "reviewed")},
        "kinds": dict(sorted(kind_counts.items())),
        "page_families": dict(sorted(page_family_counts.items())),
        "class_count": len(class_names),
        "shard_count": len(manifest["shards"]),
    }
