"""Active Effects engine — PF2e bonus-typed stat modifiers from any
source (condition / spell / feat / item / ability).

Capabilities (current pass):
  * Unified schema for conditions, spells, feats, items, custom.
  * PF2e bonus typing: highest positive / lowest negative per
    (stat, bonus_type) group; untyped stacks freely.
  * Duration tracking with auto-expiry through the round counter.
  * Modifier predicates — "+1 attack only on ranged strikes",
    "+1 AC only vs the marked creature", "fires once then consumes".
  * Save-vs-DC suppression — effects can declare a save; on success
    the effect is marked `suppressed: True` and stops contributing.
  * Effect chains — a catalog entry can declare follow-up effects
    that fire on apply (e.g. Aid casts an "Aided" marker on its
    target). The apply path materializes the chain.
  * Sheet-level integration — Character objects expose the same
    schema via `pc_active_effects`; the engine's compute helpers
    don't care whether the source list lives on a token or a sheet.

What's NOT here yet:
  * Permanent-magical-item bonuses tied to ABP / fundamental runes
    (those live in the Character ABP system, not here).
"""
from __future__ import annotations
from typing import Dict, Iterable, List, Mapping, Optional, Tuple


# ── Schema ─────────────────────────────────────────────────────────
#
# An effect (one of the entries on a token's `active_effects` list):
#   {
#     'id': 'uuid',
#     'name': 'Heroism',
#     'source': 'spell' | 'feat' | 'item' | 'condition' | 'ability' | 'custom',
#     'source_id': 'heroism' | None,    # catalog key, or None for custom
#     'caster': 'Go\'el' | None,         # who applied it
#     'tags': ['mental', 'fortune'],
#     'duration': {
#       'type': 'rounds' | 'minutes' | 'until_end_of_turn'
#             | 'until_start_of_caster_turn' | 'permanent',
#       'value': 10,
#       'expires_at_round': 7,           # for round-based, computed at apply
#       'expires_at_turn': None,         # for end-of-turn types
#     },
#     'modifiers': [
#       {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
#       ...
#     ],
#   }
#
# A modifier (within an effect):
#   stat:        'ac' | 'fort' | 'ref' | 'will' | 'perception' | 'attack'
#              | 'damage' | 'skills' | 'dc' | 'speed' | 'actions' | 'reactions'
#              | 'hp_temp'
#   op:          'add' (numeric delta) | 'set' (override)
#   value:       int (numeric). Either explicit or '-V' / 'V' (resolves
#                from a condition's numeric value at compute time —
#                only used by conditions table).
#   bonus_type:  'status' | 'circumstance' | 'item' | 'untyped'
#                Penalties carry the same `bonus_type` tag. PF2e rule
#                is "highest of each type" for positives, "lowest of
#                each type" for negatives, applied separately.
#   tag:         optional sub-filter (e.g. 'ranged' for Prone's AC
#                penalty applying only to ranged attackers, 'str' for
#                Enfeebled's penalty on Str-keyed skills). Today's
#                compute pipeline applies the modifier flatly; the
#                tag is preserved for future trigger-aware filtering.


# ── PF2E condition modifiers (re-expressed in the unified schema) ─────
PF2E_CONDITION_EFFECTS: Dict[str, List[Dict]] = {
    'frightened': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'will',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'perception', 'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Frightened'},
    ],
    'sickened': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'will',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'perception', 'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Sickened'},
    ],
    'enfeebled': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Enfeebled', 'tag': 'str_melee'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Enfeebled', 'tag': 'str_melee'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Enfeebled', 'tag': 'str'},
    ],
    'clumsy': [
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Clumsy'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Clumsy'},
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Clumsy', 'tag': 'dex_finesse'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Clumsy', 'tag': 'dex'},
    ],
    'drained': [
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Drained'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Drained', 'tag': 'con'},
    ],
    'stupefied': [
        {'stat': 'will',       'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Stupefied'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Stupefied'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'bonus_type': 'status', 'source': 'Stupefied', 'tag': 'int_wis_cha'},
    ],
    'off_guard':   [{'stat': 'ac', 'op': 'add', 'value': -2, 'bonus_type': 'circumstance', 'source': 'Off-Guard'}],
    'flat_footed': [{'stat': 'ac', 'op': 'add', 'value': -2, 'bonus_type': 'circumstance', 'source': 'Flat-Footed'}],
    'prone': [
        {'stat': 'attack',     'op': 'add', 'value': -2, 'bonus_type': 'circumstance', 'source': 'Prone'},
        {'stat': 'ac',         'op': 'add', 'value': -2, 'bonus_type': 'circumstance', 'source': 'Prone', 'tag': 'vs_ranged'},
    ],
    'slowed':   [{'stat': 'actions', 'op': 'add', 'value': '-V', 'bonus_type': 'untyped', 'source': 'Slowed'}],
    'stunned':  [{'stat': 'actions', 'op': 'add', 'value': '-V', 'bonus_type': 'untyped', 'source': 'Stunned'}],
    'quickened':[{'stat': 'actions', 'op': 'add', 'value': 1,    'bonus_type': 'untyped', 'source': 'Quickened'}],
}


# ── Effect catalog ────────────────────────────────────────────────────
# Common PF2e spells / feats / items at L1–5. Each entry is the
# template for a single application. `duration.type` and `value` follow
# PF2e norms (Bless = 1 minute = 10 rounds, Bane = 1 minute, etc.).
# Add an entry here once; the catalog endpoint surfaces it to the GM
# dropdown automatically.
#
# Tags worth knowing about:
#   - 'aoe_emanation' marks spells that pulse from the caster — the
#     UI surfaces a reminder to apply to multiple tokens.
#   - 'concentration' means the caster ending concentration drops it.
EFFECT_CATALOG: Dict[str, Dict] = {
    # — 1st-level spells —
    'bless': {
        'name': 'Bless',
        'source': 'spell',
        'source_id': 'bless',
        'tags': ['mental', 'aoe_emanation', 'concentration'],
        'duration': {'type': 'minutes', 'value': 1},
        'description': '+1 status to attack rolls of you and allies in the emanation.',
        'modifiers': [
            {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
        ],
    },
    'bane': {
        'name': 'Bane',
        'source': 'spell',
        'source_id': 'bane',
        'tags': ['mental', 'aoe_emanation', 'concentration'],
        'duration': {'type': 'minutes', 'value': 1},
        'description': '−1 status to attack rolls of enemies in the emanation.',
        'modifiers': [
            {'stat': 'attack', 'op': 'add', 'value': -1, 'bonus_type': 'status'},
        ],
    },
    'mage_armor': {
        'name': 'Mage Armor',
        'source': 'spell',
        'source_id': 'mage_armor',
        'tags': ['arcane', 'occult'],
        'duration': {'type': 'permanent', 'value': None},  # until next prep
        'description': '+1 item to AC, max Dex +5.',
        'modifiers': [
            {'stat': 'ac', 'op': 'add', 'value': 1, 'bonus_type': 'item'},
        ],
    },
    'shield': {
        'name': 'Shield',
        'source': 'spell',
        'source_id': 'shield',
        'tags': ['cantrip', 'force'],
        'duration': {'type': 'rounds', 'value': 1},
        'description': '+1 circumstance to AC; can Shield Block once before breaking.',
        'modifiers': [
            {'stat': 'ac', 'op': 'add', 'value': 1, 'bonus_type': 'circumstance'},
            # Reaction marker: scope='reaction' means "this doesn't
            # apply to baseline stats, it FIRES when the event hits."
            # Damage handlers call find_reaction_triggers to surface it.
            {'stat': None, 'op': 'add', 'value': 0, 'bonus_type': 'untyped',
             'source': 'Shield Block',
             'predicate': {'scope': 'reaction', 'event': 'on_damaged',
                           'reaction_name': 'Shield Block',
                           'hint': 'Force-shield absorbs damage equal to its Hardness, then dissipates.'}},
        ],
    },
    'sure_strike': {
        'name': 'Sure Strike',
        'source': 'spell',
        'source_id': 'sure_strike',
        'tags': ['fortune', 'self', 'roll_advantage'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        # Consumed after the marked-for strike fires. Module hook
        # `before_strike` should check for this marker; the engine
        # auto-removes it via consume_triggered('next_strike').
        'consumes_on': 'next_strike',
        'description': 'Roll the next Strike twice, take the better. (Auto-clears after the marked strike.)',
        'modifiers': [
            # Pure marker: zero stat delta, but the predicate carries
            # the scope so a strike-aware module can detect it.
            {'stat': None, 'op': 'add', 'value': 0, 'bonus_type': 'untyped',
             'predicate': {'scope': 'strike', 'target': 'self', 'until': 'next_strike'},
             'source': 'Sure Strike'},
        ],
    },
    'true_strike': {  # alias for older books
        'name': 'True Strike',
        'source': 'spell',
        'source_id': 'sure_strike',
        'tags': ['fortune', 'self'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': 'Same as Sure Strike — kept as alias for older books.',
        'modifiers': [],
    },
    'magic_weapon': {
        'name': 'Magic Weapon',
        'source': 'spell',
        'source_id': 'magic_weapon',
        'tags': ['arcane', 'divine'],
        'duration': {'type': 'minutes', 'value': 1},
        'description': 'Weapon counts as +1 striking (extra die handled on roll).',
        'modifiers': [
            {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'item'},
        ],
    },
    'protection': {
        'name': 'Protection',
        'source': 'spell',
        'source_id': 'protection',
        'tags': ['divine'],
        'duration': {'type': 'minutes', 'value': 1},
        'description': '+1 status to AC and saves vs creatures of the chosen alignment.',
        'modifiers': [
            # Predicate-gated: the +1 only applies when the context's
            # tags list includes 'vs_alignment'. The strike / save
            # resolver passes that tag in when the attacker is the
            # tagged alignment.
            {'stat': 'ac',   'op': 'add', 'value': 1, 'bonus_type': 'status',
             'predicate': {'tag': 'vs_alignment'}},
            {'stat': 'fort', 'op': 'add', 'value': 1, 'bonus_type': 'status',
             'predicate': {'tag': 'vs_alignment'}},
            {'stat': 'ref',  'op': 'add', 'value': 1, 'bonus_type': 'status',
             'predicate': {'tag': 'vs_alignment'}},
            {'stat': 'will', 'op': 'add', 'value': 1, 'bonus_type': 'status',
             'predicate': {'tag': 'vs_alignment'}},
        ],
    },
    # — 2nd-level spells —
    'mirror_image': {
        'name': 'Mirror Image',
        'source': 'spell',
        'source_id': 'mirror_image',
        'tags': ['illusion'],
        'duration': {'type': 'minutes', 'value': 1},
        'description': 'Three images; attacks have a chance to hit an image instead. (Tracked as a marker.)',
        'modifiers': [],
    },
    # — 3rd-level spells —
    'heroism': {
        'name': 'Heroism (3rd)',
        'source': 'spell',
        'source_id': 'heroism',
        'tags': ['mental'],
        'duration': {'type': 'minutes', 'value': 10},
        'description': '+1 status to attack rolls, perception, saves, and skill checks.',
        'modifiers': [
            {'stat': 'attack',     'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'perception', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'fort',       'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'ref',        'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'will',       'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'skills',     'op': 'add', 'value': 1, 'bonus_type': 'status'},
        ],
    },
    'haste': {
        'name': 'Haste',
        'source': 'spell',
        'source_id': 'haste',
        'tags': ['arcane', 'occult'],
        'duration': {'type': 'rounds', 'value': 1},  # 1 round per cast at base
        'description': '+1 extra Stride or Strike per turn.',
        'modifiers': [
            {'stat': 'actions', 'op': 'add', 'value': 1, 'bonus_type': 'untyped'},
        ],
    },
    'slow': {
        'name': 'Slow',
        'source': 'spell',
        'source_id': 'slow',
        'tags': ['arcane', 'occult', 'incapacitation'],
        'duration': {'type': 'rounds', 'value': 6},
        'description': 'Will save. Crit fail → slowed 2 / 6 rd; fail → slowed 1 / 6 rd; success → slowed 1 / 1 rd; crit success → unaffected.',
        # Default modifier reflects fail outcome (slowed 1). The save
        # resolver will halve duration on success or null on crit_success.
        'modifiers': [
            {'stat': 'actions', 'op': 'add', 'value': -1, 'bonus_type': 'untyped'},
        ],
        'save': {
            'type': 'will',
            'dc': 0,  # GM supplies on apply (spell DC)
            'on_crit_success': 'negate',
            'on_success':      'reduce',
            'on_failure':      'apply',
            'on_crit_failure': 'stronger',
        },
    },
    'hideous_laughter': {
        'name': 'Hideous Laughter',
        'source': 'spell',
        'source_id': 'hideous_laughter',
        'tags': ['emotion', 'mental'],
        'duration': {'type': 'rounds', 'value': 1},
        'description': 'Will save. Success → no effect; fail → slowed 1 + off-guard 1 rd; crit fail → also incapacitated.',
        'modifiers': [
            {'stat': 'actions', 'op': 'add', 'value': -1, 'bonus_type': 'untyped'},
            {'stat': 'ac',      'op': 'add', 'value': -2, 'bonus_type': 'circumstance'},
        ],
        'save': {
            'type': 'will',
            'dc': 0,
            'on_crit_success': 'negate',
            'on_success':      'negate',
            'on_failure':      'apply',
            'on_crit_failure': 'stronger',
        },
    },
    # — Class features —
    'inspire_courage': {
        'name': 'Inspire Courage',
        'source': 'feat',
        'source_id': 'inspire_courage',
        'tags': ['composition', 'mental', 'aoe_emanation', 'bard'],
        'duration': {'type': 'rounds', 'value': 1},
        'description': '+1 status to attacks, damage, frightened saves for allies in 60ft.',
        'modifiers': [
            {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'damage', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'will',   'op': 'add', 'value': 1, 'bonus_type': 'status', 'tag': 'vs_frightened'},
        ],
    },
    'inspire_defense': {
        'name': 'Inspire Defense',
        'source': 'feat',
        'source_id': 'inspire_defense',
        'tags': ['composition', 'mental', 'aoe_emanation', 'bard'],
        'duration': {'type': 'rounds', 'value': 1},
        'description': '+1 status to AC and all saves for allies in 60ft.',
        'modifiers': [
            {'stat': 'ac',   'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'fort', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'ref',  'op': 'add', 'value': 1, 'bonus_type': 'status'},
            {'stat': 'will', 'op': 'add', 'value': 1, 'bonus_type': 'status'},
        ],
    },
    'aid_success': {
        'name': 'Aid (success)',
        'source': 'feat',
        'source_id': 'aid_success',
        'tags': ['general'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': '+1 circumstance to the next attempted check from the aided action.',
        'modifiers': [
            {'stat': 'skills', 'op': 'add', 'value': 1, 'bonus_type': 'circumstance'},
        ],
    },
    'aid_crit_success': {
        'name': 'Aid (crit success)',
        'source': 'feat',
        'source_id': 'aid_crit_success',
        'tags': ['general'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': '+2 circumstance (or +3 at higher levels) to the next attempted check.',
        'modifiers': [
            {'stat': 'skills', 'op': 'add', 'value': 2, 'bonus_type': 'circumstance'},
        ],
    },
    # — Items —
    'bestial_mutagen_lesser': {
        'name': 'Bestial Mutagen (lesser)',
        'source': 'item',
        'source_id': 'bestial_mutagen_lesser',
        'tags': ['alchemical', 'elixir', 'polymorph'],
        'duration': {'type': 'minutes', 'value': 10},
        'description': '+1 item to attack with natural weapons (melee), −2 status to AC.',
        'modifiers': [
            # Predicate-gated: the +1 item only applies when the
            # context flags the strike as a melee natural weapon.
            # Old `tag: 'natural_weapon'` was decorative; now it
            # actually gates application.
            {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'item',
             'predicate': {'scope': 'melee', 'tag': 'natural_weapon'}},
            {'stat': 'ac',     'op': 'add', 'value': -2, 'bonus_type': 'status'},
        ],
    },
    'shield_potency_minor': {
        'name': 'Minor Shield Potency',
        'source': 'item',
        'source_id': 'shield_potency_minor',
        'tags': ['shield'],
        'duration': {'type': 'permanent', 'value': None},
        'description': '+1 item to AC (when raised).',
        'modifiers': [
            {'stat': 'ac', 'op': 'add', 'value': 1, 'bonus_type': 'item', 'tag': 'shield_raised'},
        ],
    },
    # — Reaction markers (no stat delta, just a trigger hint) —
    'nimble_dodge': {
        'name': 'Nimble Dodge (ready)',
        'source': 'feat',
        'source_id': 'nimble_dodge',
        'tags': ['rogue'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': 'When an attack targets you, gain +2 circumstance to AC against that attack.',
        'modifiers': [
            {'stat': None, 'op': 'add', 'value': 0, 'bonus_type': 'untyped',
             'source': 'Nimble Dodge',
             'predicate': {'scope': 'reaction', 'event': 'on_targeted',
                           'reaction_name': 'Nimble Dodge',
                           'hint': '+2 circumstance to AC vs this attack.'}},
        ],
    },
    'liberating_step': {
        'name': 'Liberating Step (ready)',
        'source': 'feat',
        'source_id': 'liberating_step',
        'tags': ['champion'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': 'Trigger when an enemy hits your ally: ally gets resistance, can Step.',
        'modifiers': [
            {'stat': None, 'op': 'add', 'value': 0, 'bonus_type': 'untyped',
             'source': 'Liberating Step',
             'predicate': {'scope': 'reaction', 'event': 'on_ally_struck',
                           'reaction_name': 'Liberating Step',
                           'hint': 'Ally gains resistance equal to 2+lvl + a free Step.'}},
        ],
    },
    # — Generic catch-all —
    'custom': {
        'name': 'Custom',
        'source': 'custom',
        'source_id': 'custom',
        'tags': [],
        'duration': {'type': 'permanent', 'value': None},
        'description': 'Author your own — set stat / op / value / bonus_type / duration.',
        'modifiers': [],
    },
}


# ── Modifier predicate model ─────────────────────────────────────────
# A modifier can carry an optional `predicate` dict that gates when it
# applies. The engine's compute helpers accept an optional `context`
# argument describing the current situation; predicates evaluate against
# the context.
#
#   predicate = {
#     'scope':  'always'              # default: applies to every compute
#             | 'strike'              # only when computing a strike roll
#             | 'attack'              # alias for strike
#             | 'damage'              # only when computing damage
#             | 'spell_attack' | 'spell_save'
#             | 'ranged' | 'melee'
#             | 'save' | 'skill' | 'perception',
#     'target': 'any'                 # default: any target
#             | 'self'                # only when subject == self
#             | <instance_id str>     # only when target instance_id matches,
#     'tag':    str | None,           # match if the context carries this tag
#     'until':  'permanent'           # consumes never
#             | 'next_strike'         # mark consumed after the next strike
#             | 'next_action'         # mark consumed after the next action
#             | 'end_of_turn'         # mark consumed at end of caster's turn
#   }
#
# A modifier with no predicate is treated as `{scope:'always',
# target:'any', until:'permanent'}` — same behavior as before the
# trigger pass landed.
#
# Compute context (passed into compute_token_stats / compute_for_action):
#   ctx = {
#     'scope':  'strike' | 'damage' | 'save' | 'skill' | 'perception' | None
#     'subject_id': 'instance-of-the-roller',
#     'target_id': 'instance-of-the-target' (for strikes/spells),
#     'strike_kind': 'melee' | 'ranged' | None,
#     'tags': ['vs_alignment', 'natural_weapon', ...],   # context flags
#   }


def _predicate_matches(pred: Optional[Dict], ctx: Optional[Mapping]) -> bool:
    """True if this modifier should apply in `ctx`. Missing predicate
    = always-on (back-compat with pre-trigger effects).

    A predicate with ANY filter (scope, target, or tag) that isn't
    'always'/'any'/None requires a context to check against. The
    no-context call (compute_token_stats with no context arg) asks
    'what does this creature carry universally?' — gated modifiers
    don't qualify, so they're filtered out."""
    if not pred:
        return True
    scope = (pred.get('scope') or 'always').lower()
    target = pred.get('target') or 'any'
    tag = pred.get('tag')
    has_filter = (scope != 'always') or (target not in ('any', None)) or bool(tag)
    if not ctx:
        # No context = caller wants the universally-applying baseline.
        return not has_filter

    ctx_scope = (ctx.get('scope') or 'always').lower()
    ctx_kind = (ctx.get('strike_kind') or '').lower()
    if scope != 'always':
        # Treat scope='melee' / 'ranged' as syntactic sugar for "this
        # is a strike of the named kind." Accept either:
        #  - ctx_scope is 'strike'/'attack' AND ctx_kind matches, OR
        #  - ctx_scope itself is the kind (caller passed 'melee'
        #    directly without separately setting strike_kind).
        attack_like = ctx_scope in ('strike', 'attack', 'melee', 'ranged')
        if scope in ('strike', 'attack'):
            if not attack_like:
                return False
        elif scope == 'ranged':
            if not (attack_like and (ctx_kind == 'ranged' or ctx_scope == 'ranged')):
                return False
        elif scope == 'melee':
            if not (attack_like and (ctx_kind == 'melee' or ctx_scope == 'melee')):
                return False
        elif scope != ctx_scope:
            return False
    if target == 'self':
        if not ctx.get('subject_id') or ctx.get('subject_id') != ctx.get('source_id'):
            return False
    elif target not in ('any', None):
        if ctx.get('target_id') != target:
            return False
    if tag and tag not in (ctx.get('tags') or []):
        return False
    return True


# ── Bonus stacking (PF2e core rule) ─────────────────────────────────
# Within each (stat, bonus_type) group: highest positive applies,
# lowest negative applies. Bonuses and penalties stack with each
# other (one of each typed pair applies). 'untyped' has no stacking
# limit — every untyped value sums.
_BONUS_TYPES = ('status', 'circumstance', 'item', 'untyped')


def _stack_typed(values: List[float], bonus_type: str) -> float:
    """Apply PF2e stacking for one (stat, bonus_type) group's values."""
    if not values:
        return 0
    if bonus_type == 'untyped':
        return sum(values)
    positives = [v for v in values if v > 0]
    negatives = [v for v in values if v < 0]
    best_positive = max(positives) if positives else 0
    worst_negative = min(negatives) if negatives else 0
    return best_positive + worst_negative


def _norm(key: str) -> str:
    """lowercase + strip non-alnum for canonical lookup."""
    return ''.join(c for c in (key or '').lower() if c.isalnum())


# ── Stat aliases ─────────────────────────────────────────────────────
# The conditions table uses `attack` / `damage` / `ac` / `fort` /
# `ref` / `will` / `perception` / `skills` / `dc` / `actions`. Some
# catalog effects target `saves` (any save) — expand that on the fly
# so the engine doesn't need a separate path.
_SAVES = ('fort', 'ref', 'will')


def _expand_modifier(m: Dict, condition_value: int = 1) -> List[Dict]:
    """Expand a stored modifier into the concrete list of (stat,
    delta, bonus_type) the stacking step will see. Resolves the
    'V' / '-V' markers used by conditions and expands `saves` into
    fort/ref/will."""
    raw = m.get('value', 0)
    if isinstance(raw, str):
        delta = -condition_value if raw.startswith('-') else condition_value
    else:
        delta = raw
    stat = m.get('stat')
    bonus_type = m.get('bonus_type', 'untyped')
    op = m.get('op', 'add')
    source = m.get('source', '')
    tag = m.get('tag')
    if stat == 'saves':
        return [
            {'stat': s, 'delta': delta, 'op': op,
             'bonus_type': bonus_type, 'source': source, 'tag': tag}
            for s in _SAVES
        ]
    return [{
        'stat': stat, 'delta': delta, 'op': op,
        'bonus_type': bonus_type, 'source': source, 'tag': tag,
    }]


# ── Build canonical lookups ──────────────────────────────────────────
_COND_LOOKUP: Dict[str, List[Dict]] = {
    _norm(k): v for k, v in PF2E_CONDITION_EFFECTS.items()
}


def _flatten_sources(
    conditions: Optional[Mapping[str, int]],
    active_effects: Optional[Iterable[Dict]],
    context: Optional[Mapping] = None,
) -> List[Dict]:
    """Return the unified list of expanded modifiers from all sources.
    Each entry: {stat, delta, op, bonus_type, source, tag, predicate?}.
    Predicate-gated modifiers that don't match `context` are dropped
    here (so the bonus-stacking step downstream doesn't see them at
    all)."""
    out: List[Dict] = []
    # Conditions first
    for cname, cval in (conditions or {}).items():
        if not cval:
            continue
        v = 1 if cval is True else int(cval)
        for m in _COND_LOOKUP.get(_norm(cname), []):
            if not _predicate_matches(m.get('predicate'), context):
                continue
            out.extend(_expand_modifier(m, condition_value=v))
    # Then active effects (spells / feats / items / custom)
    for eff in active_effects or []:
        # Suppressed effects (failed-save outcome) don't contribute.
        # Kept on the list so the UI can show "Heroism (suppressed)".
        if eff.get('suppressed'):
            continue
        for m in eff.get('modifiers', []) or []:
            if not _predicate_matches(m.get('predicate'), context):
                continue
            # Active-effect modifiers already carry their final
            # numeric value (catalog entries don't use the '-V' marker).
            expanded = _expand_modifier(m, condition_value=1)
            for e in expanded:
                # Tag the source label with the effect name so the
                # breakdown UI can show "+1 status to attack from
                # Heroism" instead of an empty source.
                if not e.get('source'):
                    e['source'] = eff.get('name') or '—'
            out.extend(expanded)
    return out


def compute_effects(conditions: Mapping[str, int], base_stats: Mapping[str, float]) -> Dict[str, float]:
    """Compatibility shim — same signature as the prior pass. Computes
    effective stats from conditions only (no active_effects). Kept so
    callers that only have a conditions dict don't have to change."""
    return compute_token_stats(conditions, [], base_stats)['effective']


def compute_for_action(
    conditions: Optional[Mapping[str, int]],
    active_effects: Optional[Iterable[Dict]],
    base_stats: Mapping[str, float],
    *,
    scope: str,                # 'strike' | 'damage' | 'save' | 'skill' | 'perception'
    subject_id: Optional[str] = None,
    target_id: Optional[str] = None,
    strike_kind: Optional[str] = None,    # 'melee' | 'ranged'
    tags: Optional[List[str]] = None,
) -> Dict:
    """Context-aware compute. Returns the same shape as
    compute_token_stats, but only modifiers whose predicate matches
    the action context are applied. Used by trigger-aware code paths
    (a strike-roll handler), the GM tooltip for "what applies to
    THIS attack against THIS target", and module hooks."""
    ctx = {
        'scope': scope,
        'subject_id': subject_id,
        'source_id': subject_id,   # `source` is a synonym used by predicate.target='self'
        'target_id': target_id,
        'strike_kind': strike_kind,
        'tags': list(tags or []),
    }
    return compute_token_stats(conditions, active_effects, base_stats, context=ctx)


def compute_token_stats(
    conditions: Optional[Mapping[str, int]],
    active_effects: Optional[Iterable[Dict]],
    base_stats: Mapping[str, float],
    context: Optional[Mapping] = None,
) -> Dict:
    """Apply ALL sources (conditions + active effects) to a base stat
    dict, returning effective stats + per-stat breakdown. PF2e bonus
    typing rules are honored: highest of each (stat, type) wins for
    positives, lowest for negatives, untyped sums freely.

    Returns:
        {
          'effective': {stat: final_value, ...},
          'breakdown': [{stat, delta, bonus_type, source, applied: bool, tag}, ...],
        }

    `applied: bool` flags whether this row was kept after stacking
    (highest-of-type), so the UI can show "Heroism +1 (applied)" vs
    "Bless +1 (suppressed by Heroism)" — both visible, only one
    actually contributing.
    """
    expanded = _flatten_sources(conditions, active_effects, context=context)
    # Group by (stat, bonus_type) for stacking
    groups: Dict[Tuple[str, str], List[Dict]] = {}
    for row in expanded:
        if row['op'] != 'add':
            # 'set' / 'mult' bypass stacking — apply them after.
            continue
        if row['stat'] is None:
            continue
        groups.setdefault((row['stat'], row['bonus_type']), []).append(row)

    out: Dict[str, float] = dict(base_stats)
    breakdown: List[Dict] = []

    # Track which rows survive stacking so the UI knows what
    # actually applied. Losers carry a `suppressed_by` label naming
    # the winning row's source so the player can read "Bless +1
    # suppressed by Heroism" instead of guessing why their buff
    # didn't show up.
    for (stat, btype), rows in groups.items():
        values = [r['delta'] for r in rows]
        # Apply the stacked net to the stat.
        net = _stack_typed(values, btype)
        if stat in out:
            out[stat] = out[stat] + net
        # Mark which rows are the "winners" — for typed groups the
        # most-positive AND most-negative applied; for untyped, all.
        winner_pos_source: Optional[str] = None
        winner_neg_source: Optional[str] = None
        if btype == 'untyped':
            winners = set(id(r) for r in rows)
        else:
            best_pos = max((r['delta'] for r in rows if r['delta'] > 0), default=None)
            worst_neg = min((r['delta'] for r in rows if r['delta'] < 0), default=None)
            winners = set()
            for r in rows:
                if r['delta'] > 0 and r['delta'] == best_pos:
                    winners.add(id(r))
                    if winner_pos_source is None:
                        winner_pos_source = r.get('source') or '—'
                elif r['delta'] < 0 and r['delta'] == worst_neg:
                    winners.add(id(r))
                    if winner_neg_source is None:
                        winner_neg_source = r.get('source') or '—'
                elif r['delta'] == 0:
                    winners.add(id(r))
        for r in rows:
            is_applied = id(r) in winners
            row_out = {**r, 'applied': is_applied}
            if not is_applied:
                # Pick the winner of the same sign — typed groups stack
                # positives and negatives independently, so a losing
                # buff is suppressed by the highest buff (not the
                # highest penalty), and vice versa.
                rival = None
                if r['delta'] > 0:
                    rival = winner_pos_source
                elif r['delta'] < 0:
                    rival = winner_neg_source
                if rival and rival != r.get('source'):
                    row_out['suppressed_by'] = rival
            breakdown.append(row_out)

    # Handle non-add ops separately (set / mult). These bypass
    # stacking and apply unconditionally — rare in PF2e but worth
    # supporting for sheet-driven overrides.
    for row in expanded:
        if row['op'] == 'add':
            continue
        if row['stat'] not in out:
            continue
        if row['op'] == 'set':
            out[row['stat']] = row['delta']
        elif row['op'] == 'mult':
            out[row['stat']] = out[row['stat']] * row['delta']
        breakdown.append({**row, 'applied': True})

    return {'effective': out, 'breakdown': breakdown}


# ── Save-vs-DC suppression ────────────────────────────────────────────
# Effects can declare a save block. The GM (or auto-roll path) applies
# the save outcome via `resolve_save`; the effect gains a
# `suppressed: True` flag when the target succeeds, which gates it out
# of subsequent compute calls. We DON'T remove the effect — keeping it
# in the list lets the UI render "Hideous Laughter (saved)" so the
# caster knows their slot got resisted.
#
#   effect['save'] = {
#     'type': 'fort' | 'ref' | 'will',
#     'dc':   24,
#     'on_crit_success': 'negate',
#     'on_success':      'negate' | 'reduce',
#     'on_failure':      'apply'  | 'reduce' | 'stronger',
#     'on_crit_failure': 'apply'  | 'stronger',
#   }
# `reduce` halves the numeric values of the effect's modifiers; the
# concrete semantics are spell-specific (Slow on success = 1 round
# instead of 1 min, etc.) — for the engine, `reduce` halves duration.

SAVE_TYPES = ('fort', 'ref', 'will')
SAVE_OUTCOMES = ('crit_success', 'success', 'failure', 'crit_failure')


def resolve_save(effect: Dict, roll_total: int, current_round: int = 1) -> Dict:
    """Apply a save outcome to an effect in place. Returns the outcome
    metadata for the combat log.

    Caller computes `roll_total` (d20 + save bonus + modifiers) and
    passes it in. We derive the degree of success from total vs dc
    using PF2e's standard four-tier table (crit_success on ≥ dc+10,
    crit_failure on ≤ dc−10, with nat 1 / nat 20 shift handled by
    the CALLER — the engine just sees the post-shift total).
    """
    save = effect.get('save') or {}
    dc = int(save.get('dc') or 0)
    diff = roll_total - dc
    if diff >= 10:
        degree = 'crit_success'
    elif diff >= 0:
        degree = 'success'
    elif diff <= -10:
        degree = 'crit_failure'
    else:
        degree = 'failure'
    outcome = save.get(f'on_{degree}') or 'apply'
    effect['save_result'] = {
        'roll': roll_total,
        'dc': dc,
        'degree': degree,
        'outcome': outcome,
        'rolled_at_round': current_round,
    }
    if outcome == 'negate':
        effect['suppressed'] = True
    elif outcome == 'reduce':
        # Halve the duration. Round-based and minute-based both halve
        # cleanly through the same path; expires_at_round recomputed.
        dur = effect.get('duration') or {}
        if dur.get('value'):
            dur['value'] = max(1, int(dur['value']) // 2)
            if dur.get('expires_at_round') is not None and current_round:
                # Recompute from the original applied_at_round if we
                # have it, otherwise from current.
                start = effect.get('applied_at_round') or current_round
                if dur.get('type') == 'minutes':
                    dur['expires_at_round'] = start + dur['value'] * 10
                else:
                    dur['expires_at_round'] = start + dur['value']
        effect['suppressed'] = False
    elif outcome == 'stronger':
        # Spell-specific (Hideous Laughter crit-fail upgrades the
        # condition — engine can't generalize without per-spell rules).
        # Caller (catalog handler) can post-process the effect.
        effect['suppressed'] = False
    else:
        # 'apply' — the default outcome on a failure.
        effect['suppressed'] = False
    return effect['save_result']


# ── Effect chain materialization ──────────────────────────────────────
# A catalog entry can declare follow-up effects that get instantiated
# when the parent is applied. The most common case is something like:
#   Aid (success)  → applies 'aid_success' to its target, who then
#                    benefits from the +1 circumstance on their next
#                    skill check. Without chains, the GM applies the
#                    Aid action AND remembers to drop the Aided marker
#                    manually. With chains, "Aid (success)" is itself
#                    the chained marker.
#
# Chains in this pass are SHALLOW: when the parent is applied, the
# chain entries fire once. They don't fire recursively (a chain
# making another chain is one-step deep). Deep recursion is out of
# scope — same reason we don't support effect-creates-effect-from-
# spell-cast-from-effect today.
#
#   catalog_entry['chains'] = [
#     {'when': 'on_apply', 'effect_key': 'flat_footed_marker',
#      'duration_override': {'type': 'rounds', 'value': 1},
#      'target': 'self'},                # self | caster | manual
#   ]
#
# `target: 'manual'` means the caller is expected to apply the chain
# themselves (AoE emanations etc.). The chain is returned to the
# caller as a metadata list — the engine doesn't pick WHICH ally
# tokens to apply to, just declares the intent.


def materialize_chains(
    parent_effect: Dict,
    *,
    current_round: int,
    caster: Optional[str] = None,
) -> List[Dict]:
    """Return a list of (target_kind, instantiated_effect) tuples-as-
    dicts for the chains the parent declares. Caller decides which
    tokens to attach each chain to."""
    parent_key = parent_effect.get('source_id')
    template = EFFECT_CATALOG.get(parent_key) if parent_key else None
    if not template:
        return []
    out: List[Dict] = []
    for chain in template.get('chains') or []:
        eff_key = chain.get('effect_key')
        if not eff_key or eff_key not in EFFECT_CATALOG:
            continue
        inst = instantiate_effect(
            eff_key,
            effect_id=_fresh_id(),
            caster=caster or parent_effect.get('caster'),
            current_round=current_round,
            duration_override=chain.get('duration_override'),
        )
        if inst is None:
            continue
        out.append({
            'target_kind': chain.get('target') or 'self',  # self | caster | manual
            'when': chain.get('when') or 'on_apply',
            'effect': inst,
        })
    return out


def _fresh_id() -> str:
    """uuid4 hex prefix — same convention the app.py paths use."""
    import uuid as _uuid
    return _uuid.uuid4().hex[:8]


# ── Trigger consumption ───────────────────────────────────────────────
# Modifiers with predicate.until='next_strike' / 'next_action' / etc.
# need to disappear after their gated event fires. compute_for_action
# is purely read; mutation happens through consume_triggered.

def consume_triggered(active_effects: List[Dict], *, event: str) -> List[Dict]:
    """Walk the effects list, dropping modifiers whose `until` field
    matches `event`. If an effect has zero modifiers left after the
    sweep, it's removed entirely. Returns the (potentially shorter)
    effects list. Caller commits the new list back to the token.

    `event` examples:
      - 'next_strike'   (consume Sure Strike's roll-twice flag)
      - 'next_action'   (consume single-action buffs)
      - 'end_of_turn'   (consume Aid's +1 to next check)
    """
    kept: List[Dict] = []
    for eff in active_effects:
        remaining_mods = []
        for m in eff.get('modifiers') or []:
            pred = m.get('predicate') or {}
            if (pred.get('until') or 'permanent') == event:
                continue  # consumed
            remaining_mods.append(m)
        # Some effects are pure markers (no modifiers) with their own
        # consume rule on the effect itself. Honour that too.
        eff_until = (eff.get('consumes_on') or '').lower()
        if eff_until == event:
            continue  # consume the whole effect
        eff['modifiers'] = remaining_mods
        # If the effect has no modifiers AND no save block AND no
        # chain metadata AND no marker tag, drop it — there's nothing
        # left to display.
        if (not remaining_mods and not eff.get('save')
                and not eff.get('chains') and not eff.get('tags')):
            continue
        kept.append(eff)
    return kept


# ── Reaction triggers ─────────────────────────────────────────────────
# Some effects don't apply universally — they FIRE when a specific
# event happens to the bearer. The classic case: the Shield cantrip
# gives the caster the ability to Shield Block once during the round.
# That's a reaction, and it doesn't modify any stat in isolation; it
# modifies the stat *when the trigger fires*.
#
# In our schema this is expressed as a modifier with
#   predicate.scope = 'reaction'
#   predicate.event = 'on_damaged' | 'on_struck' | 'on_targeted'
#                   | 'on_ally_struck' | 'on_critical_failure_save'
# The compute paths don't apply these modifiers to baseline stats
# (the no-context call sees `has_filter == True` and drops them).
# Damage / strike handlers call `find_reaction_triggers` after the
# event to surface the effect to the player.

REACTION_EVENT_LABELS = {
    'on_damaged':              'when you take damage',
    'on_struck':               'when you are hit by a Strike',
    'on_targeted':             'when an attack targets you',
    'on_ally_struck':          'when an ally adjacent to you is hit',
    'on_critical_failure_save':'when you critically fail a save',
}


def find_reaction_triggers(
    active_effects: Optional[Iterable[Dict]],
    *,
    event: str,
) -> List[Dict]:
    """Return effect-records whose `modifiers` carry a reaction-scoped
    predicate matching `event`. Useful payload for the damage / strike
    handlers to surface "you can use Shield Block now" hints.

    The matcher is permissive on the event side — a modifier with
    `predicate.scope='reaction'` and no `predicate.event` matches every
    reaction event (it's an always-available reaction window). A
    modifier with `predicate.event=X` only matches when `event=X`.

    Returned shape: list of dicts mirroring the source effect plus
    `trigger`:
        {
          'id': str,            # effect id
          'name': str,
          'source': str,
          'caster': str | None,
          'trigger': {
            'event': str,
            'event_label': str,
            'reaction_name': str | None,
            'hint': str | None,
          },
        }
    """
    out: List[Dict] = []
    if not active_effects:
        return out
    for eff in active_effects:
        # Suppressed effects don't fire reactions either — a saved-
        # against Hideous Laughter doesn't keep granting you anything.
        if eff.get('suppressed'):
            continue
        for m in eff.get('modifiers') or []:
            pred = m.get('predicate') or {}
            if (pred.get('scope') or '').lower() != 'reaction':
                continue
            pred_event = (pred.get('event') or '').lower() or None
            if pred_event is not None and pred_event != event:
                continue
            out.append({
                'id': eff.get('id'),
                'name': eff.get('name') or '—',
                'source': eff.get('source') or 'custom',
                'caster': eff.get('caster'),
                'trigger': {
                    'event': event,
                    'event_label': REACTION_EVENT_LABELS.get(event, event),
                    'reaction_name': pred.get('reaction_name') or m.get('source'),
                    'hint': pred.get('hint') or m.get('hint'),
                },
            })
            # One trigger per effect is enough — multiple reaction
            # modifiers on the same effect would dupe the toast.
            break
    return out


# ── Catalog application helper ────────────────────────────────────────
def instantiate_effect(
    catalog_key: str,
    *,
    effect_id: str,
    caster: Optional[str] = None,
    current_round: int = 1,
    duration_override: Optional[Dict] = None,
    custom_modifiers: Optional[List[Dict]] = None,
    custom_name: Optional[str] = None,
    save_dc: Optional[int] = None,
) -> Optional[Dict]:
    """Build a per-token effect record from a catalog entry. Computes
    `expires_at_round` from `current_round + duration.value` for
    round-based effects so cycle_turn can expire them automatically.

    `duration_override` lets the GM tweak duration at apply time
    (Heroism cast at 5th level lasts longer, etc.).
    `custom_modifiers` lets the 'custom' catalog entry carry the
    GM-authored deltas.
    `save_dc` plugs the GM's spell DC into a save-bearing effect
    (Slow / Hideous Laughter). The catalog DC field defaults to 0 —
    rolling the save against DC 0 trivially succeeds, so a missing
    save_dc is a no-op rather than a crash.
    """
    template = EFFECT_CATALOG.get(catalog_key)
    if not template:
        return None
    dur = dict(template.get('duration') or {})
    if duration_override:
        dur.update(duration_override)
    expires_at_round = None
    if dur.get('type') == 'rounds' and dur.get('value'):
        expires_at_round = current_round + int(dur['value'])
    elif dur.get('type') == 'minutes' and dur.get('value'):
        # 1 minute = 10 rounds in PF2e.
        expires_at_round = current_round + int(dur['value']) * 10
    mods = custom_modifiers if catalog_key == 'custom' and custom_modifiers else list(template.get('modifiers', []))
    save = None
    if template.get('save'):
        save = dict(template['save'])
        if save_dc is not None:
            save['dc'] = int(save_dc)
    eff = {
        'id': effect_id,
        'name': custom_name if catalog_key == 'custom' and custom_name else template.get('name', catalog_key),
        'source': template.get('source', 'custom'),
        'source_id': template.get('source_id'),
        'caster': caster,
        'tags': list(template.get('tags') or []),
        'duration': {**dur, 'expires_at_round': expires_at_round},
        'applied_at_round': current_round,
        'modifiers': mods,
        'description': template.get('description', ''),
    }
    if save is not None:
        eff['save'] = save
        eff['suppressed'] = False
    if template.get('consumes_on'):
        eff['consumes_on'] = template['consumes_on']
    return eff


def expire_round_effects(effects: List[Dict], current_round: int) -> Tuple[List[Dict], List[Dict]]:
    """Walk a token's effects list at end-of-round. Return (kept,
    expired). Caller is expected to swap `kept` into the token + log
    the expired ones."""
    kept: List[Dict] = []
    expired: List[Dict] = []
    for eff in effects:
        dur = eff.get('duration') or {}
        exp = dur.get('expires_at_round')
        if exp is not None and current_round >= exp:
            expired.append(eff)
        else:
            kept.append(eff)
    return kept, expired


def catalog_list() -> List[Dict]:
    """Return the catalog as a flat list of summaries for the GM
    dropdown UI. Sorted by source then name for predictable order."""
    out = []
    for key, e in EFFECT_CATALOG.items():
        out.append({
            'key': key,
            'name': e.get('name'),
            'source': e.get('source'),
            'tags': e.get('tags', []),
            'duration': e.get('duration', {}),
            'description': e.get('description', ''),
            'modifier_count': len(e.get('modifiers') or []),
        })
    out.sort(key=lambda x: (x['source'], x['name']))
    return out


def list_active_effects(conditions: Mapping[str, int]) -> List[Dict]:
    """Back-compat: prior pass surfaced just the condition breakdown.
    Kept so existing callers don't drift."""
    return compute_token_stats(conditions, [], {})['breakdown']
