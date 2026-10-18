"""D&D 5th Edition (2014 + 2024 rules) for the system registry.

Content comes from the 5etools data snapshot (``dnd5e_data/``; see
``tools/dnd5e/``), read through ``systems.dnd5e.content``. Characters are
build logs recomputed by ``systems.dnd5e.rules.character``; combat is run by
``systems.dnd5e.combat`` and served by the ``systems.dnd5e.routes`` blueprint.

Importing this package is cheap: nothing opens the database until used.
"""
from __future__ import annotations

from systems.base import GameSystem, CombatProfile, Condition, SystemUI, NavLink
from systems.dnd5e.rules.tables import CONDITIONS

_COMBAT = CombatProfile(
    initiative_stat='dex',
    initiative_higher_first=True,
    action_model='action_bonus_reaction',
    action_count=1,               # one Action (+ a Bonus Action and movement)
    reaction_count=1,
    defenses=('ac',),
    damage_pool='hp',
    down_condition='unconscious',
    death_model='death_saves',
    rollables=('check', 'save', 'attack', 'damage', 'initiative', 'death_save'),
    bonus_types=('untyped',),
    stacking_rule='same_name_no_stack',   # same-named effects don't stack (PHB p.205)
    conditions=tuple(Condition(k, k == 'exhaustion') for k in CONDITIONS),
)

_UI = SystemUI(
    gm_home='/dnd5e/gm',
    player_home='/dnd5e/player',
    brand='D&D 5E',
    gm_nav=(
        NavLink('GM Hub', '/dnd5e/gm', accent=True),
        NavLink('Characters', '/dnd5e/characters', title='Party characters'),
        NavLink('Encounters', '/dnd5e/encounters', title='Encounter builder'),
        NavLink('Tracker', '/dnd5e/tracker', title='Initiative + combat tracker'),
        NavLink('Bestiary', '/dnd5e/bestiary'),
        NavLink('GM Screen', '/dnd5e/gmscreen', title='Rules quick reference'),
        NavLink('Compendium', '/dnd5e/compendium', title='Spells, items, feats, rules'),
    ),
    player_nav=(
        NavLink('My Character', '/dnd5e/player', title='My character'),
        NavLink('Party', '/dnd5e/characters', title='The party'),
        NavLink('Combat', '/dnd5e/combat', title='Live combat'),
        NavLink('Compendium', '/dnd5e/compendium'),
    ),
)


class Dnd5eActor:
    """Minimal actor for registry-driven loaders (``actor_for_doc``).

    5e PCs live in their own per-campaign store and never enter the PF2e
    PARTY_LIBRARY, so this exists to honour the registry contract (and to keep
    any generic loader that meets a 5e doc from mis-loading it as PF2e)."""

    system = 'dnd5e'

    def __init__(self, data, file_path: str = ''):
        self.file_path = file_path
        self._raw = data if isinstance(data, dict) else {}
        self.name = self._raw.get('name') or 'Unnamed'
        self.id = self._raw.get('id')
        self.is_pc = True

    def sheet(self) -> dict:
        from systems.dnd5e.rules.character import compute
        return compute(self._raw)


SYSTEM = GameSystem(key='dnd5e', label='D&D 5th Edition', combat=_COMBAT, ui=_UI)
SYSTEM.bind_actor_factory(Dnd5eActor)
