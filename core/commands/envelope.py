"""The command envelope (contract section 3, C:224-235; PE-14a).

``parse_envelope`` checks the shape every command shares, whatever its type,
and returns a read-only copy holding only the known keys, so nothing the client
adds beside them (identity keys included) is ever read.  The checks that need
the command type (subject field, payload id fields, ``expected`` entries) are in
``registry.check_envelope``, which calls this.  Identity never comes from the
body: ``principal_key`` reads the authenticated ``Principal``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.request_context import Principal, PrincipalKind

SCHEMA_VERSION = 1
OPERATION_ID = re.compile(r"[A-Za-z0-9_-]{16,128}")
_STATUS = {"bad_envelope": 400, "not_permitted": 403}
# Deeper bodies are refused (400) before any recursion could raise RecursionError (500).
MAX_DEPTH = 32


class EnvelopeRefusal(Exception):
    """Check 1 refused the request; nothing is written (PE-14b answers by attribute)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        if code not in _STATUS:
            raise ValueError(f"check 1 cannot answer {code!r}")
        super().__init__(detail or code)
        self.code = code
        self.http_status = _STATUS[code]


def bad(detail: str) -> EnvelopeRefusal:
    return EnvelopeRefusal("bad_envelope", detail)


def principal_key(principal: Principal) -> tuple[str, str]:
    """The receipt and event principal: ``(kind, user_id or credential_id)``."""

    if principal.kind is PrincipalKind.USER:
        return PrincipalKind.USER.value, principal.user_id
    if principal.kind is PrincipalKind.INTEGRATION_TOKEN:
        return PrincipalKind.INTEGRATION_TOKEN.value, principal.credential_id
    raise EnvelopeRefusal("not_permitted")


def plain(value: Any, depth: int = 0) -> Any:
    """A detached copy in plain dicts and lists, at most ``MAX_DEPTH`` levels deep."""

    if depth > MAX_DEPTH:
        raise bad("the envelope is nested too deeply")
    if isinstance(value, Mapping):
        return {k: plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v, depth + 1) for v in value]
    return value


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_envelope(body: Any) -> Mapping[str, Any]:
    """The type-independent checks of check 1; raises ``EnvelopeRefusal`` (400)."""

    if not isinstance(body, Mapping):
        raise bad("the body is not an object")
    if not is_int(body.get("schema")) or body["schema"] != SCHEMA_VERSION:
        raise bad("unknown schema")
    if not isinstance(body.get("type"), str):
        raise bad("unknown type")
    operation_id = body.get("operation_id")
    if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(operation_id):
        raise bad("malformed operation_id")
    if not isinstance(body.get("campaign_id"), str):
        raise bad("campaign_id is not a string")
    if not is_int(body.get("epoch")):
        raise bad("epoch is not an integer")
    for name in ("subject", "rules", "payload"):
        if not isinstance(body.get(name), Mapping):
            raise bad(f"{name} is not an object")
    if not isinstance(body["rules"].get("profile"), str):
        raise bad("rules.profile is not a string")
    expected = body.get("expected")
    if not isinstance(expected, list) or not all(isinstance(e, Mapping) for e in expected):
        raise bad("expected is not a list of objects")
    override = body.get("override")
    if override is not None and (not isinstance(override, Mapping)
                                 or not isinstance(override.get("reason"), str)
                                 or not override["reason"].strip()):
        raise bad("override must be an object with a reason")
    parsed = {name: plain(body.get(name))
              for name in ("schema", "operation_id", "campaign_id", "epoch", "type",
                           "subject", "expected", "rules", "override", "payload", "client")}
    return MappingProxyType(parsed)
