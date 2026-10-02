"""Strict JSON shape primitives shared by authoring and predicate validation."""
from __future__ import annotations

import re
from datetime import date, datetime
from urllib.parse import urlsplit


class RulesValidationError(ValueError):
    """Stable machine-readable error without echoing untrusted field values."""

    def __init__(self, code: str, path: str):
        self.code = code
        self.path = path
        super().__init__(f"{code} at {path}")


def require(condition: bool, code: str, path: str) -> None:
    if not condition:
        raise RulesValidationError(code, path)


def shape(value, fields: str, path: str) -> dict:
    require(type(value) is dict, "invalid_type", path)
    expected = set(fields.split())
    require(not (value.keys() - expected), "unknown_field", path)
    require(value.keys() == expected, "missing_field", path)
    return value


def text(value, path: str, *, pattern: str | None = None) -> str:
    require(type(value) is str, "invalid_type", path)
    require(0 < len(value) <= 4096 and value.strip() == value, "invalid_value", path)
    require(not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value),
            "invalid_value", path)
    if pattern is not None:
        require(re.fullmatch(pattern, value) is not None, "invalid_id", path)
    return value


def integer(value, low: int, high: int, path: str) -> int:
    require(type(value) is int, "invalid_type", path)
    require(low <= value <= high, "invalid_value", path)
    return value


def choice(value, options, path: str) -> str:
    text(value, path)
    require(value in options, "invalid_value", path)
    return value


def sequence(value, path: str) -> list:
    require(type(value) is list, "invalid_type", path)
    require(len(value) <= 50000, "limit_exceeded", path)
    return value


def strings(value, path: str, *, pattern: str | None = None) -> list[str]:
    sequence(value, path)
    for i, item in enumerate(value):
        text(item, f"{path}[{i}]", pattern=pattern)
    require(len(value) == len(set(value)), "duplicate_id", path)
    return sorted(value)


def iso_date(value, path: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    text(value, path)
    try:
        require(date.fromisoformat(value).isoformat() == value, "invalid_date", path)
    except ValueError:
        raise RulesValidationError("invalid_date", path) from None


def timestamp(value, path: str) -> None:
    text(value, path)
    try:
        require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)),
                "invalid_date", path)
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise RulesValidationError("invalid_date", path) from None


def https_url(value, path: str) -> None:
    text(value, path)
    try:
        parts = urlsplit(value)
        require(parts.scheme == "https" and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and not any(c.isspace() for c in value), "invalid_url", path)
        _ = parts.port
    except ValueError:
        raise RulesValidationError("invalid_url", path) from None


KINDS = frozenset({"class", "ancestry", "heritage", "background", "feat", "spell",
                   "condition", "linked_actor", "equipment", "archetype"})
RULE_ID = r"pf2e\.(?:" + "|".join(sorted(KINDS)) + r")\.[a-z0-9]+(?:-[a-z0-9]+)*"
SOURCE_ID = r"pf2e\.(?:source|errata)\.[a-z0-9]+(?:-[a-z0-9]+)*"


def rule_id(value, path: str, kind: str | None = None) -> str:
    text(value, path, pattern=RULE_ID)
    require(kind is None or value.split(".")[1] == kind, "invalid_id", path)
    return value


def bounded_json(value) -> None:
    """Check before copying/recursing, including cyclic Python API inputs."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(depth <= 32 and count <= 500000, "limit_exceeded", "$")
        if type(item) is dict:
            require(all(type(k) is str for k in item), "invalid_type", "$")
            pending.extend((v, depth + 1) for v in item.values())
        elif type(item) is list:
            pending.extend((v, depth + 1) for v in item)
        else:
            require(item is None or type(item) in (str, bool, int), "invalid_type", "$")
