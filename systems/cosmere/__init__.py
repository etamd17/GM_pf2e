"""Cosmere RPG system definition for the registry.

Registers Cosmere against the same actor contract as PF2e, with a combat
profile describing its genuinely different model: a 4-phase turn queue with
fast/slow action election, Deflect-by-damage-type, named (not typed) effect
stacking, the injury death-spiral, and the Cosmere condition set.

Unlike PF2e (whose actor class lives in app.py and is bound by the host),
``CosmereActor`` is self-contained, so this package binds it as the actor
factory directly. Ingested content lives under ``content/`` (regenerate with
``tools/ingest_cosmere.py``).
"""
from __future__ import annotations

import json
import os

from systems.base import GameSystem, CombatProfile, Condition
from systems.cosmere.actor import CosmereActor, cosmere_max_health, tier_of  # noqa: F401

# Cosmere conditions (confirmed from cosmere-rpg v2.0.5 runtime config). The
# three stackable/valued ones carry an amount; the rest are on/off and refresh.
_VALUED = frozenset({'afflicted', 'enhanced', 'exhausted'})
_CONDITION_KEYS = (
    'afflicted', 'determined', 'disoriented', 'empowered', 'enhanced',
    'exhausted', 'focused', 'immobilized', 'prone', 'restrained', 'slowed',
    'stunned', 'surprised', 'unconscious',
)

_COMBAT = CombatProfile(
    # Turn order is a 4-phase queue, not a sorted initiative list; within a
    # phase, higher Speed acts first (then d20). Rulebook Ch.10.
    initiative_stat='spd',
    initiative_higher_first=True,
    action_model='fast_slow_phases',
    action_count=3,                # max (electing 'slow'); 'fast' is 2 (below)
    reaction_count=1,
    fast_actions=2,
    slow_actions=3,
    phases=('fast_pc', 'fast_npc', 'slow_pc', 'slow_npc'),
    defenses=('phy', 'cog', 'spi'),
    damage_pool='health',
    down_condition='unconscious',
    death_model='injuries',        # 0 HP -> Unconscious + an Injury Roll
    deflectable_damage=('impact', 'keen', 'energy'),  # spirit/vital bypass Deflect
    rollables=('skill', 'attack', 'damage', 'plot'),
    bonus_types=('named',),
    stacking_rule='named',         # same-name don't stack (take larger); diff-name sum
    conditions=tuple(Condition(k, k in _VALUED) for k in _CONDITION_KEYS),
)

SYSTEM = GameSystem(key='cosmere', label='Cosmere RPG', combat=_COMBAT)
SYSTEM.bind_actor_factory(CosmereActor)


# --- ingested content ------------------------------------------------------
_CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'content')

# Pack name -> document kind, for callers.
PACKS = (
    'ancestries', 'cultures', 'heroic-paths', 'actions', 'items',
    'companions-and-adversaries', 'tables', 'starter-rules',
)


def content_dir() -> str:
    return _CONTENT_DIR


def load_pack(pack: str) -> list:
    """Return the list of documents in an ingested pack (empty if not present)."""
    path = os.path.join(_CONTENT_DIR, f'{pack}.json')
    if not os.path.isfile(path):
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_adversaries() -> list:
    """Every adversary in the bestiary as a CosmereActor."""
    return [
        CosmereActor(doc)
        for doc in load_pack('companions-and-adversaries')
        if doc.get('type') == 'adversary'
    ]
