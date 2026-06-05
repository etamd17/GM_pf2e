"""Cosmere combat engine — pure, rulebook-accurate functions (Stormlight
Ch.9 Damage/Injury/Death, Ch.10 Combat, Ch.0/13 Plot Die).

No app or Foundry dependency. The core resolvers take the die value as input so
they're deterministic + testable; ``roll_*`` convenience wrappers roll for the
live app. Verified verbatim against the 392-page core rulebook.
"""
from __future__ import annotations

import random

# --- damage (Ch.9) ---------------------------------------------------------
# Deflect (from armor) reduces ONLY impact/keen/energy damage; spirit and vital
# bypass it.
DEFLECTABLE = frozenset({'impact', 'keen', 'energy'})
DAMAGE_TYPES = ('impact', 'keen', 'energy', 'spirit', 'vital')


def apply_damage(health, amount, dtype='impact', deflect=0):
    """Apply `amount` of `dtype` damage to `health`, subtracting Deflect for
    deflectable types only. Returns (new_health, damage_taken, reduced_to_zero)."""
    amount = max(0, int(amount or 0))
    if dtype in DEFLECTABLE:
        amount = max(0, amount - max(0, int(deflect or 0)))
    health = int(health or 0)
    new = health - amount
    reduced_to_zero = new <= 0 < health        # freshly dropped to 0
    return max(0, new), amount, reduced_to_zero


# --- injuries / the death spiral (Ch.9) ------------------------------------
INJURY_DURATION = {
    'death':       'You die.',
    'permanent':   'Permanent injury (heals only via supernatural means).',
    'vicious':     'Temporary injury — 6d6 days.',
    'shallow':     'Temporary injury — 1d6 days.',
    'flesh_wound': 'Temporary injury — until a long rest.',
}
# d8 injury-effect suggestion table (Ch.9).
INJURY_EFFECTS_D8 = {1: 'Exhausted [-1]', 2: 'Exhausted [-1]', 3: 'Exhausted [-2]',
                     4: 'Slowed', 5: 'Slowed', 6: 'Disoriented', 7: 'Surprised',
                     8: 'Can only use one hand'}


def injury_severity(total):
    """Severity for an injury-roll total (Ch.9 Injury Duration table)."""
    total = int(total)
    if total <= -6:
        return 'death'
    if total <= 0:        # -5 to 0
        return 'permanent'
    if total <= 5:        # 1 to 5
        return 'vicious'
    if total <= 15:       # 6 to 15
        return 'shallow'
    return 'flesh_wound'  # 16+


def injury_roll(d20, deflect=0, existing_injuries=0, mods=0):
    """Resolve an injury roll: d20 + Deflect + mods − 5×(existing injuries).
    The total CAN be negative. Returns the breakdown + severity."""
    total = int(d20) + int(deflect or 0) + int(mods or 0) - 5 * max(0, int(existing_injuries or 0))
    sev = injury_severity(total)
    return {'d20': int(d20), 'total': total, 'severity': sev,
            'duration': INJURY_DURATION[sev], 'is_death': sev == 'death'}


def injury_effect(d8):
    return INJURY_EFFECTS_D8.get(int(d8), 'Slowed')


def roll_injury(deflect=0, existing_injuries=0, mods=0):
    """Roll a d20 (+ a d8 effect) and resolve an injury for the live app."""
    res = injury_roll(random.randint(1, 20), deflect, existing_injuries, mods)
    if res['severity'] not in ('death',):
        res['effect'] = injury_effect(random.randint(1, 8))
    return res


# --- the Plot Die (Ch.0/13) ------------------------------------------------
# A d6 rolled alongside the d20 when the stakes are raised: 2 blank,
# 2 Opportunity, 2 Complication. A Complication face also adds +2 or +4 to the
# d20 total (but triggers a Complication side effect).
PLOT_DIE_FACES = ('blank', 'blank', 'opportunity', 'opportunity', 'complication', 'complication')
PLOT_DIE_BONUS = (0, 0, 0, 0, 2, 4)


def plot_die(face_index):
    """Resolve a plot-die face (0-5). Returns {type, bonus}."""
    i = int(face_index) % 6
    return {'type': PLOT_DIE_FACES[i], 'bonus': PLOT_DIE_BONUS[i]}


def roll_plot_die():
    return plot_die(random.randint(0, 5))


# How an Opportunity (player) or Complication (GM) can be spent (Ch.0/13).
PLOT_DIE_SPEND = {
    'opportunity': ['Aid an Ally — advantage on their next test',
                    'Collect Yourself — recover 1 focus',
                    'Critically Hit — turn a hit into a critical hit',
                    'a narrative benefit'],
    'complication': ['Hinder — the GM imposes a disadvantage',
                     'Distract — the target loses 1 focus',
                     'a narrative complication'],
    'blank': [],
}


def plot_die_result(face_index):
    """A full Plot Die roll: type + bonus + a display label + spend options."""
    r = plot_die(face_index)
    r['spend'] = list(PLOT_DIE_SPEND.get(r['type'], []))
    r['label'] = r['type'].capitalize() + (' +%d' % r['bonus'] if r['bonus'] else '')
    return r


def roll_plot_die_full():
    return plot_die_result(random.randint(0, 5))


# --- turn order: the 4-phase fast/slow queue (Ch.10) -----------------------
# Each round, every actor elects FAST (2 actions, acts early) or SLOW (3
# actions, acts late). The round resolves in four phases; within a phase, higher
# Speed acts first, then a d20 tiebreak.
TURN_PHASES = ('fast_pc', 'fast_npc', 'slow_pc', 'slow_npc')
_PHASE_RANK = {p: i for i, p in enumerate(TURN_PHASES)}


def fast_slow_actions(choice):
    """Actions granted by electing fast (2) or slow (3)."""
    return 2 if choice == 'fast' else 3


def turn_phase(is_pc, choice):
    return '%s_%s' % ('fast' if choice == 'fast' else 'slow', 'pc' if is_pc else 'npc')


def order_combatants(combatants):
    """Order combatants for a Cosmere round. Each item is a dict with
    `is_pc`, `choice` ('fast'|'slow'), `speed` (int), and `tiebreak` (a d20).
    Returns a new list sorted by phase, then Speed desc, then tiebreak desc."""
    def key(c):
        phase = turn_phase(bool(c.get('is_pc')), c.get('choice', 'slow'))
        return (_PHASE_RANK[phase], -int(c.get('speed', 0) or 0), -int(c.get('tiebreak', 0) or 0))
    return sorted(combatants, key=key)
