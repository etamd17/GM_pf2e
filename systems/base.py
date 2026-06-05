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
    # --- optional, system-specific extensions (defaulted, so PF2e stays valid) ---
    death_model: str = 'dying_wounded'  # 'dying_wounded' (pf2e) | 'injuries' (cosmere)
    phases: tuple = ()                  # named turn phases (cosmere's 4-phase queue); () = sorted-initiative list
    fast_actions: int = 0               # actions when electing 'fast' (cosmere); 0 = n/a
    slow_actions: int = 0               # actions when electing 'slow' (cosmere); 0 = n/a
    deflectable_damage: tuple = ()      # damage types Deflect reduces (cosmere); () = no deflect

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


@dataclass(frozen=True)
class NavLink:
    """One entry in a system's nav bar. `accent` flags the primary (hub) link."""
    label: str
    url: str
    title: str = ''
    accent: bool = False


@dataclass(frozen=True)
class SystemUI:
    """The host-app surfaces every system MUST provide. The invariant this
    encodes: a system always has BOTH a GM side and a player side -- `gm_home`
    and `player_home` are the landing routes the home / activate redirects send a
    GM vs a player to, and `gm_nav` / `player_nav` are their nav-bar link sets.
    Plain data (route strings + labels), so the registry stays Flask-free; the
    host app just reads it to drive redirects + the nav with no per-system
    branching, and adding a new system can't skip either hub."""
    gm_home: str
    player_home: str
    brand: str                 # short nav-bar wordmark (e.g. 'PF2E')
    gm_nav: tuple = ()         # tuple[NavLink]
    player_nav: tuple = ()     # tuple[NavLink]


@dataclass
class GameSystem:
    """A registered game system: identity + combat profile + UI hubs + a bound
    actor factory.

    The actor factory is bound at runtime by the host app (`bind_actor_factory`)
    once the concrete actor class is defined, so this package never has to
    import the Flask module (which would be a cycle: app -> systems -> app).
    """
    key: str
    label: str
    combat: CombatProfile
    ui: SystemUI
    _actor_factory: object = None

    def __post_init__(self):
        # Structural guarantee of the GM-side / player-side invariant: a system
        # cannot be constructed (hence cannot be registered) without BOTH hubs.
        if not isinstance(self.ui, SystemUI):
            raise TypeError(
                f"system {self.key!r} must declare a SystemUI (gm_home + player_home)"
            )
        if not self.ui.gm_home or not self.ui.player_home:
            raise ValueError(
                f"system {self.key!r} must provide BOTH a gm_home and a player_home"
            )

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
