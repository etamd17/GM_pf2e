"""Pure, metadata-only transforms for human-triggered AoN evidence capture.

This module performs no network or filesystem access.  The operator tool owns
those side effects; tests and offline verification can reuse the exact query
and response contracts here.
"""
from __future__ import annotations

import math
import re
from types import MappingProxyType
from urllib.parse import parse_qsl, urlsplit

from .evidence_inventory import (
    AON_AUTHORITY,
    AON_HOST,
    CENSUS_SCHEMA_VERSION,
    EVIDENCE_KINDS,
    EVIDENCE_SCHEMA_VERSION,
    evidence_fingerprint,
    normalize_aon_census,
    normalize_evidence_ledger,
)
from ..manifest import canonical_json, digest
from ..validation import (
    RulesValidationError,
    choice,
    integer,
    iso_date,
    require,
    sequence,
    text,
    timestamp,
)


SEARCH_ENDPOINT = "https://elasticsearch.aonprd.com/aon/_search"
QUERY_CONTRACT_VERSION = 1
MAX_CATEGORY_RESULTS_V1 = 9_999
MAX_CATEGORY_RESULTS = MAX_CATEGORY_RESULTS_V1
MAX_RESPONSE_NODES = 500_000
PENDING_REASON = "Awaiting Paizo source, rules, and license review."
INDEX_PATTERN = re.compile(r"aon-[0-9]{8}-[0-9]{6}")
PAGE_FAMILY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]*\.aspx")
RECORD_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*-([1-9][0-9]{0,7})")

CENSUS_SOURCE_FIELDS_V1 = (
    "category",
    "exclude_from_search",
    "id",
    "item_child_id",
    "item_parent_id",
    "legacy_id",
    "name",
    "primary_source",
    "primary_source_raw",
    "release_date",
    "remaster_id",
    "source",
    "source_raw",
    "type",
    "url",
)
LEDGER_SOURCE_FIELDS_V1 = (
    "category",
    "exclude_from_search",
    "id",
    "item_parent_id",
    "remaster_id",
    "url",
)
CENSUS_SOURCE_FIELDS = CENSUS_SOURCE_FIELDS_V1
LEDGER_SOURCE_FIELDS = LEDGER_SOURCE_FIELDS_V1

# This allowlist is deliberately reviewed rather than inferred from responses.
# Page-family coverage will be expanded category-by-category before the live
# snapshot; unexpected combinations always stop capture.
CATEGORY_SPECS_V1 = MappingProxyType({
    "action": ("action", frozenset({"Actions.aspx"})),
    "ancestry": ("ancestry", frozenset({"Ancestries.aspx"})),
    "apparition": ("class-feature", frozenset({"Apparitions.aspx"})),
    "arcane-school": ("class-feature", frozenset({"ArcaneSchools.aspx"})),
    "arcane-thesis": ("class-feature", frozenset({"ArcaneThesis.aspx"})),
    "archetype": ("archetype", frozenset({"Archetypes.aspx"})),
    "armor": ("armor", frozenset({"Armor.aspx"})),
    "armor-group": ("rule", frozenset({"ArmorGroups.aspx"})),
    "background": ("background", frozenset({"Backgrounds.aspx"})),
    "bloodline": ("class-feature", frozenset({"Bloodlines.aspx"})),
    "campsite-meal": ("item", frozenset({"CampMeals.aspx"})),
    "cause": ("class-feature", frozenset({"Causes.aspx"})),
    "class": ("class", frozenset({"Classes.aspx"})),
    "class-kit": ("equipment", frozenset({"ClassKits.aspx"})),
    "condition": ("condition", frozenset({"Conditions.aspx"})),
    "conscious-mind": ("class-feature", frozenset({"ConsciousMinds.aspx"})),
    "curse": ("affliction", frozenset({"Curses.aspx"})),
    "deity": ("deity", frozenset({"Deities.aspx"})),
    "deviant-ability-classification": ("rule", frozenset({"DeviantFeats.aspx"})),
    "disease": ("affliction", frozenset({"Diseases.aspx"})),
    "doctrine": ("class-feature", frozenset({"Doctrines.aspx"})),
    "domain": ("rule", frozenset({"Domains.aspx"})),
    "druidic-order": ("class-feature", frozenset({"DruidicOrders.aspx"})),
    "eidolon": ("class-feature", frozenset({"Eidolons.aspx"})),
    "element": ("class-feature", frozenset({"Elements.aspx"})),
    "epithet": ("class-feature", frozenset({"Epithets.aspx"})),
    "equipment": ("equipment", frozenset({"Equipment.aspx"})),
    "fatal-method": ("class-feature", frozenset({"FatalMethods.aspx"})),
    "feat": ("feat", frozenset({"Feats.aspx"})),
    "follower": ("class-feature", frozenset({"Followers.aspx"})),
    "grim-fascination": ("class-feature", frozenset({"GrimFascinations.aspx"})),
    "hellknight-order": ("class-feature", frozenset({"HellknightOrders.aspx"})),
    "heritage": ("heritage", frozenset({"Heritages.aspx"})),
    "hunters-edge": ("class-feature", frozenset({"HuntersEdge.aspx"})),
    "hybrid-study": ("class-feature", frozenset({"HybridStudies.aspx"})),
    "ikon": ("class-feature", frozenset({"Ikons.aspx"})),
    "implement": ("class-feature", frozenset({"Implements.aspx"})),
    "innovation": ("class-feature", frozenset({"Innovations.aspx"})),
    "instinct": ("class-feature", frozenset({"Instincts.aspx"})),
    "language": ("rule", frozenset({"Languages.aspx"})),
    "lesson": ("class-feature", frozenset({"Lessons.aspx"})),
    "methodology": ("class-feature", frozenset({"Methodologies.aspx"})),
    "muse": ("class-feature", frozenset({"Muses.aspx"})),
    "mystery": ("class-feature", frozenset({"Mysteries.aspx"})),
    "mythic-calling": ("class-feature", frozenset({"MythicCallings.aspx"})),
    "patron": ("class-feature", frozenset({"Patrons.aspx"})),
    "practice": ("class-feature", frozenset({"Practices.aspx"})),
    "racket": ("class-feature", frozenset({"Rackets.aspx"})),
    "relic": ("item", frozenset({"Relics.aspx"})),
    "research-field": ("class-feature", frozenset({"ResearchFields.aspx"})),
    "ritual": ("ritual", frozenset({"MythicRituals.aspx", "Rituals.aspx"})),
    "rules": ("rule", frozenset({"Rules.aspx"})),
    "runesmith-rune": ("class-feature", frozenset({"RunesmithRunes.aspx"})),
    "set-relic": ("item", frozenset({"SetRelics.aspx"})),
    "shield": ("shield", frozenset({"Shields.aspx"})),
    "skill": ("skill", frozenset({"Skills.aspx"})),
    "spell": ("spell", frozenset({"MythicSpells.aspx", "Spells.aspx"})),
    "style": ("class-feature", frozenset({"Styles.aspx"})),
    "subconscious-mind": (
        "class-feature", frozenset({"SubconsciousMinds.aspx"})
    ),
    "tactic": ("class-feature", frozenset({"Tactics.aspx"})),
    "tenet": ("class-feature", frozenset({"Tenets.aspx"})),
    "trait": ("rule", frozenset({"Traits.aspx"})),
    "vehicle": ("vehicle", frozenset({"Vehicles.aspx"})),
    "way": ("class-feature", frozenset({"Ways.aspx"})),
    "weapon": ("weapon", frozenset({"Weapons.aspx"})),
    "weapon-group": ("rule", frozenset({"WeaponGroups.aspx"})),
})
CATEGORY_SPECS = CATEGORY_SPECS_V1

IDENTITY_V2_DEFERRED_CATEGORIES = frozenset({
    "animal-companion",
    "animal-companion-advanced",
    "animal-companion-specialization",
    "animal-companion-unique",
    "familiar-ability",
    "familiar-specific",
    "siege-weapon",
})
GM_DEFERRED_CATEGORIES = frozenset({
    "article",
    "class-sample",
    "creature",
    "creature-ability",
    "creature-adjustment",
    "creature-family",
    "creature-theme-template",
    "cult-activity",
    "hazard",
    "kingdom-event",
    "kingdom-structure",
    "plane",
    "source",
    "warfare-army",
    "warfare-tactic",
    "weather-hazard",
})
EXCLUDED_PROJECTION_CATEGORIES = frozenset({
    "category-page",
    "class-feature",
    "deity-category",
    "draconic-exemplar",
    "item-bonus",
    "sidebar",
    "skill-general-action",
    "tradition",
})
REVIEWED_CATEGORIES = (
    frozenset(CATEGORY_SPECS)
    | IDENTITY_V2_DEFERRED_CATEGORIES
    | GM_DEFERRED_CATEGORIES
    | EXCLUDED_PROJECTION_CATEGORIES
)

INCLUDED_REASON = "Canonical player-build or sheet-operation identity in snapshot v1."
IDENTITY_V2_DEFERRED_REASON = (
    "Deferred until selector- and record-aware evidence identity can represent this category."
)
GM_DEFERRED_REASON = "Deferred to the GM and setting-content evidence expansion."
EXCLUDED_PROJECTION_REASON = (
    "Excluded alias or embedded projection from the canonical identity census; "
    "its current parent may be represented separately or superseded upstream."
)
EQUIPMENT_PARTITION_REASON = (
    "Canonical equipment pages are included; embedded child variants are excluded."
)
WEAPON_PARTITION_REASON = (
    "Canonical weapon pages are included; combination-weapon melee projections are excluded."
)


def _current_mode_must_not_v1() -> list[dict]:
    return [
        {"term": {"exclude_from_search": True}},
        {"exists": {"field": "remaster_id"}},
    ]


def build_scope_query_v1() -> dict:
    """Return immutable contract-v1 current-mode scope query semantics."""
    return {
        "size": 0,
        "track_total_hits": True,
        "_source": False,
        "query": {"bool": {"must_not": _current_mode_must_not_v1()}},
        "aggs": {
            "categories": {"terms": {"field": "category", "size": 1000}},
            "indices": {"terms": {"field": "_index", "size": 10}},
            "equipment": {
                "filter": {"term": {"category": "equipment"}},
                "aggs": {
                    "children": {"filter": {"exists": {"field": "item_parent_id"}}}
                },
            },
            "weapon": {
                "filter": {"term": {"category": "weapon"}},
                "aggs": {
                    "melee_projections": {
                        "filter": {"wildcard": {"id.keyword": "*--melee"}}
                    }
                },
            },
        },
    }


def build_category_query_v1(category: str, mode: str) -> dict:
    """Return immutable contract-v1 category enumeration semantics."""
    choice(category, CATEGORY_SPECS_V1, "$.category")
    choice(mode, {"census", "ledger"}, "$.mode")
    must_not = _current_mode_must_not_v1()
    if category == "equipment":
        must_not.append({"exists": {"field": "item_parent_id"}})
    elif category == "weapon":
        must_not.append({"wildcard": {"id.keyword": "*--melee"}})
    return {
        "size": MAX_CATEGORY_RESULTS_V1,
        "track_total_hits": True,
        "_source": list(
            CENSUS_SOURCE_FIELDS_V1
            if mode == "census"
            else LEDGER_SOURCE_FIELDS_V1
        ),
        "query": {
            "bool": {
                "filter": [{"term": {"category": category}}],
                "must_not": must_not,
            }
        },
    }


def build_scope_query() -> dict:
    """Return the current capture query, presently contract version 1."""
    return build_scope_query_v1()


def build_category_query(category: str, mode: str) -> dict:
    """Return the current category query, presently contract version 1."""
    return build_category_query_v1(category, mode)


def _bounded_response(value) -> None:
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 32 and count <= MAX_RESPONSE_NODES, "limit_exceeded", "$")
        if type(item) is dict:
            require(all(type(key) is str for key in item), "invalid_type", "$")
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        else:
            require(item is None or type(item) in (str, bool, int, float),
                    "invalid_type", "$")
            if type(item) is float:
                require(math.isfinite(item), "invalid_json", "$")


def _mapping(value, path: str) -> dict:
    require(type(value) is dict, "invalid_type", path)
    return value


def _closed_subset(value, allowed: tuple[str, ...], required: set[str], path: str) -> dict:
    value = _mapping(value, path)
    require(not (value.keys() - set(allowed)), "unknown_field", path)
    require(required <= value.keys(), "missing_field", path)
    return value


def _validate_response_status(response: dict) -> None:
    require(response.get("timed_out") is False,
            "partial_results", "$.timed_out")
    if "terminated_early" in response:
        require(response["terminated_early"] is False,
                "partial_results", "$.terminated_early")
    shards = _mapping(response.get("_shards"), "$._shards")
    total = integer(shards.get("total"), 1, 100_000, "$._shards.total")
    successful = integer(
        shards.get("successful"), 0, total, "$._shards.successful"
    )
    skipped = integer(shards.get("skipped"), 0, total, "$._shards.skipped")
    failed = integer(shards.get("failed"), 0, total, "$._shards.failed")
    require(failed == 0, "partial_results", "$._shards.failed")
    require(successful + failed == total,
            "partial_results", "$._shards.successful")
    require(skipped <= successful, "partial_results", "$._shards.skipped")


def _hits(response: dict, *, category_limit: bool) -> tuple[int, list]:
    hits = _mapping(response.get("hits"), "$.hits")
    total = _mapping(hits.get("total"), "$.hits.total")
    reported = integer(total.get("value"), 0, 50_000_000, "$.hits.total.value")
    require(total.get("relation") == "eq", "partial_results", "$.hits.total.relation")
    if category_limit:
        require(reported < 10_000, "limit_exceeded", "$.hits.total.value")
    returned = sequence(hits.get("hits"), "$.hits.hits")
    if category_limit:
        require(reported == len(returned), "count_mismatch", "$.hits.hits")
    return reported, returned


def _resolved_index(hit: dict, path: str, expected: str | None) -> str:
    hit = _mapping(hit, path)
    value = text(hit.get("_index"), path + "._index")
    require(INDEX_PATTERN.fullmatch(value) is not None, "invalid_value", path + "._index")
    if expected is not None:
        require(value == expected, "capture_mismatch", path + "._index")
    return value


def compile_scope_observation(response: dict) -> dict:
    """Normalize the exact category facets and concrete index from a scope run."""
    _bounded_response(response)
    response = _mapping(response, "$")
    _validate_response_status(response)
    reported, hits = _hits(response, category_limit=False)
    require(not hits, "count_mismatch", "$.hits.hits")
    aggregations = _mapping(response.get("aggregations"), "$.aggregations")
    categories = _mapping(aggregations.get("categories"), "$.aggregations.categories")
    category_other = integer(
        categories.get("sum_other_doc_count"), 0, reported,
        "$.aggregations.categories.sum_other_doc_count",
    )
    require(category_other == 0,
            "partial_results", "$.aggregations.categories.sum_other_doc_count")
    category_error = integer(
        categories.get("doc_count_error_upper_bound"), 0, reported,
        "$.aggregations.categories.doc_count_error_upper_bound",
    )
    require(category_error == 0,
            "partial_results", "$.aggregations.categories.doc_count_error_upper_bound")
    buckets = sequence(categories.get("buckets"), "$.aggregations.categories.buckets")
    observed, seen = [], set()
    for index, bucket in enumerate(buckets):
        path = f"$.aggregations.categories.buckets[{index}]"
        bucket = _mapping(bucket, path)
        require(bucket.keys() == {"key", "doc_count"}, "unknown_field", path)
        name = text(bucket["key"], path + ".key", pattern=r"[a-z][a-z0-9-]*")
        require(name not in seen, "duplicate_id", path + ".key")
        seen.add(name)
        count = integer(bucket["doc_count"], 1, 50_000_000, path + ".doc_count")
        observed.append({"name": name, "observed_records": count})
    observed.sort(key=lambda item: item["name"])
    returned = sum(item["observed_records"] for item in observed)
    require(returned == reported, "count_mismatch", "$.aggregations.categories.buckets")
    indices = _mapping(aggregations.get("indices"), "$.aggregations.indices")
    index_other = integer(
        indices.get("sum_other_doc_count"), 0, reported,
        "$.aggregations.indices.sum_other_doc_count",
    )
    require(index_other == 0,
            "partial_results", "$.aggregations.indices.sum_other_doc_count")
    index_error = integer(
        indices.get("doc_count_error_upper_bound"), 0, reported,
        "$.aggregations.indices.doc_count_error_upper_bound",
    )
    require(index_error == 0,
            "partial_results", "$.aggregations.indices.doc_count_error_upper_bound")
    index_buckets = sequence(
        indices.get("buckets"), "$.aggregations.indices.buckets"
    )
    require(len(index_buckets) == 1,
            "capture_mismatch", "$.aggregations.indices.buckets")
    index_bucket = _mapping(
        index_buckets[0], "$.aggregations.indices.buckets[0]"
    )
    require(index_bucket.keys() == {"key", "doc_count"},
            "unknown_field", "$.aggregations.indices.buckets[0]")
    resolved_index = text(
        index_bucket["key"], "$.aggregations.indices.buckets[0].key"
    )
    require(INDEX_PATTERN.fullmatch(resolved_index) is not None,
            "invalid_value", "$.aggregations.indices.buckets[0].key")
    index_records = integer(
        index_bucket["doc_count"], 0, reported,
        "$.aggregations.indices.buckets[0].doc_count",
    )
    require(index_records == reported,
            "count_mismatch", "$.aggregations.indices.buckets[0].doc_count")
    by_name = {item["name"]: item["observed_records"] for item in observed}
    partitions = {}
    for category, child_name in (
        ("equipment", "children"), ("weapon", "melee_projections")
    ):
        path = "$.aggregations." + category
        partition = _mapping(aggregations.get(category), path)
        observed_count = integer(partition.get("doc_count"), 0, reported,
                                 path + ".doc_count")
        require(observed_count == by_name.get(category, 0),
                "count_mismatch", path + ".doc_count")
        excluded = _mapping(partition.get(child_name), path + "." + child_name)
        excluded_count = integer(excluded.get("doc_count"), 0, observed_count,
                                 path + "." + child_name + ".doc_count")
        partitions[category] = {
            "included_records": observed_count - excluded_count,
            "excluded_records": excluded_count,
        }
    return {
        "resolved_index": resolved_index,
        "reported_records": reported,
        "returned_records": returned,
        "categories": observed,
        "partitions": partitions,
    }


def build_scope_policy(observation: dict, *, scope_id: str,
                       site_update_date: str) -> dict:
    """Apply the reviewed, exhaustive category decisions to one scope facet."""
    _bounded_response(observation)
    observation = _mapping(observation, "$")
    require(observation.keys() == {
        "resolved_index", "reported_records", "returned_records", "categories",
        "partitions",
    }, "unknown_field", "$")
    text(scope_id, "$.scope_id", pattern=r"pf2e-[a-z0-9]+(?:-[a-z0-9]+)*")
    iso_date(site_update_date, "$.site_update_date")
    resolved_index = text(observation["resolved_index"], "$.resolved_index")
    require(INDEX_PATTERN.fullmatch(resolved_index) is not None,
            "invalid_value", "$.resolved_index")
    reported = integer(
        observation["reported_records"], 1, 100_000, "$.reported_records"
    )
    returned = integer(
        observation["returned_records"], 1, 100_000, "$.returned_records"
    )
    require(reported == returned, "count_mismatch", "$.returned_records")
    observed_rows = sequence(observation["categories"], "$.categories")
    observed = {}
    for index, row in enumerate(observed_rows):
        path = f"$.categories[{index}]"
        row = _mapping(row, path)
        require(row.keys() == {"name", "observed_records"}, "unknown_field", path)
        name = text(row["name"], path + ".name", pattern=r"[a-z][a-z0-9-]*")
        require(name not in observed, "duplicate_id", path + ".name")
        observed[name] = integer(
            row["observed_records"], 1, 9_999, path + ".observed_records"
        )
    require(observed.keys() == REVIEWED_CATEGORIES, "scope_mismatch", "$.categories")
    require(sum(observed.values()) == reported, "count_mismatch", "$.reported_records")
    partitions = _mapping(observation["partitions"], "$.partitions")
    require(partitions.keys() == {"equipment", "weapon"},
            "scope_mismatch", "$.partitions")
    normalized_partitions = {}
    for category in ("equipment", "weapon"):
        path = "$.partitions." + category
        row = _mapping(partitions[category], path)
        require(row.keys() == {"included_records", "excluded_records"},
                "unknown_field", path)
        included = integer(row["included_records"], 0, observed[category],
                           path + ".included_records")
        excluded = integer(row["excluded_records"], 0, observed[category],
                           path + ".excluded_records")
        require(included + excluded == observed[category],
                "count_mismatch", path)
        require(included > 0 and excluded > 0, "invalid_value", path)
        normalized_partitions[category] = included, excluded

    categories = []
    for name in sorted(observed):
        count = observed[name]
        included = deferred = excluded = 0
        if name in normalized_partitions:
            included, excluded = normalized_partitions[name]
            reason = (EQUIPMENT_PARTITION_REASON if name == "equipment"
                      else WEAPON_PARTITION_REASON)
        elif name in CATEGORY_SPECS:
            included = count
            reason = INCLUDED_REASON
        elif name in IDENTITY_V2_DEFERRED_CATEGORIES:
            deferred = count
            reason = IDENTITY_V2_DEFERRED_REASON
        elif name in GM_DEFERRED_CATEGORIES:
            deferred = count
            reason = GM_DEFERRED_REASON
        else:
            excluded = count
            reason = EXCLUDED_PROJECTION_REASON
        categories.append({
            "name": name,
            "observed_records": count,
            "included_records": included,
            "deferred_records": deferred,
            "excluded_records": excluded,
            "reason": reason,
        })
    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "scope_id": scope_id,
        "authority": AON_AUTHORITY,
        "resolved_index": resolved_index,
        "site_update_date": site_update_date,
        "total_records": reported,
        "categories": categories,
    }


def _canonical_identity(raw_url, path: str) -> tuple[dict, str]:
    value = text(raw_url, path)
    try:
        parts = urlsplit(value)
        pairs = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise RulesValidationError("invalid_url", path) from None
    require(
        not parts.scheme and not parts.netloc and parts.path.startswith("/")
        and not parts.fragment and parts.username is None and parts.password is None,
        "invalid_url", path,
    )
    page_family = parts.path[1:]
    require(PAGE_FAMILY_PATTERN.fullmatch(page_family) is not None,
            "invalid_url", path)
    query = {}
    for key, item in pairs:
        require(key not in query, "invalid_url", path)
        query[key] = item
    require(set(query) <= {"ID", "Redirected"} and "ID" in query,
            "invalid_url", path)
    if "Redirected" in query:
        require(query["Redirected"] == "1", "invalid_url", path)
    require(re.fullmatch(r"[1-9][0-9]{0,7}", query["ID"]) is not None,
            "invalid_url", path)
    numeric_id = int(query["ID"])
    identity = {"page_family": page_family, "numeric_id": numeric_id}
    return identity, f"https://{AON_HOST}/{page_family}?ID={numeric_id}"


def _source_refs(source: dict, path: str) -> list[dict]:
    has_sources = "source" in source or "source_raw" in source
    require(("source" in source) == ("source_raw" in source),
            "source_mismatch", path + ".source_raw")
    titles = source.get("source", [])
    locators = source.get("source_raw", [])
    require(type(titles) is list, "invalid_type", path + ".source")
    require(type(locators) is list, "invalid_type", path + ".source_raw")
    require(has_sources and len(titles) == len(locators) and len(titles) > 0,
            "source_mismatch", path + ".source_raw")
    pairs = []
    seen = set()
    for index, (title, locator) in enumerate(zip(titles, locators)):
        title = _metadata_text(title, f"{path}.source[{index}]")
        locator = _metadata_text(locator, f"{path}.source_raw[{index}]")
        if (title, locator) in seen:
            continue
        seen.add((title, locator))
        pairs.append({"title": title, "locator": locator})
    has_primary = "primary_source" in source or "primary_source_raw" in source
    require(("primary_source" in source) == ("primary_source_raw" in source),
            "source_mismatch", path + ".primary_source_raw")
    if has_primary:
        primary = (
            _metadata_text(source["primary_source"], path + ".primary_source"),
            _metadata_text(
                source["primary_source_raw"], path + ".primary_source_raw"
            ),
        )
        require(primary in seen, "source_mismatch", path + ".primary_source_raw")
    return sorted(pairs, key=lambda item: (item["title"], item["locator"]))


def _metadata_text(value, path: str) -> str:
    require(type(value) is str, "invalid_type", path)
    terminal_crlf = value.endswith("\r\n")
    normalized = re.sub(r"[ \t]*\r\n[ \t]*", " ", value)
    if terminal_crlf and normalized.endswith(" "):
        normalized = normalized[:-1]
    return text(normalized, path)


def _record_name(value, identity: dict, kind: str, path: str) -> str:
    require(type(value) is str, "invalid_type", path)
    if value == "":
        return (
            f"Unnamed {kind} ({identity['page_family']}:{identity['numeric_id']})"
        )
    return _metadata_text(value, path)


def _linkage(source: dict, path: str) -> str:
    require("remaster_id" not in source,
            "predicate_mismatch", path + ".remaster_id")
    if "legacy_id" not in source:
        return "unverified"
    legacy = source["legacy_id"]
    require(type(legacy) is list, "invalid_type", path + ".legacy_id")
    require(bool(legacy), "invalid_value", path + ".legacy_id")
    seen = set()
    for index, value in enumerate(legacy):
        text(value, f"{path}.legacy_id[{index}]", pattern=r"[a-z0-9]+(?:-[a-z0-9]+)+")
        require(value not in seen, "duplicate_id", path + ".legacy_id")
        seen.add(value)
    return "remaster"


def _enumeration_hits(response: dict, *, category: str, expected_index: str,
                      mode: str) -> list[tuple[dict, dict, str, str]]:
    _bounded_response(response)
    response = _mapping(response, "$")
    _validate_response_status(response)
    _reported, hits = _hits(response, category_limit=True)
    allowed = CENSUS_SOURCE_FIELDS if mode == "census" else LEDGER_SOURCE_FIELDS
    required = {"category", "id", "url"}
    if mode == "census":
        required |= {"exclude_from_search", "name", "source", "source_raw", "type"}
    identities, external_ids, result = set(), set(), []
    expected_kind, page_families = CATEGORY_SPECS[category]
    for index, hit in enumerate(hits):
        path = f"$.hits.hits[{index}]"
        hit = _mapping(hit, path)
        _resolved_index(hit, path, expected_index)
        source_path = path + "._source"
        source = _closed_subset(hit.get("_source"), allowed, required, source_path)
        external_id = text(source["id"], source_path + ".id")
        require(text(hit.get("_id"), path + "._id") == external_id,
                "capture_mismatch", path + "._id")
        require(external_id not in external_ids, "duplicate_id", source_path + ".id")
        external_ids.add(external_id)
        require(source["category"] == category,
                "capture_mismatch", source_path + ".category")
        if "exclude_from_search" in source:
            require(source["exclude_from_search"] is False,
                    "predicate_mismatch", source_path + ".exclude_from_search")
        require("remaster_id" not in source,
                "predicate_mismatch", source_path + ".remaster_id")
        if category == "equipment":
            require("item_parent_id" not in source,
                    "predicate_mismatch", source_path + ".item_parent_id")
        identity, canonical_url = _canonical_identity(source["url"], source_path + ".url")
        identity_key = identity["page_family"], identity["numeric_id"]
        require(identity_key not in identities,
                "duplicate_identity", source_path + ".url")
        identities.add(identity_key)
        require(identity["page_family"] in page_families,
                "capture_mismatch", source_path + ".url")
        match = RECORD_ID_PATTERN.fullmatch(external_id)
        require(match is not None and int(match.group(1)) == identity["numeric_id"],
                "capture_mismatch", source_path + ".id")
        if mode == "census":
            require(expected_kind in EVIDENCE_KINDS,
                    "invalid_value", "$.kind")
        result.append((identity, source, canonical_url, source_path))
    return result


def compile_census_shard(response: dict, *, category: str, kind: str,
                         expected_index: str, captured_at: str,
                         site_update_date: str) -> dict:
    """Compile one independently fetched census response into schema v2."""
    choice(category, CATEGORY_SPECS, "$.category")
    choice(kind, EVIDENCE_KINDS, "$.kind")
    require(CATEGORY_SPECS[category][0] == kind, "invalid_value", "$.kind")
    require(INDEX_PATTERN.fullmatch(expected_index) is not None,
            "invalid_value", "$.expected_index")
    timestamp(captured_at, "$.captured_at")
    iso_date(site_update_date, "$.site_update_date")
    records = []
    for identity, source, canonical_url, source_path in _enumeration_hits(
            response, category=category, expected_index=expected_index, mode="census"):
        name = _record_name(source["name"], identity, kind, source_path + ".name")
        text(source["type"], source_path + ".type")
        if "release_date" in source:
            iso_date(source["release_date"], source_path + ".release_date")
        source_refs = _source_refs(source, source_path)
        rules_era = _linkage(source, source_path)
        evidence_projection = {
            key: source[key] for key in sorted(source) if key in CENSUS_SOURCE_FIELDS
        }
        record = {
            "identity": identity,
            "canonical_url": canonical_url,
            "name": name,
            "kind": kind,
            "rules_era": rules_era,
            "source_refs": source_refs,
            "evidence_sha256": digest(canonical_json(evidence_projection)),
            "fingerprint": "0" * 64,
        }
        record["fingerprint"] = evidence_fingerprint(
            record, schema_version=CENSUS_SCHEMA_VERSION
        )
        records.append(record)
    document = {
        "schema_version": CENSUS_SCHEMA_VERSION,
        "authority": AON_AUTHORITY,
        "captured_at": captured_at,
        "site_update_date": site_update_date,
        "site_update_url": f"https://{AON_HOST}/",
        "records": records,
    }
    return normalize_aon_census(document)


def compile_ledger_shard(response: dict, *, category: str, expected_index: str,
                         inventory_id: str, census_captured_at: str,
                         created_at: str) -> dict:
    """Compile a separate identity-only response into an all-pending ledger."""
    choice(category, CATEGORY_SPECS, "$.category")
    require(INDEX_PATTERN.fullmatch(expected_index) is not None,
            "invalid_value", "$.expected_index")
    text(inventory_id, "$.inventory_id",
         pattern=r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*")
    timestamp(census_captured_at, "$.census_captured_at")
    timestamp(created_at, "$.created_at")
    entries = []
    for identity, _source, _canonical_url, _source_path in _enumeration_hits(
            response, category=category, expected_index=expected_index, mode="ledger"):
        entries.append({
            "identity": identity,
            "disposition": "pending",
            "rule_id": None,
            "reason": PENDING_REASON,
            "review": {"status": "pending", "reviewer": None, "reviewed_at": None},
        })
    document = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "inventory_id": inventory_id,
        "authority": AON_AUTHORITY,
        "census_captured_at": census_captured_at,
        "created_at": created_at,
        "entries": entries,
    }
    return normalize_evidence_ledger(document)
