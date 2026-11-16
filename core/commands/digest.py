"""The command digest (contract section 3, C:236-241; PE-14a).

SHA-256 of the canonical JSON of ``schema, campaign_id, type, subject,
expected, rules, override, payload``: sorted keys, no spaces,
``ensure_ascii=False``, ``allow_nan=False``, integral floats written as ints.
An absent ``override`` is ``null``.  ``operation_id``, ``epoch`` and
``client`` are not part of it.  Ported from the VTT's ``_canonical`` /
``command_digest`` at ``00b334b4``; for dicts and lists it gives the same
bytes.  It also normalizes tuples and other mappings, so a caller that does
not pass plain dicts and lists still gets the same digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

DIGEST_FIELDS = ("schema", "campaign_id", "type", "subject", "expected", "rules",
                 "override", "payload")


def canonical(value: Any) -> Any:
    """Integral floats as ints, recursively, so ``64`` and ``64.0`` hash alike."""

    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, Mapping):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    return value


def command_digest(envelope: Mapping[str, Any]) -> str:
    """The digest of a parsed envelope.  Raises ``ValueError`` on NaN or infinity."""

    body = {name: envelope.get(name) for name in DIGEST_FIELDS}
    raw = json.dumps(canonical(body), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
