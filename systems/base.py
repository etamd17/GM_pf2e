"""Core contracts for the system registry: the actor contract (`GameSystem`)
and the declarative combat profile every system describes.

`CombatProfile` is intentionally DECLARATIVE at this stage. It describes a
system's conditions, defenses, action economy, initiative ordering and
effect-stacking rules so system-agnostic code/UI can read them from the
registry instead of hardcoding PF2e assumptions. Behavioral routing (applying
damage, resolving rolls) still lives in the host app for PF2e; it is lifted
into per-system hooks as later phases (Cosmere) actually require divergent
behavior. The slots below mirror the architecture spine:
conditions / initiative / defenses / damage / action_economy / rollables.

This module imports nothing from the host Flask app, so the registry stays
fast to import and unit-testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SYSTEM = 'pf2e'


@dataclass(frozen=True)
class Condition:
    """One entry in a system's condition catalog."""
    key: str
    valued: bool = False   # numeric value (e.g. frightened 2) vs on/off (e.g. prone)


@dataclass(frozen=True)
class CombatProfile:
    """A declarative description of a system's combat model."""
    # initiative / turn order
    initiative_stat: str          # stat that seeds initiative ('perception' for pf2e)
    initiative_higher_first: bool  # True = higher result acts earlier
    action_model: str             # 'three_action' (pf2e) | 'fast_slow_phases' (cosmere)
    action_count: int             # baseline actions per turn
    reaction_count: int           # baseline reactions per turn
    # defenses
    defenses: tuple               # ordered defense stat keys
    # damage / death model
    damage_pool: str              # the depletable pool ('hp')
    down_condition: str           # state entered at 0 of the pool ('dying' for pf2e)
    # rolls
    rollables: tuple              # roll categories the system supports
    # effect engine config
    bonus_types: tuple            # typed-bonus categories
    stacking_rule: str            # 'typed_best_worst' (pf2e) | 'named' (cosmere)
    # conditions
    conditions: tuple             # tuple[Condition]

    def condition_keys(self) -> tuple:
        """Just the condition keys, in catalog order."""
        return tuple(c.key for c in self.conditions)

    def is_valued(self, key: str) -> bool:
        """True if `key` is a numeric-valued condition in this system."""
        k = (key or '').strip().lower()
        for c in self.conditions:
            if c.key == k:
                return c.valued
        return False


@dataclass
class GameSystem:
    """A registered game system: identity + combat profile + a bound actor
    factory.

    The actor factory is bound at runtime by the host app (`bind_actor_factory`)
    once the concrete actor class is defined, so this package never has to
    import the Flask module (which would be a cycle: app -> systems -> app).
    """
    key: str
    label: str
    combat: CombatProfile
    _actor_factory: object = None

    def bind_actor_factory(self, factory) -> 'GameSystem':
        """Bind `factory(doc, file_path='') -> actor`. Returns self for chaining."""
        if not callable(factory):
            raise TypeError("actor factory must be callable")
        self._actor_factory = factory
        return self

    def make_actor(self, doc, file_path: str = ''):
        """Instantiate this system's actor from a stored character envelope."""
        if self._actor_factory is None:
            raise RuntimeError(
                f"system {self.key!r} has no actor factory bound; the host app "
                "must call bind_actor_factory() at startup"
            )
        return self._actor_factory(doc, file_path)
