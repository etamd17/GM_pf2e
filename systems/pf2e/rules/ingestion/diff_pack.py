"""Deterministic review summary of verified package records and provenance."""
from __future__ import annotations

from ..models import RulePackage


def _changes(before, after) -> dict:
    return {"added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(key for key in before.keys() & after.keys()
                              if before[key] != after[key])}


def diff_packages(before: RulePackage, after: RulePackage) -> dict:
    generated = {"inputs", "outputs", "package_hash"}
    keys = (before.manifest.keys() | after.manifest.keys()) - generated
    return {"before_hash": before.manifest["package_hash"],
            "after_hash": after.manifest["package_hash"],
            "records": _changes(before.records, after.records),
            "sources": _changes(before.sources, after.sources),
            "manifest_changed": sorted(key for key in keys
                                       if before.manifest.get(key) != after.manifest.get(key))}
