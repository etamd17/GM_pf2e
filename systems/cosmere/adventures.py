"""Normalized adventure pack indexing and deterministic validation."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .private_assets import PrivateAssetManifest
from .rules.compiler import canonical_json
from .rules.schema import (
    AdventureManifest,
    ContentPack,
    RuleRecord,
    RulesValidationError,
    SourceArtifact,
    identifier,
    require,
    sequence,
    to_dict,
)


_INDEX_KINDS = {
    "scene_order": "scene",
    "encounters": "encounter",
    "adversaries": "adversary",
    "rewards": "reward",
    "tables": "table",
    "handouts": "handout",
}


def _stable_ids(value, label):
    values = sequence(value, label)
    for item in values:
        identifier(item)
    require(len(set(values)) == len(values), f"duplicate {label}")
    return tuple(values)


@dataclass(frozen=True)
class AdventureRecordIndex:
    """Ordered scene roots and explicit typed RuleRecord identities."""

    scene_order: tuple[str, ...]
    encounters: tuple[str, ...]
    adversaries: tuple[str, ...]
    rewards: tuple[str, ...]
    tables: tuple[str, ...]
    handouts: tuple[str, ...]
    assets: tuple[str, ...]

    def __post_init__(self):
        for name in (*_INDEX_KINDS, "assets"):
            object.__setattr__(
                self,
                name,
                _stable_ids(getattr(self, name), name.replace("_", " ")),
            )
        require(bool(self.scene_order), "adventure requires a scene order")
        require(set(self.assets) <= set(self.handouts),
                "asset IDs must also be handout record IDs")


@dataclass(frozen=True)
class ValidatedAdventureBundle:
    manifest: AdventureManifest
    pack: ContentPack
    index: AdventureRecordIndex
    assets: PrivateAssetManifest


def _record_map(records, label):
    if isinstance(records, Mapping):
        values = tuple(records.values())
    else:
        values = sequence(records, label)
    require(all(isinstance(record, RuleRecord) for record in values),
            f"invalid {label}")
    require(len({record.id for record in values}) == len(values),
            f"duplicate {label}")
    return {record.id: record for record in values}


def _validate_typed_index(index, records):
    for attribute, expected_kind in _INDEX_KINDS.items():
        listed = getattr(index, attribute)
        for record_id in listed:
            record = records.get(record_id)
            require(record is not None, f"unknown {attribute} record")
            require(record.kind == expected_kind,
                    f"{record_id} expected {expected_kind} record")
        expected = {record.id for record in records.values()
                    if record.kind == expected_kind}
        require(set(listed) == expected,
                f"{expected_kind} index does not match adventure records")


def _validate_references(index, records, available):
    resolvable = set(records) | set(available)
    for record in records.values():
        for reference in record.references:
            require(not reference.endswith(".*"),
                    "adventure references must use exact stable IDs")
            require(reference in resolvable,
                    f"unresolved adventure reference: {reference}")

    reachable = set(index.scene_order)
    pending = list(index.scene_order)
    while pending:
        current = pending.pop()
        for reference in records[current].references:
            if reference in records and reference not in reachable:
                reachable.add(reference)
                pending.append(reference)
    indexed = set().union(*(set(getattr(index, name)) for name in _INDEX_KINDS))
    orphaned = indexed - reachable
    require(not orphaned,
            f"orphan adventure record: {sorted(orphaned)[0] if orphaned else ''}")


def validate_adventure_bundle(manifest, pack, index, assets, *,
                              source_artifacts, available_records=()):
    """Validate a GM-ready bundle without touching source media or storage."""
    require(isinstance(manifest, AdventureManifest),
            "invalid adventure manifest")
    require(isinstance(pack, ContentPack) and pack.kind == "adventure",
            "invalid adventure content pack")
    require(isinstance(index, AdventureRecordIndex),
            "invalid adventure record index")
    require(isinstance(assets, PrivateAssetManifest),
            "invalid private asset manifest")
    require(isinstance(source_artifacts, Mapping),
            "invalid source artifact index")
    require(all(isinstance(source, SourceArtifact)
                for source in source_artifacts.values()),
            "invalid source artifact index")
    require(all(key == source.product_id
                for key, source in source_artifacts.items()),
            "source index identity mismatch")
    require(all(source_id in source_artifacts for source_id in pack.sources),
            "unknown adventure pack source")

    require(manifest.pack_id == pack.id and manifest.version == pack.version,
            "adventure manifest does not identify its content pack")
    for name in ("sources", "worlds", "eras", "spoiler_scope", "audit_status"):
        require(getattr(manifest, name) == getattr(pack, name),
                f"adventure manifest {name} does not match its content pack")
    manifest_records = _record_map(manifest.records, "manifest records")
    pack_records = _record_map(pack.records, "pack records")
    require(manifest_records == pack_records,
            "adventure manifest records do not match its content pack")
    require(all(record.overrides is None for record in pack_records.values()),
            "adventure may not override core rules")

    available = _record_map(available_records, "enabled rules")
    collision = set(pack_records) & set(available)
    require(not collision,
            f"adventure record collides with an enabled rule: "
            f"{sorted(collision)[0] if collision else ''}")

    for record in pack_records.values():
        for citation in record.citations:
            source = source_artifacts.get(citation.artifact_id)
            require(source is not None, "unknown adventure citation source")
            require(citation.artifact_id in pack.sources,
                    "adventure citation missing from pack provenance")
            require(citation.page_end <= source.page_count,
                    "adventure citation exceeds source pages")

    _validate_typed_index(index, pack_records)
    require(assets.adventure_id == manifest.id,
            "private assets identify a different adventure")
    require({entry.id for entry in assets.entries} == set(index.assets),
            "asset index does not match private asset manifest")
    for entry in assets.entries:
        record = pack_records.get(entry.id)
        require(record is not None and record.kind == "handout",
                "asset does not resolve to a handout record")
        source = source_artifacts.get(entry.source_artifact_id)
        require(source is not None, "unknown asset source")
        require(entry.source_artifact_id in pack.sources,
                "asset source missing from pack provenance")
        require(entry.source_page <= source.page_count,
                "asset page exceeds source page count")

    _validate_references(index, pack_records, available)
    return ValidatedAdventureBundle(manifest, pack, index, assets)


def _canonical_record(record):
    return replace(
        record,
        citations=tuple(sorted(
            record.citations,
            key=lambda citation: (
                citation.artifact_id, citation.page_start, citation.page_end
            ),
        )),
        prerequisites=tuple(sorted(record.prerequisites)),
        references=tuple(sorted(record.references)),
    )


def compile_adventure_bundle(bundle):
    """Emit deterministic normalized JSON; never reads or embeds owned bytes."""
    require(isinstance(bundle, ValidatedAdventureBundle),
            "adventure bundle must be validated before compilation")
    records = tuple(sorted(
        (_canonical_record(record) for record in bundle.manifest.records),
        key=lambda record: record.id,
    ))
    manifest = replace(bundle.manifest, records=records)
    content_pack = replace(bundle.pack, records=records)
    index = {
        "scene_order": list(bundle.index.scene_order),
        "encounters": sorted(bundle.index.encounters),
        "adversaries": sorted(bundle.index.adversaries),
        "rewards": sorted(bundle.index.rewards),
        "tables": sorted(bundle.index.tables),
        "handouts": sorted(bundle.index.handouts),
        "assets": sorted(bundle.index.assets),
    }
    assets = [to_dict(entry) for entry in sorted(
        bundle.assets.entries, key=lambda entry: entry.id
    )]
    return canonical_json({
        "schema_version": 1,
        "manifest": to_dict(manifest),
        "content_pack": to_dict(content_pack),
        "index": index,
        "assets": {
            "adventure_id": bundle.assets.adventure_id,
            "entries": assets,
        },
    })
