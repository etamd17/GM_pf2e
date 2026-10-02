"""Deeply immutable values returned only after package verification."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


def freeze(value):
    if type(value) is dict:
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class RuleRecord:
    id: str
    kind: str
    name: str
    automation: str
    level: int
    traits: tuple[str, ...]
    sources: tuple[Mapping, ...]
    references: tuple[str, ...]
    prerequisite: Mapping | None
    state: str
    quarantine_reason: str | None
    mechanics: Mapping | None = None


@dataclass(frozen=True, slots=True)
class RulePackage:
    manifest: Mapping
    sources: Mapping[str, Mapping]
    records: Mapping[str, RuleRecord]

    def get(self, rule_id: str) -> RuleRecord:
        record = self.records[rule_id]
        if record.state != "enabled":
            raise KeyError(rule_id)
        return record
