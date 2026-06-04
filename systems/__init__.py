"""System registry — the seam that lets the platform host more than PF2e.

Each game system (pf2e today, cosmere next) registers a `GameSystem` describing
its actor contract + combat profile. Character/actor loading dispatches through
this registry by the envelope's flat `system` key, so PF2e stays the default and
a second system slots in without touching the load sites.

This package is intentionally decoupled from app.py (it never imports the Flask
module), so it imports fast and is unit-testable on its own. The host app binds
the concrete actor class (app.Character) at startup via
`GameSystem.bind_actor_factory`, avoiding a `systems -> app` import cycle.
"""
from __future__ import annotations

from systems.base import (  # noqa: F401  (re-exported for callers)
    GameSystem, CombatProfile, Condition, DEFAULT_SYSTEM,
)


class UnknownSystemError(KeyError):
    """Raised when a system key has no registered GameSystem."""


_REGISTRY = {}


def _norm(key) -> str:
    return str(key or '').strip().lower()


def register(system) -> GameSystem:
    """Register (or replace) a system by its key. Idempotent."""
    if not isinstance(system, GameSystem):
        raise TypeError(f"expected GameSystem, got {type(system).__name__}")
    _REGISTRY[_norm(system.key)] = system
    return system


def get(key) -> GameSystem:
    """Return the GameSystem for `key` (case-insensitive), or raise."""
    try:
        return _REGISTRY[_norm(key)]
    except KeyError:
        raise UnknownSystemError(
            f"unknown system {key!r}; registered: {sorted(_REGISTRY)}"
        ) from None


def is_registered(key) -> bool:
    return _norm(key) in _REGISTRY


def all_systems() -> list:
    """Every registered system, ordered by key."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def system_for_doc(doc, default: str = DEFAULT_SYSTEM) -> GameSystem:
    """Resolve the GameSystem for a character envelope by its flat `system` key,
    falling back to `default` when the key is absent.

    Raises UnknownSystemError if the named (or default) system isn't registered,
    so a character is never silently mis-loaded as a different system.
    """
    key = default
    if isinstance(doc, dict) and doc.get('system'):
        key = doc['system']
    return get(key)


def actor_for_doc(doc, file_path: str = '', default: str = DEFAULT_SYSTEM):
    """Instantiate the right actor class for a stored character envelope."""
    return system_for_doc(doc, default).make_actor(doc, file_path)


# --- register the built-in systems (import side effect) --------------------
from systems import pf2e as _pf2e  # noqa: E402

register(_pf2e.SYSTEM)
