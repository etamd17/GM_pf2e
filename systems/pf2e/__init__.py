"""PF2e system definition for the registry.

Declarative profile only — the live PF2e mechanics stay in app.py. The actor
factory (app.Character) is bound by the host app at startup. The condition keys
here mirror ``app.CONDITION_REFERENCE`` and are drift-guarded by
``tests/test_system_registry.py``.
"""
from __future__ import annotations

from systems.base import GameSystem, CombatProfile, Condition

# Conditions that carry a numeric value in PF2e (e.g. frightened 2). Every other
# entry in the catalog is an on/off state.
_VALUED = frozenset({
    'clumsy', 'doomed', 'drained', 'dying', 'enfeebled', 'frightened',
    'sickened', 'slowed', 'stunned', 'stupefied', 'wounded',
})

# Full PF2e condition catalog. Keys mirror app.CONDITION_REFERENCE (descriptions
# live there); the hyphenated remaster keys ('flat-footed', 'off-guard') are
# preserved as-is.
_CONDITION_KEYS = (
    'blinded', 'broken', 'clumsy', 'concealed', 'confused', 'controlled',
    'dazzled', 'deafened', 'doomed', 'drained', 'dying', 'encumbered',
    'enfeebled', 'fascinated', 'fatigued', 'flat-footed', 'fleeing',
    'frightened', 'grabbed', 'hidden', 'immobilized', 'invisible', 'observed',
    'off-guard', 'paralyzed', 'petrified', 'prone', 'quickened', 'restrained',
    'sickened', 'slowed', 'stunned', 'stupefied', 'unconscious', 'undetected',
    'unfriendly', 'unnoticed', 'wounded',
)

_COMBAT = CombatProfile(
    initiative_stat='perception',
    initiative_higher_first=True,
    action_model='three_action',
    action_count=3,
    reaction_count=1,
    defenses=('ac', 'fort', 'ref', 'will'),
    damage_pool='hp',
    down_condition='dying',
    rollables=('skill', 'attack', 'save', 'perception', 'flat'),
    bonus_types=('status', 'circumstance', 'item', 'untyped'),
    stacking_rule='typed_best_worst',
    conditions=tuple(Condition(k, k in _VALUED) for k in _CONDITION_KEYS),
)

SYSTEM = GameSystem(key='pf2e', label='Pathfinder 2e', combat=_COMBAT)
