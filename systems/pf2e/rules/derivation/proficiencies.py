"""Derive explicit class proficiency grants from a verified package."""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from ..models import RulePackage
from ..validation import integer, require


def derive_class_proficiencies(package: RulePackage, class_id: str,
                               level: int) -> Mapping[str, str]:
    """Project fixed grants at an explicitly supported level of a loaded package.

    The caller supplies a package from ``load_package`` with the trusted hash
    binding where applicable. Omitted statistics are unspecified, not untrained;
    player choices and other class mechanics are outside this projection.
    """
    require(type(package) is RulePackage, "invalid_type", "$package")
    record = package.get(class_id)
    require(record.kind == "class" and record.automation == "derived"
            and record.mechanics is not None,
            "unsupported_mechanics", "$class_id")
    integer(level, 1, 20, "$level")
    require(level in record.mechanics["supported_levels"], "unsupported_level", "$level")
    ranks = {}
    for grant in record.mechanics["proficiency_grants"]:
        if grant["at_level"] <= level:
            ranks[grant["statistic"]] = grant["rank"]
    return MappingProxyType(ranks)
