"""Fixed D&D 5e rules tables (both editions).

These are the numbers the 5etools data does not carry in machine-readable
form: XP by challenge rating, encounter-difficulty budgets, the multiclass
spellcaster table, point-buy costs. Each table cites where it comes from and
is pinned by tests/test_dnd5e_tables.py.

Edition keys used throughout the 5e package: ``'classic'`` (2014 rules: PHB /
DMG / MM) and ``'one'`` (2024 rules: XPHB / XDMG / XMM) -- the same values
5etools puts in its ``edition`` field.
"""
from __future__ import annotations

from fractions import Fraction

EDITIONS = ('classic', 'one')
ABILITIES = ('str', 'dex', 'con', 'int', 'wis', 'cha')
ABILITY_NAMES = {'str': 'Strength', 'dex': 'Dexterity', 'con': 'Constitution',
                 'int': 'Intelligence', 'wis': 'Wisdom', 'cha': 'Charisma'}

# XP by challenge rating (2014 DMG p.274 / MM p.9; unchanged in the 2024 MM).
XP_BY_CR = {
    '0': 10, '1/8': 25, '1/4': 50, '1/2': 100, '1': 200, '2': 450, '3': 700,
    '4': 1100, '5': 1800, '6': 2300, '7': 2900, '8': 3900, '9': 5000,
    '10': 5900, '11': 7200, '12': 8400, '13': 10000, '14': 11500, '15': 13000,
    '16': 15000, '17': 18000, '18': 20000, '19': 22000, '20': 25000,
    '21': 33000, '22': 41000, '23': 50000, '24': 62000, '25': 75000,
    '26': 90000, '27': 105000, '28': 120000, '29': 135000, '30': 155000,
}


def xp_for_cr(cr) -> int | None:
    if cr is None:
        return None
    return XP_BY_CR.get(str(cr).strip())


def cr_value(cr) -> float | None:
    try:
        return float(Fraction(str(cr)))
    except (ValueError, ZeroDivisionError, TypeError):
        return None


def proficiency_bonus(level: int) -> int:
    """Character proficiency bonus by total character level (1-20)."""
    level = max(1, min(20, int(level)))
    return 2 + (level - 1) // 4


def cr_proficiency_bonus(cr) -> int:
    """A monster's proficiency bonus by CR (MM p.8)."""
    v = cr_value(cr) or 0
    if v < 5:
        return 2
    return 2 + int((v - 1) // 4)


def ability_mod(score: int) -> int:
    return (int(score) - 10) // 2


# --- encounter difficulty ---------------------------------------------------

# 2014 DMG p.82: XP threshold per character, (easy, medium, hard, deadly).
XP_THRESHOLDS_2014 = {
    1: (25, 50, 75, 100), 2: (50, 100, 150, 200), 3: (75, 150, 225, 400),
    4: (125, 250, 375, 500), 5: (250, 500, 750, 1100), 6: (300, 600, 900, 1400),
    7: (350, 750, 1100, 1700), 8: (450, 900, 1400, 2100), 9: (550, 1100, 1600, 2400),
    10: (600, 1200, 1900, 2800), 11: (800, 1600, 2400, 3600), 12: (1000, 2000, 3000, 4500),
    13: (1100, 2200, 3400, 5100), 14: (1250, 2500, 3800, 5700), 15: (1400, 2800, 4300, 6400),
    16: (1600, 3200, 4800, 7200), 17: (2000, 3900, 5900, 8800), 18: (2100, 4200, 6300, 9500),
    19: (2400, 4900, 7300, 10900), 20: (2800, 5700, 8500, 12700),
}
DIFFICULTY_2014 = ('easy', 'medium', 'hard', 'deadly')

# 2014 DMG p.82: encounter multiplier ladder. Index 1..6 are the printed rows
# (1 monster, 2, 3-6, 7-10, 11-14, 15+); 0 and 7 are the extra steps used when
# the party is unusually large (6+) or small (1-2).
_MULT_LADDER = (0.5, 1, 1.5, 2, 2.5, 3, 4, 5)


def _mult_row(n_monsters: int) -> int:
    if n_monsters <= 0:
        return 1
    if n_monsters == 1:
        return 1
    if n_monsters == 2:
        return 2
    if n_monsters <= 6:
        return 3
    if n_monsters <= 10:
        return 4
    if n_monsters <= 14:
        return 5
    return 6


def encounter_multiplier_2014(n_monsters: int, party_size: int) -> float:
    row = _mult_row(n_monsters)
    if party_size < 3:
        row += 1
    elif party_size >= 6:
        row -= 1
    return _MULT_LADDER[row]


# 2024 DMG p.114: XP budget per character, (low, moderate, high).
XP_BUDGET_2024 = {
    1: (50, 75, 100), 2: (100, 150, 200), 3: (150, 225, 400), 4: (250, 375, 500),
    5: (500, 750, 1100), 6: (600, 1000, 1400), 7: (750, 1300, 1700),
    8: (1000, 1700, 2100), 9: (1300, 2000, 2600), 10: (1600, 2300, 3100),
    11: (1900, 2900, 4100), 12: (2200, 3700, 4700), 13: (2600, 4200, 5400),
    14: (2900, 4900, 6200), 15: (3300, 5400, 7800), 16: (3800, 6100, 9800),
    17: (4500, 7200, 11700), 18: (5000, 8700, 14200), 19: (5500, 10700, 17200),
    20: (6400, 13200, 22000),
}
DIFFICULTY_2024 = ('low', 'moderate', 'high')


def encounter_difficulty(edition: str, party_levels, monster_crs) -> dict:
    """Rate an encounter.

    ``party_levels``: list of character levels. ``monster_crs``: list of CR
    strings (one per monster; XP is looked up per CR). Returns the budget
    thresholds, the XP the encounter is worth, the XP that is compared against
    the thresholds (adjusted by the multiplier in 2014), and the rating.
    """
    levels = [max(1, min(20, int(lv))) for lv in party_levels if lv]
    xps = [xp_for_cr(cr) or 0 for cr in monster_crs]
    base = sum(xps)
    if edition == 'one':
        names = DIFFICULTY_2024
        thresholds = [sum(XP_BUDGET_2024[lv][i] for lv in levels) for i in range(3)]
        mult = 1.0
    else:
        names = DIFFICULTY_2014
        thresholds = [sum(XP_THRESHOLDS_2014[lv][i] for lv in levels) for i in range(4)]
        # Monsters with CR far below the party's average are ignored for the
        # multiplier count only (DMG p.83 says "consider"); we count them all.
        mult = encounter_multiplier_2014(len(xps), len(levels)) if xps else 1.0
    adjusted = int(base * mult)
    rating = 'trivial'
    for name, t in zip(names, thresholds):
        if adjusted >= t:
            rating = name
    if edition == 'one' and thresholds and adjusted > thresholds[-1]:
        rating = 'beyond high'
    return {
        'edition': 'one' if edition == 'one' else 'classic',
        'thresholds': dict(zip(names, thresholds)),
        'order': list(names),          # JSON objects lose key order through jsonify
        'base_xp': base,
        'multiplier': mult,
        'adjusted_xp': adjusted,
        'rating': rating,
    }


# --- spellcasting -----------------------------------------------------------

# PHB p.165 (2014) / XPHB p.26 (2024): multiclass spellcaster slots by combined
# caster level; also the full-caster single-class table.
SPELL_SLOTS_BY_CASTER_LEVEL = {
    0: (),
    1: (2,), 2: (3,), 3: (4, 2), 4: (4, 3), 5: (4, 3, 2), 6: (4, 3, 3),
    7: (4, 3, 3, 1), 8: (4, 3, 3, 2), 9: (4, 3, 3, 3, 1), 10: (4, 3, 3, 3, 2),
    11: (4, 3, 3, 3, 2, 1), 12: (4, 3, 3, 3, 2, 1), 13: (4, 3, 3, 3, 2, 1, 1),
    14: (4, 3, 3, 3, 2, 1, 1), 15: (4, 3, 3, 3, 2, 1, 1, 1),
    16: (4, 3, 3, 3, 2, 1, 1, 1), 17: (4, 3, 3, 3, 2, 1, 1, 1, 1),
    18: (4, 3, 3, 3, 3, 1, 1, 1, 1), 19: (4, 3, 3, 3, 3, 2, 1, 1, 1),
    20: (4, 3, 3, 3, 3, 2, 2, 1, 1),
}


def multiclass_caster_level(edition: str, parts) -> int:
    """Combined caster level for the multiclass slot table.

    ``parts``: iterable of (caster_progression, class_level) where progression
    is the 5etools ``casterProgression`` value: 'full', '1/2', '1/3',
    'artificer' or 'pact' (pact magic does not contribute).

    2014 rounds half/third casters DOWN per class (PHB p.164); 2024 rounds them
    UP (XPHB p.26). The artificer always rounds up (TCE p.9 / EFA).
    """
    total = 0
    up = edition == 'one'
    for prog, lvl in parts:
        lvl = int(lvl)
        if prog == 'full':
            total += lvl
        elif prog == 'artificer':
            total += -(-lvl // 2)
        elif prog == '1/2':
            total += -(-lvl // 2) if up else lvl // 2
        elif prog == '1/3':
            total += -(-lvl // 3) if up else lvl // 3
    return min(20, total)


# --- character creation -----------------------------------------------------

STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27

# XP needed to reach each level (PHB p.15 / XPHB p.43).
XP_FOR_LEVEL = {
    1: 0, 2: 300, 3: 900, 4: 2700, 5: 6500, 6: 14000, 7: 23000, 8: 34000,
    9: 48000, 10: 64000, 11: 85000, 12: 100000, 13: 120000, 14: 140000,
    15: 165000, 16: 195000, 17: 225000, 18: 265000, 19: 305000, 20: 355000,
}

SKILLS = {
    'acrobatics': 'dex', 'animal handling': 'wis', 'arcana': 'int',
    'athletics': 'str', 'deception': 'cha', 'history': 'int', 'insight': 'wis',
    'intimidation': 'cha', 'investigation': 'int', 'medicine': 'wis',
    'nature': 'int', 'perception': 'wis', 'performance': 'cha',
    'persuasion': 'cha', 'religion': 'int', 'sleight of hand': 'dex',
    'stealth': 'dex', 'survival': 'wis',
}

# Conditions common to both editions; exhaustion is valued (levels 1-6).
CONDITIONS = (
    'blinded', 'charmed', 'deafened', 'exhaustion', 'frightened', 'grappled',
    'incapacitated', 'invisible', 'paralyzed', 'petrified', 'poisoned', 'prone',
    'restrained', 'stunned', 'unconscious',
)

SIZE_NAMES = {'F': 'Fine', 'D': 'Diminutive', 'T': 'Tiny', 'S': 'Small', 'M': 'Medium',
              'L': 'Large', 'H': 'Huge', 'G': 'Gargantuan', 'C': 'Colossal', 'V': 'Varies'}
SCHOOL_NAMES = {'A': 'Abjuration', 'C': 'Conjuration', 'D': 'Divination',
                'E': 'Enchantment', 'V': 'Evocation', 'I': 'Illusion',
                'N': 'Necromancy', 'T': 'Transmutation', 'P': 'Psionic'}
