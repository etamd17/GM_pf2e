"""Strict versioned records, provenance and publication gates."""
from __future__ import annotations

from copy import deepcopy

from .predicates import validate_predicate
from .validation import (
    KINDS, RULE_ID, SOURCE_ID, RulesValidationError, bounded_json, choice,
    https_url, integer, iso_date, require, rule_id, sequence, shape, strings,
    text, timestamp,
)

SCHEMA_VERSION = 2
SUPPORTED_AUTOMATION = frozenset({"reference_only", "gm_adjudicated"})
PROFICIENCY_STATISTICS = frozenset({
    "perception", "fortitude", "reflex", "will", "class_dc", "unarmored",
    "light", "medium", "heavy", "unarmed", "simple", "martial", "advanced",
    "spell_attack", "spell_dc",
})
PROFICIENCY_RANKS = frozenset({"untrained", "trained", "expert", "master", "legendary"})


def _manifest(value: dict) -> dict:
    p = "$.manifest"
    shape(value, "system ruleset_id content_version schema_version effective_date "
          "publication_date publication_status base_package overlays source_ids errata_ids "
          "supported_classes exclusions required_migrations license_family notices created_at reviews", p)
    choice(value["system"], {"pf2e"}, p + ".system")
    text(value["ruleset_id"], p + ".ruleset_id",
         pattern=r"pf2e-[a-z0-9]+(?:[.-][a-z0-9]+)*")
    require(len(value["ruleset_id"]) <= 100, "invalid_id", p + ".ruleset_id")
    version = text(value["content_version"], p + ".content_version")
    import re
    require(re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version)
            is not None, "invalid_value", p + ".content_version")
    require(type(value["schema_version"]) is int, "invalid_type", p + ".schema_version")
    require(value["schema_version"] in {1, SCHEMA_VERSION}, "unsupported_version", p + ".schema_version")
    iso_date(value["effective_date"], p + ".effective_date")
    iso_date(value["publication_date"], p + ".publication_date", nullable=True)
    timestamp(value["created_at"], p + ".created_at")
    choice(value["publication_status"], {"draft", "published"}, p + ".publication_status")
    require(value["base_package"] is None and value["overlays"] == [],
            "unsupported_composition", p)
    for field in ("source_ids", "errata_ids"):
        value[field] = strings(value[field], p + "." + field, pattern=SOURCE_ID)
    value["supported_classes"] = strings(value["supported_classes"], p + ".supported_classes")
    for rid in value["supported_classes"]:
        rule_id(rid, p + ".supported_classes", "class")
    value["required_migrations"] = strings(value["required_migrations"], p + ".required_migrations",
                                           pattern=r"pf2e\.migration\.[a-z0-9]+(?:-[a-z0-9]+)*")
    text(value["license_family"], p + ".license_family")
    value["notices"] = strings(value["notices"], p + ".notices")
    require(bool(value["notices"]), "missing_notice", p)
    sequence(value["exclusions"], p + ".exclusions")
    ids = set()
    for i, exclusion in enumerate(value["exclusions"]):
        path = f"{p}.exclusions[{i}]"
        shape(exclusion, "id reason", path)
        rule_id(exclusion["id"], path + ".id")
        text(exclusion["reason"], path + ".reason")
        require(exclusion["id"] not in ids, "duplicate_id", path)
        ids.add(exclusion["id"])
    value["exclusions"].sort(key=lambda x: x["id"])
    shape(value["reviews"], "rules license", p + ".reviews")
    for kind, review in value["reviews"].items():
        path = p + ".reviews." + kind
        if review is not None:
            shape(review, "reviewer reviewed_at", path)
            text(review["reviewer"], path + ".reviewer")
            timestamp(review["reviewed_at"], path + ".reviewed_at")
        if value["publication_status"] == "published":
            require(review is not None, "missing_review", path)
    require((value["publication_date"] is not None) == (value["publication_status"] == "published"),
            "invalid_publication", p + ".publication_date")
    return value


def _source(value: dict, p: str) -> dict:
    shape(value, "id publisher product product_id edition publication_date verification_date "
          "scope rights license_family notices url errata_ids", p)
    text(value["id"], p + ".id", pattern=SOURCE_ID)
    for field in ("publisher", "product", "product_id", "edition", "license_family"):
        text(value[field], p + "." + field)
    iso_date(value["publication_date"], p + ".publication_date")
    iso_date(value["verification_date"], p + ".verification_date", nullable=True)
    if value["verification_date"] is not None:
        require(value["verification_date"] >= value["publication_date"], "invalid_date", p)
    choice(value["scope"], {"base", "optional_sourcebook", "campaign_policy", "test"}, p + ".scope")
    choice(value["rights"], {"unknown", "reviewed_redistributable", "test_only"}, p + ".rights")
    value["notices"] = strings(value["notices"], p + ".notices")
    https_url(value["url"], p + ".url")
    value["errata_ids"] = strings(value["errata_ids"], p + ".errata_ids", pattern=SOURCE_ID)
    return value


def _class_mechanics(value: dict, p: str) -> dict:
    shape(value, "supported_levels proficiency_grants", p)
    levels = sequence(value["supported_levels"], p + ".supported_levels")
    require(bool(levels), "invalid_value", p + ".supported_levels")
    for i, level in enumerate(levels):
        integer(level, 1, 20, f"{p}.supported_levels[{i}]")
    require(len(levels) == len(set(levels)), "duplicate_id", p + ".supported_levels")
    levels.sort()

    grants = sequence(value["proficiency_grants"], p + ".proficiency_grants")
    seen = set()
    for i, grant in enumerate(grants):
        path = f"{p}.proficiency_grants[{i}]"
        shape(grant, "at_level statistic rank", path)
        level = integer(grant["at_level"], 1, 20, path + ".at_level")
        require(level in levels, "invalid_value", path + ".at_level")
        statistic = choice(grant["statistic"], PROFICIENCY_STATISTICS, path + ".statistic")
        choice(grant["rank"], PROFICIENCY_RANKS, path + ".rank")
        key = (level, statistic)
        require(key not in seen, "duplicate_id", path)
        seen.add(key)
    grants.sort(key=lambda grant: (grant["at_level"], grant["statistic"]))
    require(any(grant["at_level"] == 1 for grant in grants),
            "invalid_value", p + ".proficiency_grants")
    rank_order = {rank: index for index, rank in enumerate(
        ("untrained", "trained", "expert", "master", "legendary"))}
    prior_rank = {}
    for i, grant in enumerate(grants):
        statistic = grant["statistic"]
        current = rank_order[grant["rank"]]
        require(current >= prior_rank.get(statistic, current),
                "invalid_value", f"{p}.proficiency_grants[{i}].rank")
        prior_rank[statistic] = current
    return value


def _record(value: dict, p: str, schema_version: int) -> tuple[dict, set[str]]:
    fields = "id kind name automation level traits sources references prerequisite state quarantine_reason"
    if schema_version == 2:
        fields += " mechanics"
    shape(value, fields, p)
    choice(value["kind"], KINDS, p + ".kind")
    rule_id(value["id"], p + ".id", value["kind"])
    text(value["name"], p + ".name")
    choice(value["automation"], SUPPORTED_AUTOMATION | {"derived", "validated_choice", "scripted"},
           p + ".automation")
    integer(value["level"], 0, 20, p + ".level")
    value["traits"] = strings(value["traits"], p + ".traits", pattern=r"[a-z0-9]+(?:-[a-z0-9]+)*")
    value["references"] = strings(value["references"], p + ".references", pattern=RULE_ID)
    sequence(value["sources"], p + ".sources")
    citations = set()
    for i, citation in enumerate(value["sources"]):
        path = f"{p}.sources[{i}]"
        shape(citation, "source_id page", path)
        text(citation["source_id"], path + ".source_id", pattern=SOURCE_ID)
        text(citation["page"], path + ".page")
        key = (citation["source_id"], citation["page"])
        require(key not in citations, "duplicate_id", path)
        citations.add(key)
    value["sources"].sort(key=lambda x: (x["source_id"], x["page"]))
    refs = set(value["references"])
    if value["prerequisite"] is not None:
        refs.update(validate_predicate(value["prerequisite"], p + ".prerequisite"))
    choice(value["state"], {"enabled", "quarantined"}, p + ".state")
    if value["state"] == "enabled":
        require(value["quarantine_reason"] is None, "invalid_quarantine", p)
        if schema_version == 2 and value["automation"] == "derived":
            require(value["kind"] == "class", "unsupported_automation", p + ".automation")
            _class_mechanics(value["mechanics"], p + ".mechanics")
        else:
            require(value["automation"] in SUPPORTED_AUTOMATION,
                    "unsupported_automation", p + ".automation")
            if schema_version == 2:
                require(value["mechanics"] is None, "unsupported_automation", p + ".mechanics")
        require(bool(citations), "missing_provenance", p + ".sources")
    else:
        text(value["quarantine_reason"], p + ".quarantine_reason")
        if schema_version == 2:
            require(value["mechanics"] is None, "unsupported_automation", p + ".mechanics")
    return value, refs


def normalize_authoring(authoring: dict) -> dict:
    """Validate all authoring inputs and return a detached, normalized document."""
    bounded_json(authoring)
    value = deepcopy(authoring)
    shape(value, "manifest sources records", "$")
    manifest = _manifest(value["manifest"])
    sources, records, references = {}, {}, {}
    for i, source in enumerate(sequence(value["sources"], "$.sources")):
        p = f"$.sources[{i}]"
        source = _source(source, p)
        require(source["id"] not in sources, "duplicate_id", p)
        sources[source["id"]] = source
    for i, record in enumerate(sequence(value["records"], "$.records")):
        p = f"$.records[{i}]"
        record, refs = _record(record, p, manifest["schema_version"])
        require(record["id"] not in records, "duplicate_id", p)
        records[record["id"]], references[record["id"]] = record, refs
    require(manifest["source_ids"] == sorted(sources), "source_inventory_mismatch", "$.manifest.source_ids")
    errata_ids = sorted(s for s in sources if s.startswith("pf2e.errata."))
    require(manifest["errata_ids"] == errata_ids, "source_inventory_mismatch", "$.manifest.errata_ids")
    for i, source in enumerate(value["sources"]):
        require(set(source["errata_ids"]) <= set(errata_ids), "dangling_reference", f"$.sources[{i}].errata_ids")
    enabled = {rid: r for rid, r in records.items() if r["state"] == "enabled"}
    classes = sorted(rid for rid, r in enabled.items() if r["kind"] == "class")
    require(manifest["supported_classes"] == classes, "roster_mismatch", "$.manifest.supported_classes")
    excluded = {x["id"] for x in manifest["exclusions"]}
    require(not (excluded & enabled.keys()), "invalid_exclusion", "$.manifest.exclusions")
    for i, record in enumerate(value["records"]):
        p = f"$.records[{i}]"
        if record["state"] == "quarantined":
            require(record["id"] in excluded, "missing_exclusion", p)
            continue
        for rid in references[record["id"]]:
            require(rid in records, "dangling_reference", p)
            require(rid in enabled, "quarantined_reference", p)
            require(rid != record["id"], "self_reference", p)
        for citation in record["sources"]:
            source = sources.get(citation["source_id"])
            require(source is not None, "missing_provenance", p + ".sources")
            # Errata can cite other errata. Validate the complete reachable graph;
            # a visited set bounds mutual references without skipping any source.
            pending, visited = [source["id"]], set()
            while pending:
                sid = pending.pop()
                if sid in visited:
                    continue
                visited.add(sid)
                cited = sources[sid]
                pending.extend(cited["errata_ids"])
                require(cited["rights"] != "unknown" and cited["verification_date"] is not None,
                        "unverified_source", p + ".sources")
                require(bool(cited["notices"]), "missing_notice", p + ".sources")
                require(cited["license_family"] == manifest["license_family"]
                        and set(cited["notices"]) <= set(manifest["notices"]),
                        "license_mismatch", p + ".sources")
                if manifest["publication_status"] == "published":
                    require(cited["rights"] != "test_only" and cited["scope"] != "test",
                            "test_source_publication", p + ".sources")
    value["sources"] = sorted(sources.values(), key=lambda x: x["id"])
    value["records"] = sorted(records.values(), key=lambda x: x["id"])
    return value
