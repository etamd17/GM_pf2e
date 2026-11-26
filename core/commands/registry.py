"""Command types and check 1 (contract section 2-3; PE-14a).

A :class:`CommandType` has PE-13b's ``CheckSpec`` fields under the same names,
so PE-14b passes a registered type straight to ``run_checks`` with its
``rules_adapter``.  ``lock_plan``, ``apply`` and ``rules_adapter`` are stored,
never called, here.  Real types register at import of their module, which its
owner appends to :data:`COMMAND_MODULES`; :func:`check_envelope` imports them
first, so a real type never reaches check 1 as unknown.

:func:`check_envelope` is check 1 (pure, no database), first failure wins:
an unsupported principal kind (403), a token bound to another campaign (403),
the static schema (400 ``bad_envelope``, including every input shape PE-13b's
runner assumes, then the digest), a principal kind the type does not allow
(403).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.request_context import Principal, PrincipalKind

from .checks import expected_kinds
from .digest import command_digest
from .envelope import EnvelopeRefusal, bad, is_int, parse_envelope, principal_key

ACCEPTED_PRINCIPALS = frozenset({PrincipalKind.USER, PrincipalKind.INTEGRATION_TOKEN})
# Dotted modules whose import registers real command types (PE-14d, PE-14e, ...).
COMMAND_MODULES: tuple[str, ...] = ("core.commands.grant_commands", "core.commands.sessions")
_LOADED: set[str] = set()
_TYPES: dict[str, "CommandType"] = {}
_MISSING = object()


def _never_terminal(ctx: Any, subject: Any) -> bool:
    return False


@dataclass(frozen=True, kw_only=True)
class CommandType:
    """Everything the pipeline knows about one command type (C:87-95, C:130-140)."""

    type: str
    subject: tuple[str, str]  # (envelope subject field, record kind)
    dependencies: Mapping[str, Callable[..., bool]]  # expected kind -> relation
    payload_ids: Mapping[str, str]  # payload field -> record kind
    requires_expected: bool
    derives_rules: bool
    allowed_principals: frozenset[PrincipalKind]
    rules_adapter: Any
    lock_plan: Callable[[Any], Any]
    apply: Callable[[Any], Any]
    terminal: Callable[[Any, Any], bool] = _never_terminal
    override_gm_only: bool = False

    def __post_init__(self) -> None:
        for name in ("dependencies", "payload_ids"):
            value = getattr(self, name)
            if isinstance(value, Mapping):
                object.__setattr__(self, name, MappingProxyType(dict(value)))
        if isinstance(self.allowed_principals, (set, frozenset)):
            object.__setattr__(self, "allowed_principals", frozenset(self.allowed_principals))


def _name(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def register(command_type: CommandType) -> None:
    """Register a type at import of its module; ``ValueError`` on any bad field."""

    if not isinstance(command_type, CommandType):
        raise TypeError("register() takes a CommandType")
    t = command_type
    problems = []
    if not _name(t.type):
        problems.append("type")
    if not (isinstance(t.subject, tuple) and len(t.subject) == 2
            and all(_name(part) for part in t.subject)):
        problems.append("subject")
    kinds = expected_kinds()
    if not (isinstance(t.dependencies, Mapping)
            and all(kind in kinds and callable(rel) for kind, rel in t.dependencies.items())):
        problems.append("dependencies")
    if not (isinstance(t.payload_ids, Mapping)
            and all(_name(k) and _name(v) for k, v in t.payload_ids.items())):
        problems.append("payload_ids")
    for name in ("requires_expected", "derives_rules", "override_gm_only"):
        if not isinstance(getattr(t, name), bool):
            problems.append(name)
    if not (isinstance(t.allowed_principals, frozenset) and t.allowed_principals
            and t.allowed_principals <= ACCEPTED_PRINCIPALS):
        problems.append("allowed_principals")
    adapter = t.rules_adapter
    if adapter is None or not all(callable(getattr(adapter, name, None))
                                  for name in ("check", "build_edition_matches")):
        problems.append("rules_adapter")
    for name in ("terminal", "lock_plan", "apply"):
        if not callable(getattr(t, name)):
            problems.append(name)
    if problems:
        raise ValueError(f"command type {t.type!r}: invalid {', '.join(problems)}")
    if t.type in _TYPES:
        raise ValueError(f"command type {t.type} is already registered")
    _TYPES[t.type] = t


def get(type: str) -> CommandType | None:
    return _TYPES.get(type)


def load_command_modules() -> None:
    """Import every listed type module once; an import error propagates."""

    for name in COMMAND_MODULES:
        if name not in _LOADED:
            importlib.import_module(name)
            _LOADED.add(name)


@dataclass(frozen=True)
class CheckedEnvelope:
    """Check 1's result: PE-14b takes the receipt key, principal and digest only from it."""

    envelope: Mapping[str, Any]
    command_type: CommandType
    principal: Principal
    principal_kind: str
    principal_id: str
    digest: str


def _check_typed(envelope: Mapping[str, Any], t: CommandType) -> None:
    subject_field = t.subject[0]
    subject = envelope["subject"]
    if set(subject) != {subject_field} or not _name(subject[subject_field]):
        raise bad(f"subject must hold exactly {subject_field}")
    payload = envelope["payload"]
    for name in t.payload_ids:
        value = payload.get(name)
        if not (value is None or _name(value)
                or (isinstance(value, list) and all(_name(v) for v in value))):
            raise bad(f"payload field {name} is not a record id")
    kinds, seen = expected_kinds(), set()
    for entry in envelope["expected"]:
        name = entry.get("kind")
        if not isinstance(name, str) or name not in t.dependencies or name not in kinds:
            raise bad("expected kind outside the dependency set")
        if not _name(entry.get("id")):
            raise bad("expected entry has no id")
        kind, value = kinds[name], entry.get(kinds[name].field, _MISSING)
        if kind.fixed is not None:
            ok = isinstance(value, type(kind.fixed)) and value == kind.fixed
        elif kind.field == "rev":
            ok = is_int(value) and value >= 0
        else:
            ok = value is not _MISSING
        if not ok:
            raise bad(f"expected {name} entry has an invalid {kind.field}")
        if (name, entry["id"]) in seen:
            raise bad("expected names a record twice")
        seen.add((name, entry["id"]))
    if t.requires_expected and not seen:
        raise bad("this type requires expected")


def check_envelope(envelope: Any, principal: Principal, *,
                   route_campaign_id: str) -> CheckedEnvelope:
    """Check 1 for an authenticated ``principal`` and the raw request body."""

    load_command_modules()
    if not isinstance(principal, Principal):
        raise TypeError("check_envelope needs a verified core.request_context.Principal")
    if not isinstance(route_campaign_id, str):
        raise TypeError("route_campaign_id must be a string")
    if principal.kind not in ACCEPTED_PRINCIPALS:
        raise EnvelopeRefusal("not_permitted")
    if (principal.kind is PrincipalKind.INTEGRATION_TOKEN
            and principal.campaign_id != route_campaign_id):
        raise EnvelopeRefusal("not_permitted")
    parsed = parse_envelope(envelope)
    command_type = _TYPES.get(parsed["type"])
    if command_type is None:
        raise bad("unknown type")
    if parsed["campaign_id"] != route_campaign_id:
        raise bad("campaign_id differs from the route")
    _check_typed(parsed, command_type)
    try:
        digest = command_digest(parsed)
    except (TypeError, ValueError) as exc:
        raise bad("the envelope is not finite JSON") from exc
    if principal.kind not in command_type.allowed_principals:
        raise EnvelopeRefusal("not_permitted")
    principal_kind, principal_id = principal_key(principal)
    return CheckedEnvelope(parsed, command_type, principal, principal_kind, principal_id,
                           digest)
