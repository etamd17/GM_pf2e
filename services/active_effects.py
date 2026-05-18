"""Active Effects skeleton — PF2e condition → stat delta system.

Foundry VTT's killer feature for system-aware play is the Active
Effects engine: applying Off-Guard knocks AC down 2 automatically;
removing it restores it. We track conditions in the Character /
Combatant model and apply most of them via hand-wired code (the
tracker's stunned-action-spend logic, drawConditionIcons, etc.).
This module is the start of a generic engine for "condition is
present → these stat deltas apply."

Today's scope (intentionally narrow — this is the foundation, not
the full system):

  * One table mapping condition name → list of stat deltas.
  * `compute_effects(conditions, base_stats) → dict` returns the
    modified stat dict.
  * Tracker / map renders can call this to surface the EFFECTIVE
    AC of an off-guard creature without each call site having to
    know the rule.

What's NOT here yet (deliberate; large scope):
  * Effects from spells / feats / items (no source-of-truth model
    for those yet — would need a SpellEffect / ItemEffect schema).
  * Effects that modify dice rolls beyond stat deltas (Frightened
    is supposed to apply to ATTACK + DC + SAVES + SKILL CHECKS,
    not just a flat number).
  * Effects from token-attached lights or terrain (e.g. Concealed
    from dim light, Off-Guard from prone-while-attacking-melee).
  * Persistent damage tracking with end-of-turn flat checks
    (already hand-wired in app.py's cycle_turn).
  * Rule of "two penalties of the same kind don't stack" — PF2e's
    status/circumstance/item bonus typing.

The current callers only care about visual effect display (token
tooltip showing effective AC). Wider system integration is a
follow-up project.
"""
from __future__ import annotations
from typing import Dict, Iterable, List, Mapping


# ── Condition → stat-delta table ────────────────────────────────────
# Each entry is a list of dicts: {stat, op, value, source}
#   stat:   what's modified ('ac', 'fort', 'ref', 'will', 'attack',
#           'damage', 'perception', 'skills', 'speed', 'dc')
#   op:     'add' (delta) | 'set' (override) | 'mult' (multiplier)
#   value:  int (delta / target) — for valued conditions like
#           Frightened N, the value is the condition's numeric value.
#           Use string 'V' as a marker for "use the condition's value".
#   source: human-readable origin for the tooltip ('Frightened',
#           'Sickened 2', etc.)
#
# Anything keyed on 'V' is multiplied by the condition's numeric
# value at apply time. So Frightened 2 = {value: -'V'} → -2.
PF2E_CONDITION_EFFECTS: Dict[str, List[Dict]] = {
    'frightened': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'will',       'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'perception', 'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Frightened'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'source': 'Frightened'},
    ],
    'sickened': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'will',       'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'perception', 'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Sickened'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'source': 'Sickened'},
    ],
    'enfeebled': [
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'source': 'Enfeebled'},
        {'stat': 'damage',     'op': 'add', 'value': '-V', 'source': 'Enfeebled'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Enfeebled', 'tag': 'str'},
    ],
    'clumsy': [
        {'stat': 'ac',         'op': 'add', 'value': '-V', 'source': 'Clumsy'},
        {'stat': 'ref',        'op': 'add', 'value': '-V', 'source': 'Clumsy'},
        {'stat': 'attack',     'op': 'add', 'value': '-V', 'source': 'Clumsy', 'tag': 'dex_finesse'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Clumsy', 'tag': 'dex'},
    ],
    'drained': [
        {'stat': 'fort',       'op': 'add', 'value': '-V', 'source': 'Drained'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Drained', 'tag': 'con'},
    ],
    'stupefied': [
        {'stat': 'will',       'op': 'add', 'value': '-V', 'source': 'Stupefied'},
        {'stat': 'dc',         'op': 'add', 'value': '-V', 'source': 'Stupefied'},
        {'stat': 'skills',     'op': 'add', 'value': '-V', 'source': 'Stupefied', 'tag': 'int_wis_cha'},
    ],
    'off_guard': [
        {'stat': 'ac',         'op': 'add', 'value': -2, 'source': 'Off-Guard'},
    ],
    'flat_footed': [   # alias older name
        {'stat': 'ac',         'op': 'add', 'value': -2, 'source': 'Flat-Footed'},
    ],
    'prone': [
        {'stat': 'attack',     'op': 'add', 'value': -2, 'source': 'Prone'},
        {'stat': 'ac',         'op': 'add', 'value': -2, 'source': 'Prone', 'tag': 'ranged'},
    ],
    'slowed': [
        # Slowed reduces actions per turn rather than a stat. Tracker
        # has the action-economy widget; this entry is here so the
        # token tooltip can surface the "lose N actions" reminder.
        {'stat': 'actions',    'op': 'add', 'value': '-V', 'source': 'Slowed'},
    ],
    'stunned': [
        {'stat': 'actions',    'op': 'add', 'value': '-V', 'source': 'Stunned'},
    ],
    'quickened': [
        {'stat': 'actions',    'op': 'add', 'value': 1, 'source': 'Quickened'},
    ],
    'dying': [
        # Dying doesn't modify stats; it's a flag the renderer styles
        # the token with. Listed here for completeness.
    ],
}


def _norm(key: str) -> str:
    """Match drawConditionIcons / _conditionColor canonicalization:
    lowercase + strip ' _-' so 'Off-Guard' / 'off_guard' / 'offguard'
    all resolve to one table key."""
    return ''.join(c for c in key.lower() if c.isalnum())


# Build a normalized lookup so all the casing variants hit one row.
_EFFECT_LOOKUP: Dict[str, List[Dict]] = {}
for k, v in PF2E_CONDITION_EFFECTS.items():
    _EFFECT_LOOKUP[_norm(k)] = v


def compute_effects(conditions: Mapping[str, int], base_stats: Mapping[str, float]) -> Dict[str, float]:
    """Apply all active conditions to a base stat dict; return the modified
    copy. Unknown stats pass through unchanged. Unknown conditions are
    ignored (skeleton — explicit table only, no inference).

    conditions: {'frightened': 2, 'off_guard': True, ...}
    base_stats: {'ac': 18, 'fort': 7, 'ref': 9, 'will': 6, 'attack': 11, ...}

    Returns a new dict with deltas applied. Numeric `value` is used
    directly; the 'V' string is replaced with the condition's value.
    """
    out: Dict[str, float] = dict(base_stats)
    for cname, cval in (conditions or {}).items():
        if not cval:
            continue
        # Treat True (boolean conditions like Off-Guard / Prone) as
        # value 1 so the table's '-V' tagged entries still resolve.
        v = 1 if cval is True else int(cval)
        effects = _EFFECT_LOOKUP.get(_norm(cname), [])
        for e in effects:
            stat = e.get('stat')
            op = e.get('op', 'add')
            raw = e.get('value', 0)
            if isinstance(raw, str):
                # '-V' or 'V' marker resolves to ±v
                num = -v if raw.startswith('-') else v
            else:
                num = raw
            if stat not in out:
                continue
            if op == 'add':
                out[stat] = out[stat] + num
            elif op == 'set':
                out[stat] = num
            elif op == 'mult':
                out[stat] = out[stat] * num
    return out


def list_active_effects(conditions: Mapping[str, int]) -> List[Dict]:
    """Flatten the conditions → effect list view. Powers a hover-tooltip
    "what's modifying this creature?" panel for the GM. Each item
    carries {stat, delta, source} for direct display.
    """
    out: List[Dict] = []
    for cname, cval in (conditions or {}).items():
        if not cval:
            continue
        v = 1 if cval is True else int(cval)
        effects = _EFFECT_LOOKUP.get(_norm(cname), [])
        for e in effects:
            raw = e.get('value', 0)
            if isinstance(raw, str):
                num = -v if raw.startswith('-') else v
            else:
                num = raw
            out.append({
                'stat': e.get('stat'),
                'delta': num,
                'op': e.get('op', 'add'),
                'source': e.get('source', cname.title()),
                'tag': e.get('tag'),
            })
    return out
