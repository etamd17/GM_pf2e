"""Active Effects engine — PF2e bonus-typed stat modifiers from any
source (condition / spell / feat / item / ability).

Design goals:
  * One schema, many sources. A status-bonus from Heroism is the same
    shape as a status-bonus from Frightened; consumers don't have to
    handle them differently.
  * PF2e bonus typing applied correctly. Within (stat, bonus_type)
    pairs, the highest positive applies and the lowest negative
    applies (only one bonus / penalty of each type stacks). Untyped
    bonuses and penalties stack with everything.
  * Duration auto-expiry. cycle_turn fires `expire_round_effects` so
    Bless and Bane fall off automatically.
  * Catalog of ~15 common L1-5 effects so the GM doesn't have to
    re-author Heroism / Bless / Bane every session.

What's NOT here (deliberate, follow-up scope):
  * Trigger conditions (e.g. "+1 attack against THIS creature only").
    Today every modifier applies to its stat unconditionally — good
    enough for the common cases (Bless, Heroism, Mage Armor, Shield).
  * Effect-from-effect chains (one effect creating another).
  * Save vs DC suppression. Today the GM toggles an effect on/off.
  * Effects that modify the sheet itself (PC sheet calculations) —
    this engine is map-token-scoped. Sheet-level effects would need
    to land in Character.compute_*.
  * Drag-from-spell-card-to-token UX. Catalog adds via dropdown.

PF2E_CONDITION_EFFECTS (from the prior pass) still maps conditions →
modifiers but is now a SOURCE for the same `Modifier` schema below,
unified with spell / feat / item modifiers.
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
        ],
    },
    'sure_strike': {
        'name': 'Sure Strike',
        'source': 'spell',
        'source_id': 'sure_strike',
        'tags': ['fortune', 'self'],
        'duration': {'type': 'until_end_of_turn', 'value': None},
        'description': 'Roll the next Strike twice, take the better. (Tracked as a turn-tag — apply on the strike roll.)',
        # No numeric modifier — purely a roll modification. Lives here so
        # the GM can see the marker on the token; the engine attaches no
        # stat delta. Tag flagged 'roll_advantage' for future modules.
        'modifiers': [],
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
            {'stat': 'ac',   'op': 'add', 'value': 1, 'bonus_type': 'status', 'tag': 'vs_alignment'},
            {'stat': 'fort', 'op': 'add', 'value': 1, 'bonus_type': 'status', 'tag': 'vs_alignment'},
            {'stat': 'ref',  'op': 'add', 'value': 1, 'bonus_type': 'status', 'tag': 'vs_alignment'},
            {'stat': 'will', 'op': 'add', 'value': 1, 'bonus_type': 'status', 'tag': 'vs_alignment'},
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
        'tags': ['arcane', 'occult'],
        'duration': {'type': 'rounds', 'value': 1},
        'description': 'Lose 1 action per turn (target chooses which).',
        'modifiers': [
            {'stat': 'actions', 'op': 'add', 'value': -1, 'bonus_type': 'untyped'},
        ],
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
        'description': '+1 item to attack with claws/jaws, −2 status to AC.',
        'modifiers': [
            {'stat': 'attack', 'op': 'add', 'value': 1, 'bonus_type': 'item', 'tag': 'natural_weapon'},
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
) -> List[Dict]:
    """Return the unified list of expanded modifiers from all sources.
    Each entry: {stat, delta, op, bonus_type, source, tag}."""
    out: List[Dict] = []
    # Conditions first
    for cname, cval in (conditions or {}).items():
        if not cval:
            continue
        v = 1 if cval is True else int(cval)
        for m in _COND_LOOKUP.get(_norm(cname), []):
            out.extend(_expand_modifier(m, condition_value=v))
    # Then active effects (spells / feats / items / custom)
    for eff in active_effects or []:
        for m in eff.get('modifiers', []) or []:
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


def compute_token_stats(
    conditions: Optional[Mapping[str, int]],
    active_effects: Optional[Iterable[Dict]],
    base_stats: Mapping[str, float],
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
    expanded = _flatten_sources(conditions, active_effects)
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
    # actually applied.
    for (stat, btype), rows in groups.items():
        values = [r['delta'] for r in rows]
        # Apply the stacked net to the stat.
        net = _stack_typed(values, btype)
        if stat in out:
            out[stat] = out[stat] + net
        # Mark which rows are the "winners" — for typed groups the
        # most-positive AND most-negative applied; for untyped, all.
        if btype == 'untyped':
            winners = set(id(r) for r in rows)
        else:
            best_pos = max((r['delta'] for r in rows if r['delta'] > 0), default=None)
            worst_neg = min((r['delta'] for r in rows if r['delta'] < 0), default=None)
            winners = set()
            for r in rows:
                if r['delta'] > 0 and r['delta'] == best_pos:
                    winners.add(id(r))
                elif r['delta'] < 0 and r['delta'] == worst_neg:
                    winners.add(id(r))
                elif r['delta'] == 0:
                    winners.add(id(r))
        for r in rows:
            breakdown.append({
                **r,
                'applied': id(r) in winners,
            })

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
) -> Optional[Dict]:
    """Build a per-token effect record from a catalog entry. Computes
    `expires_at_round` from `current_round + duration.value` for
    round-based effects so cycle_turn can expire them automatically.

    `duration_override` lets the GM tweak duration at apply time
    (Heroism cast at 5th level lasts longer, etc.).
    `custom_modifiers` lets the 'custom' catalog entry carry the
    GM-authored deltas.
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
    return {
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
