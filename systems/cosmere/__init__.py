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

from systems.base import GameSystem, CombatProfile, Condition, SystemUI, NavLink
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

# The GM side + player side for Cosmere: the GM works from a command-center hub
# (the dashboard) with the roster / Builder / Bestiary / Tracker / session tools
# hanging off it; a player lands on their own character hub.
_UI = SystemUI(
    gm_home='/cosmere/gm',
    player_home='/cosmere/player',
    brand='COSMERE',
    gm_nav=(
        NavLink('GM Hub', '/cosmere/gm', accent=True),
        NavLink('Characters', '/cosmere/pcs', title='Cosmere characters'),
        NavLink('Builder', '/cosmere/builder'),
        NavLink('Bestiary', '/cosmere/bestiary'),
        NavLink('Tracker', '/tracker'),
        NavLink('GM Screen', '/cosmere/gmscreen', title='Cosmere rules reference'),
        NavLink('Generators', '/cosmere/generator', title='Rosharan names, NPCs, weather, loot'),
        NavLink('Threads', '/gm/threads'),
        NavLink('Status', '/status'),
        NavLink('Notes', '/notes', title='Session notes scratchpad'),
    ),
    player_nav=(
        NavLink('My Character', '/cosmere/player', title='My character'),
        NavLink('Party', '/cosmere/pcs', title='The party'),
        NavLink('Combat', '/tracker', title='Live combat'),
        NavLink('Notes', '/notes', title='Session notes scratchpad'),
    ),
)

SYSTEM = GameSystem(key='cosmere', label='Cosmere RPG', combat=_COMBAT, ui=_UI)
SYSTEM.bind_actor_factory(CosmereActor)


# Display names for the 3-letter Foundry codes (sheets / tracker).
ATTR_NAMES = {
    'str': 'Strength', 'spd': 'Speed', 'int': 'Intellect',
    'wil': 'Willpower', 'awa': 'Awareness', 'pre': 'Presence',
}
DEFENSE_NAMES = {'phy': 'Physical', 'cog': 'Cognitive', 'spi': 'Spiritual'}
SKILL_NAMES = {
    # 18 basic skills
    'agi': 'Agility', 'ath': 'Athletics', 'cra': 'Crafting', 'dec': 'Deception',
    'ded': 'Deduction', 'dis': 'Discipline', 'hwp': 'Heavy Weaponry',
    'inm': 'Intimidation', 'ins': 'Insight', 'lea': 'Leadership', 'lor': 'Lore',
    'lwp': 'Light Weaponry', 'med': 'Medicine', 'prc': 'Perception',
    'prs': 'Persuasion', 'stl': 'Stealth', 'sur': 'Survival', 'thv': 'Thievery',
    # 10 Surge skills (unlocked only for Radiants)
    'abr': 'Abrasion', 'adh': 'Adhesion', 'chs': 'Cohesion', 'dvs': 'Division',
    'grv': 'Gravitation', 'ill': 'Illumination', 'prg': 'Progression',
    'trp': 'Transportation', 'trs': 'Transformation', 'tsn': 'Tension',
}

# Governing attribute per skill code (authoritative — matches the Foundry actor
# data; guarded by tests). skill mod = ranks + this attribute's score.
SKILL_ATTR = {
    # 18 basic
    'agi': 'spd', 'ath': 'str', 'cra': 'int', 'dec': 'pre', 'ded': 'int',
    'dis': 'wil', 'hwp': 'str', 'inm': 'wil', 'ins': 'awa', 'lea': 'pre',
    'lor': 'int', 'lwp': 'spd', 'med': 'int', 'prc': 'awa', 'prs': 'pre',
    'stl': 'spd', 'sur': 'awa', 'thv': 'spd',
    # 10 Surge
    'abr': 'spd', 'adh': 'pre', 'chs': 'wil', 'dvs': 'int', 'grv': 'awa',
    'ill': 'pre', 'prg': 'awa', 'trp': 'int', 'trs': 'wil', 'tsn': 'str',
}
SURGE_SKILLS = ('abr', 'adh', 'chs', 'dvs', 'grv', 'ill', 'prg', 'trp', 'trs', 'tsn')
BASIC_SKILLS = tuple(c for c in SKILL_ATTR if c not in SURGE_SKILLS)
PATHS = ('agent', 'envoy', 'hunter', 'leader', 'scholar', 'warrior')

# Concise, rulebook-faithful one-line summaries of the conditions (Ch.9), for
# the GM Screen and condition tooltips. Keyed by _CONDITION_KEYS; the three in
# _VALUED carry a bracketed value and stack cumulatively.
CONDITION_INFO = {
    'afflicted':   'Take the bracketed damage at the end of each of your turns (every 10 seconds out of combat). Multiple Afflicted effects resolve separately.',
    'determined':  'When you fail a test you may add an Opportunity to the result, then remove Determined.',
    'disoriented': 'No reactions; your senses always count as obscured; Perception and other sense-based tests have disadvantage.',
    'empowered':   'On swearing an Ideal: advantage on all tests and your Investiture refills to maximum at the start of each turn. Ends at the end of the scene.',
    'enhanced':    'The bracketed attribute gains its bonus to tests, talents, and movement (not to defenses or maximums). Cumulative; several attributes can be Enhanced at once.',
    'exhausted':   'Subtract the bracketed penalty from every test result. Reduce by 1 per long rest (removed at 0). Cumulative; reaching -10 or lower kills you.',
    'focused':     'Abilities that cost focus cost 1 less.',
    'immobilized': 'Your movement rate becomes 0; you cannot move or be moved.',
    'prone':       'Lying down: you are also Slowed and melee attacks against you gain advantage. Standing (1 action) ends it; your movement is then reduced by 5 until your next turn.',
    'restrained':  'Your movement rate becomes 0 and you have disadvantage on all tests except those to escape.',
    'slowed':      'Your movement rate is halved (halve remaining movement if you are mid-move).',
    'stunned':     'Lose your reactions; on your turn you gain two fewer actions and no reaction.',
    'surprised':   'Lose reactions; you cannot take a fast turn and gain one fewer action. Removed after your next turn.',
    'unconscious': 'Movement 0; you fall Prone, drop held items, and can act only via Breathe Stormlight / Regenerate (if Radiant). You always go slow. A PC may wake at the end of any turn or on being healed to 1+ health (recovering 1 health if at 0).',
}


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


def adversary_docs() -> list:
    """Every bestiary adversary DOCUMENT -- the base ``cosmere-rpg`` system pack
    plus the ingested Foundry modules (Worldguide, Chasmfiend, Stonewalkers,
    animal companions), deduped by name (the base system wins a name clash).
    The single source for the bestiary, the tracker's adversary picker, and the
    by-id lookup used when adding a Cosmere combatant."""
    out, seen = [], set()
    for pack in ('companions-and-adversaries', 'module-adversaries'):
        for doc in load_pack(pack):
            if doc.get('type') != 'adversary':
                continue
            key = (doc.get('name') or '').strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(doc)
    return out


def load_adversaries() -> list:
    """Every adversary in the bestiary (base system + modules) as a CosmereActor."""
    return [CosmereActor(doc) for doc in adversary_docs()]
