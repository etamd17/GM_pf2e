"""Read-only compendium projection over an active rules-profile catalog."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import re

from systems.cosmere.rules.schema import SPOILER_RANK


class CompendiumValidationError(ValueError):
    pass


class CompendiumNotFound(LookupError):
    pass


_UNSAFE_KEYS = {"html", "raw_html", "source_html", "raw_text", "pdf_text",
                "foundry_document", "foundry_data"}


def _assert_normalized(value, *, depth=0):
    if depth > 24:
        raise CompendiumValidationError("compendium record is not normalized")
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if str(key).lower() in _UNSAFE_KEYS or normalized in {
                    "rawhtml", "sourcehtml", "rawtext", "pdftext",
                    "foundrydocument", "foundrydata"}:
                raise CompendiumValidationError("compendium record is not normalized")
            _assert_normalized(child, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_normalized(child, depth=depth + 1)


def _visible(entry, *, world, era, spoiler_scope, pack_ids):
    package = entry.get("package") or {}
    if pack_ids is not None and package.get("id") not in pack_ids:
        return False
    worlds = entry.get("worlds") or []
    if "cosmere" not in worlds and world not in worlds:
        return False
    eras = entry.get("eras") or []
    if eras and (era is None or era not in eras):
        return False
    spoiler = entry.get("spoiler_scope", "gm")
    return (spoiler in SPOILER_RANK and spoiler_scope in SPOILER_RANK
            and SPOILER_RANK[spoiler] <= SPOILER_RANK[spoiler_scope])


def _entries(catalog, *, world, era, spoiler_scope, pack_ids=None):
    if not isinstance(catalog, Mapping):
        raise CompendiumValidationError("invalid profile catalog")
    result = []
    for catalog_kind, rows in (catalog.get("catalogs") or {}).items():
        if isinstance(rows, Mapping):
            rows = list(rows.values())
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, Mapping):
                raise CompendiumValidationError("compendium record is not normalized")
            _assert_normalized(raw)
            entry = deepcopy(dict(raw))
            entry.setdefault("kind", str(catalog_kind).rstrip("s"))
            if not isinstance(entry.get("id"), str) or not entry["id"]:
                continue
            package = entry.get("package")
            if not isinstance(package, Mapping) or not all(
                    isinstance(package.get(key), str) and package.get(key)
                    for key in ("id", "version", "package_hash")):
                raise CompendiumValidationError("compendium record is missing exact package identity")
            if _visible(entry, world=world, era=era, spoiler_scope=spoiler_scope,
                        pack_ids=pack_ids):
                result.append(entry)
    result.sort(key=lambda row: (str(row.get("name", "")).casefold(),
                                 str(row.get("id", ""))))
    return result


def _projection(entry, *, include_parameters):
    result = {
        key: deepcopy(entry.get(key))
        for key in ("id", "version", "kind", "name", "summary", "worlds",
                    "eras", "spoiler_scope", "automation", "audit_status",
                    "citations", "citation_state", "provenance", "package")
        if key in entry
    }
    if include_parameters:
        result["parameters"] = deepcopy(entry.get("parameters") or {})
        # Provisional legacy records have independently authored normalized
        # fields rather than RuleRecord.parameters. Preserve them without raw
        # source or transport metadata.
        known = set(result) | {"parameters"}
        result["legacy_fields"] = {
            key: deepcopy(value) for key, value in entry.items()
            if key not in known and key not in {"package"}
        }
        if not result["legacy_fields"]:
            result.pop("legacy_fields")
    return result


def search(catalog, *, query, world, era=None, spoiler_scope="player",
           kinds=None, pack_ids=None, limit=50):
    if not isinstance(query, str) or len(query) > 200:
        raise CompendiumValidationError("invalid compendium query")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise CompendiumValidationError("invalid compendium limit")
    kinds = set(kinds) if kinds is not None else None
    needle = query.strip().casefold()
    found = []
    for entry in _entries(catalog, world=world, era=era,
                          spoiler_scope=spoiler_scope, pack_ids=pack_ids):
        if kinds is not None and entry.get("kind") not in kinds:
            continue
        haystack = " ".join(str(entry.get(key) or "")
                            for key in ("id", "name", "summary")).casefold()
        if needle and needle not in haystack:
            continue
        found.append(_projection(entry, include_parameters=False))
    return {"items": found[:limit], "total": len(found), "limit": limit,
            "query": query}


def detail(catalog, record_id, *, world, era=None, spoiler_scope="player",
           pack_ids=None):
    if not isinstance(record_id, str) or not record_id:
        raise CompendiumNotFound("compendium record not found")
    for entry in _entries(catalog, world=world, era=era,
                          spoiler_scope=spoiler_scope, pack_ids=pack_ids):
        if entry["id"] == record_id:
            return _projection(entry, include_parameters=True)
    raise CompendiumNotFound("compendium record not found")
