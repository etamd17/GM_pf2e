"""Deterministic, package-scoped Cosmere random tables and durable state.

The engine consumes only records already projected by a rules profile.  It has
no knowledge of PDF text, Foundry data, PF2e tables, Flask, or campaign paths.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from collections.abc import Mapping

from systems.cosmere.rules.schema import SPOILER_RANK, to_dict


MAX_NESTING_DEPTH = 16
DEFAULT_HISTORY_LIMIT = 100
DEFAULT_PIN_LIMIT = 50
RECEIPT_LIMIT = 100
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TableValidationError(ValueError):
    pass


class TableNotFoundError(LookupError):
    pass


class TableStateError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def _json_copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise TableValidationError("table content must be normalized JSON") from None


def _structured(value, *, depth=0):
    if depth > 20:
        raise TableValidationError("table value nesting is too deep")
    if value is None or type(value) in (bool, int, float):
        return
    if isinstance(value, str):
        if re.search(r"<\s*/?\s*[A-Za-z][^>]*>", value):
            raise TableValidationError("table results must be structured data, never HTML")
        return
    if isinstance(value, list):
        for child in value:
            _structured(child, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {"html", "raw_html", "source_html"}:
                raise TableValidationError("table results must be structured data, never HTML")
            _structured(child, depth=depth + 1)
        return
    raise TableValidationError("table value must be normalized JSON")


def _package(record):
    package = record.get("package")
    if not isinstance(package, Mapping):
        raise TableValidationError("table record is missing exact package identity")
    required = ("id", "version", "package_hash")
    if any(not isinstance(package.get(key), str) or not package.get(key) for key in required):
        raise TableValidationError("table record has invalid package identity")
    if not re.fullmatch(r"[0-9a-f]{64}", package["package_hash"]):
        raise TableValidationError("table record has invalid package hash")
    return dict(package)


def _record_visible(record, *, world, era, spoiler_scope, allowed_pack_ids):
    package = record.get("package") or {}
    if allowed_pack_ids is not None and package.get("id") not in allowed_pack_ids:
        return False
    worlds = record.get("worlds") or []
    if "cosmere" not in worlds and world not in worlds:
        return False
    eras = record.get("eras") or []
    if eras and (era is None or era not in eras):
        return False
    spoiler = record.get("spoiler_scope")
    return spoiler in SPOILER_RANK and SPOILER_RANK[spoiler] <= SPOILER_RANK[spoiler_scope]


def _validated_results(record):
    results = (record.get("parameters") or {}).get("results")
    if not isinstance(results, list) or not results:
        raise TableValidationError("table must have results")
    seen = set()
    for row in results:
        if not isinstance(row, dict):
            raise TableValidationError("invalid table result")
        result_id = row.get("id")
        if not isinstance(result_id, str) or not result_id:
            raise TableValidationError("invalid table result ID")
        if result_id in seen:
            raise TableValidationError("duplicate result ID")
        seen.add(result_id)
        weight = row.get("weight")
        if type(weight) is not int or weight <= 0:
            raise TableValidationError("table weight must be a positive integer")
        nested = row.get("table_id")
        has_value = "value" in row
        if bool(nested) == has_value:
            raise TableValidationError(
                "table result must contain exactly one value or nested table")
        if nested is not None and not isinstance(nested, str):
            raise TableValidationError("invalid nested table ID")
        if has_value:
            _structured(row["value"])
    return results


def _dependency_ids(packages, package_id):
    found = set()
    pending = list((packages.get(package_id) or {}).get("dependencies") or [])
    while pending:
        dependency = pending.pop()
        if dependency in found:
            continue
        found.add(dependency)
        pending.extend((packages.get(dependency) or {}).get("dependencies") or [])
    return found


def _validate_reference_graph(tables, owners, packages, *, max_depth):
    edges = {}
    for table_id, record in tables.items():
        source_package = owners[table_id]
        allowed_packages = _dependency_ids(packages, source_package) | {source_package}
        edges[table_id] = []
        for row in record["parameters"]["results"]:
            target_id = row.get("table_id")
            if target_id is None:
                continue
            if target_id not in tables:
                raise TableValidationError(
                    f"missing nested table {target_id} referenced by {table_id}")
            if owners[target_id] not in allowed_packages:
                raise TableValidationError(
                    "nested table crosses an undeclared package dependency")
            edges[table_id].append(target_id)

    visiting = set()
    depth_by_table = {}

    def longest_path(table_id):
        if table_id in visiting:
            raise TableValidationError("nested table cycle")
        if table_id in depth_by_table:
            return depth_by_table[table_id]
        visiting.add(table_id)
        depth = 1
        for target_id in edges[table_id]:
            depth = max(depth, 1 + longest_path(target_id))
        visiting.remove(table_id)
        depth_by_table[table_id] = depth
        return depth

    for table_id in sorted(edges):
        if longest_path(table_id) > max_depth:
            raise TableValidationError("nested table depth exceeds limit")


def validate_authoring_tables(packs, *, max_depth=MAX_NESTING_DEPTH):
    """Fail compilation when authored table records cannot resolve safely."""
    normalized_packs = {pack_id: to_dict(pack) for pack_id, pack in packs.items()}
    tables = {}
    owners = {}
    for pack_id, pack in normalized_packs.items():
        for record in pack.get("records") or []:
            if record.get("kind") != "table":
                continue
            table_id = record.get("id")
            if table_id in tables:
                raise TableValidationError("duplicate table ID")
            _validated_results(record)
            tables[table_id] = record
            owners[table_id] = pack_id
    _validate_reference_graph(tables, owners, normalized_packs,
                              max_depth=max_depth)


class TableRegistry:
    """Validated immutable-by-copy view of visible table RuleRecords."""

    def __init__(self, catalog, *, world, era=None, spoiler_scope="player",
                 allowed_pack_ids=None, max_depth=MAX_NESTING_DEPTH):
        if spoiler_scope not in SPOILER_RANK:
            raise TableValidationError("invalid spoiler scope")
        if not isinstance(catalog, Mapping):
            raise TableValidationError("invalid profile catalog")
        packages = catalog.get("packages") or []
        self._packages = {}
        for package in packages:
            if not isinstance(package, Mapping) or not package.get("id"):
                raise TableValidationError("invalid catalog package")
            self._packages[package["id"]] = _json_copy(package)
        allowed = set(allowed_pack_ids) if allowed_pack_ids is not None else None
        raw_tables = (catalog.get("catalogs") or {}).get("table", [])
        if isinstance(raw_tables, Mapping):
            raw_tables = list(raw_tables.values())
        if not isinstance(raw_tables, list):
            raise TableValidationError("invalid table catalog")
        self._tables = {}
        for raw in raw_tables:
            if not isinstance(raw, Mapping) or raw.get("kind") != "table":
                raise TableValidationError("invalid table record")
            record = _json_copy(raw)
            package = _package(record)
            if package["id"] not in self._packages:
                raise TableValidationError("table package is not active")
            active = self._packages[package["id"]]
            if any(active.get(key) != package.get(key)
                   for key in ("id", "version", "package_hash")):
                raise TableValidationError("table package identity does not match active package")
            if not _record_visible(record, world=world, era=era,
                                   spoiler_scope=spoiler_scope,
                                   allowed_pack_ids=allowed):
                continue
            table_id = record.get("id")
            if not isinstance(table_id, str) or not table_id or table_id in self._tables:
                raise TableValidationError("duplicate or invalid table ID")
            _validated_results(record)
            self._tables[table_id] = record
        self._validate_graph(max_depth=max_depth)

    def _dependencies(self, package_id):
        return _dependency_ids(self._packages, package_id)

    def _validate_graph(self, *, max_depth):
        owners = {table_id: record["package"]["id"]
                  for table_id, record in self._tables.items()}
        _validate_reference_graph(self._tables, owners, self._packages,
                                  max_depth=max_depth)

    @property
    def table_ids(self):
        return tuple(sorted(self._tables))

    def roll(self, table_id, *, seed):
        if table_id not in self._tables:
            raise TableNotFoundError("table not found")
        if not isinstance(seed, str) or not seed or len(seed) > 256:
            raise TableValidationError("seed must be a nonempty string of at most 256 characters")
        path = []
        citations = []
        hashes = []
        current_id = table_id
        depth = 0
        while True:
            depth += 1
            if depth > MAX_NESTING_DEPTH:
                raise TableValidationError("nested table depth exceeds limit")
            record = self._tables[current_id]
            package = record["package"]
            rows = record["parameters"]["results"]
            total = sum(row["weight"] for row in rows)
            material = "\0".join((seed, table_id, str(depth), current_id,
                                    package["package_hash"]))
            point = int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest(),
                                   "big") % total
            selected = rows[-1]
            cursor = 0
            for row in rows:
                cursor += row["weight"]
                if point < cursor:
                    selected = row
                    break
            path.append({"table_id": current_id, "result_id": selected["id"],
                         "package_id": package["id"],
                         "package_hash": package["package_hash"]})
            if package["package_hash"] not in hashes:
                hashes.append(package["package_hash"])
            for citation in record.get("citations") or []:
                if citation not in citations:
                    citations.append(deepcopy(citation))
            nested = selected.get("table_id")
            if nested is not None:
                current_id = nested
                continue
            return {
                "table_id": table_id,
                "seed": seed,
                "result": {"id": selected["id"],
                           "value": deepcopy(selected["value"])},
                "resolution_path": path,
                "citations": citations,
                "package_hashes": hashes,
            }


def _request_id(value):
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise TableStateError("invalid_request_id", "Invalid table request ID.")
    return value


def normalize_table_state(value):
    value = value if isinstance(value, Mapping) else {}
    revision = value.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise TableStateError("invalid_revision", "Invalid table state revision.")
    history = value.get("history", [])
    pins = value.get("pins", [])
    receipts = value.get("receipts", [])
    if not all(isinstance(rows, list) for rows in (history, pins, receipts)):
        raise TableStateError("invalid_state", "Invalid table state.")
    return {"schema_version": 1, "revision": revision,
            "history": _json_copy(history[-DEFAULT_HISTORY_LIMIT:]),
            "pins": _json_copy(pins[-DEFAULT_PIN_LIMIT:]),
            "receipts": _json_copy(receipts[-RECEIPT_LIMIT:])}


def _receipt(state, request_id):
    return next((row for row in reversed(state["receipts"])
                 if row.get("request_id") == request_id), None)


def _check_revision(state, expected_revision):
    if type(expected_revision) is not int or expected_revision < 0:
        raise TableStateError("invalid_revision", "Expected revision must be a nonnegative integer.")
    if state["revision"] != expected_revision:
        raise TableStateError("stale_revision", "stale table revision; reload before trying again.", 409)


def _append_receipt(state, receipt):
    state["receipts"].append(deepcopy(receipt))
    state["receipts"] = state["receipts"][-RECEIPT_LIMIT:]


def roll_and_record(state, registry, table_id, *, seed, expected_revision,
                    request_id, history_limit=DEFAULT_HISTORY_LIMIT):
    request_id = _request_id(request_id)
    state = normalize_table_state(state)
    prior = _receipt(state, request_id)
    if prior is not None:
        if (prior.get("operation") != "roll" or prior.get("table_id") != table_id
                or prior.get("seed") != seed):
            raise TableStateError("request_id_conflict",
                                  "Table request ID belongs to a different roll.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    _check_revision(state, expected_revision)
    if type(history_limit) is not int or history_limit < 1 or history_limit > DEFAULT_HISTORY_LIMIT:
        raise TableStateError("invalid_history_limit", "Invalid table history limit.")
    rolled = registry.roll(table_id, seed=seed)
    roll_id = "roll." + hashlib.sha256(
        (request_id + "\0" + table_id + "\0" + seed).encode("utf-8")).hexdigest()[:32]
    history = {"id": roll_id, "request_id": request_id, **deepcopy(rolled)}
    state["history"].append(history)
    state["history"] = state["history"][-history_limit:]
    state["revision"] += 1
    result = {**deepcopy(rolled), "id": roll_id, "revision": state["revision"],
              "replayed": False}
    _append_receipt(state, {"request_id": request_id, "operation": "roll",
                            "table_id": table_id, "seed": seed,
                            "result": deepcopy(result)})
    return state, result


def pin_roll(state, roll_id, *, expected_revision, request_id,
             pin_limit=DEFAULT_PIN_LIMIT):
    request_id = _request_id(request_id)
    state = normalize_table_state(state)
    prior = _receipt(state, request_id)
    if prior is not None:
        if prior.get("operation") != "pin" or prior.get("roll_id") != roll_id:
            raise TableStateError("request_id_conflict",
                                  "Table request ID belongs to a different pin.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    _check_revision(state, expected_revision)
    history = next((row for row in state["history"] if row.get("id") == roll_id), None)
    if history is None:
        raise TableStateError("roll_not_found", "Roll history entry not found.", 404)
    if type(pin_limit) is not int or pin_limit < 1 or pin_limit > DEFAULT_PIN_LIMIT:
        raise TableStateError("invalid_pin_limit", "Invalid table pin limit.")
    if not any(row.get("roll_id") == roll_id for row in state["pins"]):
        state["pins"].append({"roll_id": roll_id, "roll": deepcopy(history)})
        state["pins"] = state["pins"][-pin_limit:]
    state["revision"] += 1
    result = {"roll_id": roll_id, "revision": state["revision"], "replayed": False}
    _append_receipt(state, {"request_id": request_id, "operation": "pin",
                            "roll_id": roll_id, "result": deepcopy(result)})
    return state, result


def unpin_roll(state, roll_id, *, expected_revision, request_id):
    request_id = _request_id(request_id)
    state = normalize_table_state(state)
    prior = _receipt(state, request_id)
    if prior is not None:
        if prior.get("operation") != "unpin" or prior.get("roll_id") != roll_id:
            raise TableStateError("request_id_conflict",
                                  "Table request ID belongs to a different unpin.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    _check_revision(state, expected_revision)
    state["pins"] = [row for row in state["pins"] if row.get("roll_id") != roll_id]
    state["revision"] += 1
    result = {"roll_id": roll_id, "revision": state["revision"], "replayed": False}
    _append_receipt(state, {"request_id": request_id, "operation": "unpin",
                            "roll_id": roll_id, "result": deepcopy(result)})
    return state, result
