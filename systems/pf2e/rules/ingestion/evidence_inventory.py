"""Offline, deterministic evidence inventories for PF2e source review.

This sidecar deliberately does not participate in compiled rules packages.  It
records identities and review evidence, never rules prose or executable data.
"""
from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import parse_qsl, urlsplit

from ..manifest import canonical_json, digest
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
