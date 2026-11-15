"""The ``409 stale_revision`` body (contract C:147-151; PE-13b)."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

_CONFLICT_FIELDS = ("kind", "id", "expected", "current")


def conflict_body(operation_id: str, conflicts: Iterable[Mapping[str, Any]],
                  current: Mapping[str, Any]) -> dict[str, Any]:
    """The exact conflict body.  ``conflicts`` and ``current`` are already projected."""

    return {
        "ok": False,
        "code": "stale_revision",
        "operation_id": operation_id,
        "conflicts": [{name: conflict[name] for name in _CONFLICT_FIELDS}
                      for conflict in conflicts],
        "current": dict(current),
    }
