"""Compile private Mistborn audits into safe, deterministic authoring shards.

The private audit files are build inputs only.  Output contains normalized mechanics,
independently written summaries, citations, and coverage metadata; it never retains
source paths, extracted prose, PDFs, or media.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .schema import RuleRecord, RulesValidationError, freeze, from_dict, to_dict


class MistbornAuthoringError(ValueError):
    """A source audit or generated authoring shard violated its contract."""


SCHEMA_VERSION = 1
COMPILER_VERSION = "mistborn-audit-1"
PACKAGE_VERSION = "1.1.0"
POWER_AUDIT_SHA256 = "a209ab3a840a546a66b748c938a6cdc819f15dbe0dbc899f7a8dbe52039120f8"
CONTENT_AUDIT_SHA256 = "91b81b4aab5222edaeb66b9b4b204015670b5e615742ee50f4b0c4372947543b"
AUTOMATION = frozenset({"calculated", "tracked", "reference", "gm_adjudicated"})
SPOILER_SCOPES = frozenset({"none", "player", "gm"})
SOURCE_PAGE_COUNTS = {"mistborn.handbook": 410, "mistborn.world-guide": 290}

# These player actions have deterministic resource costs, but their effects need
# a target, a resolved test, a later trigger, a random roll, or another piece of
# scene context that the actor-only action boundary cannot authoritatively apply.
# Keep that limitation in the immutable authoring record instead of claiming
# calculated automation and downgrading only at runtime.
PLAYER_ACTION_AUTOMATION_OVERRIDES = frozenset({
    "mistborn.allomancy.aluminum.action.burn",
    "mistborn.allomancy.bendalloy.action.burn",
    "mistborn.allomancy.bronze.action.burn",
    "mistborn.allomancy.chromium.action.burn",
    "mistborn.allomancy.copper.action.burn",
    "mistborn.allomancy.duralumin.action.burn",
    "mistborn.allomancy.electrum.action.burn",
    "mistborn.allomancy.iron.action.burn",
    "mistborn.allomancy.iron.action.ironpull",
    "mistborn.allomancy.nicrosil.action.burn",
    "mistborn.allomancy.steel.action.burn",
    "mistborn.allomancy.steel.action.steelpush",
    "mistborn.feruchemy.aluminum.action.tap-identity",
    "mistborn.feruchemy.atium.action.tap-youth",
    "mistborn.feruchemy.bronze.action.tap-wakefulness",
    "mistborn.feruchemy.chromium.action.tap-fortune",
    "mistborn.feruchemy.electrum.action.tap-determination",
    "mistborn.feruchemy.gold.action.tap-health",
    "mistborn.feruchemy.iron.action.tap-weight",
    "mistborn.feruchemy.nicrosil.action.tap-investiture",
    "mistborn.feruchemy.zinc.action.tap-mental-speed",
})
ID_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:-[a-z0-9]+)*)+")
PRIVATE_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/(?:Users|home)/)")
PROHIBITED_KEYS = frozenset({
    "local_path", "absolute_path", "raw_text", "extracted_text", "extracted_prose",
    "pdf_text", "pdf_bytes", "page_images", "page_image", "watermark", "watermarks",
    "source_binary", "source_binaries", "source_bytes", "binary_payload",
})

POWER_COLLECTIONS = (
    "core_rules", "goals", "path_keys", "path_talents", "allomancy_actions",
    "feruchemy_actions", "power_talents",
)
CONTENT_EXCLUDED_COLLECTIONS = frozenset({
    "source_artifacts", "human_verification_gaps", "cross_record_references",
    "external_dependencies", "orphan_risk_map", "rights", "expected_counts",
    "automation_disposition_policy", "audit_metrics",
})
SOURCE_ALIASES = {
    "mistborn.handbook.official.v1_02": "mistborn.handbook",
    "mistborn.source.handbook.mb001": "mistborn.handbook",
    "mistborn.source.world-guide.mb002": "mistborn.world-guide",
}
REFERENCE_ALIASES = {
    "mistborn.talent.mistborn-snap": "mistborn.path.key.mistborn-snap",
}
CONTEXTUAL_ID_ALIASES = {
    "coppermind": "mistborn.adversary.feruchemist.resource.coppermind",
    "tinmind": "mistborn.adversary.feruchemist.resource.tinmind",
    "steelmind": "mistborn.adversary.feruchemist.resource.steelmind",
    "bronzemind": "mistborn.adversary.feruchemist.resource.bronzemind",
    "acid-flasks": "mistborn.adversary.kandra-defender.resource.acid-flasks",
    "steel-charged-primer-cubes": (
        "mistborn.adversary.malwish-airship-pilot.resource.steel-charged-primer-cubes"
    ),
    "iron-medallion": "mistborn.adversary.malwish-airship-pilot.resource.iron-medallion",
}
DYNAMIC_EXTERNAL_REFERENCES = (
    "mistborn.resource.metalmind.*",
    "mistborn.rule.adversary-health-adjustment",
)
CEILING_DIVIDE_RE = re.compile(
    r"^(?P<target>[a-z][a-z0-9_]*)=ceil\(\(1\+spend\)/2\)$"
)

PACKAGE_SPECS = {
    "cosmere.mistborn.core": {
        "filename": "core-1.1.0.json",
        "kind": "world",
        "dependencies": ["cosmere.core"],
        "eras": [],
    },
    "cosmere.mistborn.era-1": {
        "filename": "era-1-1.1.0.json",
        "kind": "world",
        "dependencies": ["cosmere.core", "cosmere.mistborn.core"],
        "eras": ["era-1"],
    },
    "cosmere.mistborn.era-2": {
        "filename": "era-2-1.1.0.json",
        "kind": "world",
        "dependencies": ["cosmere.core", "cosmere.mistborn.core"],
        "eras": ["era-2"],
    },
}

POWER_KINDS = {
    "core_rules": "rule",
    "goals": "grant",
    "path_keys": "path",
    "path_talents": "talent",
    "allomancy_actions": "action",
    "feruchemy_actions": "action",
    "power_talents": "talent",
}
CONTENT_KINDS = {
    "eras": "rule",
    "era_availability": "rule",
    "currency": "resource",
    "starting_kits": "item",
    "weapon_traits": "rule",
    "weapons": "item",
    "armor": "item",
    "vial_resource": "resource",
    "metalmind_resource_schema": "resource",
    "rare_vial_prices": "rule",
    "equipment": "item",
    "equipment_mechanic_rules": "rule",
    "travel": "item",
    "crafting": "rule",
    "rewards": "reward",
    "encounter_rules": "encounter",
    "adversaries": "adversary",
    "modules": "effect",
    "special_tables": "table",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MistbornAuthoringError(message)


def _structure_supported_expressions(value: Any) -> Any:
    """Replace audited expression strings that resemble paths with JSON operations."""
    if isinstance(value, str):
        match = CEILING_DIVIDE_RE.fullmatch(value)
        if match is None:
            return value
        return {
            "operation": "assign",
            "target": match.group("target"),
            "expression": {
                "operation": "ceiling_divide",
                "addends": [1, {"variable": "spend"}],
                "divisor": 2,
            },
        }
    if isinstance(value, Mapping):
        return {
            key: _structure_supported_expressions(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_structure_supported_expressions(item) for item in value]
    return value


def normalize_audit_payload(value: Any) -> Any:
    """Return schema-safe JSON or fail on copied source, media, and runtime paths."""
    try:
        return to_dict(freeze(_structure_supported_expressions(value)))
    except RulesValidationError as exc:
        raise MistbornAuthoringError(str(exc)) from exc


def _validate_private_audit_payload(audit: Mapping[str, Any]) -> None:
    """Validate all audit content while allowing only declared private source paths."""
    detached = deepcopy(dict(audit))
    artifacts = detached.get("source_artifacts", [])
    _require(isinstance(artifacts, list), "invalid source artifact registry")
    for artifact in artifacts:
        _require(isinstance(artifact, dict), "invalid source artifact")
        source_path = artifact.pop("path", None)
        if source_path is not None:
            _require(
                isinstance(source_path, str) and PRIVATE_PATH_RE.search(source_path) is not None,
                "invalid private source artifact path",
            )
    normalize_audit_payload(detached)


def canonical_id(value: str) -> str:
    """Return the public stable-ID spelling used by the shared rules kernel."""
    _require(isinstance(value, str) and bool(value.strip()), "invalid stable ID")
    segments = value.strip().lower().replace("_", "-").split(".")
    result = ".".join(f"n-{segment}" if segment.isdigit() else segment for segment in segments)
    _require(ID_RE.fullmatch(result) is not None, f"invalid stable ID: {value}")
    return result


def canonical_source_id(value: str) -> str:
    """Normalize a declared audit ID, including seven contextual pool shorthands."""
    if value in CONTEXTUAL_ID_ALIASES:
        return CONTEXTUAL_ID_ALIASES[value]
    return canonical_id(value)


def _canonical_reference(value: str) -> str:
    result = canonical_id(value)
    return REFERENCE_ALIASES.get(result, result)


def _canonical_selector(value: str) -> str:
    result = value.strip().lower().replace("_", "-")
    _require(result.startswith("mistborn.") and "*" in result, "invalid selector")
    _require(not PRIVATE_PATH_RE.search(result), "private path in selector")
    return result


def canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise MistbornAuthoringError("value is not canonical JSON") from exc


def package_hash(package: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(package))
    payload.pop("package_hash", None)
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def bundle_hash(packages: Iterable[Mapping[str, Any]]) -> str:
    payload = sorted((deepcopy(dict(package)) for package in packages), key=lambda item: item["id"])
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _semantic_record(record: Mapping[str, Any]) -> dict[str, Any]:
    value = deepcopy(dict(record))
    value.pop("citations", None)
    value.pop("references", None)
    parameters = value.get("parameters", {})
    if isinstance(parameters, dict):
        for key in ("source_audit", "source_collection", "source_record_id",
                    "source_parent_id", "source_path", "source_pages"):
            parameters.pop(key, None)
    return value


def _sorted_unique_dicts(values: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[bytes, dict[str, Any]] = {}
    for value in values:
        detached = deepcopy(dict(value))
        unique[canonical_bytes(detached)] = detached
    return [unique[key] for key in sorted(unique)]


def merge_normalized_records(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge semantically identical records while failing closed on ID conflicts."""
    merged: dict[str, dict[str, Any]] = {}
    for source in records:
        record = deepcopy(dict(source))
        record_id = canonical_id(record.get("id"))
        record["id"] = record_id
        record["references"] = sorted({
            _canonical_selector(value) if "*" in value else _canonical_reference(value)
            for value in record.get("references", [])
        })
        if record_id not in merged:
            merged[record_id] = record
            continue
        current = merged[record_id]
        _require(_semantic_record(current) == _semantic_record(record),
                 f"semantic conflict for duplicate record {record_id}")
        current["citations"] = _sorted_unique_dicts(
            [*current.get("citations", []), *record.get("citations", [])]
        )
        current["references"] = sorted(set(current.get("references", ())) |
                                        set(record.get("references", ())))
        current_pages = current.setdefault("parameters", {}).setdefault("source_pages", [])
        other_pages = record.get("parameters", {}).get("source_pages", [])
        current["parameters"]["source_pages"] = _sorted_unique_dicts([*current_pages, *other_pages])
    return dict(sorted(merged.items()))


def _source_artifact_id(value: str) -> str:
    _require(value in SOURCE_ALIASES, f"unknown source artifact alias {value}")
    return SOURCE_ALIASES[value]


def _range(value: Any) -> tuple[int, int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value, value
    _require(isinstance(value, str), "invalid page range")
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", value.strip())
    _require(match is not None, "invalid page range")
    start = int(match.group(1))
    end = int(match.group(2) or start)
    _require(start >= 1 and end >= start, "invalid page range")
    return start, end


def _page_runs(artifact_id: str, printed: list[int], pdf: list[int]) -> list[dict[str, Any]]:
    _require(len(printed) == len(pdf) and bool(printed), "invalid paired source pages")
    pairs = sorted(set(zip(printed, pdf)))
    for printed_page, pdf_page in pairs:
        _require(printed_page >= 1 and pdf_page == printed_page + 3,
                 "Mistborn printed/PDF page mapping must be +3")
    runs: list[tuple[int, int, int, int]] = []
    for printed_page, pdf_page in pairs:
        if runs and printed_page == runs[-1][1] + 1 and pdf_page == runs[-1][3] + 1:
            first_printed, _, first_pdf, _ = runs[-1]
            runs[-1] = (first_printed, printed_page, first_pdf, pdf_page)
        else:
            runs.append((printed_page, printed_page, pdf_page, pdf_page))
    return [{
        "artifact_id": artifact_id,
        "printed_page_start": printed_start,
        "printed_page_end": printed_end,
        "pdf_page_start": pdf_start,
        "pdf_page_end": pdf_end,
    } for printed_start, printed_end, pdf_start, pdf_end in runs]


def _pages_from_power_refs(value: Mapping[str, Any], page_refs: Mapping[str, Any]) -> list[dict[str, Any]]:
    aliases: list[str] = []
    if isinstance(value.get("source_ref"), str):
        aliases.append(value["source_ref"])
    aliases.extend(item for item in value.get("source_refs", []) if isinstance(item, str))
    _require(bool(aliases), f"missing power citation for {value.get('id')}")
    result: list[dict[str, Any]] = []
    for alias in aliases:
        _require(alias in page_refs, f"unknown power page reference {alias}")
        source = page_refs[alias]
        artifact_id = _source_artifact_id(source["artifact_id"])
        printed_start, printed_end = _range(source["printed"])
        pdf_start, pdf_end = _range(source["pdf"])
        _require(pdf_start == printed_start + 3 and pdf_end == printed_end + 3,
                 "Mistborn printed/PDF page mapping must be +3")
        result.append({
            "artifact_id": artifact_id,
            "printed_page_start": printed_start,
            "printed_page_end": printed_end,
            "pdf_page_start": pdf_start,
            "pdf_page_end": pdf_end,
        })
    return _sorted_unique_dicts(result)


def _pages_from_content_source(source: Any) -> list[dict[str, Any]]:
    sources = source if isinstance(source, list) else [source]
    result: list[dict[str, Any]] = []
    for item in sources:
        _require(isinstance(item, dict), "invalid content citation")
        artifact_id = _source_artifact_id(item.get("artifact_id"))
        printed = item.get("printed_pages")
        pdf = item.get("pdf_pages")
        if isinstance(printed, int):
            printed = [printed]
        if isinstance(pdf, int):
            pdf = [pdf]
        _require(isinstance(printed, list) and isinstance(pdf, list), "missing content source pages")
        _require(all(isinstance(page, int) and not isinstance(page, bool) for page in [*printed, *pdf]),
                 "invalid content source page")
        result.extend(_page_runs(artifact_id, printed, pdf))
    return _sorted_unique_dicts(result)


def _citations(source_pages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return _sorted_unique_dicts({
        "artifact_id": page["artifact_id"],
        "page_start": page["pdf_page_start"],
        "page_end": page["pdf_page_end"],
    } for page in source_pages)


def _normalize_eras(value: Any, inherited: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return inherited or ("era-1", "era-2")
    values = value if isinstance(value, list) else [value]
    result: set[str] = set()
    for item in values:
        _require(isinstance(item, str), "invalid era scope")
        normalized = item.lower().replace("_", "-").replace(" ", "-")
        if normalized in {"both", "all", "any"}:
            result.update(("era-1", "era-2"))
            continue
        if normalized in {"era1", "era-1", "era1-late", "era-1-late"}:
            result.add("era-1")
            continue
        if normalized in {"era2", "era-2"}:
            result.add("era-2")
            continue
        raise MistbornAuthoringError(f"unknown era scope {item}")
    return tuple(sorted(result))


def normalize_era_scope(value: Any, inherited: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Validate every declared era label and return the canonical scope."""
    return _normalize_eras(value, inherited)


def _automation(value: Mapping[str, Any], inherited: str | None) -> str:
    result = value.get("automation", value.get("automation_disposition",
                                                value.get("disposition", inherited or "reference")))
    _require(result in AUTOMATION, f"invalid automation disposition for {value.get('id')}")
    return result


def _spoiler(value: Mapping[str, Any], inherited: str | None, source_pages: list[dict[str, Any]]) -> str:
    result = value.get("spoiler_scope", inherited)
    if result is None:
        result = "gm" if any(page["artifact_id"] == "mistborn.world-guide" for page in source_pages) else "player"
    _require(isinstance(result, str) and bool(result), f"invalid spoiler scope for {value.get('id')}")
    return result if result in SPOILER_SCOPES else "gm"


@dataclass(frozen=True)
class _Node:
    value: dict[str, Any]
    origin: str
    collection: str
    path: tuple[str, ...]
    parent_id: str | None
    source: Any
    eras: tuple[str, ...]
    automation: str
    spoiler_scope: str | None


def _walk_nodes(value: Any, *, origin: str, collection: str, path: tuple[str, ...],
                parent_id: str | None = None, inherited_source: Any = None,
                inherited_eras: tuple[str, ...] = (), inherited_automation: str | None = None,
                inherited_spoiler: str | None = None) -> Iterable[_Node]:
    if isinstance(value, dict):
        source = value.get("source", inherited_source)
        eras = _normalize_eras(value.get("era", value.get("eras")), inherited_eras)
        automation = _automation(value, inherited_automation)
        spoiler = value.get("spoiler_scope", inherited_spoiler)
        this_parent = parent_id
        if isinstance(value.get("id"), str):
            node = _Node(value, origin, collection, path, parent_id, source, eras, automation, spoiler)
            yield node
            this_parent = canonical_source_id(value["id"])
        for key, item in value.items():
            if key in {"source", "source_ref", "source_refs"}:
                continue
            yield from _walk_nodes(
                item, origin=origin, collection=collection, path=(*path, str(key)),
                parent_id=this_parent, inherited_source=source, inherited_eras=eras,
                inherited_automation=automation, inherited_spoiler=spoiler,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_nodes(
                item, origin=origin, collection=collection, path=(*path, str(index)),
                parent_id=parent_id, inherited_source=inherited_source,
                inherited_eras=inherited_eras, inherited_automation=inherited_automation,
                inherited_spoiler=inherited_spoiler,
            )


ROOT_FIELDS = frozenset({
    "id", "name", "summary", "source", "source_ref", "source_refs", "automation",
    "automation_disposition", "disposition", "era", "eras", "spoiler_scope",
    "prerequisites", "references",
})


def _clean_data(value: Any, *, root: bool = False) -> Any:
    if isinstance(value, dict):
        if not root and isinstance(value.get("id"), str):
            return canonical_source_id(value["id"])
        result = {}
        for key, item in value.items():
            normalized_key = key.lower().replace("-", "_").strip()
            _require(normalized_key not in PROHIBITED_KEYS, "private source payload key is prohibited")
            if key in {"source", "source_ref", "source_refs"} or root and key in ROOT_FIELDS:
                continue
            result[key] = _clean_data(item)
        return normalize_audit_payload(result)
    if isinstance(value, list):
        return normalize_audit_payload([_clean_data(item) for item in value])
    if isinstance(value, str):
        _require(PRIVATE_PATH_RE.search(value) is None, "private source path is prohibited")
        if value.startswith("mistborn."):
            if "*" in value:
                return _canonical_selector(value)
            return _canonical_reference(value)
    return normalize_audit_payload(value)


def _find_references(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"artifact_id", "source_record_id"}:
                continue
            result.update(_find_references(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_find_references(item))
    elif isinstance(value, str) and value.startswith("mistborn."):
        result.add(_canonical_selector(value) if "*" in value else _canonical_reference(value))
    return result


def _title(record_id: str) -> str:
    return record_id.rsplit(".", 1)[-1].replace("-", " ").title()


def _summary(value: Mapping[str, Any], kind: str, name: str) -> str:
    for key in ("summary", "rule", "effect", "issue"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            _require(PRIVATE_PATH_RE.search(candidate) is None, "private path in summary")
            return candidate.strip()
    return f"Normalized {kind} mechanics for {name}."


def _kind(node: _Node) -> str:
    if node.origin == "mistborn-powers-audit":
        return POWER_KINDS[node.collection]
    if node.collection == "adversaries" and "actions" in node.path:
        return "action"
    if node.collection == "adversaries" and "resource_pools" in node.path:
        return "resource"
    if node.collection == "adversaries" and node.parent_id is not None:
        return "effect"
    if node.collection == "modules" and "actions" in node.path:
        return "action"
    if node.collection == "vial_resource" and node.parent_id is not None:
        return "action"
    return CONTENT_KINDS.get(node.collection, "rule")


def _record_from_node(node: _Node, power_page_refs: Mapping[str, Any]) -> dict[str, Any]:
    record_id = canonical_source_id(node.value["id"])
    source_pages = (_pages_from_power_refs(node.value, power_page_refs)
                    if node.origin == "mistborn-powers-audit"
                    else _pages_from_content_source(node.source))
    kind = _kind(node)
    name = node.value.get("name") if isinstance(node.value.get("name"), str) else _title(record_id)
    data = _clean_data(node.value, root=True)
    prerequisites = _clean_data(node.value.get("prerequisites", []))
    _require(isinstance(prerequisites, list), f"invalid prerequisites for {record_id}")
    explicit_references = _clean_data(node.value.get("references", []))
    _require(isinstance(explicit_references, list), f"invalid references for {record_id}")
    references = _find_references(data) | _find_references(prerequisites) | _find_references(explicit_references)
    if node.parent_id:
        references.add(node.parent_id)
    references.discard(record_id)
    parameters = {
        "source_audit": node.origin,
        "source_collection": node.collection,
        "source_record_id": node.value["id"],
        "source_parent_id": node.parent_id,
        "source_path": "/".join(node.path),
        "source_pages": source_pages,
        "data": data,
    }
    source_spoiler = node.value.get("spoiler_scope", node.spoiler_scope)
    if isinstance(source_spoiler, str) and source_spoiler not in SPOILER_SCOPES:
        parameters["source_spoiler_scope"] = source_spoiler
    return {
        "id": record_id,
        "version": PACKAGE_VERSION,
        "kind": kind,
        "name": name.strip(),
        "summary": _summary(node.value, kind, name),
        "citations": _citations(source_pages),
        "worlds": ["scadrial"],
        "eras": list(node.eras),
        "spoiler_scope": _spoiler(node.value, node.spoiler_scope, source_pages),
        "automation": node.automation,
        "audit_status": "verified",
        "prerequisites": prerequisites,
        "references": sorted(references),
        "parameters": parameters,
    }


def _all_declared_ids(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            result.append(value["id"])
        for item in value.values():
            result.extend(_all_declared_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_all_declared_ids(item))
    return result


def _power_nodes(audit: Mapping[str, Any]) -> list[_Node]:
    nodes: list[_Node] = []
    for collection in POWER_COLLECTIONS:
        _require(isinstance(audit.get(collection), list), f"missing power collection {collection}")
        nodes.extend(_walk_nodes(audit[collection], origin="mistborn-powers-audit",
                                 collection=collection, path=(collection,)))
    expected = audit.get("expected_counts", {}).get("total_rule_records")
    _require(expected == 260 and len(nodes) == expected, "power audit record count mismatch")
    _require(len({canonical_source_id(node.value["id"]) for node in nodes}) == expected,
             "duplicate power audit stable ID")
    return nodes


def _content_nodes(audit: Mapping[str, Any]) -> list[_Node]:
    declared = _all_declared_ids(audit)
    expected = audit.get("audit_metrics", {}).get("unique_declared_ids")
    _require(expected == 667 and len(declared) == expected and len(set(declared)) == expected,
             "content audit declared-ID count mismatch")
    nodes: list[_Node] = []
    for collection, value in audit.items():
        if collection in CONTENT_EXCLUDED_COLLECTIONS or collection in {
            "schema_version", "kind", "generated_for", "provenance_note"
        }:
            continue
        # The audit's two era labels are the headings for the cited availability
        # matrix immediately below them, so they share that matrix's provenance.
        inherited_source = (audit.get("era_availability", {}).get("source")
                            if collection == "eras" else None)
        nodes.extend(_walk_nodes(value, origin="mistborn-content-audit",
                                 collection=collection, path=(collection,),
                                 inherited_source=inherited_source))
    _require(len(nodes) == 658, "content audit compiled-record count mismatch")
    _require(len({canonical_source_id(node.value["id"]) for node in nodes}) == 658,
             "duplicate content audit stable ID")
    expected_counts = audit.get("expected_counts", {})
    _require(len(audit.get("adversaries", [])) == expected_counts.get("adversaries") == 38,
             "adversary count mismatch")
    _require(sum(len(value.get("actions", [])) for value in audit["adversaries"]) ==
             expected_counts.get("adversary_actions") == 188, "adversary action count mismatch")
    _require(len(audit.get("modules", [])) == expected_counts.get("modules_total") == 45,
             "module count mismatch")
    _require(len(audit.get("special_tables", [])) == expected_counts.get("special_tables") == 18,
             "special table count mismatch")
    return nodes


def _verified_content_counts(audit: Mapping[str, Any]) -> dict[str, Any]:
    adversaries = audit.get("adversaries", [])
    modules = audit.get("modules", [])
    crafting = audit.get("crafting", {})
    rewards = audit.get("rewards", {})
    actual = {
        "currencies": len(audit.get("currency", [])),
        "starting_kits": len(audit.get("starting_kits", [])),
        "weapons": len(audit.get("weapons", [])),
        "armor": len(audit.get("armor", [])),
        "equipment_entries": len(audit.get("equipment", [])),
        "adversaries": len(adversaries),
        "modules_total": len(modules),
        "modules_by_family": dict(sorted(Counter(item.get("family") for item in modules).items())),
        "adversary_roles": dict(sorted(Counter(item.get("role") for item in adversaries).items())),
        "adversary_tiers": dict(sorted(Counter(str(item.get("tier")) for item in adversaries).items())),
        "adversary_era_labels": dict(sorted(Counter(item.get("era") for item in adversaries).items())),
        "advanced_crafting_features": len(crafting.get("advanced_features", [])),
        "weapon_traits": len(audit.get("weapon_traits", [])),
        "firearm_prototype_components": len(crafting.get("firearm_components", [])),
        "pc_hemalurgic_spike_rewards": len(rewards.get("hemalurgy", {}).get("spike_effects", [])),
        "adversary_actions": sum(len(item.get("actions", [])) for item in adversaries),
        "special_tables": len(audit.get("special_tables", [])),
    }
    expected = audit.get("expected_counts")
    _require(isinstance(expected, Mapping) and actual == dict(expected),
             "content expected-count validation failed")
    return actual


def _records_by_collection(records: Mapping[str, dict[str, Any]], collection: str,
                           *, roots_only: bool = False, path_component: str | None = None) -> list[str]:
    result = []
    for record_id, record in records.items():
        parameters = record["parameters"]
        if parameters["source_collection"] != collection:
            continue
        if roots_only and parameters["source_parent_id"] is not None:
            continue
        if path_component and path_component not in parameters["source_path"].split("/"):
            continue
        result.append(record_id)
    return sorted(result)


def _gap_rows(powers: Mapping[str, Any], content: Mapping[str, Any],
              records: dict[str, dict[str, Any]], page_refs: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for gap in powers.get("ambiguities", []):
        if gap.get("status") != "human_verification_required":
            continue
        affected = sorted({_canonical_reference(item) for item in gap.get("related_ids", [])})
        _require(all(item in records for item in affected), f"unknown power-gap record {gap.get('id')}")
        pages = _pages_from_power_refs(gap, page_refs)
        result.append({
            "id": canonical_id(gap["id"]),
            "source_audit": "mistborn-powers-audit",
            "summary": gap["issue"],
            "recommended_resolution": gap.get("recommended_interpretation"),
            "automation": "gm_adjudicated",
            "review_state": "human_verification_required",
            "affected_record_ids": affected,
            "citations": _citations(pages),
        })

    adversary_actions = _records_by_collection(records, "adversaries", path_component="actions")
    module_roots = _records_by_collection(records, "modules", roots_only=True)
    hemalurgy_modules = [item for item in module_roots if item.startswith("mistborn.module.hemalurgy.")]
    embedded_adversaries = {
        "mistborn.adversary.ghostblood",
        "mistborn.adversary.agent-of-the-set",
        "mistborn.adversary.avatar-of-autonomy",
    }
    embedded_actions = [
        item for item, record in records.items()
        if record["parameters"].get("source_parent_id") in embedded_adversaries
    ]
    affected_by_gap = {
        "mistborn.gap.action-glyph-enum": adversary_actions,
        "mistborn.gap.adversary-exact-action-transcription": adversary_actions,
        "mistborn.gap.spoiler-taxonomy": [],
        "mistborn.gap.module-threat-order": sorted(["mistborn.encounter-budget.core", *module_roots]),
        "mistborn.gap.spike-choice-difference": sorted([
            *hemalurgy_modules,
            *(["mistborn.reward.hemalurgy"] if "mistborn.reward.hemalurgy" in records else []),
        ]),
        "mistborn.gap.action-embedded-items": sorted(embedded_adversaries | set(embedded_actions)),
        "mistborn.gap-era-action-filtering": [],
    }
    gap_automation = {
        "mistborn.gap.action-glyph-enum": "tracked",
        "mistborn.gap.adversary-exact-action-transcription": "tracked",
        "mistborn.gap.module-threat-order": "gm_adjudicated",
        "mistborn.gap.spike-choice-difference": "gm_adjudicated",
        "mistborn.gap.action-embedded-items": "tracked",
    }
    for gap in content.get("human_verification_gaps", []):
        gap_id = canonical_id(gap["id"])
        affected = affected_by_gap.get(gap_id, [])
        _require(all(item in records for item in affected), f"unknown content-gap record {gap_id}")
        result.append({
            "id": gap_id,
            "source_audit": "mistborn-content-audit",
            "summary": gap["summary"],
            "severity": gap.get("severity"),
            "automation": gap_automation.get(gap_id, "reference"),
            "review_state": "human_verification_required",
            "affected_record_ids": affected,
            "citations": _sorted_unique_dicts(
                citation for item in affected for citation in records[item]["citations"]
            ),
        })
    _require(len(result) == 17, "human-verification gap count mismatch")
    return sorted(result, key=lambda item: item["id"])


def _apply_gaps(records: dict[str, dict[str, Any]], gaps: list[dict[str, Any]]) -> None:
    for gap in gaps:
        for record_id in gap["affected_record_ids"]:
            record = records[record_id]
            record["audit_status"] = "blocked"
            current = record["automation"]
            if current == "calculated":
                record["parameters"].setdefault("source_automation", current)
                record["automation"] = gap["automation"] if gap["automation"] != "reference" else "tracked"
            record["parameters"].setdefault("verification_gaps", []).append(gap["id"])
    for record in records.values():
        if "verification_gaps" in record["parameters"]:
            record["parameters"]["verification_gaps"] = sorted(set(record["parameters"]["verification_gaps"]))


def _apply_player_action_automation_policy(
        records: dict[str, dict[str, Any]]) -> None:
    """Persist runtime-context limits in the normalized authoring disposition."""
    _require(
        PLAYER_ACTION_AUTOMATION_OVERRIDES <= records.keys(),
        "missing player action automation override record",
    )
    for record_id in PLAYER_ACTION_AUTOMATION_OVERRIDES:
        record = records[record_id]
        _require(record["kind"] == "action", "automation override must name an action")
        if record["automation"] == "calculated":
            record["parameters"].setdefault("source_automation", "calculated")
            record["automation"] = "tracked"
        record["parameters"]["automation_policy"] = {
            "reason": "requires_target_resolution_or_scene_context",
            "runtime_boundary": "actor_action",
        }


def _pack_id(record: Mapping[str, Any]) -> str:
    eras = record["eras"]
    if eras == ["era-1"]:
        return "cosmere.mistborn.era-1"
    if eras == ["era-2"]:
        return "cosmere.mistborn.era-2"
    return "cosmere.mistborn.core"


def _build_packages(records: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = {pack_id: [] for pack_id in PACKAGE_SPECS}
    for record in records.values():
        grouped[_pack_id(record)].append(record)
    packages = []
    for pack_id, spec in PACKAGE_SPECS.items():
        package_records = sorted(grouped[pack_id], key=lambda item: item["id"])
        sources = sorted({citation["artifact_id"] for record in package_records
                          for citation in record["citations"]})
        package = {
            "schema_version": SCHEMA_VERSION,
            "compiler_version": COMPILER_VERSION,
            "id": pack_id,
            "version": PACKAGE_VERSION,
            "kind": spec["kind"],
            "dependencies": list(spec["dependencies"]),
            "sources": sources,
            "worlds": ["scadrial"],
            "eras": list(spec["eras"]),
            "spoiler_scope": "gm",
            "audit_status": "provisional",
            "records": package_records,
        }
        package["package_hash"] = package_hash(package)
        packages.append(package)
    return sorted(packages, key=lambda item: item["id"])


def validate_reference_graph(records: Mapping[str, Mapping[str, Any]],
                             external_references: Iterable[str]) -> dict[str, int]:
    record_ids = set(records)
    external = set(external_references)
    unresolved: set[str] = set()
    unmatched: set[str] = set()
    for record in records.values():
        for reference in record.get("references", []):
            if "*" in reference:
                if reference not in external and not any(fnmatchcase(item, reference) for item in record_ids):
                    unmatched.add(reference)
            elif reference not in record_ids and reference not in external:
                unresolved.add(reference)
    _require(not unresolved, f"unresolved exact Mistborn references: {sorted(unresolved)}")
    _require(not unmatched, f"unmatched Mistborn selectors: {sorted(unmatched)}")
    return {"exact": 0, "selectors": 0}


def _validate_source_page_mapping(page: Any) -> None:
    _require(isinstance(page, Mapping), "invalid source page mapping")
    artifact_id = page.get("artifact_id")
    _require(artifact_id in SOURCE_PAGE_COUNTS, "unknown source page artifact")
    printed_start = page.get("printed_page_start")
    printed_end = page.get("printed_page_end")
    pdf_start = page.get("pdf_page_start")
    pdf_end = page.get("pdf_page_end")
    _require(
        type(printed_start) is int and type(printed_end) is int,
        "invalid printed page",
    )
    _require(
        printed_start >= 1 and printed_start <= printed_end,
        "invalid printed page range",
    )
    _require(type(pdf_start) is int and type(pdf_end) is int, "invalid PDF page")
    _require(pdf_start >= 1 and pdf_start <= pdf_end, "invalid PDF page range")
    _require(
        pdf_start == printed_start + 3 and pdf_end == printed_end + 3,
        "Mistborn printed/PDF page mapping must be +3",
    )
    _require(pdf_end <= SOURCE_PAGE_COUNTS[artifact_id], "source page exceeds source page count")


def _validate_record(record: Mapping[str, Any], *, version: str = PACKAGE_VERSION) -> None:
    _require(canonical_id(record.get("id")) == record["id"], "noncanonical record ID")
    _require(record.get("version") == version, "wrong record version")
    _require(record.get("worlds") == ["scadrial"], "wrong record world")
    _require(record.get("eras") in (["era-1"], ["era-2"], ["era-1", "era-2"]),
             "wrong record era scope")
    _require(record.get("automation") in AUTOMATION, "missing automation disposition")
    _require(record.get("spoiler_scope") in SPOILER_SCOPES, "invalid spoiler scope")
    _require(record.get("audit_status") in {"verified", "blocked"}, "invalid audit status")
    _require(isinstance(record.get("prerequisites"), list), "missing prerequisites")
    _require(isinstance(record.get("references"), list), "missing references")
    _require(isinstance(record.get("citations"), list) and record["citations"], "missing citations")
    for citation in record["citations"]:
        artifact_id = citation.get("artifact_id")
        _require(artifact_id in SOURCE_PAGE_COUNTS, "unknown citation artifact")
        _require(isinstance(citation.get("page_start"), int) and not isinstance(citation["page_start"], bool)
                 and isinstance(citation.get("page_end"), int) and not isinstance(citation["page_end"], bool)
                 and 1 <= citation["page_start"] <= citation["page_end"], "invalid citation pages")
        _require(citation["page_end"] <= SOURCE_PAGE_COUNTS[artifact_id],
                 "citation exceeds source page count")
    parameters = record.get("parameters")
    _require(isinstance(parameters, dict) and parameters.get("source_pages"), "missing source page mapping")
    for source_page in parameters["source_pages"]:
        _validate_source_page_mapping(source_page)
    expected_citations = _citations(parameters["source_pages"])
    _require(expected_citations == record["citations"], "citation/page mapping mismatch")
    raw = canonical_bytes(record).decode("utf-8")
    _require(PRIVATE_PATH_RE.search(raw) is None, "private path in compiled record")
    for key in _iter_keys(record):
        _require(key.lower().replace("-", "_") not in PROHIBITED_KEYS,
                 "private source payload key in compiled record")
    try:
        from_dict(RuleRecord, record)
    except RulesValidationError as exc:
        raise MistbornAuthoringError(str(exc)) from exc


def validate_normalized_record(record: Mapping[str, Any]) -> None:
    """Validate one generated record at an integration or test boundary."""
    _validate_record(record)


def _iter_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def _coverage(records: Mapping[str, dict[str, Any]], gaps: list[dict[str, Any]],
              packages: list[dict[str, Any]]) -> dict[str, Any]:
    gap_by_record: dict[str, list[str]] = {}
    for gap in gaps:
        for record_id in gap["affected_record_ids"]:
            gap_by_record.setdefault(record_id, []).append(gap["id"])
    package_by_record = {
        record["id"]: package["id"]
        for package in packages
        for record in package["records"]
    }
    entries = []
    for record_id, record in sorted(records.items()):
        test_refs = []
        if record["automation"] == "calculated":
            test_refs.append(
                "tests/cosmere_rules/test_mistborn_content.py::"
                "test_every_record_has_scope_payload_disposition_and_exact_page_mapping"
            )
            if record_id == "mistborn.encounter-budget.core":
                test_refs.append(
                    "tests/cosmere_rules/test_mistborn_content.py::"
                    "test_literal_encounter_threat_table_preserves_zero_cells"
                )
        entries.append({
            "id": record_id,
            "pack_id": package_by_record[record_id],
            "automation": record["automation"],
            "review_state": ("human_verification_required" if record_id in gap_by_record else "verified"),
            "verification_gaps": sorted(gap_by_record.get(record_id, [])),
            "citations": deepcopy(record["citations"]),
            "source_audit": record["parameters"]["source_audit"],
            "source_collection": record["parameters"]["source_collection"],
            "source_path": record["parameters"]["source_path"],
            "test_refs": test_refs,
        })

    source_counts = Counter()
    for record in records.values():
        parameters = record["parameters"]
        prefix = "powers" if parameters["source_audit"] == "mistborn-powers-audit" else "content"
        collection = parameters["source_collection"]
        if prefix == "powers":
            source_counts[f"{prefix}.{collection}"] += 1
        elif collection == "adversaries":
            if parameters["source_parent_id"] is None:
                source_counts["content.adversaries"] += 1
            if "actions" in parameters["source_path"].split("/"):
                source_counts["content.adversary_actions"] += 1
        elif collection == "modules":
            if parameters["source_parent_id"] is None:
                source_counts["content.modules"] += 1
        elif parameters["source_parent_id"] is None:
            source_counts[f"content.{collection}"] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "bundle_hash": bundle_hash(packages),
        "summary": {
            "record_count": len(entries),
            "source_counts": dict(sorted(source_counts.items())),
            "automation_counts": dict(sorted(Counter(entry["automation"] for entry in entries).items())),
            "human_verification_gap_count": len(gaps),
            "human_verification_record_count": len(gap_by_record),
            "missing_citations": 0,
            "missing_automation": 0,
            "unresolved_exact_references": 0,
            "unmatched_selectors": 0,
        },
        "human_verification_gaps": deepcopy(gaps),
        "records": entries,
    }


def compile_audits(powers: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any]:
    """Compile parsed private audit documents without retaining private metadata."""
    _require(isinstance(powers, Mapping) and isinstance(content, Mapping), "audit inputs must be objects")
    _validate_private_audit_payload(powers)
    _validate_private_audit_payload(content)
    power_nodes = _power_nodes(powers)
    content_nodes = _content_nodes(content)
    verified_content_counts = _verified_content_counts(content)
    page_refs = powers.get("page_refs")
    _require(isinstance(page_refs, Mapping), "missing power page-reference table")
    normalized = [_record_from_node(node, page_refs) for node in [*power_nodes, *content_nodes]]
    records = merge_normalized_records(normalized)
    _require(len(records) == 918, "combined Mistborn record count mismatch")
    gaps = _gap_rows(powers, content, records, page_refs)
    _apply_gaps(records, gaps)
    _apply_player_action_automation_policy(records)
    for record in records.values():
        _validate_record(record)
    external_references = list(DYNAMIC_EXTERNAL_REFERENCES)
    validate_reference_graph(records, external_references)
    packages = _build_packages(records)
    compiled_bundle_hash = bundle_hash(packages)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "bundle_hash": compiled_bundle_hash,
        "record_count": len(records),
        "verified_content_counts": verified_content_counts,
        "source_audits": {
            "mistborn-content-audit": {
                "sha256": CONTENT_AUDIT_SHA256,
                "declared_id_count": 667,
                "compiled_record_count": 658,
            },
            "mistborn-powers-audit": {
                "sha256": POWER_AUDIT_SHA256,
                "declared_id_count": 260,
                "compiled_record_count": 260,
            },
        },
        "reference_aliases": dict(sorted(REFERENCE_ALIASES.items())),
        "external_references": external_references,
        "packages": [{
            "id": package["id"],
            "version": package["version"],
            "filename": PACKAGE_SPECS[package["id"]]["filename"],
            "package_hash": package["package_hash"],
            "record_count": len(package["records"]),
        } for package in packages],
    }
    coverage = _coverage(records, gaps, packages)
    return {"manifest": manifest, "packages": packages, "coverage": coverage}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MistbornAuthoringError(f"cannot read JSON input {path.name}") from exc
    _require(isinstance(value, dict), "JSON root must be an object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def compile_files(power_path: Path, content_path: Path, output_dir: Path,
                  coverage_path: Path) -> dict[str, Any]:
    power_raw = power_path.read_bytes()
    content_raw = content_path.read_bytes()
    _require(hashlib.sha256(power_raw).hexdigest() == POWER_AUDIT_SHA256,
             "power audit hash mismatch")
    _require(hashlib.sha256(content_raw).hexdigest() == CONTENT_AUDIT_SHA256,
             "content audit hash mismatch")
    try:
        powers = json.loads(power_raw.decode("utf-8"))
        content = json.loads(content_raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MistbornAuthoringError("invalid private audit JSON") from exc
    bundle = compile_audits(powers, content)
    for package in bundle["packages"]:
        _write_json(output_dir / PACKAGE_SPECS[package["id"]]["filename"], package)
    _write_json(output_dir / "manifest.json", bundle["manifest"])
    _write_json(coverage_path, bundle["coverage"])
    return bundle


def load_compiled_bundle(output_dir: Path, coverage_path: Path, *,
                         baseline: bool = False) -> dict[str, Any]:
    """Load the current release, or explicitly check its immutable audit baseline.

    Source reviews retain the original shards and coverage as historical evidence;
    callers get the reviewed release unless they request that baseline explicitly.
    """
    manifest = _read_json(output_dir / "manifest.json")
    coverage = _read_json(coverage_path)
    if baseline and "baseline" in manifest:
        return _load_bundle_documents(output_dir, manifest["baseline"], coverage["baseline"])
    bundle = _load_bundle_documents(output_dir, manifest, coverage)
    if "source_review" in manifest:
        _require(isinstance(manifest.get("baseline"), dict)
                 and isinstance(coverage.get("baseline"), dict), "missing review baseline")
        from .mistborn_review import validate_source_review
        original = _load_bundle_documents(output_dir, manifest["baseline"], coverage["baseline"])
        validate_source_review(original, bundle)
    return bundle


def _load_bundle_documents(output_dir: Path, manifest: dict[str, Any],
                           coverage: dict[str, Any]) -> dict[str, Any]:
    _require(manifest.get("schema_version") == SCHEMA_VERSION, "unsupported Mistborn manifest")
    _require(manifest.get("compiler_version") == COMPILER_VERSION, "wrong Mistborn compiler version")
    packages = []
    for descriptor in manifest.get("packages", []):
        filename = descriptor.get("filename")
        _require(isinstance(filename, str) and Path(filename).name == filename,
                 "invalid package filename")
        package = _read_json(output_dir / filename)
        _require(package.get("id") == descriptor.get("id"), "package identity mismatch")
        _require(package.get("version") == descriptor.get("version"), "package version mismatch")
        _require(package_hash(package) == package.get("package_hash") == descriptor.get("package_hash"),
                 "package hash mismatch")
        _require(len(package.get("records", [])) == descriptor.get("record_count"),
                 "package record-count mismatch")
        packages.append(package)
    _require(len(packages) == 3 and {p["id"] for p in packages} == set(PACKAGE_SPECS),
             "missing or duplicate Mistborn authoring package")
    _require(bundle_hash(packages) == manifest.get("bundle_hash"), "bundle hash mismatch")
    records = merge_normalized_records(record for package in packages for record in package["records"])
    reviewed = "source_review" in manifest
    _require(not reviewed or isinstance(manifest["source_review"], dict), "invalid source review")
    version = manifest["source_review"].get("release_version") if reviewed else PACKAGE_VERSION
    _require(not reviewed or (isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                             and tuple(map(int, version.split("."))) > (1, 1, 0)),
             "invalid reviewed release version")
    _require({p["version"] for p in packages} == {version}, "mixed package versions")
    _require(len(records) == manifest.get("record_count") and (reviewed or len(records) == 918),
             "manifest record-count mismatch")
    for record in records.values():
        _validate_record(record, version=version)
    validate_reference_graph(records, manifest.get("external_references", []))
    _require(coverage.get("bundle_hash") == manifest["bundle_hash"], "coverage bundle mismatch")
    _require(coverage.get("summary", {}).get("record_count") == len(records), "coverage record-count mismatch")
    _require({entry.get("id") for entry in coverage.get("records", [])} == set(records),
             "coverage record IDs mismatch")
    return {"manifest": manifest, "packages": packages, "coverage": coverage}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile private Mistborn audits into safe authoring shards")
    parser.add_argument("--powers", required=True, type=Path)
    parser.add_argument("--content", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--coverage", required=True, type=Path)
    args = parser.parse_args(argv)
    bundle = compile_files(args.powers, args.content, args.output_dir, args.coverage)
    print(json.dumps({
        "bundle_hash": bundle["manifest"]["bundle_hash"],
        "record_count": bundle["manifest"]["record_count"],
        "packages": bundle["manifest"]["packages"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
