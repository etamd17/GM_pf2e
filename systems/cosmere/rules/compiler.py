"""Deterministic compiler for normalized JSON; source binaries are never inputs."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import hashlib
import json

from .schema import (ContentPack, RulesValidationError, SPOILER_RANK, SourceArtifact, freeze,
                     from_dict, require, sequence, to_dict)

SCHEMA_VERSION = 1
COMPILER_VERSION = "cosmere-rules-1"
MAX_JSON_BYTES = 16 * 1024 * 1024


class AuthoringPacks(Mapping):
    """All authored versions with a first-version compatibility mapping.

    Older callers index packs by stable ID.  Compilation additionally needs to
    retain every semantic version so exact immutable pins can coexist.
    """

    def __init__(self, packs):
        self._versions = tuple(packs)
        self._legacy = {}
        for pack in self._versions:
            self._legacy.setdefault(pack.id, pack)

    def __getitem__(self, key):
        return self._legacy[key]

    def __iter__(self):
        return iter(self._legacy)

    def __len__(self):
        return len(self._legacy)

    def all_versions(self):
        return self._versions


def canonical_json(value) -> bytes:
    try:
        normalized = to_dict(freeze(to_dict(value)))
        result = (json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
    except (UnicodeError, RecursionError):
        raise RulesValidationError("invalid normalized JSON") from None
    require(len(result) <= MAX_JSON_BYTES, "JSON size limit exceeded")
    return result


def package_hash(package, *, source_artifacts=None) -> str:
    """Hash a package without its own hash, optionally binding its source facts.

    Compiled pack hashes include the resolved source artifacts as a canonical
    ``source_artifacts`` field in the hash payload. This field is derived from the
    registry artifact table so a pinned pack cannot silently change source edition,
    binary identity, or rights state. Registry hashes cover the complete document.
    """
    data = to_dict(package)
    require(isinstance(data, dict), "package must be a JSON object")
    data.pop("package_hash", None)
    if source_artifacts is not None:
        data["source_artifacts"] = [to_dict(source) for source in sorted(source_artifacts, key=lambda item: item.product_id)]
    return hashlib.sha256(canonical_json(data)).hexdigest()


def read_json(raw: bytes):
    require(type(raw) is bytes and len(raw) <= MAX_JSON_BYTES, "invalid JSON bytes or size")

    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    def constant(value):
        raise RulesValidationError("invalid JSON number")

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise RulesValidationError("invalid UTF-8 JSON") from None
    freeze(result)
    return result


def unique(items, attribute, label):
    result = {}
    for item in items:
        key = getattr(item, attribute)
        require(key not in result, f"duplicate {label} ID")
        result[key] = item
    return result


def validate_authoring(data):
    require(isinstance(data, Mapping) and set(data) == {"schema_version", "artifacts", "packs"}, "invalid authoring fields")
    require(type(data["schema_version"]) is int and data["schema_version"] == SCHEMA_VERSION, "unsupported schema version")
    artifacts = unique((from_dict(SourceArtifact, value) for value in sequence(data["artifacts"], "artifacts")), "product_id", "artifact")
    authored_packs = tuple(from_dict(ContentPack, value)
                           for value in sequence(data["packs"], "packs"))
    require(bool(authored_packs) and (bool(artifacts) or all(
        not pack.records and not pack.sources for pack in authored_packs)),
        "missing artifacts or packs")
    exact_versions = set()
    pack_ids = {pack.id for pack in authored_packs}
    record_owners = {}
    for pack in authored_packs:
        identity = (pack.id, pack.version)
        require(identity not in exact_versions, "duplicate pack ID/version")
        exact_versions.add(identity)
        for record in pack.records:
            owner = record_owners.setdefault(record.id, pack.id)
            require(owner == pack.id, "duplicate record ID across packages")
    packs = AuthoringPacks(authored_packs)
    for pack in authored_packs:
        require(all(dependency in pack_ids for dependency in pack.dependencies), "unknown pack dependency")
        require(all(source in artifacts for source in pack.sources), "unknown provenance artifact")
        require(pack.audit_status != "verified" or all(artifacts[source].rights_state != "quarantined" for source in pack.sources),
                "quarantined provenance cannot be verified")
        for record in pack.records:
            require("cosmere" in pack.worlds or set(record.worlds) <= set(pack.worlds), "record world outside pack scope")
            require(not pack.eras or bool(record.eras) and set(record.eras) <= set(pack.eras), "record era outside pack scope")
            require(SPOILER_RANK[record.spoiler_scope] <= SPOILER_RANK[pack.spoiler_scope], "record spoiler scope exceeds pack")
            for citation in record.citations:
                require(citation.artifact_id in artifacts, "unknown citation artifact")
                require(citation.artifact_id in pack.sources, "citation missing from pack provenance")
                source = artifacts[citation.artifact_id]
                require(citation.page_end <= source.page_count, "citation exceeds artifact page count")
                require(record.audit_status != "verified" or source.rights_state != "quarantined", "quarantined citation cannot be verified")
    dependencies_by_id = {pack_id: set() for pack_id in pack_ids}
    for pack in authored_packs:
        dependencies_by_id[pack.id].update(pack.dependencies)
    visiting, visited = set(), set()

    def visit(pack_id):
        require(pack_id not in visiting, "pack dependency cycle")
        if pack_id in visited:
            return
        visiting.add(pack_id)
        for dependency in dependencies_by_id[pack_id]:
            visit(dependency)
        visiting.remove(pack_id)
        visited.add(pack_id)

    for pack_id in pack_ids:
        visit(pack_id)
    from systems.cosmere.tables import validate_authoring_tables
    # The compatibility Mapping intentionally exposes each ID's first seed.
    # Validate a coherent ID view with every authored version substituted so a
    # later immutable version cannot hide an invalid table graph behind v1.0.
    legacy_view = dict(packs)
    for pack in authored_packs:
        version_view = dict(legacy_view)
        version_view[pack.id] = pack
        validate_authoring_tables(version_view)
    return artifacts, packs


def compile_registry(authoring) -> bytes:
    artifacts, packs = validate_authoring(authoring)
    normalized = []
    for pack in sorted(packs.all_versions(), key=lambda p: (p.id, p.version)):
        value = replace(pack, records=tuple(sorted(pack.records, key=lambda r: r.id)))
        computed = package_hash(value, source_artifacts=(artifacts[source] for source in value.sources))
        require(pack.package_hash is None or pack.package_hash == computed, "package hash mismatch")
        normalized.append(to_dict(replace(value, package_hash=computed)))
    output = {"schema_version": SCHEMA_VERSION, "compiler_version": COMPILER_VERSION,
              "artifacts": [to_dict(artifacts[key]) for key in sorted(artifacts)], "packs": normalized}
    output["package_hash"] = package_hash(output)
    return canonical_json(output)
