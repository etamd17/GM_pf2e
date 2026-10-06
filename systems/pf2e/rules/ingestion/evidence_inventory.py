"""Offline, deterministic evidence inventories for PF2e source review.

This sidecar deliberately does not participate in compiled rules packages.  It
records identities and review evidence, never rules prose or executable data.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qsl, urlsplit

from ..manifest import canonical_json, digest, read_file, reject_links
from ..validation import (
    RulesValidationError,
    bounded_json,
    choice,
    integer,
    iso_date,
    require,
    sequence,
    shape,
    text,
    timestamp,
)


EVIDENCE_SCHEMA_VERSION = 1
AON_AUTHORITY = "archives-of-nethys"
AON_HOST = "2e.aonprd.com"
SHA256 = re.compile(r"[0-9a-f]{64}")
PAGE_FAMILY = r"[A-Za-z][A-Za-z0-9-]*\.aspx"
EVIDENCE_KINDS = frozenset({
    "action", "affliction", "ancestry", "ancestry-feature", "archetype",
    "armor", "background", "class", "class-feature", "condition", "deity",
    "effect", "equipment", "feat", "hazard", "heritage", "item", "ritual",
    "rule", "shield", "skill", "spell", "vehicle", "weapon",
})
EVIDENCE_RULE_ID = re.compile(
    r"pf2e\.(?:" + "|".join(sorted(EVIDENCE_KINDS))
    + r")\.[a-z0-9]+(?:-[a-z0-9]+)*"
)
MAX_CORPUS_FILES = 50_000
MAX_CORPUS_ENTRIES = 100_000
MAX_CORPUS_DIRECTORIES = 10_000
MAX_CORPUS_DEPTH = 32
MAX_CORPUS_BYTES = 256 * 1024 * 1024


def _sha256(value, path: str) -> str:
    value = text(value, path)
    require(SHA256.fullmatch(value) is not None, "invalid_value", path)
    return value


def _identity(value, path: str) -> dict:
    shape(value, "page_family numeric_id", path)
    text(value["page_family"], path + ".page_family", pattern=PAGE_FAMILY)
    integer(value["numeric_id"], 1, 99_999_999, path + ".numeric_id")
    return value


def _identity_key(value: dict) -> tuple[str, int]:
    return value["page_family"], value["numeric_id"]


def _canonical_aon_url(value, identity: dict, path: str) -> str:
    text(value, path)
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise RulesValidationError("invalid_url", path) from None
    require(
        parts.scheme == "https"
        and parts.netloc == AON_HOST
        and parts.path == "/" + identity["page_family"]
        and not parts.fragment
        and parts.username is None
        and parts.password is None,
        "invalid_url",
        path,
    )
    require(len(pairs) in {1, 2}, "invalid_url", path)
    query = {}
    for key, item in pairs:
        require(key not in query, "invalid_url", path)
        query[key] = item
    require(set(query) <= {"ID", "Redirected"}, "invalid_url", path)
    require(query.get("ID") == str(identity["numeric_id"]), "invalid_url", path)
    if "Redirected" in query:
        require(query["Redirected"] == "1", "invalid_url", path)
    return f"https://{AON_HOST}/{identity['page_family']}?ID={identity['numeric_id']}"


def _source_ref(value, path: str) -> dict:
    shape(value, "title locator", path)
    text(value["title"], path + ".title")
    text(value["locator"], path + ".locator")
    return value


def _normalize_census_record(value, path: str, *, verify_fingerprint: bool) -> dict:
    shape(
        value,
        "identity canonical_url name kind rules_era source_refs evidence_sha256 fingerprint",
        path,
    )
    _identity(value["identity"], path + ".identity")
    value["canonical_url"] = _canonical_aon_url(
        value["canonical_url"], value["identity"], path + ".canonical_url"
    )
    text(value["name"], path + ".name")
    choice(value["kind"], EVIDENCE_KINDS, path + ".kind")
    choice(value["rules_era"], {"remaster", "legacy", "mixed", "not-applicable"},
           path + ".rules_era")
    refs = sequence(value["source_refs"], path + ".source_refs")
    seen = set()
    for index, source_ref in enumerate(refs):
        source_path = f"{path}.source_refs[{index}]"
        _source_ref(source_ref, source_path)
        key = source_ref["title"], source_ref["locator"]
        require(key not in seen, "duplicate_id", source_path)
        seen.add(key)
    refs.sort(key=lambda item: (item["title"], item["locator"]))
    _sha256(value["evidence_sha256"], path + ".evidence_sha256")
    claimed = _sha256(value["fingerprint"], path + ".fingerprint")
    if verify_fingerprint:
        require(_fingerprint_normalized_record(value) == claimed,
                "fingerprint_mismatch", path + ".fingerprint")
    return value


def _fingerprint_normalized_record(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "fingerprint"}
    return digest(canonical_json(body))


def evidence_fingerprint(entry: dict) -> str:
    """Return the fingerprint for one detached, normalized census record."""
    bounded_json(entry)
    value = deepcopy(entry)
    _normalize_census_record(value, "$", verify_fingerprint=False)
    return _fingerprint_normalized_record(value)


def normalize_aon_census(document: dict) -> dict:
    """Validate and detach one independently captured AoN identity census."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version authority captured_at site_update_date site_update_url records",
        "$",
    )
    require(type(value["schema_version"]) is int, "invalid_type", "$.schema_version")
    require(value["schema_version"] == EVIDENCE_SCHEMA_VERSION,
            "unsupported_version", "$.schema_version")
    choice(value["authority"], {AON_AUTHORITY}, "$.authority")
    timestamp(value["captured_at"], "$.captured_at")
    iso_date(value["site_update_date"], "$.site_update_date")
    require(value["site_update_url"] == f"https://{AON_HOST}/",
            "invalid_url", "$.site_update_url")
    records = sequence(value["records"], "$.records")
    identities = set()
    for index, record in enumerate(records):
        path = f"$.records[{index}]"
        _normalize_census_record(record, path, verify_fingerprint=True)
        identity = _identity_key(record["identity"])
        require(identity not in identities, "duplicate_identity", path + ".identity")
        identities.add(identity)
    records.sort(key=lambda item: _identity_key(item["identity"]))
    return value


def _review(value, path: str) -> dict:
    shape(value, "status reviewer reviewed_at", path)
    status = choice(value["status"], {"pending", "reviewed"}, path + ".status")
    if status == "pending":
        require(value["reviewer"] is None and value["reviewed_at"] is None,
                "invalid_review", path)
    else:
        require(value["reviewer"] is not None and value["reviewed_at"] is not None,
                "invalid_review", path)
        text(value["reviewer"], path + ".reviewer")
        timestamp(value["reviewed_at"], path + ".reviewed_at")
    return value


def _ledger_entry(value, path: str) -> dict:
    shape(value, "identity disposition rule_id reason review", path)
    _identity(value["identity"], path + ".identity")
    disposition = choice(
        value["disposition"], {"mapped", "pending", "excluded"},
        path + ".disposition",
    )
    if disposition == "mapped":
        require(type(value["rule_id"]) is str
                and len(value["rule_id"]) <= 4096
                and EVIDENCE_RULE_ID.fullmatch(value["rule_id"]) is not None,
                "invalid_disposition", path)
        require(value["reason"] is None, "invalid_disposition", path)
    else:
        require(value["rule_id"] is None and value["reason"] is not None,
                "invalid_disposition", path)
        text(value["reason"], path + ".reason")
    _review(value["review"], path + ".review")
    return value


def normalize_evidence_ledger(document: dict) -> dict:
    """Validate a reviewed disposition ledger independently of its census."""
    bounded_json(document)
    value = deepcopy(document)
    shape(
        value,
        "schema_version inventory_id authority census_captured_at created_at entries",
        "$",
    )
    require(type(value["schema_version"]) is int, "invalid_type", "$.schema_version")
    require(value["schema_version"] == EVIDENCE_SCHEMA_VERSION,
            "unsupported_version", "$.schema_version")
    text(value["inventory_id"], "$.inventory_id",
         pattern=r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*")
    choice(value["authority"], {AON_AUTHORITY}, "$.authority")
    timestamp(value["census_captured_at"], "$.census_captured_at")
    timestamp(value["created_at"], "$.created_at")
    entries = sequence(value["entries"], "$.entries")
    identities, rule_ids = set(), set()
    for index, entry in enumerate(entries):
        path = f"$.entries[{index}]"
        _ledger_entry(entry, path)
        identity = _identity_key(entry["identity"])
        require(identity not in identities, "duplicate_identity", path + ".identity")
        identities.add(identity)
        if entry["rule_id"] is not None:
            require(entry["rule_id"] not in rule_ids, "duplicate_id", path + ".rule_id")
            rule_ids.add(entry["rule_id"])
    entries.sort(key=lambda item: _identity_key(item["identity"]))
    return value


def _identity_label(identity: dict) -> str:
    return f"{identity['page_family']}:{identity['numeric_id']}"


def _empty_coverage_bucket() -> dict[str, int]:
    return {"excluded": 0, "mapped": 0, "missing": 0, "pending": 0, "observed": 0}


def _validate_coverage_counts(value: dict, fields: str, path: str) -> None:
    shape(value, fields, path)
    for field in fields.split():
        integer(value[field], 0, 50_000, path + "." + field)


def _validate_coverage_dimension(value: dict, expected: dict[str, dict[str, int]],
                                 missing_count: int, allowed: set[str] | frozenset[str],
                                 path: str) -> None:
    require(type(value) is dict, "invalid_type", path)
    require(len(value) <= len(allowed), "limit_exceeded", path)
    for key in sorted(value):
        choice(key, allowed, path)
        bucket_path = path + "." + key
        bucket = value[key]
        _validate_coverage_counts(
            bucket, "excluded mapped missing pending observed", bucket_path
        )
        known = expected.get(key, _empty_coverage_bucket())
        for disposition in ("excluded", "mapped", "pending"):
            require(bucket[disposition] == known[disposition],
                    "invalid_value", bucket_path + "." + disposition)
        require(bucket["observed"] == sum(
            bucket[field] for field in ("excluded", "mapped", "missing", "pending")
        ), "invalid_value", bucket_path + ".observed")
        require(bucket["observed"] > 0, "invalid_value", bucket_path + ".observed")
    require(expected.keys() <= value.keys(), "invalid_value", path)
    require(sum(bucket["missing"] for bucket in value.values()) == missing_count,
            "invalid_value", path)
    require(sum(bucket["observed"] for bucket in value.values())
            == sum(bucket["observed"] for bucket in expected.values()) + missing_count,
            "invalid_value", path)


def _validate_coverage(value: dict, records: list[dict], missing: list[str], path: str) -> None:
    shape(value, "by_disposition by_kind by_rules_era by_review", path)
    expected_disposition = {"excluded": 0, "mapped": 0, "missing": len(missing), "pending": 0}
    expected_review = {"missing": len(missing), "pending": 0, "reviewed": 0}
    by_kind: dict[str, dict[str, int]] = {}
    by_rules_era: dict[str, dict[str, int]] = {}
    for record in records:
        disposition = record["disposition"]
        expected_disposition[disposition] += 1
        expected_review[record["review"]["status"]] += 1
        for key, target in ((record["kind"], by_kind),
                            (record["rules_era"], by_rules_era)):
            bucket = target.setdefault(key, _empty_coverage_bucket())
            bucket["observed"] += 1
            bucket[disposition] += 1

    _validate_coverage_counts(
        value["by_disposition"], "excluded mapped missing pending",
        path + ".by_disposition",
    )
    for field, expected in expected_disposition.items():
        require(value["by_disposition"][field] == expected,
                "invalid_value", path + ".by_disposition." + field)
    _validate_coverage_counts(
        value["by_review"], "missing pending reviewed", path + ".by_review"
    )
    for field, expected in expected_review.items():
        require(value["by_review"][field] == expected,
                "invalid_value", path + ".by_review." + field)
    _validate_coverage_dimension(
        value["by_kind"], by_kind, len(missing), EVIDENCE_KINDS,
        path + ".by_kind",
    )
    _validate_coverage_dimension(
        value["by_rules_era"], by_rules_era, len(missing),
        {"remaster", "legacy", "mixed", "not-applicable"},
        path + ".by_rules_era",
    )


def audit_evidence_inventory(census: dict, ledger: dict) -> dict:
    """Compare independent evidence and disposition artifacts for completeness."""
    observed = normalize_aon_census(census)
    reviewed = normalize_evidence_ledger(ledger)
    require(observed["authority"] == reviewed["authority"],
            "census_mismatch", "$.ledger.authority")
    require(observed["captured_at"] == reviewed["census_captured_at"],
            "census_mismatch", "$.ledger.census_captured_at")

    observed_by_key = {
        _identity_key(record["identity"]): record for record in observed["records"]
    }
    ledger_by_key = {
        _identity_key(entry["identity"]): entry for entry in reviewed["entries"]
    }
    ledger_indexes = {
        _identity_key(entry["identity"]): index
        for index, entry in enumerate(reviewed["entries"])
    }
    for key, entry in ledger_by_key.items():
        if key not in observed_by_key:
            code = "stale_exclusion" if entry["disposition"] == "excluded" else "stale_identity"
            raise RulesValidationError(code, f"$.ledger.entries[{ledger_indexes[key]}]")

    by_kind: dict[str, dict[str, int]] = {}
    by_rules_era: dict[str, dict[str, int]] = {}
    by_disposition = {"excluded": 0, "mapped": 0, "missing": 0, "pending": 0}
    by_review = {"pending": 0, "reviewed": 0, "missing": 0}
    missing, combined = [], []
    for key, record in observed_by_key.items():
        identity_label = _identity_label(record["identity"])
        kind_counts = by_kind.setdefault(record["kind"], _empty_coverage_bucket())
        era_counts = by_rules_era.setdefault(record["rules_era"], _empty_coverage_bucket())
        kind_counts["observed"] += 1
        era_counts["observed"] += 1
        entry = ledger_by_key.get(key)
        if entry is None:
            missing.append(identity_label)
            by_disposition["missing"] += 1
            by_review["missing"] += 1
            kind_counts["missing"] += 1
            era_counts["missing"] += 1
            continue
        disposition = entry["disposition"]
        review_status = entry["review"]["status"]
        by_disposition[disposition] += 1
        by_review[review_status] += 1
        kind_counts[disposition] += 1
        era_counts[disposition] += 1
        combined.append({
            **deepcopy(record),
            "identity_key": identity_label,
            "disposition": disposition,
            "rule_id": entry["rule_id"],
            "reason": entry["reason"],
            "review": deepcopy(entry["review"]),
        })

    combined.sort(key=lambda item: item["identity_key"])
    missing.sort()
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "authority": observed["authority"],
        "inventory_id": reviewed["inventory_id"],
        "census_captured_at": observed["captured_at"],
        "ledger_created_at": reviewed["created_at"],
        "site_update_date": observed["site_update_date"],
        "site_update_url": observed["site_update_url"],
        "complete": not missing,
        "missing": missing,
        "coverage": {
            "by_disposition": by_disposition,
            "by_kind": {key: by_kind[key] for key in sorted(by_kind)},
            "by_rules_era": {key: by_rules_era[key] for key in sorted(by_rules_era)},
            "by_review": by_review,
        },
        "records": combined,
    }


def _validate_audit_report(value: dict, path: str) -> dict:
    """Validate the report boundary consumed by offline diff tooling."""
    bounded_json(value)
    shape(
        value,
        "schema_version authority inventory_id census_captured_at ledger_created_at "
        "site_update_date site_update_url complete missing coverage records",
        path,
    )
    require(type(value["schema_version"]) is int,
            "invalid_type", path + ".schema_version")
    require(value["schema_version"] == EVIDENCE_SCHEMA_VERSION,
            "unsupported_version", path + ".schema_version")
    choice(value["authority"], {AON_AUTHORITY}, path + ".authority")
    text(value["inventory_id"], path + ".inventory_id",
         pattern=r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*")
    timestamp(value["census_captured_at"], path + ".census_captured_at")
    timestamp(value["ledger_created_at"], path + ".ledger_created_at")
    iso_date(value["site_update_date"], path + ".site_update_date")
    require(value["site_update_url"] == f"https://{AON_HOST}/",
            "invalid_url", path + ".site_update_url")
    require(type(value["complete"]) is bool, "invalid_type", path + ".complete")
    missing = sequence(value["missing"], path + ".missing")
    for index, identity_label in enumerate(missing):
        text(identity_label, f"{path}.missing[{index}]",
             pattern=PAGE_FAMILY + r":[1-9][0-9]{0,7}")
    require(type(value["coverage"]) is dict, "invalid_type", path + ".coverage")
    records = sequence(value["records"], path + ".records")
    seen, rule_ids = set(), set()
    for index, record in enumerate(records):
        record_path = f"{path}.records[{index}]"
        shape(
            record,
            "identity canonical_url name kind rules_era source_refs evidence_sha256 fingerprint "
            "identity_key disposition rule_id reason review",
            record_path,
        )
        census_record = {key: deepcopy(record[key]) for key in (
            "identity", "canonical_url", "name", "kind", "rules_era", "source_refs",
            "evidence_sha256", "fingerprint",
        )}
        _normalize_census_record(census_record, record_path, verify_fingerprint=True)
        record.update(census_record)
        require(record["identity_key"] == _identity_label(record["identity"]),
                "invalid_identity", record_path + ".identity_key")
        ledger_entry = {key: deepcopy(record[key]) for key in (
            "identity", "disposition", "rule_id", "reason", "review",
        )}
        _ledger_entry(ledger_entry, record_path)
        record.update({key: ledger_entry[key] for key in (
            "disposition", "rule_id", "reason", "review",
        )})
        require(record["identity_key"] not in seen,
                "duplicate_identity", record_path + ".identity")
        seen.add(record["identity_key"])
        if record["rule_id"] is not None:
            require(record["rule_id"] not in rule_ids,
                    "duplicate_id", record_path + ".rule_id")
            rule_ids.add(record["rule_id"])
    require(value["missing"] == sorted(set(value["missing"])),
            "invalid_value", path + ".missing")
    require(not (seen & set(value["missing"])),
            "duplicate_identity", path + ".missing")
    require(value["complete"] == (not value["missing"]),
            "invalid_value", path + ".complete")
    _validate_coverage(value["coverage"], records, missing, path + ".coverage")
    records.sort(key=lambda item: item["identity_key"])
    return value


def diff_evidence_inventories(before: dict, after: dict) -> dict:
    """Return mutually exclusive identity drift between two audited snapshots."""
    bounded_json(before)
    bounded_json(after)
    before_value = _validate_audit_report(deepcopy(before), "$.before")
    after_value = _validate_audit_report(deepcopy(after), "$.after")
    require(before_value["complete"] and after_value["complete"],
            "incomplete_inventory", "$")
    require(before_value["authority"] == after_value["authority"],
            "census_mismatch", "$.after.authority")

    before_records = {record["identity_key"]: record for record in before_value["records"]}
    after_records = {record["identity_key"]: record for record in after_value["records"]}
    before_ids = {
        record["rule_id"]: key for key, record in before_records.items()
        if record["rule_id"] is not None
    }
    after_ids = {
        record["rule_id"]: key for key, record in after_records.items()
        if record["rule_id"] is not None
    }
    for rule_id in sorted(before_ids.keys() & after_ids.keys()):
        if before_ids[rule_id] != after_ids[rule_id]:
            key = after_ids[rule_id]
            raise RulesValidationError(
                "unstable_id", f"$.after.records[{key}].rule_id"
            )
    for key in sorted(before_records.keys() & after_records.keys()):
        before_id = before_records[key]["rule_id"]
        after_id = after_records[key]["rule_id"]
        if before_id is not None and after_id is not None and before_id != after_id:
            raise RulesValidationError(
                "unstable_id", f"$.after.records[{key}].rule_id"
            )

    result = {
        "before_census": before_value["census_captured_at"],
        "after_census": after_value["census_captured_at"],
        "added": sorted(after_records.keys() - before_records.keys()),
        "removed": sorted(before_records.keys() - after_records.keys()),
        "evidence_changed": [],
        "newly_excluded": [],
        "restored": [],
        "exclusion_changed": [],
        "disposition_changed": [],
        "review_changed": [],
    }
    for key in sorted(before_records.keys() & after_records.keys()):
        old, new = before_records[key], after_records[key]
        if old["disposition"] != "excluded" and new["disposition"] == "excluded":
            category = "newly_excluded"
        elif old["disposition"] == "excluded" and new["disposition"] != "excluded":
            category = "restored"
        elif (old["disposition"] == new["disposition"] == "excluded"
              and old["reason"] != new["reason"]):
            category = "exclusion_changed"
        elif old["fingerprint"] != new["fingerprint"]:
            category = "evidence_changed"
        elif (old["disposition"], old["rule_id"], old["reason"]) != (
                new["disposition"], new["rule_id"], new["reason"]):
            category = "disposition_changed"
        elif old["review"] != new["review"]:
            category = "review_changed"
        else:
            continue
        result[category].append(key)
    return result


def _corpus_json(data: bytes, path: str):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate_key", path)
            value[key] = item
        return value

    def forbidden(_):
        raise RulesValidationError("invalid_json", path)

    def finite_float(value):
        number = float(value)
        require(math.isfinite(number), "invalid_json", path)
        return number

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=forbidden, parse_float=finite_float,
        )
    except RulesValidationError:
        raise
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise RulesValidationError("invalid_json", path) from None
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 64 and count <= 1_000_000, "limit_exceeded", path)
        if type(item) is dict:
            require(all(type(key) is str for key in item), "invalid_type", path)
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        else:
            require(item is None or type(item) in (str, bool, int, float),
                    "invalid_type", path)
            if type(item) is float:
                require(math.isfinite(item), "invalid_json", path)
    return value


def _corpus_text(value, path: str) -> str:
    """Validate source evidence without silently trimming its irregularities."""
    require(type(value) is str, "invalid_type", path)
    require(0 < len(value) <= 4096, "invalid_value", path)
    require(not any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF
                    for character in value), "invalid_value", path)
    return value


def _corpus_paths(root: Path) -> list[Path]:
    try:
        reject_links(root)
        require(root.is_dir(), "invalid_package_layout", "$files")
    except (OSError, RulesValidationError):
        raise RulesValidationError("invalid_package_layout", "$files") from None
    paths, pending = [], [(root, 0)]
    entries_seen = 0
    directories_seen = 1
    apparent_bytes = 0
    while pending:
        directory, depth = pending.pop()
        try:
            children = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    require(entries_seen <= MAX_CORPUS_ENTRIES,
                            "limit_exceeded", "$files")
                    child = Path(entry.path)
                    relative = child.relative_to(root).as_posix()
                    path = "$files/" + relative
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        raise RulesValidationError("io_error", path) from None
                    children.append((entry.name, child, info))
            children.sort(key=lambda item: item[0])
        except OSError:
            raise RulesValidationError("io_error", "$files") from None
        child_directories: list[tuple[Path, int]] = []
        for _name, child, info in children:
            relative = child.relative_to(root).as_posix()
            path = "$files/" + relative
            require(
                not stat.S_ISLNK(info.st_mode)
                and not (getattr(info, "st_file_attributes", 0)
                         & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)),
                "invalid_package_layout",
                path,
            )
            if stat.S_ISDIR(info.st_mode):
                child_depth = depth + 1
                require(child_depth <= MAX_CORPUS_DEPTH,
                        "limit_exceeded", path)
                directories_seen += 1
                require(directories_seen <= MAX_CORPUS_DIRECTORIES,
                        "limit_exceeded", path)
                child_directories.append((child, child_depth))
            elif stat.S_ISREG(info.st_mode) and child.suffix.lower() == ".json":
                paths.append(child)
                require(len(paths) <= MAX_CORPUS_FILES, "limit_exceeded", path)
                apparent_bytes += info.st_size
                require(apparent_bytes <= MAX_CORPUS_BYTES, "limit_exceeded", path)
        pending.extend(reversed(child_directories))
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _publication(system: dict, path: str) -> tuple[dict, str]:
    publication = system.get("publication")
    if publication is None:
        return {"title": None, "license": None, "remaster": None}, "unknown"
    require(type(publication) is dict, "invalid_type", path + ".system.publication")
    normalized = {}
    for field in ("title", "license"):
        value = publication.get(field)
        if value in (None, ""):
            normalized[field] = None
        else:
            normalized[field] = _corpus_text(
                value, path + ".system.publication." + field
            )
    remaster = publication.get("remaster")
    require(remaster is None or type(remaster) is bool,
            "invalid_type", path + ".system.publication.remaster")
    normalized["remaster"] = remaster
    rules_era = "unknown" if remaster is None else ("remaster" if remaster else "legacy")
    return normalized, rules_era


def _traits(system: dict) -> list[str]:
    traits = system.get("traits")
    if type(traits) is not dict or type(traits.get("value")) is not list:
        return []
    result = [item for item in traits["value"] if type(item) is str and item]
    return sorted(set(result))


def _semantic_kind(pack: str, declared_type: str) -> str:
    if pack == "class-features":
        return "class-feature"
    if pack == "ancestry-features":
        return "ancestry-feature"
    if pack == "equipment":
        return "equipment"
    return declared_type.replace("_", "-")


def _subcategory(pack: str, kind: str, declared_type: str, system: dict,
                 traits: list[str]) -> str | None:
    if pack == "equipment":
        return declared_type
    if kind == "feat":
        if "dedication" in traits:
            return "dedication"
        if "archetype" in traits:
            return "archetype"
        category = system.get("category")
        return category if type(category) is str and category else None
    category = system.get("category")
    return category if kind == "action" and type(category) is str and category else None


def _scope(pack: str) -> str:
    if pack == "adventure-specific-actions":
        return "adventure"
    if pack == "campaign-effects":
        return "campaign"
    if pack == "iconics":
        return "example"
    return "global"


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def scan_corpus(root: Path) -> dict:
    """Inventory every local JSON artifact without inferring AoN identity."""
    root = Path(root).absolute()
    json_paths = _corpus_paths(root)
    records, structural, generated = [], [], []
    identities = set()
    by_kind: dict[str, int] = {}
    by_license: dict[str, int] = {}
    by_pack: dict[str, int] = {}
    by_rules_era: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    processed_bytes = 0

    for source_path in json_paths:
        relative = source_path.relative_to(root).as_posix()
        path = "$files/" + relative
        try:
            data = read_file(source_path)
        except RulesValidationError as error:
            raise RulesValidationError(error.code, path) from None
        processed_bytes += len(data)
        require(processed_bytes <= MAX_CORPUS_BYTES, "limit_exceeded", path)
        value = _corpus_json(data, path)
        content_sha256 = digest(data)
        if source_path.name == "_folders.json":
            require(type(value) is list, "invalid_type", path)
            structural.append({
                "relative_path": relative,
                "kind": "folder-metadata",
                "entry_count": len(value),
                "content_sha256": content_sha256,
            })
            continue
        if relative == "spells/master_spells.json":
            require(type(value) is list, "invalid_type", path)
            generated.append({
                "relative_path": relative,
                "kind": "spell-projection",
                "entry_count": len(value),
                "content_sha256": content_sha256,
            })
            continue

        require(type(value) is dict, "invalid_type", path)
        require(all(field in value for field in ("_id", "name", "type", "system")),
                "missing_field", path)
        foundry_id = text(value["_id"], path + "._id")
        require(re.fullmatch(r"[A-Za-z0-9]{16}", foundry_id) is not None,
                "invalid_id", path + "._id")
        name = text(value["name"], path + ".name")
        declared_type = text(value["type"], path + ".type")
        require(re.fullmatch(r"[a-z][a-z0-9-]*", declared_type) is not None,
                "invalid_value", path + ".type")
        system = value["system"]
        require(type(system) is dict, "invalid_type", path + ".system")
        pack = relative.split("/", 1)[0]
        identity = pack, declared_type, foundry_id
        require(identity not in identities, "duplicate_identity", path)
        identities.add(identity)
        publication, rules_era = _publication(system, path)
        traits = _traits(system)
        kind = _semantic_kind(pack, declared_type)
        scope = _scope(pack)
        record = {
            "local_identity": {
                "pack": pack,
                "declared_type": declared_type,
                "foundry_id": foundry_id,
            },
            "relative_path": relative,
            "name": name,
            "kind": kind,
            "subcategory": _subcategory(pack, kind, declared_type, system, traits),
            "scope": scope,
            "publication": publication,
            "rules_era": rules_era,
            "traits": traits,
            "content_sha256": content_sha256,
        }
        records.append(record)
        _increment(by_kind, kind)
        _increment(by_license, publication["license"] or "unknown")
        _increment(by_pack, pack)
        _increment(by_rules_era, rules_era)
        _increment(by_scope, scope)

    records.sort(key=lambda item: item["relative_path"])
    structural.sort(key=lambda item: item["relative_path"])
    generated.sort(key=lambda item: item["relative_path"])
    coverage = {
        "files": len(json_paths),
        "records": len(records),
        "structural_files": len(structural),
        "structural_entries": sum(item["entry_count"] for item in structural),
        "generated_files": len(generated),
        "generated_entries": sum(item["entry_count"] for item in generated),
        "by_kind": {key: by_kind[key] for key in sorted(by_kind)},
        "by_license": {key: by_license[key] for key in sorted(by_license)},
        "by_pack": {key: by_pack[key] for key in sorted(by_pack)},
        "by_rules_era": {key: by_rules_era[key] for key in sorted(by_rules_era)},
        "by_scope": {key: by_scope[key] for key in sorted(by_scope)},
    }
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "records": records,
        "structural": structural,
        "generated": generated,
        "coverage": coverage,
    }
