# class_matrix.py
# Comprehensive PF2E Remaster Class Data
# Proficiency values: 0=untrained, 2=trained, 4=expert, 6=master, 8=legendary

import json
import os
import re

# =============================================================================
# AUTOMATIC BONUS PROGRESSION (ABP) - Variant Rule
# =============================================================================
# Automatic Bonus Progression variant (GM Core p.83). Perception potency is
# L7/L13/L19 (audited 2026-06-09; was wrongly L9/L15). Skill potency and the L17
# Ability apex are player-choice/multi-skill and aren't modeled here (a flat
# per-type value can't represent "three skills at +2, one at +1").
ABP_TABLE = {
    1: {}, 2: {"attack_potency": 1}, 3: {}, 4: {"devastating_attacks": 2}, 5: {"defense_potency": 1},
    6: {}, 7: {"perception_potency": 1}, 8: {"save_potency": 1}, 9: {}, 10: {"attack_potency": 2},
    11: {"defense_potency": 2}, 12: {"devastating_attacks": 3}, 13: {"perception_potency": 2}, 14: {"save_potency": 2},
    15: {}, 16: {"attack_potency": 3}, 17: {}, 18: {"defense_potency": 3},
    19: {"devastating_attacks": 4, "perception_potency": 3}, 20: {"save_potency": 3}
}

def get_abp_bonus(level, bonus_type):
    current_bonus = 0
    if bonus_type == "devastating_attacks": current_bonus = 1
    for l in range(1, level + 1):
        if l in ABP_TABLE and bonus_type in ABP_TABLE[l]:
            current_bonus = ABP_TABLE[l][bonus_type]
    return current_bonus

# =============================================================================
# SKILL RANK GATING - Enforced during level-up
# =============================================================================
SKILL_RANK_GATES = {
    2: 1,   # Trained: available from level 1
    4: 7,   # Expert: can't be gained until you already have trained, but no level gate beyond initial
    6: 7,   # Master: requires level 7+
    8: 15,  # Legendary: requires level 15+
}

def validate_skill_rank(rank, character_level):
    """Returns True if the character can have this skill rank at this level."""
    if rank <= 2: return True  # Trained is always valid
    if rank == 4: return True  # Expert available whenever you get a skill increase (level 3+)
    if rank == 6: return character_level >= 7
    if rank == 8: return character_level >= 15
    return False

# =============================================================================
# ENCOUNTER XP — single source of truth for the encounter builder + tracker
# =============================================================================
# PF2e GM Core p.74. The XP value of a creature depends on its level relative
# to the party. ENCOUNTER_XP_BY_DIFF maps (creature_level - party_level)
# clamped to [-4, 4] to the XP value for ONE creature at that diff.
ENCOUNTER_XP_BY_DIFF = {
    -4: 10, -3: 15, -2: 20, -1: 30,
     0: 40,
     1: 60,  2: 80,  3: 120, 4: 160,
}

# Encounter difficulty thresholds (4-player baseline) and per-extra-PC scaling.
# `base` is the XP at which a 4-player encounter just hits this difficulty;
# `per_extra` is the +/- adjustment per PC above/below 4. So a 5-player
# Severe is 120 + 30 = 150 XP; a 3-player Severe is 120 - 30 = 90 XP.
ENCOUNTER_DIFFICULTY = [
    {"name": "Trivial",  "base": 40,  "per_extra": 10},
    {"name": "Low",      "base": 60,  "per_extra": 15},
    {"name": "Moderate", "base": 80,  "per_extra": 20},
    {"name": "Severe",   "base": 120, "per_extra": 30},
    {"name": "Extreme",  "base": 160, "per_extra": 40},
]

def encounter_threshold(name, party_size=4):
    """Return the XP threshold for a given difficulty at a given party size.
    Anything at-or-above the next tier's threshold gets the next-tier label.
    Below "Trivial" is just "Trivial-or-lower"."""
    party_size = max(1, int(party_size or 4))
    for entry in ENCOUNTER_DIFFICULTY:
        if entry["name"] == name:
            return entry["base"] + entry["per_extra"] * (party_size - 4)
    return None

# =============================================================================
# ANCESTRY DATA - Speeds, sizes, HP, senses, languages
# =============================================================================
ANCESTRY_SPEEDS = {
    "human": 25, "half-elf": 25, "half-orc": 25,
    "elf": 30,
    "dwarf": 20,
    "gnome": 25,
    "goblin": 25,
    "halfling": 25,
    "orc": 25,
    "leshy": 25,
    "kobold": 25,
    "catfolk": 25,
    "ratfolk": 25,
    "tengu": 25,
    "kitsune": 25,
    "automaton": 25,
    "fleshwarp": 25,
    "sprite": 20,
    "hobgoblin": 25,
    "lizardfolk": 25,
    "goloma": 30,
    "gnoll": 25,
    "grippli": 25,
    "shisk": 25,
    "conrasu": 25,
    "anadi": 25,
    "strix": 25,
    "android": 25,
    "fetchling": 25,
    "kashrishi": 25,
    "nagaji": 25,
    "vanara": 25,
    "vishkanya": 25,
    "ghoran": 25,
    "poppet": 25,
    "skeleton": 25,
    "awakened animal": 20,
    "surki": 25,
    "minotaur": 25,
    "centaur": 40,
    "merfolk": 5,
    "athamaru": 25,
}

ANCESTRY_SENSES = {
    "elf": ["Low-Light Vision"],
    "dwarf": ["Darkvision"],
    "gnome": ["Low-Light Vision"],
    "goblin": ["Darkvision"],
    "halfling": [],
    "human": [],
    "orc": ["Darkvision"],
    "leshy": ["Low-Light Vision"],
    "kobold": ["Darkvision"],
    "catfolk": ["Low-Light Vision"],
    "ratfolk": ["Low-Light Vision"],
    "tengu": ["Low-Light Vision"],
    "kitsune": [],
    "hobgoblin": ["Darkvision"],
    "lizardfolk": [],
    "fetchling": ["Darkvision"],
    "nagaji": ["Low-Light Vision"],
    "awakened animal": ["Darkvision"],
}

ANCESTRY_SIZES = {
    "human": "Medium", "elf": "Medium", "dwarf": "Medium", "gnome": "Small",
    "goblin": "Small", "halfling": "Small", "orc": "Medium", "leshy": "Small",
    "kobold": "Small", "catfolk": "Medium", "ratfolk": "Small", "tengu": "Medium",
    "kitsune": "Medium", "automaton": "Medium", "fleshwarp": "Medium",
    "sprite": "Tiny", "hobgoblin": "Medium", "lizardfolk": "Medium", "goloma": "Medium",
    "awakened animal": "Tiny",
    # Content audit 2026-06-09 (vs compendium): these defaulted to Medium but are
    # Large/Small in PF2e. (Other exotic-ancestry speed gaps were left as report
    # recommendations — the compendium itself has errors there, e.g. centaur speed.)
    "centaur": "Large", "minotaur": "Large", "poppet": "Small",
}

# =============================================================================
# ANCESTRY FEATURES — Key ancestry abilities displayed on character sheet
# =============================================================================
ANCESTRY_FEATURES = {
    "human": [
        {"name": "Versatile", "type": "passive", "desc": "Humans' ambition and versatility grant them an extra general feat at 1st level."},
    ],
    "elf": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light, and you ignore the concealed condition due to dim light."},
        {"name": "Elven Longevity", "type": "passive", "desc": "Elves can live to be over 600 years old. You gain access to age-related ancestry feats earlier than other ancestries."},
    ],
    "dwarf": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light, though your vision in darkness is in black and white."},
        {"name": "Clan Dagger", "type": "passive", "desc": "You get one clan dagger of your clan for free at character creation. It's a martial melee weapon in the knife group (1d4 P, agile, finesse, versatile S)."},
    ],
    "gnome": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light."},
        {"name": "Fey Touched", "type": "passive", "desc": "Your connection to the First World grants you a primal innate cantrip."},
    ],
    "goblin": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light."},
        {"name": "Goblin Scuttle", "type": "passive", "desc": "You are smaller and quicker than most, granting access to unique movement feats."},
    ],
    "halfling": [
        {"name": "Keen Eyes", "type": "passive", "desc": "Your eyes are sharp, allowing you to make out small details others miss. You gain a +2 circumstance bonus when using the Seek action to find hidden or undetected creatures within 30 feet of you."},
        {"name": "Halfling Luck", "type": "passive", "desc": "Your people have always seemed to bounce back from misfortune. You have access to ancestry feats that let you reroll failed checks."},
    ],
    "orc": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light."},
        {"name": "Orc Ferocity", "type": "passive", "desc": "Fierceness in battle runs through your blood. You have access to feats that let you avoid being knocked unconscious once per day."},
    ],
    "leshy": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light."},
        {"name": "Plant Nourishment", "type": "passive", "desc": "You gain nourishment from photosynthesis. You typically don't need to eat food as long as you have access to sunlight and water."},
    ],
    "kobold": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light."},
        {"name": "Draconic Exemplar", "type": "passive", "desc": "You draw connection to a specific type of dragon, granting you access to related ancestry feats and abilities."},
    ],
    "catfolk": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light."},
        {"name": "Land on Your Feet", "type": "passive", "desc": "When you fall, you take only half the normal damage and don't land prone."},
    ],
    "ratfolk": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light."},
        {"name": "Cheek Pouches", "type": "passive", "desc": "You can store up to 1 Bulk of items in your cheek pouches, accessed as a free action with the Interact trait."},
    ],
    "tengu": [
        {"name": "Low-Light Vision", "type": "passive", "desc": "You can see in dim light as though it were bright light."},
        {"name": "Sharp Beak", "type": "passive", "desc": "With your sharp beak, you are never without a weapon. You have a beak unarmed attack that deals 1d6 piercing damage (finesse)."},
    ],
    "kitsune": [
        {"name": "Change Shape", "type": "action", "actions": "◆", "desc": "You transform into your alternate form: a unique human appearance specific to you, or back to your true kitsune form. You gain a +4 circumstance bonus to Deception checks to Impersonate in your human form."},
    ],
    "hobgoblin": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light."},
        {"name": "Hobgoblin Weapon Familiarity", "type": "passive", "desc": "You are trained with longbows, composite longbows, longswords, and halberds."},
    ],
    "fetchling": [
        {"name": "Darkvision", "type": "passive", "desc": "You can see in darkness and dim light just as well as you can see in bright light."},
        {"name": "Shadow Blending", "type": "passive", "desc": "You slip between the shadows. You gain a +1 circumstance bonus to Stealth checks in dim light or darkness."},
    ],
}

# =============================================================================
# SPELL SLOT TABLES
# =============================================================================
SPELL_SLOT_TABLES = {
    # [1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th, 10th]
    "prepared": {
        1: [2,0,0,0,0,0,0,0,0,0], 2: [3,0,0,0,0,0,0,0,0,0], 3: [3,2,0,0,0,0,0,0,0,0],
        4: [3,3,0,0,0,0,0,0,0,0], 5: [3,3,2,0,0,0,0,0,0,0], 6: [3,3,3,0,0,0,0,0,0,0],
        7: [3,3,3,2,0,0,0,0,0,0], 8: [3,3,3,3,0,0,0,0,0,0], 9: [3,3,3,3,2,0,0,0,0,0],
        10: [3,3,3,3,3,0,0,0,0,0], 11: [3,3,3,3,3,2,0,0,0,0], 12: [3,3,3,3,3,3,0,0,0,0],
        13: [3,3,3,3,3,3,2,0,0,0], 14: [3,3,3,3,3,3,3,0,0,0], 15: [3,3,3,3,3,3,3,2,0,0],
        16: [3,3,3,3,3,3,3,3,0,0], 17: [3,3,3,3,3,3,3,3,2,0], 18: [3,3,3,3,3,3,3,3,3,0],
        19: [3,3,3,3,3,3,3,3,3,1], 20: [3,3,3,3,3,3,3,3,3,1]
    },
    "spontaneous": {
        1: [2,0,0,0,0,0,0,0,0,0], 2: [3,0,0,0,0,0,0,0,0,0], 3: [3,2,0,0,0,0,0,0,0,0],
        4: [3,3,0,0,0,0,0,0,0,0], 5: [3,3,2,0,0,0,0,0,0,0], 6: [3,3,3,0,0,0,0,0,0,0],
        7: [3,3,3,2,0,0,0,0,0,0], 8: [3,3,3,3,0,0,0,0,0,0], 9: [3,3,3,3,2,0,0,0,0,0],
        10: [3,3,3,3,3,0,0,0,0,0], 11: [3,3,3,3,3,2,0,0,0,0], 12: [3,3,3,3,3,3,0,0,0,0],
        13: [3,3,3,3,3,3,2,0,0,0], 14: [3,3,3,3,3,3,3,0,0,0], 15: [3,3,3,3,3,3,3,2,0,0],
        16: [3,3,3,3,3,3,3,3,0,0], 17: [3,3,3,3,3,3,3,3,2,0], 18: [3,3,3,3,3,3,3,3,3,0],
        19: [3,3,3,3,3,3,3,3,3,1], 20: [3,3,3,3,3,3,3,3,3,1]
    },
    "sorcerer": {
        1: [4,0,0,0,0,0,0,0,0,0], 2: [4,0,0,0,0,0,0,0,0,0], 3: [4,4,0,0,0,0,0,0,0,0],
        4: [4,4,0,0,0,0,0,0,0,0], 5: [4,4,4,0,0,0,0,0,0,0], 6: [4,4,4,0,0,0,0,0,0,0],
        7: [4,4,4,4,0,0,0,0,0,0], 8: [4,4,4,4,0,0,0,0,0,0], 9: [4,4,4,4,4,0,0,0,0,0],
        10: [4,4,4,4,4,0,0,0,0,0], 11: [4,4,4,4,4,4,0,0,0,0], 12: [4,4,4,4,4,4,0,0,0,0],
        13: [4,4,4,4,4,4,4,0,0,0], 14: [4,4,4,4,4,4,4,0,0,0], 15: [4,4,4,4,4,4,4,4,0,0],
        16: [4,4,4,4,4,4,4,4,0,0], 17: [4,4,4,4,4,4,4,4,4,0], 18: [4,4,4,4,4,4,4,4,4,0],
        19: [4,4,4,4,4,4,4,4,4,1], 20: [4,4,4,4,4,4,4,4,4,1]
    },
    "bounded": {
        1: [1,0,0,0,0,0,0,0,0,0], 2: [2,0,0,0,0,0,0,0,0,0], 3: [1,2,0,0,0,0,0,0,0,0],
        4: [0,2,0,0,0,0,0,0,0,0], 5: [0,2,2,0,0,0,0,0,0,0], 6: [0,0,2,0,0,0,0,0,0,0],
        7: [0,0,2,2,0,0,0,0,0,0], 8: [0,0,0,2,0,0,0,0,0,0], 9: [0,0,0,2,2,0,0,0,0,0],
        10: [0,0,0,0,2,0,0,0,0,0], 11: [0,0,0,0,2,2,0,0,0,0], 12: [0,0,0,0,0,2,0,0,0,0],
        13: [0,0,0,0,0,2,2,0,0,0], 14: [0,0,0,0,0,0,2,0,0,0], 15: [0,0,0,0,0,0,2,2,0,0],
        16: [0,0,0,0,0,0,0,2,0,0], 17: [0,0,0,0,0,0,0,2,2,0], 18: [0,0,0,0,0,0,0,0,2,0],
        19: [0,0,0,0,0,0,0,0,2,0], 20: [0,0,0,0,0,0,0,0,2,0]
    }
}

# =============================================================================
# SPELLS KNOWN (REPERTOIRE) for spontaneous casters
# PF2e Remaster: spontaneous casters know a fixed number of spells per rank.
# Format: level -> [cantrips_known, 1st, 2nd, 3rd, 4th, 5th, 6th, 7th, 8th, 9th]
# These are CUMULATIVE totals (not deltas).
# Bard/Oracle/Psychic use "spontaneous" (3 slots/rank), Sorcerer uses "sorcerer" (4 slots/rank).
# =============================================================================
SPELLS_KNOWN = {
    # Bard, Oracle, Psychic — standard spontaneous casters
    "spontaneous": {
        1:  [5,2,0,0,0,0,0,0,0,0],   2:  [5,3,0,0,0,0,0,0,0,0],
        3:  [5,3,2,0,0,0,0,0,0,0],    4:  [5,3,3,0,0,0,0,0,0,0],
        5:  [5,3,3,2,0,0,0,0,0,0],    6:  [5,3,3,3,0,0,0,0,0,0],
        7:  [5,3,3,3,2,0,0,0,0,0],    8:  [5,3,3,3,3,0,0,0,0,0],
        9:  [5,3,3,3,3,2,0,0,0,0],    10: [5,3,3,3,3,3,0,0,0,0],
        11: [5,3,3,3,3,3,2,0,0,0],    12: [5,3,3,3,3,3,3,0,0,0],
        13: [5,3,3,3,3,3,3,2,0,0],    14: [5,3,3,3,3,3,3,3,0,0],
        15: [5,3,3,3,3,3,3,3,2,0],    16: [5,3,3,3,3,3,3,3,3,0],
        17: [5,3,3,3,3,3,3,3,3,2],    18: [5,3,3,3,3,3,3,3,3,3],
        19: [5,3,3,3,3,3,3,3,3,3],    20: [5,3,3,3,3,3,3,3,3,3],
    },
    # Sorcerer — gets more spells known (matches slot count)
    "sorcerer": {
        1:  [5,2,0,0,0,0,0,0,0,0],   2:  [5,4,0,0,0,0,0,0,0,0],
        3:  [5,4,2,0,0,0,0,0,0,0],    4:  [5,4,4,0,0,0,0,0,0,0],
        5:  [5,4,4,2,0,0,0,0,0,0],    6:  [5,4,4,4,0,0,0,0,0,0],
        7:  [5,4,4,4,2,0,0,0,0,0],    8:  [5,4,4,4,4,0,0,0,0,0],
        9:  [5,4,4,4,4,2,0,0,0,0],    10: [5,4,4,4,4,4,0,0,0,0],
        11: [5,4,4,4,4,4,2,0,0,0],    12: [5,4,4,4,4,4,4,0,0,0],
        13: [5,4,4,4,4,4,4,2,0,0],    14: [5,4,4,4,4,4,4,4,0,0],
        15: [5,4,4,4,4,4,4,4,2,0],    16: [5,4,4,4,4,4,4,4,4,0],
        17: [5,4,4,4,4,4,4,4,4,2],    18: [5,4,4,4,4,4,4,4,4,4],
        19: [5,4,4,4,4,4,4,4,4,4],    20: [5,4,4,4,4,4,4,4,4,4],
    },
}

# =============================================================================
# AUTO-GRANTED FEATS BY CLASS / HERITAGE (Player Core / Player Core 2)
# Used by save_new_character to mirror what Pathbuilder marks as "Awarded Feat"
# entries — feats every PC of that class/heritage starts with at L1.
# Each entry: name, type, level, optional ability(skill name), optional desc.
# =============================================================================
CLASS_AWARDED_FEATS = {
    'champion':   [{'name': 'Shield Block', 'type': 'Awarded Feat', 'level': 1,
                    'desc': 'You snap your shield in to ward off a blow.'},
                   # Devotion Spells: every Champion gets Lay on Hands (holy) or Touch of the Void
                   # (unholy). Defaults to Lay on Hands; sanctification handling overrides via the
                   # builder if the chosen cause is unholy (Tyrant/Desecrator/Antipaladin).
                   {'name': 'Lay on Hands', 'type': 'Focus Spell', 'level': 1,
                    'desc': 'Heal a willing living target with a touch (1d6 per spell rank, +6 if undead).'}],
    'cleric':     [{'name': 'Shield Block', 'type': 'Awarded Feat', 'level': 1}],
    'druid':      [{'name': 'Shield Block', 'type': 'Awarded Feat', 'level': 1}],
    'fighter':    [{'name': 'Shield Block', 'type': 'Awarded Feat', 'level': 1},
                   {'name': 'Attack of Opportunity', 'type': 'Class Feature', 'level': 1}],
    'monk':       [],
    'wizard':     [],
    'sorcerer':   [],
    'bard':       [],
    'rogue':      [],
    'ranger':     [],
    'witch':      [],
    'oracle':     [],
    'magus':      [],
    'summoner':   [],
    'investigator': [],
    'kineticist': [],
    'barbarian':  [],
    'thaumaturge':[],
    'animist':    [],
    'inventor':   [],
    'gunslinger': [],
    'swashbuckler':[],
    'psychic':    [],
}

# Subclass/order/cause-specific awarded feats (e.g. Storm Druid's Storm Born)
SUBCLASS_AWARDED_FEATS = {
    # Druid orders
    'Storm':      [{'name': 'Storm Born', 'type': 'Awarded Feat', 'level': 1,
                    'desc': "You ignore concealment from fog/precipitation; you don't take environmental Perception/ranged-attack penalties from precipitation."}],
    # Champion causes — Lay on Hands etc. handled via SUBCLASS_MATRIX[*].focus_spell
    # Cleric doctrine
    'Warpriest':  [],
    'Cloistered Cleric': [],
}

# Heritage-granted feats (e.g. Hold-Scarred Orc grants Diehard)
HERITAGE_AWARDED_FEATS = {
    'Hold-Scarred Orc':     [{'name': 'Diehard', 'type': 'Awarded Feat', 'level': 1,
                              'desc': 'You die from dying at value 5 instead of 4.'}],
    'Versatile Human':      [],   # Bonus general feat handled separately (player picks)
    'Skilled Heritage Human':[],  # Bonus skill — chosen
    'Wintertouched Human':  [],
    'Half-Elf':             [],
    'Half-Orc':             [],
}

# =============================================================================
# PER-CLASS LEVEL PROGRESSION
# Each entry: level -> {proficiency_key: new_minimum_rank}
# These are the AUTOMATIC proficiency bumps from class features.
# Values: 2=trained, 4=expert, 6=master, 8=legendary
# =============================================================================
CLASS_PROGRESSION = {
    # -------------------------------------------------------------------------
    # FIGHTER (Player Core p.136)
    # -------------------------------------------------------------------------
    "fighter": {
        # L1 initial: perception=4, fort=4, ref=4, will=2, simple=4, martial=4, unarmed=4, advanced=2, all armor=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Bravery
        7:  {"perception": 6},                                                               # Battlefield Surveyor
        9:  {"fortitude": 6},                                                                # Battle Hardened (Fort Master) + Combat Flexibility
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4, "class_dc": 4},           # Armor Expertise + Fighter Expertise (class DC Expert @11, not @9)
        13: {"simple": 6, "martial": 6, "unarmed": 6, "advanced": 4},                       # Weapon Legend
        15: {"reflex": 6},                                                                   # Tempered Reflexes
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6},                          # Armor Mastery
        19: {"simple": 8, "martial": 8, "unarmed": 8, "advanced": 6},                       # Versatile Legend
    },
    # -------------------------------------------------------------------------
    # RANGER (Player Core p.152)
    # -------------------------------------------------------------------------
    "ranger": {
        # Player Core p.152 advancement table (audited 2026-06-09).
        # L1 initial: perception=4, fort=4, ref=4, will=2, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Will Expertise
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Ranger Weapon Expertise
        7:  {"perception": 6, "reflex": 6},                                                 # Perception Mastery + Natural Reflexes (Reflex Master)
        9:  {"class_dc": 4},                                                                 # Ranger Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4, "fortitude": 6},                      # Medium Armor Expertise + Warden's Endurance (Fort Master)
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Martial Weapon Mastery
        15: {"reflex": 8, "perception": 8},                                                 # Greater Natural Reflexes (Reflex Legendary) + Perception Legend
        17: {"class_dc": 6},                                                                 # Masterful Hunter (class DC Master)
        19: {"unarmored": 6, "light": 6, "medium": 6},                                      # Medium Armor Mastery
    },
    # -------------------------------------------------------------------------
    # BARBARIAN (Player Core p.118)
    # -------------------------------------------------------------------------
    "barbarian": {
        # L1 initial: perception=4, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        # Player Core 2 advancement table (audited 2026-06-10).
        3:  {},                                                                              # Furious Footfalls (Speed only)
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Brutality (weapons Expert)
        7:  {"fortitude": 6},                                                                # Juggernaut (Fort Master)
        9:  {"reflex": 4},                                                                   # Reflex Expertise
        11: {"class_dc": 4},                                                                 # Mighty Rage (class DC Expert)
        13: {"fortitude": 8, "light": 4, "medium": 4, "unarmored": 4, "simple": 6, "martial": 6, "unarmed": 6},  # Greater Juggernaut (Fort Legendary) + Medium Armor Expertise + Weapon Mastery
        15: {"will": 6},                                                                     # Indomitable Will (Will Master)
        17: {"perception": 6},                                                               # Perception Mastery
        19: {"light": 6, "medium": 6, "unarmored": 6, "class_dc": 6},                        # Armor Mastery + Devastator (class DC Master)
    },
    # -------------------------------------------------------------------------
    # CHAMPION (Player Core p.124)
    # -------------------------------------------------------------------------
    "champion": {
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, all armor=2, unarmored=2, class_dc=2
        # NB: Champion does NOT get Reflex Expert at L3 — that was a bug; their Reflex stays Trained until Greater Reflex at L13.
        3:  {},                                                                              # Divine Ally — no proficiency change
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4},                                                               # Vigilant Senses
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Champion Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4},                          # Armor Expertise
        13: {"reflex": 4, "simple": 6, "martial": 6, "unarmed": 6},                         # Greater Reflex + Weapon Mastery
        15: {"will": 6},                                                                     # Greater Will (Master Will save)
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6},                          # Armor Mastery
        19: {"perception": 6, "fortitude": 8},                                               # Hero's Defenses
    },
    # -------------------------------------------------------------------------
    # MONK (Player Core p.146)
    # -------------------------------------------------------------------------
    "monk": {
        # L1 initial: perception=2, fort=4, ref=4, will=4, simple=2, unarmed=4, unarmored=4, class_dc=2
        # Player Core 2 advancement table (audited 2026-06-10). L1 unarmed is
        # Trained (corrected in CLASS_MATRIX base) -> Expert at L5 via Expert Strikes.
        3:  {},                                                                              # Mystic Strikes (no proficiency change)
        5:  {"perception": 4, "unarmed": 4, "simple": 4},                                    # Perception Expertise + Expert Strikes (unarmed/simple Expert)
        7:  {},                                                                              # Path to Perfection (player-choice save -> Master; not modeled)
        9:  {"class_dc": 4},                                                                 # Monk Expertise
        11: {},                                                                              # Second Path to Perfection (player-choice save -> Master)
        13: {"unarmed": 6, "simple": 6, "unarmored": 6},                                     # Master Strikes (weapons Master) + Graceful Mastery (unarmored Master)
        15: {},                                                                              # Third Path to Perfection (player-choice save -> Legendary)
        17: {"unarmored": 8, "class_dc": 6},                                                 # Graceful Legend (unarmored Legendary + class DC Master). Monk has NO Legendary weapons.
        19: {},                                                                              # Perfected Form (fortune effect; no proficiency change)
    },
    # -------------------------------------------------------------------------
    # ROGUE (Player Core p.160)
    # -------------------------------------------------------------------------
    "rogue": {
        # Player Core p.156 advancement table (audited 2026-06-09).
        # L1 initial: perception=4, fort=2, ref=4, will=4, simple=2, martial=2(rapier/sap/shortbow/shortsword), unarmed=2, light=2, unarmored=2, class_dc=2
        3:  {},                                                                              # Deny Advantage (no proficiency bump)
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Tricks (weapons Expert)
        7:  {"perception": 6, "reflex": 6},                                                  # Vigilant Senses (Perception Master) + Evasive Reflexes (Reflex Master)
        9:  {"fortitude": 4},                                                                # Rogue Resilience (Fort Expert)
        11: {"class_dc": 4},                                                                 # Rogue Expertise (class DC Expert @11, not @9)
        13: {"simple": 6, "martial": 6, "unarmed": 6, "reflex": 8, "perception": 8, "unarmored": 4, "light": 4},  # Master Tricks (weapons Master) + Improved Rogue Reflexes (Reflex Legendary) + Incredible Senses (Perception Legendary) + Light Armor Expertise
        15: {},                                                                              # Double Debilitation / Greater Weapon Spec (no rank)
        17: {"will": 6},                                                                     # Slippery Mind (Will Master @17, not @15)
        19: {"unarmored": 6, "light": 6},                                                   # Light Armor Mastery (no phantom Perception/Fort bump)
    },
    # -------------------------------------------------------------------------
    # SWASHBUCKLER (Player Core 2 / APG)
    # -------------------------------------------------------------------------
    "swashbuckler": {
        # L1 initial: perception=4, fort=2, ref=4, will=4, simple=2, martial=2, unarmed=2, light=2, unarmored=2, class_dc=2
        # Player Core 2 advancement table (audited 2026-06-10).
        3:  {"fortitude": 4},                                                                # Fortitude Expertise
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"reflex": 6},                                                                   # Confident Evasion (Reflex Master)
        9:  {"class_dc": 4},                                                                 # Swashbuckler Expertise
        11: {"perception": 6},                                                               # Perception Mastery
        13: {"reflex": 8, "light": 4, "unarmored": 4, "simple": 6, "martial": 6, "unarmed": 6},  # Assured Evasion (Reflex Legendary) + Light Armor Expertise + Weapon Mastery
        15: {},                                                                              # Greater Weapon Spec / Keen Flair (no rank)
        17: {"will": 6},                                                                     # Reinforced Ego (Will Master)
        19: {"class_dc": 6, "light": 6, "unarmored": 6},                                     # Eternal Confidence (class DC Master) + Light Armor Mastery
    },
    # -------------------------------------------------------------------------
    # GUNSLINGER (Guns & Gears)
    # -------------------------------------------------------------------------
    "gunslinger": {
        # L1 initial: perception=4, fort=2, ref=4, will=2, simple=4, martial=4, unarmed=2, advanced(firearms/crossbow)=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Stubborn
        5:  {"simple": 6, "martial": 6},                                                     # Gun Weapon Mastery
        7:  {"perception": 6},                                                               # Vigilant Senses
        9:  {"reflex": 6, "class_dc": 4},                                                   # Evasion + Gunslinger Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Armor Expertise
        13: {"simple": 8, "martial": 8},                                                     # Weapon Legend (with firearms group)
        15: {"fortitude": 6},                                                                # Greater saves
        17: {"unarmored": 6, "light": 6, "medium": 6},                                      # Armor Mastery
        19: {"reflex": 8},                                                                   # Improved Evasion → Legendary
    },
    # -------------------------------------------------------------------------
    # INVESTIGATOR (APG / Player Core 2)
    # -------------------------------------------------------------------------
    "investigator": {
        # L1 initial: perception=4, fort=2, ref=4, will=4, simple=2, martial=2, unarmed=2, light=2, unarmored=2, class_dc=2
        # Player Core 2 advancement table (audited 2026-06-10).
        3:  {},                                                                              # Keen Recollection (no proficiency rank change)
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6},                                                               # Vigilant Senses (Perception Master)
        9:  {"fortitude": 4, "class_dc": 4},                                                # Fortitude Expertise + Investigator Expertise
        11: {"will": 6},                                                                     # Dogged Will (Will Master)
        13: {"perception": 8, "light": 4, "unarmored": 4, "simple": 6, "martial": 6, "unarmed": 6},  # Incredible Senses (Perception Legendary) + Light Armor Expertise + Weapon Mastery
        15: {"reflex": 6},                                                                   # Savvy Reflexes (Reflex Master)
        17: {"will": 8},                                                                     # Greater Dogged Will (Will Legendary)
        19: {"light": 6, "unarmored": 6, "class_dc": 6},                                     # Light Armor Mastery + Master Detective (class DC Master)
    },
    # -------------------------------------------------------------------------
    # THAUMATURGE (Dark Archive)
    # -------------------------------------------------------------------------
    "thaumaturge": {
        # L1 initial: perception=4, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6},                                                               # Vigilant Senses
        9:  {"fortitude": 6, "class_dc": 4},                                                # Resolve + Thaumaturge Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"will": 6, "reflex": 6},                                                       # Greater Will + Greater Ref
        17: {"unarmored": 6, "light": 6, "medium": 6},                                      # Armor Mastery
        19: {"fortitude": 8},                                                                # Legendary Fort
    },
    # -------------------------------------------------------------------------
    # ALCHEMIST (Player Core p.108)
    # -------------------------------------------------------------------------
    "alchemist": {
        # L1 initial: perception=2, fort=4, ref=4, will=2, simple=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        # Player Core 2 advancement table (audited 2026-06-10). Remaster reworked
        # the alchemist heavily; Fort caps at Master (Chemical Hardiness), Will/
        # Perception cap at Expert, class DC reaches Master (Alchemical Mastery).
        7:  {"simple": 4, "unarmed": 4, "will": 4},                                          # Alchemical Weapon Expertise (bombs/simple/unarmed Expert) + Will Expertise
        9:  {"class_dc": 4, "perception": 4},                                                # Alchemical Expertise (class DC Expert) + Perception Expertise
        11: {"fortitude": 6},                                                                # Chemical Hardiness (Fort Master)
        13: {"light": 4, "medium": 4, "unarmored": 4},                                       # Medium Armor Expertise
        15: {"simple": 6, "unarmed": 6, "reflex": 6},                                        # Alchemical Weapon Mastery + Explosion Dodger (Reflex Master)
        17: {"class_dc": 6},                                                                 # Alchemical Mastery (class DC Master)
        19: {"light": 6, "medium": 6, "unarmored": 6},                                       # Medium Armor Mastery
    },
    # -------------------------------------------------------------------------
    # INVENTOR (Guns & Gears)
    # -------------------------------------------------------------------------
    "inventor": {
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4, "fortitude": 6},                                               # Alertness + Juggernaut
        9:  {"class_dc": 4, "will": 6},                                                     # Inventor Expertise + Resolve
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Medium Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"reflex": 6},                                                                   # Greater Ref
        17: {"unarmored": 6, "light": 6, "medium": 6, "fortitude": 8},                      # Armor Mastery + Legendary Fort
        19: {"class_dc": 6, "perception": 6},                                               # Master Inventor
    },
    # -------------------------------------------------------------------------
    # BARD (Player Core p.56)
    # -------------------------------------------------------------------------
    "bard": {
        # L1 initial: perception=4, fort=2, ref=2, will=4, simple=2, martial=2(longsword,rapier,sap,shortbow,shortsword,whip), unarmed=2, light=2, unarmored=2, spell_attack=2, spell_dc=2
        # Player Core p.100 advancement table (audited 2026-06-09).
        3:  {"reflex": 4},                                                                  # Reflex Expertise
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster (Fortitude Expertise is L9, not L7)
        9:  {"will": 6, "fortitude": 4},                                                     # Performer's Heart (Will Master) + Fortitude Expertise
        11: {"simple": 4, "martial": 4, "unarmed": 4, "perception": 6},                     # Bard Weapon Expertise + Vigilant Senses (Perception Master). Bard has NO Reflex Master.
        13: {"unarmored": 4, "light": 4},                                                   # Light Armor Expertise
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Legendary Will
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # SORCERER (Player Core p.170)
    # Verified against Pathbuilder L12 character export
    # -------------------------------------------------------------------------
    "sorcerer": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        5:  {"fortitude": 4},                                                                # Magical Fortitude (Expert Fort)
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"reflex": 4},                                                                   # Lightning Reflexes (Expert Ref)
        11: {"perception": 4, "simple": 4, "unarmed": 4},                                   # Alertness (Expert Perception) + Weapon Expertise
        13: {"unarmored": 4},                                                                # Defensive Robes
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 6, "fortitude": 6},                                                     # Majestic Will (Will → Master) + Greater Fortitude
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # ORACLE (Player Core 2)
    # -------------------------------------------------------------------------
    "oracle": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, light=2, unarmored=2, spell_attack=2, spell_dc=2
        # Player Core 2 advancement table (audited 2026-06-10).
        7:  {"spell_attack": 4, "spell_dc": 4, "will": 6},                                   # Expert Spellcaster + Mysterious Resolve (Will Master)
        9:  {"fortitude": 4},                                                                # Magical Fortitude (Fort Expert)
        11: {"perception": 4, "simple": 4, "unarmed": 4},                                    # Oracular Senses (Perception Expert) + Weapon Expertise
        13: {"reflex": 4, "light": 4, "unarmored": 4},                                       # Premonition's Reflexes (Reflex Expert) + Light Armor Expertise
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Greater Mysterious Resolve (Will Legendary)
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # WITCH (Player Core p.184)
    # -------------------------------------------------------------------------
    "witch": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        5:  {"fortitude": 4},                                                                # Expert Fort (Patron's Resilience)
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"perception": 4, "will": 6},                                                    # Alertness + Resolve
        11: {"reflex": 4, "simple": 4, "unarmed": 4},                                       # Lightning Reflexes + Weapon Expertise
        13: {"unarmored": 4},                                                                # Defensive Robes
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Legendary Will
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # PSYCHIC (Dark Archive)
    # -------------------------------------------------------------------------
    "psychic": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"fortitude": 4},                                                                # Expert Fort
        5:  {"perception": 4},                                                               # Alertness
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"will": 6},                                                                     # Resolve
        11: {"reflex": 4, "simple": 4, "unarmed": 4},                                       # Lightning Reflexes + Weapon Expertise
        13: {"unarmored": 4},                                                                # Defensive Robes
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Legendary Will
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # ANIMIST (Player Core 2)
    # -------------------------------------------------------------------------
    "animist": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, light=2, medium=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"fortitude": 4},                                                                # Expert Fort
        5:  {"perception": 4},                                                               # Alertness
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"will": 6},                                                                     # Resolve
        11: {"reflex": 4, "unarmored": 4, "light": 4, "medium": 4},                         # Lightning Reflexes + Armor Expertise
        13: {"simple": 4, "unarmed": 4},                                                    # Weapon Expertise
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Legendary Will
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # =========================================================================
    # BOUNDED / HYBRID CASTERS
    # =========================================================================
    # -------------------------------------------------------------------------
    # MAGUS (Secrets of Magic)
    # -------------------------------------------------------------------------
    "magus": {
        # Verified vs Secrets of Magic Table 2-1 (Magus Advancement) + the
        # feature descriptions, cross-checked with the Foundry pf2e data
        # (2026-06-15). L1 initial: perception=2, fort=4, ref=2, will=4,
        # simple/martial/unarmed=2, light/medium/unarmored=2, spell_attack/dc=2;
        # magus has NO class DC progression and no legendary proficiencies.
        5:  {"reflex": 4, "simple": 4, "martial": 4, "unarmed": 4},                          # Lightning Reflexes + Weapon Expertise
        9:  {"perception": 4, "spell_attack": 4, "spell_dc": 4, "will": 6},                  # Alertness + Expert Spellcaster + Resolve
        11: {"unarmored": 4, "light": 4, "medium": 4},                                       # Medium Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                       # Weapon Mastery
        15: {"fortitude": 6},                                                                # Juggernaut
        17: {"unarmored": 6, "light": 6, "medium": 6, "spell_attack": 6, "spell_dc": 6},     # Medium Armor Mastery + Master Spellcaster
    },
    # -------------------------------------------------------------------------
    # SUMMONER (Secrets of Magic)
    # -------------------------------------------------------------------------
    "summoner": {
        # Verified vs Secrets of Magic Table 2-3 (Summoner Advancement) + the
        # feature descriptions, cross-checked with the Foundry pf2e data
        # (2026-06-15). L1 initial: perception=2, fort=4, ref=2, will=4,
        # simple/unarmed=2, unarmored=2, spell_attack/dc=2 (NO martial, NO
        # light/medium armor). Many advancement features boost the EIDOLON, not
        # the summoner's own sheet, and are excluded here. The summoner gains no
        # legendary proficiency and no class DC progression. NB simple-weapon /
        # unarmed expertise is L11 (Simple Weapon Expertise), not L5.
        3:  {"perception": 4},                                                               # Shared Vigilance
        9:  {"reflex": 4, "spell_attack": 4, "spell_dc": 4},                                 # Shared Reflexes + Expert Spellcaster
        11: {"simple": 4, "unarmed": 4, "fortitude": 6},                                     # Simple Weapon Expertise + Twin Juggernauts
        13: {"unarmored": 4},                                                                # Defensive Robes
        15: {"will": 6},                                                                     # Shared Resolve
        17: {"spell_attack": 6, "spell_dc": 6},                                              # Master Spellcaster
    },
    # =========================================================================
    # WAR OF IMMORTALS / PLAYER CORE 2 CLASSES
    # =========================================================================
    # -------------------------------------------------------------------------
    # EXEMPLAR (War of Immortals)
    # -------------------------------------------------------------------------
    "exemplar": {
        # L1 initial: perception=2, fort=4, ref=4, will=2, simple=2, martial=2, unarmed=2, light=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Bravery equivalent
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4, "class_dc": 4},                                               # Alertness + Exemplar Expertise
        9:  {"fortitude": 6},                                                                # Juggernaut
        11: {"unarmored": 4, "light": 4, "reflex": 6},                                      # Armor Expertise + Evasion
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"will": 6},                                                                     # Greater Will
        17: {"unarmored": 6, "light": 6},                                                   # Armor Mastery
        19: {"perception": 6, "fortitude": 8},                                               # Greater saves
    },
    # -------------------------------------------------------------------------
    # COMMANDER (War of Immortals)
    # -------------------------------------------------------------------------
    "commander": {
        # L1 initial: perception=4, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, all armor=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6},                                                               # Vigilant Senses
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Commander Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4},                          # Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"will": 6, "reflex": 6},                                                       # Greater saves
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6},                          # Armor Mastery
        19: {"fortitude": 8, "perception": 8},                                               # Legendary saves
    },
    # -------------------------------------------------------------------------
    # GUARDIAN (War of Immortals)
    # -------------------------------------------------------------------------
    "guardian": {
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, all armor=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4, "fortitude": 6},                                               # Alertness + Juggernaut
        9:  {"class_dc": 4, "will": 6},                                                     # Guardian Expertise + Resolve
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4},                          # Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"reflex": 6},                                                                   # Greater Ref
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6, "fortitude": 8},          # Armor Mastery + Legendary Fort
        19: {"perception": 6, "will": 8},                                                    # Master Perception + Legendary Will
    },
    # -------------------------------------------------------------------------
    # WIZARD (Player Core p.190) — Remaster verified
    # -------------------------------------------------------------------------
    "wizard": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        5:  {"reflex": 4},                                                                  # Reflex Expertise
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"fortitude": 4},                                                                # Magical Fortitude (Expert Fort)
        11: {"simple": 4, "unarmed": 4, "perception": 4},                                   # Weapon Expertise + Alertness (Expert Perception)
        13: {"unarmored": 4},                                                                # Defensive Robes (Expert Unarmored)
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 6},                                                                     # Prodigious Will (Master Will)
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # CLERIC - Cloistered Cleric doctrine (Player Core p.130)
    # Warpriest has its own SUBCLASS_PROGRESSION entry
    # -------------------------------------------------------------------------
    "cleric": {
        # Cloistered Cleric (Player Core p.130 doctrine table; audited 2026-06-09).
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"fortitude": 4},                                                                # 2nd Doctrine: Fort Expert
        5:  {"perception": 4},                                                               # Perception Expertise
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # 3rd Doctrine: spell Expert
        9:  {"will": 6},                                                                     # Resolute Faith (Will Master)
        11: {"reflex": 4, "simple": 4, "unarmed": 4},                                       # Reflex Expertise + 4th Doctrine (favored/simple/unarmed Expert)
        13: {"unarmored": 4},                                                                # Divine Defense (unarmored Expert)
        15: {"spell_attack": 6, "spell_dc": 6},                                             # 5th Doctrine: spell Master
        # L17 grants no proficiency increase (Cloistered Will caps at Master/L9, Fort at Expert/L3).
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Final Doctrine: spell Legendary
    },
    # -------------------------------------------------------------------------
    # DRUID (Player Core p.134) — rulebook advancement table verified 2026-06-09.
    # NB: the prior "Weapon Expertise removed in Remaster" note was a misread of a
    # LEVEL-10 Pathbuilder export — Druid Weapon Expertise is at L11, so an L10
    # export can't show it. Player Core table (lines 11817-11845) is authoritative:
    # L3 Fortitude+Perception expertise; L5 Reflex expertise; L11 Weapon Expertise +
    # Wild Willpower; L13 Medium Armor Expertise; no L17 save increase.
    # -------------------------------------------------------------------------
    "druid": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, light=2, medium=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"perception": 4, "fortitude": 4},                                              # Perception Expertise + Fortitude Expertise
        5:  {"reflex": 4},                                                                   # Reflex Expertise
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        11: {"will": 6, "simple": 4, "unarmed": 4},                                         # Wild Willpower (Will→Master) + Weapon Expertise (simple/unarmed→Expert)
        13: {"light": 4, "medium": 4, "unarmored": 4},                                      # Medium Armor Expertise (Weapon Specialization grants no rank)
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        # L17 grants no proficiency increase (9th-rank spells / ancestry feat / skill increase).
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # KINETICIST (Rage of Elements) — AoN verified
    # -------------------------------------------------------------------------
    "kineticist": {
        # L1 initial: perception=2, fort=4, ref=4, will=2, simple=2, unarmed=2, light=2, unarmored=2, class_dc=2
        # (Rage of Elements advancement table p.16 + feature texts; audited 2026-06-09.)
        3:  {"will": 4},                                                                    # Will Expertise
        7:  {"fortitude": 6, "class_dc": 4},                                                # Kinetic Durability (Fort→Master) + Kinetic Expertise (class DC→Expert)
        9:  {"perception": 4},                                                               # Perception Expertise
        11: {"reflex": 6, "simple": 4, "unarmed": 4},                                       # Kinetic Quickness (Reflex→Master) + Weapon Expertise (simple/unarmed→Expert)
        13: {"light": 4, "unarmored": 4},                                                   # Light Armor Expertise (Weapon Specialization grants no rank)
        15: {"fortitude": 8, "class_dc": 6},                                               # Greater Kinetic Durability (Fort→Legendary) + Kinetic Mastery (class DC→Master)
        # L17 (Double Reflow / Final Gate region) grants no proficiency increase.
        19: {"light": 6, "unarmored": 6, "class_dc": 8},                                    # Light Armor Mastery (armor→Master) + Kinetic Legend (class DC→Legendary)
    },
}

# =============================================================================
# SUBCLASS-SPECIFIC PROGRESSION OVERRIDES
# These REPLACE the base CLASS_PROGRESSION for the given subclass.
# Combines shared class features + doctrine-specific features.
# =============================================================================
SUBCLASS_PROGRESSION = {
    # -------------------------------------------------------------------------
    # WARPRIEST (Cleric Doctrine) — Verified against Pathbuilder L12 export
    # Shared features: L5 Alertness, L9 Resolute Faith, L11 Lightning Reflexes
    # Doctrine-specific: L1 armor/Fort, L3 martial, L7 weapons, L11 Expert Spell, L13 armor Expert
    # -------------------------------------------------------------------------
    "Warpriest": {
        # Warpriest (Player Core p.130 doctrine table; audited 2026-06-09).
        # L1 initial overrides handled by SUBCLASS_MATRIX (Fort Expert, light/medium armor)
        3:  {"martial": 2},                                                                  # 2nd Doctrine: Trained martial weapons
        5:  {"perception": 4},                                                               # Perception Expertise (shared)
        7:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # 3rd Doctrine: weapons Expert
        9:  {"will": 6},                                                                     # Resolute Faith (Will Master, shared)
        11: {"reflex": 4, "spell_attack": 4, "spell_dc": 4},                                # Reflex Expertise (shared) + 4th Doctrine: spell Expert
        13: {"unarmored": 4, "light": 4, "medium": 4},                                      # Divine Defense + Warpriest light/medium armor Expert
        15: {"fortitude": 6},                                                                # 5th Doctrine: Fortitude Master (NOT spell — that's the Cloistered doctrine)
        # L17 grants no proficiency increase (Will caps at Master/L9).
        19: {"spell_attack": 6, "spell_dc": 6},                                             # Final Doctrine: spell + favored weapon Master (weapon categories stay Expert in this model)
    },
    # Cloistered Cleric uses the base "cleric" CLASS_PROGRESSION — no override needed
    
    # -------------------------------------------------------------------------
    # RUFFIAN (Rogue Racket) — medium armor scales with light armor
    # "When you gain light armor expertise, also gain expert in medium armor"
    # "When you gain light armor mastery, also gain master in medium armor"
    # -------------------------------------------------------------------------
    "Ruffian": {
        # Same as base rogue, but with medium armor added at L11 and L17
        3:  {"perception": 4, "will": 4},
        5:  {"simple": 4, "martial": 4, "unarmed": 4},
        7:  {"perception": 6, "reflex": 6},
        9:  {"fortitude": 4, "class_dc": 4},
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Light Armor Expertise + Ruffian medium
        13: {"simple": 6, "martial": 6, "unarmed": 6, "reflex": 8},
        15: {"will": 6},
        17: {"unarmored": 6, "light": 6, "medium": 6},                                      # Light Armor Mastery + Ruffian medium
        19: {"perception": 8, "fortitude": 6},
    },
}

# =============================================================================
# MONK PATH TO PERFECTION CONFIG
# Player-choice proficiency bumps at specific levels
# =============================================================================
MONK_PATH_CONFIG = {
    7: {
        "feature_name": "Path to Perfection",
        "description": "Choose your Fortitude, Reflex, or Will saving throw. Your proficiency rank for the chosen saving throw increases to master. When you roll a success on the chosen saving throw, you get a critical success instead.",
        "choices": ["fortitude", "reflex", "will"],
        "target_rank": 6,  # Master
        "restriction": None,  # Any of the three
    },
    11: {
        "feature_name": "Second Path to Perfection",
        "description": "Choose a different saving throw than the one you chose for Path to Perfection. Your proficiency rank for the chosen saving throw increases to master. When you roll a success on the chosen saving throw, you get a critical success instead.",
        "choices": ["fortitude", "reflex", "will"],
        "target_rank": 6,  # Master
        "restriction": "exclude_previous",  # Must be different from L7 choice
    },
    15: {
        "feature_name": "Third Path to Perfection",
        "description": "Choose one of the saving throws you selected for Path to Perfection or Second Path to Perfection. Your proficiency rank for the chosen saving throw increases to legendary. When you critically fail the chosen saving throw, you get a failure instead. When you fail the chosen saving throw against a damaging effect, you take half damage.",
        "choices": ["fortitude", "reflex", "will"],
        "target_rank": 8,  # Legendary
        "restriction": "only_previous",  # Must be one of L7 or L11 choices
    },
}

# =============================================================================
# HELPER: Get cumulative proficiency for a class at a given level
# =============================================================================
def get_class_proficiency_at_level(class_name, level, subclass=None):
    """
    Returns a dict of all proficiency bumps that have occurred up to and including the given level.
    If a subclass is provided and has a SUBCLASS_PROGRESSION entry, uses that instead.
    """
    c_name = class_name.lower()
    
    # Check for subclass-specific progression first
    prog = None
    if subclass and subclass in SUBCLASS_PROGRESSION:
        prog = SUBCLASS_PROGRESSION[subclass]
    elif c_name in CLASS_PROGRESSION:
        prog = CLASS_PROGRESSION[c_name]
    else:
        return {}

    result = {}
    for lvl in sorted(prog.keys()):
        if lvl <= level:
            for key, val in prog[lvl].items():
                result[key] = max(result.get(key, 0), val)
    return result

def get_new_bumps_at_level(class_name, level, subclass=None):
    """
    Returns only the proficiency bumps that happen at exactly this level.
    If a subclass is provided and has a SUBCLASS_PROGRESSION entry, uses that instead.
    """
    c_name = class_name.lower()
    
    if subclass and subclass in SUBCLASS_PROGRESSION:
        return SUBCLASS_PROGRESSION[subclass].get(level, {})
    
    if c_name not in CLASS_PROGRESSION:
        return {}
    return CLASS_PROGRESSION[c_name].get(level, {})

# =============================================================================
# BASE FEAT/SLOT PROGRESSION (shared by all classes)
# =============================================================================
base_prog = {
    1: {"class_feat": 1, "ancestry_feat": 1},
    2: {"class_feat": 1, "skill_feat": 1},
    3: {"general_feat": 1, "skill_increase": 1},
    4: {"class_feat": 1, "skill_feat": 1},
    5: {"ancestry_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    6: {"class_feat": 1, "skill_feat": 1},
    7: {"general_feat": 1, "skill_increase": 1},
    8: {"class_feat": 1, "skill_feat": 1},
    9: {"ancestry_feat": 1, "skill_increase": 1},
    10: {"class_feat": 1, "skill_feat": 1, "ability_boosts": 4},
    11: {"general_feat": 1, "skill_increase": 1},
    12: {"class_feat": 1, "skill_feat": 1},
    13: {"ancestry_feat": 1, "skill_increase": 1},
    14: {"class_feat": 1, "skill_feat": 1},
    15: {"general_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    16: {"class_feat": 1, "skill_feat": 1},
    17: {"ancestry_feat": 1, "skill_increase": 1},
    18: {"class_feat": 1, "skill_feat": 1},
    19: {"general_feat": 1, "skill_increase": 1},
    20: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4}
}

# Rogue: skill feat at EVERY level, skill increase at EVERY level starting L2
rogue_prog = {
    1: {"class_feat": 1, "ancestry_feat": 1, "skill_feat": 1},
    2: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    3: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    4: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    5: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    6: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    7: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    8: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    9: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    10: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    11: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    12: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    13: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    14: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    15: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    16: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    17: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    18: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    19: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    20: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4}
}

def get_progression_table(class_name):
    """Return the per-level feat/skill-increase requirement table for a class.
    Used by the level-up validator to confirm the player picked everything
    they're owed at this level."""
    cn = (class_name or '').lower()
    if cn == 'rogue':
        return rogue_prog
    if cn == 'investigator':
        return investigator_prog
    return base_prog

def get_required_slots_at_level(class_name, level):
    """Return {slot_name: count} of choices the player MUST make at this level."""
    table = get_progression_table(class_name)
    return dict(table.get(level, {}))

# Investigator: skill feats at EVERY level (Skillful Lessons at odd levels), skill increase at EVERY level starting L2
investigator_prog = {
    1: {"class_feat": 1, "ancestry_feat": 1},
    2: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    3: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    4: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    5: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    6: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    7: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    8: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    9: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    10: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    11: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    12: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    13: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    14: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    15: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    16: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    17: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1},
    18: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    19: {"general_feat": 1, "skill_feat": 1, "skill_increase": 1},
    20: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4}
}

# =============================================================================
# CLASS_MATRIX - Initial proficiencies (level 1)
# =============================================================================
CLASS_MATRIX = {
    "alchemist":    {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "animist":      {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "barbarian":    {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "bard":         {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "champion":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "cleric":       {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "commander":    {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": base_prog},
    "druid":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "exemplar":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "fighter":      {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 4, "simple": 4, "martial": 4, "advanced": 2, "perception": 4, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "guardian":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "gunslinger":   {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 4, "martial": 4, "advanced": 2, "perception": 4, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "investigator": {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": investigator_prog},
    "inventor":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "kineticist":   {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "magus":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "monk":         {"base_proficiencies": {"unarmored": 4, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 4}, "progression": base_prog},  # PC2: unarmed is Trained at L1 (Expert via Expert Strikes @5)
    "oracle":       {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "psychic":      {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "ranger":       {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "rogue":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": rogue_prog},
    "sorcerer":     {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "summoner":     {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "swashbuckler": {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": base_prog},
    "thaumaturge":  {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "witch":        {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "wizard":       {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog}
}

# =============================================================================
# SUBCLASS_MATRIX - Subclass overrides
# =============================================================================
SUBCLASS_MATRIX = {
    "Ruffian": {"armor": {"medium": 2}, "key_ability": "str", "skills": ["intimidation"]},
    "Thief": {"key_ability": "dex", "flags": ["dex_to_damage"], "skills": ["thievery"]},
    "Scoundrel": {"key_ability": "cha", "skills": ["deception", "diplomacy"]},
    "Mastermind": {"key_ability": "int", "skills": ["society"]},
    "Warpriest": {"armor": {"light": 2, "medium": 2}, "weapons": {"martial": 2}, "fortitude": 4, "skills": []},
    "Cloistered Cleric": {"armor": {"unarmored": 2}},
    "Aberrant": {"tradition": "occult", "skills": ["intimidation", "occultism"], "granted_spells": [{"lvl": 0, "name": "Daze"}, {"lvl": 1, "name": "Phantom Pain"}], "focus_spell": "Tentacular Limbs"},
    "Angelic": {"tradition": "divine", "skills": ["diplomacy", "religion"], "granted_spells": [{"lvl": 0, "name": "Light"}, {"lvl": 1, "name": "Heal"}], "focus_spell": "Angelic Halo"},
    "Demonic": {"tradition": "divine", "skills": ["intimidation", "religion"], "granted_spells": [{"lvl": 0, "name": "Acid Splash"}, {"lvl": 1, "name": "Fear"}], "focus_spell": "Glutton's Jaw"},
    "Diabolic": {"tradition": "divine", "skills": ["deception", "religion"], "granted_spells": [{"lvl": 0, "name": "Ignition"}, {"lvl": 1, "name": "Charm"}], "focus_spell": "Diabolic Edict"},
    "Draconic": {"tradition": "arcane", "skills": ["arcana", "intimidation"], "granted_spells": [{"lvl": 0, "name": "Shield"}, {"lvl": 1, "name": "Sure Strike"}], "focus_spell": "Dragon Claws"},
    "Elemental": {"tradition": "primal", "skills": ["intimidation", "nature"], "granted_spells": [{"lvl": 0, "name": "Ignition"}, {"lvl": 1, "name": "Breathe Fire"}], "focus_spell": "Elemental Toss"},
    "Fey": {"tradition": "primal", "skills": ["deception", "nature"], "granted_spells": [{"lvl": 0, "name": "Ghost Sound"}, {"lvl": 1, "name": "Charm"}], "focus_spell": "Faerie Dust"},
    "Hag": {"tradition": "occult", "skills": ["deception", "occultism"], "granted_spells": [{"lvl": 0, "name": "Daze"}, {"lvl": 1, "name": "Illusory Object"}], "focus_spell": "Jealous Hex"},
    "Imperial": {"tradition": "arcane", "skills": ["arcana", "society"], "granted_spells": [{"lvl": 0, "name": "Detect Magic"}, {"lvl": 1, "name": "Force Barrage"}], "focus_spell": "Ancestral Memories"},
    "Nymph": {"tradition": "primal", "skills": ["diplomacy", "nature"], "granted_spells": [{"lvl": 0, "name": "Light"}, {"lvl": 1, "name": "Charm"}], "focus_spell": "Nymph's Token"},
    "Undead": {"tradition": "divine", "skills": ["intimidation", "religion"], "granted_spells": [{"lvl": 0, "name": "Void Warp"}, {"lvl": 1, "name": "Harm"}], "focus_spell": "Undeath's Blessing"},
    "Justice": {"focus_spell": "Retributive Strike", "skills": ["athletics", "intimidation"], "desc": "You are a champion of justice, punishing those who harm the innocent. You gain the Retributive Strike reaction."},
    "Mercy": {"focus_spell": "Glimpse of Redemption", "skills": ["diplomacy", "medicine"], "desc": "You show mercy to your foes and protect the weak. You gain the Glimpse of Redemption reaction."},
    "Grandeur": {"focus_spell": "Liberating Step", "skills": ["acrobatics", "diplomacy"], "desc": "You exemplify grandeur and free the oppressed. You gain the Liberating Step reaction."},
    "Paladin": {"focus_spell": "Retributive Strike", "skills": ["athletics", "intimidation"], "desc": "You follow the tenets of good and law. You gain the Retributive Strike champion's reaction."},
    "Redeemer": {"focus_spell": "Glimpse of Redemption", "skills": ["diplomacy", "medicine"], "desc": "You follow the tenets of good. You gain the Glimpse of Redemption champion's reaction."},
    "Liberator": {"focus_spell": "Liberating Step", "skills": ["acrobatics", "diplomacy"], "desc": "You follow the tenets of good. You gain the Liberating Step champion's reaction."},
    "Desecrator": {"focus_spell": "Touch of Corruption", "skills": ["athletics", "intimidation"], "desc": "You follow the tenets of evil. You gain the Touch of Corruption champion's reaction."},
    "Tyrant": {"focus_spell": "Iron Command", "skills": ["intimidation", "deception"], "desc": "You follow the tenets of evil and law. You gain the Iron Command champion's reaction."},
    "Antipaladin": {"focus_spell": "Selfish Shield", "skills": ["athletics", "deception"], "desc": "You follow the tenets of evil and chaos. You gain the Selfish Shield champion's reaction."},
    # --- Oracle Mysteries ---
    "Ancestors": {"focus_spell": "Spirit Veil", "skills": ["society"]},
    "Battle": {"focus_spell": "Call to Arms", "skills": ["athletics"]},
    "Bones": {"focus_spell": "Soul Siphon", "skills": ["religion"]},
    "Cosmos": {"focus_spell": "Spray of Stars", "skills": ["nature"]},
    "Flames": {"focus_spell": "Incendiary Aura", "skills": ["athletics"]},
    "Life": {"focus_spell": "Life Link", "skills": ["medicine"]},
    "Lore": {"focus_spell": "Brain Drain", "skills": ["occultism"]},
    "Tempest": {"focus_spell": "Tempest Touch", "skills": ["nature"]},
    "Time": {"focus_spell": "Time Skip", "skills": ["occultism"]},
    # --- Druid Orders ---
    "Animal": {"focus_spell": "Heal Animal", "skills": ["athletics"]},
    "Leaf": {"focus_spell": "Goodberry", "skills": ["diplomacy"]},
    "Storm": {"focus_spell": "Tempest Surge", "skills": ["acrobatics"]},
    "Untamed": {"focus_spell": "Wild Shape", "skills": ["intimidation"]},
    # --- Witch Patrons ---
    "Curse": {"focus_spell": "Evil Eye", "tradition": "occult"},
    "Fate": {"focus_spell": "Nudge Fate", "tradition": "occult"},
    "Fervor": {"focus_spell": "Stoke the Heart", "tradition": "divine"},
    "Night": {"focus_spell": "Shroud of Night", "tradition": "occult"},
    "Rune": {"focus_spell": "Discern Secrets", "tradition": "arcane"},
    "Wild": {"focus_spell": "Wilding Word", "tradition": "primal"},
    "Winter": {"focus_spell": "Clinging Ice", "tradition": "primal"},
}

# =============================================================================
# SUBCLASS DESCRIPTIONS — Shown in the builder when selecting a subclass
# =============================================================================
SUBCLASS_DESCRIPTIONS = {
    # --- Barbarian Instincts ---
    "Barbarian:Animal": "You have a kinship with an animal. While raging, you can grow animal features like claws or fangs, gaining an unarmed attack. You gain animal skin at higher levels for AC bonus.",
    "Barbarian:Dragon": "You channel the fury of a mighty dragon. While raging, you can use a breath weapon. You gain draconic resistance and at higher levels can grow wings.",
    "Fury": "Your rage comes from within — pure, undiluted fury. You aren't limited by an instinct's appearance or anathema. You gain additional damage while raging.",
    "Giant": "Your rage gives you the strength of giants. While raging, you can wield weapons built for larger creatures. At higher levels you can grow in size.",
    "Spirit": "You channel rage from the spirits around you. While raging, your strikes deal additional positive or negative damage. You can see invisible spirits.",
    "Superstition": "You distrust magic intensely. While raging, you gain bonuses to saves vs magic. You deal extra damage to spellcasters but must avoid beneficial magic from allies.",
    # --- Bard Muses ---
    "Enigma": "Your muse is a mystery — the unknown itself. You gain Bardic Lore (a special Lore check for any topic) and the True Strike composition spell.",
    "Maestro": "Your muse drives you to perform. You gain the Lingering Composition feat, extending your compositions an extra round.",
    "Polymath": "Your muse is the pursuit of all knowledge. You gain the Versatile Performance feat and can swap out spells daily like a prepared caster.",
    "Warrior": "Your muse drives you to battle. You gain martial weapon proficiency and the Inspire Heroics composition spell.",
    # --- Champion Causes ---
    "Justice": "You are a champion of justice, punishing those who harm the innocent. You gain the Retributive Strike reaction — when an enemy damages your ally, you can Strike that enemy and reduce the damage your ally takes.",
    "Mercy": "You show mercy to your foes and protect the weak. You gain the Glimpse of Redemption reaction — when an enemy damages your ally, the enemy must choose to stop or take a penalty.",
    "Grandeur": "You exemplify grandeur and free the oppressed. You gain the Liberating Step reaction — when an enemy restricts your ally, your ally can Step away and gains a bonus to AC.",
    "Paladin": "You follow the tenets of good and law. You gain the Retributive Strike champion's reaction — make a Strike against an enemy that damaged your ally.",
    "Redeemer": "You follow the tenets of good. You gain the Glimpse of Redemption reaction — force foes to choose between taking enfeebled 2 or stopping their attack.",
    "Liberator": "You follow the tenets of good. You gain the Liberating Step reaction — let allies escape from enemy control and gain AC bonus.",
    "Desecrator": "You follow the tenets of evil. You gain the Touch of Corruption reaction — deal negative damage to foes who strike your allies.",
    "Tyrant": "You follow the tenets of evil and law. You gain the Iron Command reaction — enemies that damage your allies must kneel or take mental damage.",
    "Antipaladin": "You follow the tenets of evil and chaos. You gain the Selfish Shield reaction — absorb damage meant for you from an ally's attack.",
    # --- Cleric Doctrines ---
    "Cloistered Cleric": "You are a scholar of the faith. You gain expert proficiency in your deity's favored weapon, additional trained skills, and enhanced spellcasting. Your fortitude save starts at trained.",
    "Warpriest": "You are a warrior of the faith. You gain better armor and weapon proficiency (trained in martial weapons, medium armor), but your spellcasting advances more slowly. Expert fortitude.",
    # --- Druid Orders ---
    "Animal": "You have a special connection to animals. You gain an animal companion and the Heal Animal focus spell. Anathema: You must not harm animals unnecessarily.",
    "Leaf": "You revere plant life and natural growth. You gain the Goodberry focus spell and add additional plant-related spells. Anathema: Destroying plant life needlessly.",
    "Storm": "You are a conduit for nature's destructive power. You gain the Tempest Surge focus spell, dealing electricity damage. Anathema: Polluting the air or sky.",
    "Untamed": "You embody wild nature. You gain the Wild Shape focus spell, letting you transform into animals. Anathema: Teaching the secrets of the wild to those who would exploit them.",
    # --- Fighter Styles ---
    "Two-Handed": "You specialize in powerful two-handed weapons like greatswords and greataxes. You deal devastating single strikes.",
    "Dual-Wielding": "You specialize in fighting with a weapon in each hand. You gain advantages with Double Slice and similar two-weapon feats.",
    "Sword & Board": "You specialize in weapon-and-shield combat. Shield Block and Reactive Shield are your bread and butter.",
    "Archery": "You specialize in ranged combat with bows and crossbows. You excel at Point-Blank Stance and precise shots.",
    # --- Kineticist Gates ---
    "Single Gate": "You connect to a single element (Air, Earth, Fire, Metal, Water, or Wood). You gain deeper mastery over one element with access to all its impulses.",
    "Dual Gate": "You connect to two elements. You gain access to impulses from both elements and powerful composite blasts that combine them.",
    # --- Magus Hybrid Studies ---
    "Inexorable Iron": "You wield a two-handed weapon with arcane might. Your Spellstrike uses Strength for the melee attack. You gain damage resistance while in Arcane Cascade.",
    "Laughing Shadow": "You combine speed and shadow magic. You gain a bonus to Speed while in Arcane Cascade and deal extra damage on your first Strike each turn.",
    "Sparkling Targe": "You combine shield and spell. You gain the Raise a Shield action for free when entering Arcane Cascade and can Shield Block magical damage.",
    "Starlit Span": "You channel spells through ranged attacks. Your Spellstrike works with ranged weapons, letting you combine spells with bow shots.",
    "Twisting Tree": "You fight with a staff, switching between one-handed and two-handed grips. You gain flexible weapon use and can change grip as part of Spellstrike.",
    # --- Monk (no official subclasses in PF2E but we list styles) ---
    # --- Oracle Mysteries ---
    "Ancestors": "Your mystery connects you to ancestral spirits. Your curse causes spirits to cloud your vision. You gain Spirit Veil (focus spell) and bonuses to knowledge.",
    "Battle": "Your mystery grants martial prowess. Your curse makes your body fragile outside combat. You gain Call to Arms (focus spell) and weapon proficiency.",
    "Bones": "Your mystery connects you to undeath. Your curse drains your vitality. You gain Soul Siphon (focus spell) and resistance to negative damage.",
    "Cosmos": "Your mystery ties you to the stars. Your curse makes you vulnerable to darkness. You gain Spray of Stars (focus spell) and darkvision.",
    "Flames": "Your mystery channels fire. Your curse makes you vulnerable to cold. You gain Incendiary Aura (focus spell) and fire resistance.",
    "Life": "Your mystery channels healing. Your curse makes your own healing less effective. You gain Life Link (focus spell) and enhanced Heal.",
    "Lore": "Your mystery grants forbidden knowledge. Your curse fragments your thoughts. You gain Brain Drain (focus spell) and extra trained skills.",
    "Tempest": "Your mystery commands storms. Your curse creates dangerous electricity around you. You gain Tempest Touch (focus spell) and electricity resistance.",
    "Time": "Your mystery connects you to the flow of time. Your curse destabilizes your temporal existence. You gain Time Skip (focus spell).",
    # --- Psychic Conscious Minds ---
    "Distant Grasp": "You move things with your mind. Your unique cantrip is Telekinetic Rend. Your psyche grants telekinetic force.",
    "Infinite Eye": "You perceive beyond normal senses. Your unique cantrip is Glimpse Weakness. Your psyche reveals hidden truths.",
    "Silent Whisper": "You project thoughts into minds. Your unique cantrip is Shatter Mind. Your psyche overwhelms foes mentally.",
    "Tangent Strike": "You combine psychic power with weapon strikes. Your unique cantrip is Redistribution of Force. Your psyche enhances attacks.",
    "Unbound Step": "You bend space around you. Your unique cantrip is Warp Step. Your psyche lets you teleport.",
    # --- Ranger Hunter's Edge ---
    "Flurry": "You strike your prey in quick succession. While hunting your prey, the penalty for your second attack is reduced (–3 with agile, –4 otherwise).",
    "Outwit": "You outthink your prey. You gain a +2 circumstance bonus to Deception, Intimidation, Stealth, and Recall Knowledge checks against your hunted prey.",
    "Precision": "You strike your prey with pinpoint accuracy. The first time each round you hit your hunted prey, you deal 1d8 additional precision damage (increases at higher levels).",
    # --- Rogue Rackets ---
    "Ruffian": "You use thuggish tactics. You can Sneak Attack with medium armor and can use d8 weapons. You deal Sneak Attack damage with simple weapons that deal d8 or less.",
    "Scoundrel": "You use charm and deception. You can Sneak Attack foes you've Feinted against. You gain trained in Deception and Diplomacy.",
    "Thief": "You rely on agility and precision. You add your Dexterity modifier to damage with finesse weapons instead of Strength. You gain trained in Thievery.",
    "Eldritch Trickster": "You combine roguish skills with minor spellcasting. You gain a multiclass spellcasting dedication feat for free at 1st level.",
    "Mastermind": "You outthink your foes. You can Sneak Attack creatures that are off-guard to you due to Recall Knowledge. You gain trained in Society.",
    # --- Sorcerer Bloodlines ---
    "Aberrant": "Occult tradition. Strange and alien power flows through you. Blood Magic: Mental damage or create difficult terrain.",
    "Angelic": "Divine tradition. Celestial power shines through you. Blood Magic: Healing or status bonus to saves.",
    "Demonic": "Divine tradition. Fiendish fury courses through you. Blood Magic: Frightened condition or bonus damage.",
    "Diabolic": "Divine tradition. Infernal order guides your magic. Blood Magic: Status bonus to Deception or fascinated condition.",
    "Draconic": "Arcane tradition. The blood of dragons grants you power. Blood Magic: Energy resistance or bonus damage matching your element.",
    "Elemental": "Primal tradition. Elemental forces surge through you. Blood Magic: Energy damage or push enemies.",
    "Sorcerer:Fey": "Primal tradition. Fey magic runs in your blood. Blood Magic: Concealment or mental damage.",
    "Hag": "Occult tradition. Dark magic from hag ancestry. Blood Magic: Frightened or sickened condition.",
    "Imperial": "Arcane tradition. Ancient magical bloodline. Blood Magic: Status bonus to skill checks or force damage.",
    "Nymph": "Primal tradition. Natural beauty and charm. Blood Magic: Status bonus to Diplomacy or fascinated condition.",
    "Sorcerer:Undead": "Divine tradition. Death's power animates you. Blood Magic: Negative damage or temp HP from harming living.",
    # --- Swashbuckler Styles ---
    "Battledancer": "You gain panache by successfully Performing. Your Confident Finisher deals extra damage and can fascinate.",
    "Braggart": "You gain panache by Demoralizing foes. Your Confident Finisher deals extra damage and can frighten.",
    "Fencer": "You gain panache by Feinting. Your Confident Finisher deals extra damage and can make the target off-guard.",
    "Gymnast": "You gain panache by Tripping, Shoving, or Grappling. Your Confident Finisher deals extra damage and can trip.",
    "Wit": "You gain panache by using Bon Mot (a witty retort). Your Confident Finisher deals extra damage and imposes a penalty to Will saves.",
    # --- Investigator Methodologies ---
    "Alchemical Sciences": "You use alchemy in your investigations. You gain the Alchemical Crafting feat and can create alchemical items during daily preparations.",
    "Empiricism": "You rely on your senses and careful observation. You gain Perception as a trained skill and bonuses to Recall Knowledge when using Devise a Stratagem.",
    "Interrogation": "You extract truth from suspects. You gain Intimidation bonuses and can Coerce faster. Your Devise a Stratagem works with Intimidation-based strikes.",
    "Forensic Medicine": "You apply medical knowledge to your investigations. You gain the Battle Medicine feat and bonuses to Medicine checks when examining bodies or wounds.",
    # --- Thaumaturge Implements ---
    "Amulet": "Your amulet protects you. You gain a reaction that grants resistance when you or an ally is damaged. At higher levels, the protection strengthens.",
    "Bell": "Your bell disrupts foes. You can ring the bell to deal sonic damage and potentially stun enemies. At higher levels, the effect intensifies.",
    "Chalice": "Your chalice heals. You can drink from the chalice to heal yourself. At higher levels, allies can also benefit from your chalice.",
    "Tome": "Your tome grants knowledge. You gain a bonus to Recall Knowledge and can learn weaknesses from it. At higher levels, you gain additional trained skills.",
    "Wand": "Your wand shoots force bolts. You can fire the wand to deal force damage at range. At higher levels, the bolts become more powerful.",
    "Weapon": "Your weapon implement grants combat prowess. You gain bonus damage with your implement weapon. At higher levels, you can imbue it with additional properties.",
    # --- Gunslinger Ways ---
    "Drifter": "You mix gunplay with melee combat. Your Slinger's Reload lets you Stride and reload. You excel at closing distance and switching between ranged and melee.",
    "Pistolero": "You are a master duelist with one-handed firearms. Your Slinger's Reload lets you Interact to reload and gain a bonus to your next attack. You specialize in pistols.",
    "Sniper": "You are a patient marksman. Your Slinger's Reload lets you Hide or take cover while reloading. You specialize in long-range shots.",
    "Vanguard": "You use firearms at close range with a shield. Your Slinger's Reload lets you Raise a Shield while reloading. You specialize in shotguns and blunderbusses.",
    "Spellshot": "You combine magic with gunplay. You gain minor spellcasting and can channel spells through your firearm.",
    # --- Inventor Innovations ---
    "Armor": "Your innovation is a suit of power armor. You gain medium armor proficiency and your armor has special modifications that improve as you level.",
    # --- Witch Patrons ---
    "Curse": "Your patron deals in curses and misfortune. Your tradition is occult. You gain the Evil Eye hex cantrip.",
    "Fate": "Your patron controls destiny. Your tradition is occult. You gain the Nudge Fate hex cantrip.",
    "Fervor": "Your patron inspires zealous devotion. Your tradition is divine. You gain the Stoke the Heart hex cantrip.",
    "Night": "Your patron rules the darkness. Your tradition is occult. You gain the Shroud of Night hex cantrip.",
    "Rune": "Your patron works through magical runes. Your tradition is arcane. You gain the Discern Secrets hex cantrip.",
    "Wild": "Your patron embodies untamed nature. Your tradition is primal. You gain the Wilding Word hex cantrip.",
    "Winter": "Your patron commands cold and frost. Your tradition is primal. You gain the Clinging Ice hex cantrip.",
    # --- Wizard Schools ---
    "Abjuration": "You specialize in protective magic. You gain the Protective Ward school spell and bonus Abjuration spell slots.",
    "Conjuration": "You specialize in summoning and teleportation. You gain the Augment Summoning school spell and bonus Conjuration slots.",
    "Divination": "You specialize in gaining knowledge. You gain the Diviner's Sight school spell and bonus Divination slots.",
    "Enchantment": "You specialize in influencing minds. You gain the Charming Words school spell and bonus Enchantment slots.",
    "Evocation": "You specialize in energy and destruction. You gain the Force Bolt school spell and bonus Evocation slots.",
    "Illusion": "You specialize in deceiving senses. You gain the Warped Terrain school spell and bonus Illusion slots.",
    "Necromancy": "You specialize in life and death. You gain the Call of the Grave school spell and bonus Necromancy slots.",
    "Transmutation": "You specialize in changing forms. You gain the Physical Boost school spell and bonus Transmutation slots.",
    "Universalist": "You don't specialize — you master all schools equally. You gain Drain Bonded Item an additional time per day and a free extra spell slot at each level.",
    # --- Alchemist Research Fields ---
    "Bomber": "You specialize in explosive alchemical bombs. Your bombs deal additional splash damage and you gain proficiency with bomb-like items.",
    "Chirurgeon": "You specialize in healing elixirs and medicines. You can use Crafting instead of Medicine for healing checks and your elixirs of life are more potent.",
    "Mutagenist": "You specialize in transformative mutagens. Your mutagens last longer and you gain the Mutagenic Flashback ability to regain mutagen benefits.",
    "Toxicologist": "You specialize in poisons and venoms. Your poisons are more potent and harder to resist. You can apply poisons more efficiently.",
    # --- Commander Drilled Responses ---
    # --- Guardian ---
    # --- Exemplar ---
    # --- Summoner Eidolons ---
    "Beast": "Your eidolon is a primal beast. It gains powerful natural attacks and primal tradition spells.",
    "Construct": "Your eidolon is an arcane construct. It gains construct traits and arcane tradition spells.",
    "Demon": "Your eidolon is a demon. It gains fiendish traits and divine tradition spells.",
    "Devotion": "Your eidolon is a holy phantom. It gains celestial traits and divine tradition spells.",
    "Dragon": "Your eidolon is a dragon. It gains a breath weapon and arcane tradition spells.",
    "Fey": "Your eidolon is a fey creature. It gains fey traits and primal tradition spells.",
    "Plant": "Your eidolon is a plant creature. It gains plant traits and primal tradition spells.",
    "Undead": "Your eidolon is undead. It gains undead traits and divine tradition spells.",
}

# =============================================================================
# SPELL/ABILITY ACTION COSTS — ◆ = 1 action, ◆◆ = 2 actions, ◆◆◆ = 3 actions
# ◇ = free action, ⟳ = reaction, ◆-◆◆◆ = variable
# =============================================================================
_LEGACY_SPELL_ACTIONS = {
    # --- Common Spells ---
    'acid splash': '◆◆', 'air bubble': '⟳', 'alarm': '◆◆◆', 'animate dead': '◆◆◆',
    'bane': '◆◆', 'bless': '◆◆', 'blur': '◆◆', 'breathe fire': '◆◆',
    'burning hands': '◆◆', 'calm emotions': '◆◆', 'chain lightning': '◆◆',
    'charm': '◆◆', 'chill touch': '◆◆', 'color spray': '◆◆', 'command': '◆◆',
    'cone of cold': '◆◆', 'confusion': '◆◆', 'counterspell': '⟳',
    'daze': '◆◆', 'detect magic': '◆◆', 'dimension door': '◆◆', 'disguise self': '◆◆',
    'disintegrate': '◆◆', 'dispel magic': '◆◆', 'divine lance': '◆◆',
    'divine wrath': '◆◆', 'dominate': '◆◆',
    'earthquake': '◆◆', 'electric arc': '◆◆', 'energy burst': '◆◆',
    'enlarge': '◆◆', 'entangle': '◆◆',
    'fabricated truth': '◆◆◆', 'faerie fire': '◆◆', 'fear': '◆◆',
    'feather fall': '⟳', 'finger of death': '◆◆', 'fire ray': '◆◆',
    'fire shield': '◆◆', 'fireball': '◆◆', 'fleet step': '◆◆',
    'fly': '◆◆', 'forbidding ward': '◆◆', 'force barrage': '◆-◆◆◆',
    'freedom of movement': '◆◆',
    'ghost sound': '◆◆', 'goblin pox': '◆◆', 'grim tendrils': '◆◆',
    'guidance': '◆', 'gust of wind': '◆◆',
    'harm': '◆-◆◆◆', 'haste': '◆◆', 'heal': '◆-◆◆◆', 'heroism': '◆◆',
    'hideous laughter': '◆◆', 'holy cascade': '◆◆', 'holy light': '◆◆',
    'hydraulic push': '◆◆',
    'ignition': '◆◆', 'illusory disguise': '◆◆', 'illusory object': '◆◆',
    'invisibility': '◆◆',
    'jump': '◆', 'knock': '◆◆',
    'light': '◆◆', 'lightning bolt': '◆◆',
    'mage armor': '◆◆', 'mage hand': '◆◆', 'magic missile': '◆-◆◆◆',
    'magic weapon': '◆◆', 'maze': '◆◆', 'message': '◆', 'meteor swarm': '◆◆',
    'mirror image': '◆◆', 'mystic armor': '◆◆',
    'paralyze': '◆◆', 'pass without trace': '◆◆', 'phantom pain': '◆◆',
    'power word kill': '◆', 'prestidigitation': '◆◆', 'produce flame': '◆◆',
    'protection': '◆◆',
    'raise dead': '◆◆◆', 'ray of enfeeblement': '◆◆', 'ray of frost': '◆◆',
    'regenerate': '◆◆', 'remove curse': '◆◆', 'remove disease': '◆◆',
    'resist energy': '◆◆', 'restoration': '◆◆', 'restore senses': '◆◆',
    'reveal true name': '◆◆', 'reverse gravity': '◆◆', 'revivify': '◆◆',
    'runic weapon': '◆',
    'sanctuary': '◆◆', 'searing light': '◆◆', 'see invisibility': '◆◆',
    'shield': '◆', 'shield other': '◆◆', 'shocking grasp': '◆◆',
    'silence': '◆◆', 'sleep': '◆◆', 'slow': '◆◆', 'sound burst': '◆◆',
    'speak with animals': '◆◆', 'spider climb': '◆◆', 'spirit blast': '◆◆',
    'spiritual weapon': '◆◆', 'stabilize': '◆◆', 'stinking cloud': '◆◆◆',
    'stone tell': '◆◆', 'stoneskin': '◆◆', 'suggestion': '◆◆',
    'summon animal': '◆◆◆', 'summon construct': '◆◆◆', 'summon elemental': '◆◆◆',
    'sure strike': '◆', 'synesthesia': '◆◆',
    'tanglefoot': '◆◆', 'telekinetic maneuver': '◆◆', 'telekinetic projectile': '◆◆',
    'teleport': '◆◆◆', 'time stop': '◆◆◆', 'tongues': '◆◆', 'tree stride': '◆◆◆',
    'true seeing': '◆◆', 'true strike': '◆',
    'vampiric touch': '◆◆', 'veil': '◆◆', 'vitality lash': '◆◆',
    'void warp': '◆◆',
    'wall of fire': '◆◆◆', 'wall of force': '◆◆◆', 'wall of stone': '◆◆◆',
    'wall of thorns': '◆◆◆', 'wall of wind': '◆◆◆', 'warp step': '◆',
    'water breathing': '◆◆', 'weapon surge': '◆', 'web': '◆◆◆',
    'wish': '◆◆◆', 'word of truth': '◆',
    'zone of truth': '◆◆',
    # --- Focus Spells ---
    'lay on hands': '◆', 'retributive strike': '⟳', 'glimpse of redemption': '⟳',
    'liberating step': '⟳', 'touch of corruption': '⟳', 'iron command': '⟳',
    'selfish shield': '⟳', 'sun blade': '◆◆', 'light of revelation': '◆◆',
    'shield of faith': '◆', 'sacred form': '◆',
    'ki strike': '◆', 'ki blast': '◆◆', 'ki rush': '◆◆', 'wholeness of body': '◆',
    'wild shape': '◆◆', 'wild morph': '◆◆', 'tempest surge': '◆◆',
    'goodberry': '◆◆', 'heal animal': '◆',
    'courageous anthem': '◆', 'counter performance': '⟳', 'inspire defense': '◆',
    'lingering composition': '◇', 'fortissimo composition': '◆', 'dirge of doom': '◆',
    'triple time': '◆', 'allegro': '◆', 'soothing ballad': '◆',
    'dragon claws': '◆', 'angelic halo': '◆', 'tentacular limbs': '◆',
    "glutton's jaw": '◆', 'diabolic edict': '◆', 'elemental toss': '◆◆',
    'faerie dust': '◆', 'jealous hex': '◆', 'ancestral memories': '◆',
    "nymph's token": '◆', "undeath's blessing": '◆',
    'soul siphon': '◆◆', 'incendiary aura': '◆◆', 'life link': '◆',
    'brain drain': '◆◆', 'tempest touch': '◆◆', 'time skip': '◆',
    'call to arms': '⟳', 'spirit veil': '◆', 'spray of stars': '◆◆',
    'evolution surge': '◆◆', 'extend boost': '◇', 'lifelink surge': '◆◆',
    "eidolon's wrath": '◆◆◆', 'unfetter eidolon': '◆◆',
    'evil eye': '◆', 'nudge fate': '◆', 'stoke the heart': '◆',
    'shroud of night': '◆', 'discern secrets': '◆', 'wilding word': '◆',
    'clinging ice': '◆',
    'heal companion': '◆◆', 'enlarge companion': '◆◆', "ranger's bramble": '◆◆',
    'magic hide': '◆◆', 'snare hopping': '◆◆',
    # --- Kineticist Impulses ---
    'burning jet': '◆◆', 'scorching column': '◆◆', 'flying flame': '◆◆',
    'versatile blasts': '◆', 'elemental blast': '◆-◆◆', 'base kinesis': '◆◆',
    'channel elements': '◆', 'weapon infusion': '◇', 'winter sleet': '◆◆◆',
    'thermal nimbus': '◆◆', 'chain infusion': '◇', 'tidal hands': '◆◆',
    'stone shield': '⟳', 'tremor': '◆◆', 'aerial boomerang': '◆◆',
    'timber sentinel': '◆◆◆', 'metal carapace': '◆',
    # --- Common Attack Actions ---
    'strike': '◆', 'raise a shield': '◆', 'shield block': '⟳',
    'stride': '◆', 'step': '◆', 'interact': '◆', 'recall knowledge': '◆',
    'seek': '◆', 'demoralize': '◆', 'feint': '◆', 'trip': '◆',
    'grapple': '◆', 'shove': '◆', 'disarm': '◆',
}

# --- Action cost, driven by the Foundry-sourced master spell list ----------
# The legacy table above is kept ONLY as a fallback for non-spell combat
# actions (Strike, Stride, Shield Block) and any pre-remaster name the master
# list no longer carries. Every real spell's cost comes from
# compendium_data/spells/master_spells.json, which has an authoritative
# `actions` value for all ~1,795 spells. Master wins on any name overlap.

def _action_display(raw):
    """Map a Foundry casting-time value to what the sheet should show.

    Action counts -> Paizo action glyphs; reaction/free -> their glyphs;
    variable counts -> a glyph range; anything else is a duration
    (minutes / hours / days / rounds) rendered as compact text."""
    low = str(raw or '').strip().lower()
    glyphs = {
        '1': '◆', '2': '◆◆', '3': '◆◆◆',
        'reaction': '⟳', 'free': '◇',
        '1 to 3': '◆-◆◆◆', '1 or 2': '◆-◆◆', '2 or 3': '◆◆-◆◆◆',
    }
    if low in glyphs:
        return glyphs[low]
    if not low:
        return ''
    # Duration: "10 minutes" -> "10 min", "1 hour" -> "1 hr",
    # "2 to 2 rounds" -> "2 rounds"; days / weeks kept as written.
    m = re.match(r'^(\d+)\s+to\s+\1\s+(\w+)$', low)
    if m:
        low = f"{m.group(1)} {m.group(2)}"
    low = re.sub(r'\bminutes?\b', 'min', low)
    low = re.sub(r'\bhours?\b', 'hr', low)
    return low


def foundry_action_cost(system):
    """Action cost from a raw Foundry feat/action `system` block, read the way a
    stat block is: leading action-glyphs in the description (variable '1 or 2')
    win, then actionType / actions.value, then a casting-time duration. Passive
    abilities return '' (no action glyph). Used for kineticist impulses, which
    are feats/actions rather than spells and so aren't in the master spell list."""
    if not isinstance(system, dict):
        return ''
    glyph = {'1': '◆', '2': '◆◆', '3': '◆◆◆'}
    d_obj = system.get('description')
    head = (d_obj.get('value', '') if isinstance(d_obj, dict) else '')[:160]
    gl = re.findall(r'action-glyph[^>]*>\s*([0-9])\s*<', head)
    if gl:
        if len(gl) >= 2 and (' or ' in head.lower() or ' to ' in head.lower()):
            a, b = glyph.get(gl[0], ''), glyph.get(gl[-1], '')
            if a and b:
                return f"{a}-{b}"
        if gl[0] in glyph:
            return glyph[gl[0]]
    at = (system.get('actionType') or {}).get('value')
    av = (system.get('actions') or {}).get('value')
    if at == 'reaction':
        return '⟳'
    if at == 'free':
        return '◇'
    if at == 'action' and isinstance(av, int) and str(av) in glyph:
        return glyph[str(av)]
    tm = (system.get('time') or {}).get('value')
    if tm:
        return _action_display(tm)
    return ''


def _load_master_spell_actions():
    """name (lowercased) -> display cost, from the master spell list. Returns
    {} if the file is absent so the module still imports (CI/headless)."""
    out = {}
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'compendium_data', 'spells', 'master_spells.json')
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        for s in data:
            name = (s.get('name') or '').strip().lower()
            if name:
                out[name] = _action_display(s.get('actions'))
    except (OSError, ValueError):
        pass
    return out


SPELL_ACTIONS = {**_LEGACY_SPELL_ACTIONS, **_load_master_spell_actions()}


def get_action_cost(name):
    return SPELL_ACTIONS.get((name or '').strip().lower(), '')
# type: "passive" (always on), "toggle" (player activates), "reaction", "action"
# toggle_effects: dict of stat modifications when active
# =============================================================================
CLASS_FEATURES = {
    "alchemist": [
        {"name": "Quick Alchemy", "level": 1, "type": "action", "actions": "◆", "desc": "You swiftly mix up a short-lived alchemical item to use at a moment's notice. You create a single alchemical item of your advanced alchemy level or lower that's in your formula book without having to spend the normal monetary cost in alchemical reagents or needing to attempt a Crafting check. This item has the infused trait, and it remains potent only until the start of your next turn."},
        {"name": "Research Field", "level": 1, "type": "passive", "desc": "Your research field adds a number of formulas to your formula book and grants additional benefits. Fields include Bomber (splash damage), Chirurgeon (healing), Mutagenist (mutagens), and Toxicologist (poisons)."},
        {"name": "Versatile Vials", "level": 1, "type": "passive", "desc": "You can use Quick Alchemy to create versatile vials — alchemical bombs that deal damage based on your level."},
    ],
    "animist": [
        {"name": "Apparition", "level": 1, "type": "passive", "desc": "You forge connections with spiritual apparitions that grant you spells and abilities. Each day, you can attune to different apparitions, changing your available powers."},
        {"name": "Channeling", "level": 1, "type": "passive", "desc": "You channel spiritual energy from your attuned apparitions to fuel your spellcasting. Your apparitions determine the spells you can prepare each day."},
        {"name": "Wandering Vessel", "level": 1, "type": "passive", "desc": "You serve as a vessel for wandering spirits. As you gain levels, you can attune to more apparitions simultaneously."},
    ],
    "barbarian": [
        {"name": "Rage", "level": 1, "type": "toggle", "actions": "◆", "desc": "You gain temporary HP equal to your level + your Con modifier. You deal +2 damage with melee Strikes and thrown weapon Strikes. You take a −1 penalty to AC. You can't use actions with the concentrate trait (except Seek and recall knowledge). Rage lasts 1 minute, until there are no enemies you can perceive, or until you fall unconscious. You can't voluntarily stop raging. After you stop raging, you can't Rage again for 1 round.",
         "toggle_effects": {"damage": 2, "ac": -1, "temp_hp": "level+con"}},
        {"name": "Instinct", "level": 1, "type": "passive", "desc": "Your instinct gives you an ability, requires you to avoid certain behaviors, grants you additional damage during your frenzy, and grants an instinct-specific ability at higher levels."},
        {"name": "Deny Advantage", "level": 3, "type": "passive", "desc": "Your foes struggle to pass your defenses. You aren't off-guard to hidden, undetected, or flanking creatures of your level or lower, or to creatures of your level or lower using surprise attack."},
        {"name": "Weapon Specialization", "level": 7, "type": "passive", "desc": "Your rage helps you hit harder. You deal 2 additional damage with weapons and unarmed attacks in which you are an expert. This damage increases to 3 if you're a master, and to 4 if you're legendary."},
        {"name": "Greater Weapon Specialization", "level": 15, "type": "passive", "desc": "Your damage from weapon specialization increases to 4 with weapons and unarmed attacks in which you're an expert, 6 if you're a master, and 8 if you're legendary."},
    ],
    "fighter": [
        {"name": "Reactive Strike", "level": 1, "type": "reaction", "actions": "⟳", "desc": "Trigger: A creature within your reach uses a manipulate action or a move action, makes a ranged attack, or leaves a square during a move action it's using. You lash out at a foe that leaves an opening. Make a melee Strike against the triggering creature. If your Strike is a critical hit and the trigger was a manipulate action, you disrupt that action."},
        {"name": "Shield Block", "level": 1, "type": "reaction", "actions": "⟳", "desc": "Trigger: While you have your shield raised, you take damage from a physical attack. You snap your shield in place to ward off a blow. Your shield prevents you from taking an amount of damage up to the shield's Hardness."},
        {"name": "Bravery", "level": 3, "type": "passive", "desc": "Having faced countless foes and the chaos of battle, you have become resolute against all manner of fear. When you roll a success on a Will save against a fear effect, you get a critical success instead. In addition, any time you gain the frightened condition, reduce its value by 1."},
        {"name": "Weapon Specialization", "level": 7, "type": "passive", "desc": "You deal 2 additional damage with weapons and unarmed attacks in which you are an expert. This damage increases to 3 if you're a master, and to 4 if you're legendary."},
    ],
    "rogue": [
        {"name": "Sneak Attack", "level": 1, "type": "passive", "desc": "When your enemy can't properly defend itself, you take advantage to deal extra damage. If you Strike a creature that has the off-guard condition, you deal an extra 1d6 precision damage (2d6 at level 5, 3d6 at level 11, 4d6 at level 17)."},
        {"name": "Surprise Attack", "level": 1, "type": "passive", "desc": "You spring into combat faster than foes can react. On the first round of combat, if you roll Deception or Stealth for initiative, creatures that haven't acted are off-guard to you."},
        {"name": "Deny Advantage", "level": 3, "type": "passive", "desc": "You aren't off-guard to hidden, undetected, or flanking creatures of your level or lower, or to creatures of your level or lower using surprise attack."},
        {"name": "Debilitating Strike", "level": 9, "type": "passive", "desc": "When you deal sneak attack damage, you can also apply one of these debilitations (lasts until end of your next turn): Debilitation: target takes −10-foot status penalty to Speed. Debilitation: target becomes enfeebled 1."},
        {"name": "Master Strike", "level": 19, "type": "passive", "desc": "Your can incapacitate a foe with a well-placed strike. When you deal sneak attack damage, the target must attempt a Fortitude save against your class DC or be paralyzed for 4 rounds."},
    ],
    "monk": [
        {"name": "Flurry of Blows", "level": 1, "type": "action", "actions": "◆", "desc": "Make two unarmed Strikes. If both hit the same creature, combine their damage for resistances and weaknesses. Apply your multiple attack penalty to the Strikes normally."},
        {"name": "Powerful Fist", "level": 1, "type": "passive", "desc": "You know how to wield your fists as deadly weapons. Your fist unarmed attacks deal 1d6 bludgeoning damage instead of 1d4. They don't have the nonlethal trait and gain the shove trait."},
        {"name": "Mystic Strikes", "level": 3, "type": "passive", "desc": "Focusing your will on your hands or weapons, you can produce effects that aren't shown by your physical might. Your unarmed attacks and monk weapons deal their damage as your choice of magical or normal damage."},
    ],
    "ranger": [
        {"name": "Hunt Prey", "level": 1, "type": "toggle", "actions": "◆", "desc": "You designate a single creature as your prey and focus your attacks against that creature. You must be able to see or hear the prey, or you must be tracking the prey during exploration. You gain a +2 circumstance bonus to Perception checks when you Seek your prey and a +2 circumstance bonus to Survival checks when you Track your prey. Your Hunter's Edge also applies against your hunted prey.",
         "toggle_effects": {"perception_vs_prey": 2, "survival_vs_prey": 2}},
        {"name": "Hunter's Edge", "level": 1, "type": "passive", "desc": "You have trained for countless hours to become a more skilled hunter and tracker, gaining an additional benefit when you Hunt Prey depending on the focus of your training: Flurry, Precision, or Outwit."},
    ],
    "champion": [
        {"name": "Champion's Reaction", "level": 1, "type": "reaction", "actions": "⟳", "desc": "Your cause grants you a special reaction. Paladin: Retributive Strike — make a Strike against the attacker. Redeemer: Glimpse of Redemption — target takes enfeebled 2 unless it stops attacking ally. Liberator: Liberating Step — ally can Step as a free action."},
        {"name": "Lay on Hands", "level": 1, "type": "action", "actions": "◆", "desc": "Your hands become infused with positive energy, healing a living target or damaging an undead target. You restore 6 HP per spell rank to a willing living target, or deal 1d6 damage per spell rank to undead (basic Fortitude save)."},
        {"name": "Shield Block", "level": 1, "type": "reaction", "actions": "⟳", "desc": "You place your shield to block a blow. Your shield prevents you from taking an amount of damage up to the shield's Hardness. You and the shield each take any remaining damage."},
    ],
    "swashbuckler": [
        {"name": "Panache", "level": 1, "type": "toggle", "actions": "—", "desc": "You care as much about the way you accomplish something as whether you actually accomplish it in the first place. When your flair is at its peak, you have panache. You gain panache when you successfully Tumble Through, Feint, Demoralize, or perform other style-specific actions. While you have panache, you gain a +5-foot status bonus to Speed and can use finishers. Panache ends when you use a finisher or at the end of an encounter.",
         "toggle_effects": {"speed": 5}},
        {"name": "Precise Strike", "level": 1, "type": "passive", "desc": "You deal +2 precision damage on Strikes with agile/finesse melee weapons or unarmed attacks when you have panache (increases to +4 at level 5, +6 at level 11)."},
        {"name": "Confident Finisher", "level": 1, "type": "action", "actions": "◆", "desc": "You make a Strike with a weapon or unarmed attack that would apply your precise strike damage, with the following failure effect: you still deal your precise strike damage to the target."},
    ],
    "cleric": [
        {"name": "Divine Font", "level": 1, "type": "passive", "desc": "Through your deity's body, you gain additional spells that channel the essence of good or evil. You gain additional spell slots each day at your highest rank of cleric spell slots. You prepare either heal or harm in these slots depending on your deity."},
        {"name": "Raise Symbol", "level": 1, "type": "action", "actions": "◆", "desc": "You present your religious symbol. You gain a +2 circumstance bonus to saving throws until the start of your next turn. This is a fortune effect."},
    ],
    "wizard": [
        {"name": "Arcane Bond", "level": 1, "type": "action", "actions": "◈", "desc": "You place some of your magical power in a bonded item. Each day, you can use Drain Bonded Item once to cast a spell you've prepared today without expending the spell slot. This is a free action."},
        {"name": "Arcane School", "level": 1, "type": "passive", "desc": "You specialize in a particular school of magic, gaining a curriculum of additional spells and a school spell (focus spell)."},
    ],
    "druid": [
        {"name": "Wild Empathy", "level": 1, "type": "passive", "desc": "You have a connection to the creatures of the natural world that allows you to communicate with them on a rudimentary level. You can use Diplomacy to Make an Impression on animals and to make very simple Requests of them."},
        {"name": "Shield Block", "level": 1, "type": "reaction", "actions": "⟳", "desc": "Your shield prevents you from taking an amount of damage up to the shield's Hardness."},
        {"name": "Druidic Order", "level": 1, "type": "passive", "desc": "Your training taught you in a specific druidic order (Animal, Leaf, Storm, or Untamed), granting a focus spell, additional trained skill, and specific anathema."},
    ],
    "bard": [
        {"name": "Inspire Courage", "level": 1, "type": "action", "actions": "◆", "desc": "You inspire your allies with words or tunes of encouragement. You and all allies in the area gain a +1 status bonus to attack rolls, damage rolls, and saves against fear effects. Cantrip, 60-foot emanation."},
        {"name": "Inspire Defense", "level": 1, "type": "action", "actions": "◆", "desc": "You inspire your allies to protect themselves. You and all allies in the area gain a +1 status bonus to AC and saving throws, as well as resistance to physical damage equal to half the spell's rank. 60-foot emanation."},
        {"name": "Muse", "level": 1, "type": "passive", "desc": "You find inspiration in the world around you. Your muse (Enigma, Maestro, Polymath, or Warrior) grants additional spells or feats."},
    ],
    "sorcerer": [
        {"name": "Blood Magic", "level": 1, "type": "passive", "desc": "Whenever you cast a bloodline spell using Focus Points or a granted spell from your bloodline, you gain a blood magic effect. The effect depends on your bloodline."},
        {"name": "Bloodline", "level": 1, "type": "passive", "desc": "You carry the magical blood of a powerful supernatural lineage. Your bloodline determines your tradition, granted spells, initial bloodline spell, and blood magic effect."},
    ],
    "kineticist": [
        {"name": "Channel Elements", "level": 1, "type": "toggle", "actions": "◆", "desc": "You channel your kinetic gate to activate your elemental powers. While channeling, you are surrounded by a kinetic aura that enables your impulses. This lasts until you use Channel Elements again or until the encounter ends.",
         "toggle_effects": {"aura": True}},
        {"name": "Elemental Blast", "level": 1, "type": "action", "actions": "◆◆", "desc": "You gather elemental matter into a projectile or surround your fist with elemental energy to strike a foe. Make a ranged or melee attack using your class DC − 10 as your attack modifier. You deal 1d8 damage of your element's type."},
        {"name": "Base Kinesis", "level": 1, "type": "action", "actions": "◆◆", "desc": "You can perform simple telekinesis or manipulation of your element at range. Generate, move, or shape a small amount of your element within 30 feet."},
    ],
    "oracle": [
        {"name": "Cursebound", "level": 1, "type": "passive", "desc": "Drawing on the power of your mystery causes a defensive curse to progress. Your mystery grants unique curse effects at mild, moderate, and major stages."},
        {"name": "Mystery", "level": 1, "type": "passive", "desc": "An oracle draws upon divine power through a connection they have to a mystery. Your mystery determines your revelation spells, related domains, and curse effects."},
    ],
    "witch": [
        {"name": "Familiar", "level": 1, "type": "passive", "desc": "Your patron has gifted you a magical creature: a familiar. This familiar grants you a number of abilities based on your level and the familiar abilities you select each day."},
        {"name": "Patron", "level": 1, "type": "passive", "desc": "Your patron determines your tradition (arcane, divine, occult, or primal), grants a patron spell (focus spell), and adds spells to your spell list."},
    ],
    "psychic": [
        {"name": "Psyche", "level": 1, "type": "passive", "desc": "Your mind is incredibly powerful, but in exchange for your heightened abilities you are more vulnerable during periods of rest. During encounters, you can Unleash your Psyche as a free action."},
        {"name": "Unleash Psyche", "level": 1, "type": "toggle", "actions": "◈", "desc": "You call on the full strength of your psyche. You gain a +1 status bonus to spell attack rolls and a +1 status bonus to the DCs of your spells. Lasts 2 rounds, then you become stupefied 1 for 2 rounds.",
         "toggle_effects": {"spell_attack": 1, "spell_dc": 1}},
    ],
    "investigator": [
        {"name": "Devise a Stratagem", "level": 1, "type": "action", "actions": "◆", "desc": "You assess a foe's weaknesses before attacking. Choose a creature you can see. Roll a d20 and record the result. You can use that die result instead of rolling for your next Strike against that creature this turn. If the Strike is made with an agile or finesse weapon, add your Intelligence modifier to damage."},
        {"name": "Strategic Strike", "level": 1, "type": "passive", "desc": "When you Strike with a weapon with which you are trained and use Devise a Stratagem, you deal 1d6 additional precision damage (increases at 5th, 11th, and 17th level)."},
    ],
    "thaumaturge": [
        {"name": "Exploit Vulnerability", "level": 1, "type": "action", "actions": "◆", "desc": "You know that every extant thing has a matter of extant weakness. Select a creature you can see. You recall the ways in which the creature is vulnerable. You gain a +2 status bonus to your next Strike's damage against the creature (increasing at higher levels). This applies your weakness knowledge."},
        {"name": "Implement", "level": 1, "type": "passive", "desc": "Your chosen implement (Amulet, Bell, Chalice, Tome, Wand, or Weapon) grants you a unique ability and reaction."},
    ],
    "inventor": [
        {"name": "Overdrive", "level": 1, "type": "toggle", "actions": "◆", "desc": "Temporarily overcharge your innovation to deal extra damage. Make a Crafting check (DC = standard for your level). On success, you deal additional fire damage with Strikes equal to your Intelligence modifier. Lasts 1 minute.",
         "toggle_effects": {"damage": "int"}},
        {"name": "Innovation", "level": 1, "type": "passive", "desc": "You carry an innovation — an invention of your own design that you use to enhance your capabilities (Armor, Construct, or Weapon Innovation)."},
    ],
    "gunslinger": [
        {"name": "Singular Expertise", "level": 1, "type": "passive", "desc": "You have particular expertise with guns and crossbows that grants you greater proficiency with them."},
        {"name": "Slinger's Reload", "level": 1, "type": "action", "actions": "◆", "desc": "Your way grants you a unique action that combines a reload with another quick action. This varies based on your chosen way (Drifter, Pistolero, Sniper, Vanguard)."},
    ],
    "swashbuckler": [
        {"name": "Panache", "level": 1, "type": "toggle", "actions": "—", "desc": "When your flair is at its peak, you have panache, gaining +5-foot status bonus to Speed and access to finishers.",
         "toggle_effects": {"speed": 5}},
        {"name": "Precise Strike", "level": 1, "type": "passive", "desc": "You deal +2 precision damage (increases to +4 at 5, +6 at 11) on agile/finesse melee Strikes or unarmed attacks while you have panache."},
        {"name": "Confident Finisher", "level": 1, "type": "action", "actions": "◆", "desc": "You make a finishing Strike. On a failure, you still deal precise strike damage."},
    ],
    "magus": [
        {"name": "Spellstrike", "level": 1, "type": "action", "actions": "◆◆", "desc": "You channel a spell into your weapon or body, then Strike. Make a melee Strike. On hit, the Strike also deals the effects of a spell you cast as part of this activity. Spellstrike recharges after you Recharge Spellstrike."},
        {"name": "Arcane Cascade", "level": 1, "type": "toggle", "actions": "◆", "desc": "You divert a spell's residual magic to flow into your strikes, entering an arcane cascade stance. While in this stance, your melee Strikes deal 1 extra damage (increases at higher levels). This extra damage is of the type determined by your hybrid study.",
         "toggle_effects": {"damage": 1}},
    ],
    "summoner": [
        {"name": "Eidolon", "level": 1, "type": "passive", "desc": "You have a connection to a powerful and otherworldly entity called an eidolon that manifests as a companion creature. Your eidolon shares your actions, HP, and some of your statistics."},
        {"name": "Act Together", "level": 1, "type": "action", "actions": "◆-◆◆◆", "desc": "You and your eidolon act as one. Either you or your eidolon takes an action or activity using the appropriate number of actions, and the other takes a single action."},
    ],
    "commander": [
        {"name": "Commander's Banner", "level": 1, "type": "passive", "desc": "You carry a banner that inspires your allies with a tactical benefit based on your chosen drilled response."},
        {"name": "Drilled Response", "level": 1, "type": "passive", "desc": "Your squad training grants allies a specific coordinated reaction based on your chosen response type."},
    ],
    "guardian": [
        {"name": "Intercept Strike", "level": 1, "type": "reaction", "actions": "⟳", "desc": "You throw yourself between an ally and danger. When an ally within your reach is hit by an attack, you can take the damage instead."},
        {"name": "Taunt", "level": 1, "type": "action", "actions": "◆", "desc": "You demand a foe's attention, compelling them to focus their attacks on you rather than your allies."},
    ],
    "exemplar": [
        {"name": "Ikon", "level": 1, "type": "passive", "desc": "You carry items imbued with divine spark called ikons. Each ikon has a passive immanence and an active transcendence ability."},
        {"name": "Spark Transcendence", "level": 1, "type": "action", "actions": "◈", "desc": "You shift your divine spark from one ikon to another, activating the transcendence of the ikon you're shifting to."},
    ],
}

# Keep backward compat — PASSIVE_FEATURES points to CLASS_FEATURES
PASSIVE_FEATURES = CLASS_FEATURES

# =============================================================================
# CLASS-SPECIFIC LEVEL-UP FEATURES + CHOICES
# =============================================================================
# This catalogs the per-level class features that the levelup wizard
# should surface — both passive-info ("you gain X at this level") and
# choices ("pick one of A/B/C"). Distinct from CLASS_PROGRESSION (which
# only carries cumulative proficiency bumps) and CLASS_FEATURES (which
# is mostly L1 features rendered on the sheet).
#
# Each entry: { name, type, desc, choices?, applies_passive? }
#   type: "info" | "choice" | "skill_grant"
#   choices: [{name, desc}]  - if type is "choice", the player must pick one
#   applies_passive: True     - render as info-only, no pick required
#
# When wired to the wizard, choice-typed entries render as required
# choice cards. Info-typed entries render as a small "you gained" pill.
#
# Sources: Player Core 1, Player Core 2, GM Core, Lost Omens books.
# Keep entries terse — players read these inline at level-up time.
# =============================================================================
CLASS_LEVEL_FEATURES = {
    # Each entry may include an optional `subclass` list — the entry only
    # surfaces for PCs whose subclass matches one of those names (case-
    # insensitive). Entries without a `subclass` key apply to every
    # subclass. PCs with no subclass set yet see ALL variants so they can
    # preview the choice before committing.
    "champion": {
        3: [
            {"name": "Divine Ally", "type": "choice",
             "desc": "Pick the divine bond that empowers you.",
             "choices": [
                {"name": "Blade Ally", "desc": "Bond with a weapon. While wielded, it benefits from one rune of your choice (disrupting, ghost touch, returning, or shifting), and you can switch the rune as a free action once per day."},
                {"name": "Steed Ally", "desc": "Gain a young animal companion that serves as your mount (horse, pony, riding dog, etc.)."},
                {"name": "Shield Ally", "desc": "Your shield gains +2 Hardness, +5 HP, and +5 BT. Stacks with material upgrades."},
             ]},
            # Cause-specific reaction reminders at L3 — players forget which
            # reaction their cause uses; surface it here.
            {"name": "Champion's Reaction (Justice)", "type": "info", "subclass": ["Justice", "Paladin"],
             "desc": "Retributive Strike: When an ally within 15 ft is hit, you Step toward them and Strike the attacker (granting resistance to the ally equal to 2 + half your level)."},
            {"name": "Champion's Reaction (Mercy)", "type": "info", "subclass": ["Mercy", "Redeemer"],
             "desc": "Glimpse of Redemption: When an ally within 15 ft is hit, the attacker chooses — take resistance and damage the ally less, or take retributive damage."},
            {"name": "Champion's Reaction (Grandeur)", "type": "info", "subclass": ["Grandeur", "Liberator"],
             "desc": "Liberating Step: When an ally within 15 ft is grabbed/restrained/etc., they automatically escape one effect and Step."},
            {"name": "Champion's Reaction (Desecrator/Tyrant)", "type": "info", "subclass": ["Desecrator", "Tyrant", "Antipaladin"],
             "desc": "Destructive Vengeance: When an enemy within 15 ft damages your ally, deal force/negative damage to the attacker."},
        ],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts (any ability at +4 or lower goes up by 2; +5+ goes up by 1)."},
            {"name": "Weapon Expertise", "type": "info", "desc": "Expert in simple, martial, and unarmed weapons."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase — Trained → Expert, or Expert → Master if Lvl 7+."}],
        7: [{"name": "Armor Specialization Effects", "type": "info", "desc": "Your armor's specialization effect activates (heavy gives DR, medium adds resistance, etc.)."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage on expert weapons, +3 master, +4 legendary."}],
        9: [{"name": "Juggernaut", "type": "info", "desc": "Fortitude reaches Master. Successful Fort saves become critical successes."},
            {"name": "Champion Expertise", "type": "info", "desc": "Class DC and (if applicable) spellcasting reach Expert."}],
        11: [{"name": "Divine Will", "type": "info", "desc": "Will save reaches Master. Successful Will saves become critical successes."},
             {"name": "Exalt (Justice)", "type": "info", "subclass": ["Justice", "Paladin"], "desc": "When you trigger Retributive Strike, allies in 15 ft can spend a reaction to Strike the attacker too."},
             {"name": "Exalt (Mercy)", "type": "info", "subclass": ["Mercy", "Redeemer"], "desc": "Glimpse of Redemption now grants resistance to ALL allies adjacent to the target, not just the triggering ally."},
             {"name": "Exalt (Grandeur)", "type": "info", "subclass": ["Grandeur", "Liberator"], "desc": "Liberating Step grants ALL allies in 15 ft a Step in addition to the triggering ally."},
             {"name": "Exalt (Tyrant)", "type": "info", "subclass": ["Desecrator", "Tyrant", "Antipaladin"], "desc": "Destructive Vengeance damages ALL adjacent enemies, not just the attacker."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple, martial, and unarmed weapons."},
             {"name": "Armor Expertise", "type": "info", "desc": "Expert in light, medium, and heavy armor."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles to +4 expert / +6 master / +8 legendary."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Champion Mastery", "type": "info", "desc": "Class DC reaches Master."},
             {"name": "Legendary Armor", "type": "info", "desc": "Legendary in light, medium, and heavy armor."}],
        19: [{"name": "Hero's Defiance", "type": "info", "desc": "Once per day, when you would be reduced to 0 HP, drop to 1 HP instead. Free action triggered by the killing blow."},
             {"name": "Three Cheers (Cause Capstone)", "type": "info", "desc": "Your cause grants its capstone benefit (varies by Justice/Mercy/Grandeur/Desecration)."}],
    },

    "cleric": {
        3: [{"name": "Second Doctrine (Cloistered)", "type": "info", "subclass": ["Cloistered Cleric"],
             "desc": "Reflex Expertise: Reflex save advances to Expert."},
            {"name": "Second Doctrine (Warpriest)", "type": "info", "subclass": ["Warpriest"],
             "desc": "Trained in martial weapons + simple-weapon expertise. Your domain spell DC matches your spellcasting DC."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase."}],
        7: [{"name": "Third Doctrine (Cloistered)", "type": "info", "subclass": ["Cloistered Cleric"],
             "desc": "Cleric Expertise: Expert spellcasting (DC + attack)."},
            {"name": "Third Doctrine (Warpriest)", "type": "info", "subclass": ["Warpriest"],
             "desc": "Expert in simple/martial weapons + unarmed strikes. Weapon Specialization unlocks."}],
        9: [{"name": "Resolve", "type": "info",
             "desc": "Will save reaches Master. Successful Will saves become critical successes."}],
        11: [{"name": "Fourth Doctrine (Cloistered)", "type": "info", "subclass": ["Cloistered Cleric"],
              "desc": "Lightning Reflexes: Reflex save reaches Expert."},
             {"name": "Fourth Doctrine (Warpriest)", "type": "info", "subclass": ["Warpriest"],
              "desc": "Lightning Reflexes + Expert spellcasting (catches up to Cloistered)."}],
        13: [{"name": "Divine Defense", "type": "info", "desc": "Expert in unarmored defense."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."}],
        15: [{"name": "Fifth Doctrine", "type": "info",
              "desc": "Master spellcaster: Spell Attack + Spell DC reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Final Doctrine (Cloistered)", "type": "info", "subclass": ["Cloistered Cleric"],
              "desc": "Legendary Spellcaster + Master Will. Cloistered hits the spell ceiling."},
             {"name": "Final Doctrine (Warpriest)", "type": "info", "subclass": ["Warpriest"],
              "desc": "Master Will. Warpriest's spellcasting now matches a Cloistered Cleric two levels back."}],
        19: [{"name": "Miraculous Spell", "type": "info",
              "desc": "Once per day, expend a slot two ranks below your max to cast a spell at your max rank."},
             {"name": "Making Miracles", "type": "info", "desc": "Capstone — once per day, cast Miracle without paying components."}],
    },

    "druid": {
        3: [{"name": "General Feat", "type": "info", "desc": "Pick a general feat (granted at every odd level from L3)."},
            {"name": "Skill Increase", "type": "info", "desc": "Trained → Expert in one skill."},
            # Order-specific L3 features (the user-reported bug — Storm
            # Druid never saw Tempest Surge improvement before).
            {"name": "Tempest Surge Improvement", "type": "info", "subclass": ["Storm"],
             "desc": "Your Tempest Surge focus spell heightens automatically as you gain levels — at L3 it deals 2d12 damage and applies clumsy 1 on a fail."},
            {"name": "Wild Shape (extra form)", "type": "info", "subclass": ["Wild"],
             "desc": "Your Wild Shape pool grants you a new battle form. Pick from Pest Form, Animal Form, or Plant Form variants you qualify for."},
            {"name": "Animal Companion (Mature)", "type": "info", "subclass": ["Animal"],
             "desc": "Your animal companion advances to Mature: Master in unarmored defense, +1 to attack/damage, +5 HP, gains Support benefit."},
            {"name": "Leaf Order's Cantrip", "type": "info", "subclass": ["Leaf"],
             "desc": "Your Goodberry focus spell heightens — each berry now restores 1d8+4 HP, and you can prepare an extra rank-1 healing spell."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Wild Empathy upgrade", "type": "info", "desc": "Your communication with animals improves; you can attempt Diplomacy on animals."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC and spell attack rolls reach Expert."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase."}],
        9: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
            {"name": "Storm Retribution", "type": "info", "subclass": ["Storm"],
             "desc": "Once per round when an enemy in your aura attacks you, deal electricity damage equal to your Wis modifier as a free action."},
            {"name": "Wild Shape advance", "type": "info", "subclass": ["Wild"],
             "desc": "Your Wild Shape pool gains 1 additional use per day; you can shape-shift as 2 actions instead of 3 for forms you've used today."},
            {"name": "Animal Companion (Nimble or Savage)", "type": "info", "subclass": ["Animal"],
             "desc": "Pick: Nimble (Expert Reflex, +10 ft Speed, evasion-like dodge) or Savage (Expert Fort, +1 die step on Strikes, deathblow)."},
            {"name": "Leaf Order — Verdant Metamorphosis", "type": "info", "subclass": ["Leaf"],
             "desc": "You can transform into a plant for 8 hours — gaining tremorsense and resistance to poison and bleed."}],
        11: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
             {"name": "Storm Spell Mastery", "type": "info", "subclass": ["Storm"],
              "desc": "You always have an extra prepared rank-5 spell from the storm domain (Lightning Bolt, Wind Walk, etc.)."},
             {"name": "Untamed Form upgrade", "type": "info", "subclass": ["Wild"],
              "desc": "Wild Shape now grants a Hydraulic Push or 30 ft fly speed when used on certain forms."},
             {"name": "Animal Companion (Specialized)", "type": "info", "subclass": ["Animal"],
              "desc": "Pick a specialization: ambusher, bully, daredevil, racer, tracker, or wrecker — adds a unique bonus to your companion's role."},
             {"name": "Leaf Order — Cantrip Expansion", "type": "info", "subclass": ["Leaf"],
              "desc": "Add an additional cantrip from the primal list and your Goodberry yields one extra berry per day."}],
        13: [{"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."},
             {"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC and spell attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Storm Lord", "type": "info", "subclass": ["Storm"],
              "desc": "You can summon a permanent storm aura — enemies in 30 ft take electricity damage at start of their turn equal to your level/2."},
             {"name": "Wild Shape Mastery", "type": "info", "subclass": ["Wild"],
              "desc": "Wild Shape becomes a 1-action activity, and you gain access to a permanent Apex animal form (Megafauna, Dragon, etc.)."},
             {"name": "Animal Companion (Apex)", "type": "info", "subclass": ["Animal"],
              "desc": "Your companion ages into Apex: +30 HP, Master Athletics, gains a unique apex-tier ability."},
             {"name": "Leaf Order — Verdant Lord", "type": "info", "subclass": ["Leaf"],
              "desc": "Your healing magic doubles its die size when targeting allies in natural terrain (forests, jungles, etc.)."}],
        19: [{"name": "Legendary Primal Spellcaster", "type": "info", "desc": "Spell DC and spell attack reach Legendary."},
             {"name": "Hierophant", "type": "info", "desc": "Capstone — auto-success on first failed Will save per day."}],
    },

    "kineticist": {
        3: [{"name": "Element Junction", "type": "choice",
             "desc": "Pick a Junction for one of your gates. (Single Gate kineticists pick from their one element; Dual Gate picks from either.)",
             "choices": [
                {"name": "Earth Junction", "desc": "Increase your max HP by your level. While Channeling Earth, this bonus is doubled."},
                {"name": "Fire Junction", "desc": "Your impulses that deal fire damage gain +1 persistent fire damage that increases with level."},
                {"name": "Air Junction", "desc": "+10 ft Speed while Channeling Air; ignore Difficult Terrain caused by air, weather, or non-magical hazards."},
                {"name": "Water Junction", "desc": "Cold resistance equal to half your level; impulses that deal cold gain +1 persistent cold."},
                {"name": "Wood Junction", "desc": "When you Channel Wood you gain temporary HP equal to twice your level (lasts 1 minute)."},
                {"name": "Metal Junction", "desc": "Strikes made via impulses gain the Versatile (S/P) trait."},
             ]},
            {"name": "General Feat", "type": "info", "desc": "Pick a general feat (granted at L3, 7, 11, 15, 19)."},
            {"name": "Skill Increase", "type": "info", "desc": "Trained → Expert in one skill."},
            # Gate-specific subclass deltas at L3
            {"name": "Single Gate Focus", "type": "info", "subclass": ["Single Gate"],
             "desc": "You gain a bonus impulse feat from your gate's element list — a free taste of a higher-level impulse."},
            {"name": "Dual Gate Synergy", "type": "info", "subclass": ["Dual Gate"],
             "desc": "When you Channel both elements in the same turn, you gain a +1 status bonus to the next impulse you cast that turn."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Specialty Impulse", "type": "info",
             "desc": "Learn a specialty-slot impulse — an impulse you have a permanent access slot for, even if it's not normally on your gate's list."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase."}],
        7: [{"name": "Aura Junction", "type": "info", "desc": "Your gate's Junction now extends as an aura: 10 ft for solo gates, 5 ft for dual gates. Allies in your aura get a small benefit tied to the element."},
            {"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."}],
        9: [{"name": "Gate's Threshold", "type": "info", "desc": "When you Overflow with an impulse, the threshold ability of your gate triggers (extra damage, additional target, terrain alteration depending on element)."},
            {"name": "Resolve", "type": "info", "desc": "Will save reaches Expert."}],
        11: [{"name": "Impulse Mastery", "type": "info", "desc": "Class DC reaches Master. Impulse damage dice increase by one step."},
             {"name": "Greater Junction", "type": "info", "subclass": ["Single Gate"],
              "desc": "Your single-gate Junction strengthens — Earth gives DR, Fire gives extra persistent damage on a critical, etc."},
             {"name": "Greater Synergy", "type": "info", "subclass": ["Dual Gate"],
              "desc": "Channeling both elements simultaneously now stacks Junction benefits AND grants a free reposition."}],
        13: [{"name": "Skilled Kineticist", "type": "info", "desc": "Strikes made via impulses gain Master proficiency in their chosen weapon group."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage on impulse Strikes (expert), +3 master, +4 legendary."}],
        15: [{"name": "Greater Specialty Impulse", "type": "info", "desc": "A second specialty-slot impulse — pick a second cross-element impulse to keep permanently."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
             {"name": "Evasion", "type": "info", "desc": "Successful Reflex saves become critical successes."}],
        17: [{"name": "Legendary Kinetic Mastery", "type": "info", "desc": "Class DC reaches Legendary."},
             {"name": "Apex Junction", "type": "info", "desc": "Your gate Junction expands to its strongest tier — Earth gives DR/—, Fire gives persistent fire to ALL impulses, etc."}],
        19: [{"name": "Apex Gate", "type": "info", "desc": "You can Channel two gates simultaneously without dual-gate restriction, paying focus / overflow costs from either pool."},
             {"name": "Heart of the Element", "type": "info", "desc": "Capstone — once per day, channel an Apex impulse that doesn't cost overflow."}],
    },

    # ── Other classes — full L3-L19 progression with subclass tagging ──
    "alchemist": {
        3: [{"name": "Field Discovery (Bomber)", "type": "info", "subclass": ["Bomber"],
             "desc": "When you craft a bomb with Quick Alchemy, you can use a free hand splash effect — splash damage doubles for the round."},
            {"name": "Field Discovery (Chirurgeon)", "type": "info", "subclass": ["Chirurgeon"],
             "desc": "You can use Healing Bombs that deal positive damage to undead and heal allies caught in splash."},
            {"name": "Field Discovery (Mutagenist)", "type": "info", "subclass": ["Mutagenist"],
             "desc": "Your mutagens last 10 minutes (was 1 minute) and you suffer reduced drawback durations."},
            {"name": "Field Discovery (Toxicologist)", "type": "info", "subclass": ["Toxicologist"],
             "desc": "Quick Alchemy can produce 2 doses of a poison instead of 1, applied to a single weapon."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Weapon Expertise", "type": "info", "desc": "Expert in alchemical bombs + simple weapons."}],
        7: [{"name": "Iron Will", "type": "info", "desc": "Will save reaches Expert."},
            {"name": "Perpetual Infusions", "type": "info", "desc": "Field-specific: 2 chosen items at lower-level can be made infinitely many times via Quick Alchemy."}],
        9: [{"name": "Alchemical Alacrity", "type": "info", "desc": "Quick Alchemy creates 3 items in 1 action instead of 1."},
            {"name": "Juggernaut", "type": "info", "desc": "Fort save reaches Expert."}],
        11: [{"name": "Juggernaut Mastery", "type": "info", "desc": "Fort save reaches Master."},
             {"name": "Perpetual Potency", "type": "info", "desc": "Field-specific lower-tier perpetual items advance to higher-tier."}],
        13: [{"name": "Greater Field Discovery", "type": "info", "desc": "Field's signature ability gets its mid-tier upgrade."},
             {"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Alchemical Mastery", "type": "info", "desc": "Class DC reaches Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Perpetual Perfection", "type": "info", "desc": "All formerly-perpetual items advance another tier — the alchemist becomes a one-person item factory."}],
        19: [{"name": "Mega-Bomb / Field Capstone", "type": "info", "desc": "Capstone — Bomber gets Mega Bomb, Chirurgeon Mass Heal, Mutagenist Greater Mutagen, Toxicologist Lethal Toxin."}],
    },

    "barbarian": {
        3: [{"name": "Deny Advantage", "type": "info",
             "desc": "Higher-level enemies cannot make you off-guard with skill checks (Feint, Hide, etc.)."},
            {"name": "General Feat", "type": "info", "desc": "Pick a general feat."},
            {"name": "Skill Increase", "type": "info", "desc": "Trained → Expert in one skill."},
            # Instinct-specific L3 feature scaling
            {"name": "Animal Instinct (form upgrade)", "type": "info", "subclass": ["Animal"],
             "desc": "Your Animal Form scales — gain new natural-weapon damage dice and an extra elemental rider."},
            {"name": "Dragon Instinct (breath improvement)", "type": "info", "subclass": ["Dragon"],
             "desc": "Your dragon-breath rage damage increases; the breath weapon's recharge timer is reduced."},
            {"name": "Fury Instinct (free feat)", "type": "info", "subclass": ["Fury"],
             "desc": "Pick any one barbarian or general feat of L3 or lower (you have no instinct ability to lose)."},
            {"name": "Giant Instinct (weapon scale)", "type": "info", "subclass": ["Giant"],
             "desc": "You can wield even larger weapons; titan mauler bonus damage scales."},
            {"name": "Spirit Instinct (haunting damage)", "type": "info", "subclass": ["Spirit"],
             "desc": "Your spirit damage now applies to all rage Strikes, not just unarmed."},
            {"name": "Superstition Instinct (anti-magic shrug)", "type": "info", "subclass": ["Superstition"],
             "desc": "+1 status to saves vs magic when raging; you can re-roll a failed save vs a spell."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Brutality", "type": "info", "desc": "Expert in martial + simple weapons. Crit specialization on rage Strikes."}],
        7: [{"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons (rage stacks add more)."},
            {"name": "Furious Footfalls", "type": "info", "desc": "+5 ft Speed."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Raging Resistance (Animal)", "type": "info", "subclass": ["Animal"], "desc": "Resistance to piercing or slashing while raging (your choice)."},
            {"name": "Raging Resistance (Dragon)", "type": "info", "subclass": ["Dragon"], "desc": "Resistance to your dragon's element while raging."},
            {"name": "Raging Resistance (Fury)", "type": "info", "subclass": ["Fury"], "desc": "Resistance to physical damage of your choice while raging."},
            {"name": "Raging Resistance (Giant)", "type": "info", "subclass": ["Giant"], "desc": "Resistance to bludgeoning + your choice of cold/electricity/fire while raging."},
            {"name": "Raging Resistance (Spirit)", "type": "info", "subclass": ["Spirit"], "desc": "Resistance to negative + void damage while raging."},
            {"name": "Raging Resistance (Superstition)", "type": "info", "subclass": ["Superstition"], "desc": "Resistance to occult + arcane damage while raging."}],
        11: [{"name": "Mighty Rage", "type": "info", "desc": "When you Rage, you can spend a free action to use an instinct-specific ability."},
             {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L11 odd-level feat + skill increase."}],
        13: [{"name": "Greater Juggernaut", "type": "info", "desc": "Fort save reaches Master + crits become legendary criticals."},
             {"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Weapon Mastery", "type": "info", "desc": "Master in martial weapons."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Indomitable Will", "type": "info", "desc": "Will save reaches Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Heightened Senses", "type": "info", "desc": "Perception reaches Master."},
             {"name": "Quick Rage", "type": "info", "desc": "Rage as a free action once per round (was 1 action)."}],
        19: [{"name": "Armor of Fury", "type": "info", "desc": "Master in armor proficiencies."},
             {"name": "Devastator", "type": "info", "desc": "Capstone — your Strikes ignore 10 points of resistance to physical damage."}],
    },

    "bard": {
        3: [{"name": "Signature Spells", "type": "info",
             "desc": "Pick 1 spell per rank you can cast as a Signature Spell — auto-heightens to any rank slot you spend."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."},
            {"name": "Muse Feature (Enigma)", "type": "info", "subclass": ["Enigma"], "desc": "Your Bardic Lore skill advances + you gain True Strike as a focus spell option."},
            {"name": "Muse Feature (Maestro)", "type": "info", "subclass": ["Maestro"], "desc": "You learn Soothe and a healing-themed composition cantrip."},
            {"name": "Muse Feature (Polymath)", "type": "info", "subclass": ["Polymath"], "desc": "You can swap one spell from your repertoire each day, gaining short-term Versatile Performance."},
            {"name": "Muse Feature (Warrior)", "type": "info", "subclass": ["Warrior"], "desc": "Trained in martial weapons + your Inspire Courage scales earlier."}],
        5: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Inspire Heroics", "type": "info", "desc": "(Some muses): Inspire Courage / Inspire Defense scales to +2."}],
        9: [{"name": "Great Fortitude", "type": "info", "desc": "Fort save reaches Expert."},
            {"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."}],
        11: [{"name": "Bard Weapon Expertise", "type": "info", "desc": "Expert in simple + longsword + rapier + sap + shortbow + shortsword + whip."},
             {"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."}],
        13: [{"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."},
             {"name": "Greater Resolve", "type": "info", "desc": "Will save reaches Master."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Greater Performance", "type": "info", "desc": "All compositions and bardic feats scale once more — Inspire Courage +3, Lingering Composition +2 rounds."}],
        19: [{"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."},
             {"name": "Magnum Opus", "type": "info", "desc": "Capstone — gain 2 unique 10th-rank composition spells from your muse."}],
    },

    "fighter": {
        3: [{"name": "Bravery", "type": "info",
             "desc": "Will save advances. Frightened value is reduced by 1 (min 0). Successes vs fear become critical successes."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."},
            {"name": "Fighter Style Bonus (Two-Handed)", "type": "info", "subclass": ["Two-Handed", "Two Handed"],
             "desc": "Your two-handed Strike crit adds an additional die of damage at L3+ — devastating critical."},
            {"name": "Fighter Style Bonus (Sword & Board)", "type": "info", "subclass": ["Sword & Board", "Sword and Board"],
             "desc": "While you have a shield raised, gain a +1 circumstance bonus to Strikes (already-raised shield bonus)."},
            {"name": "Fighter Style Bonus (Dual-Wielding)", "type": "info", "subclass": ["Dual-Wielding", "Dual Wielding"],
             "desc": "Your second attack with a different weapon takes a smaller MAP — agile weapons in your offhand reduce penalties further."},
            {"name": "Fighter Style Bonus (Archery)", "type": "info", "subclass": ["Archery"],
             "desc": "Your bow Strike critical applies its bow's critical specialization regardless of weapon proficiency."}],
        5: [{"name": "Fighter Weapon Mastery", "type": "info",
             "desc": "Pick a weapon group — Master in those weapons. Master in simple, advanced, and martial of one chosen group."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Battlefield Surveyor", "type": "info", "desc": "Perception reaches Expert; +2 status to Perception for initiative."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Combat Flexibility", "type": "info",
             "desc": "Each day, pick one fighter feat of L8 or lower as a temporary feat for the day."},
            {"name": "Juggernaut", "type": "info", "desc": "Fort save reaches Master."}],
        11: [{"name": "Armor Expertise", "type": "info", "desc": "Expert in unarmored, light, medium, heavy armor."},
             {"name": "Fighter Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        13: [{"name": "Weapon Legend", "type": "info",
              "desc": "Master in advanced weapons of your group; Legendary in simple/martial of group; Master in unarmed."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Master."}],
        15: [{"name": "Evasion", "type": "info", "desc": "Reflex save reaches Master + auto-crit on success."},
             {"name": "Improved Flexibility", "type": "info", "desc": "Combat Flexibility now picks two feats — one L14 or lower, one L8 or lower."},
             {"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Armor Mastery", "type": "info", "desc": "Master in unarmored, light, medium, heavy armor."},
             {"name": "Greater Resolve", "type": "info", "desc": "Will save reaches Master."}],
        19: [{"name": "Versatile Legend", "type": "info", "desc": "Class DC reaches Legendary; weapon group reaches Legendary in advanced too."},
             {"name": "Capstone — Final Strike", "type": "info", "desc": "Once per day, your weapon Strike automatically critically hits."}],
    },

    "rogue": {
        3: [{"name": "Surprise Attack improvement", "type": "info",
             "desc": "When you act in the first round of combat, creatures that haven't acted yet are off-guard to you."},
            {"name": "Skill Feat", "type": "info", "desc": "Rogues gain a skill feat at every level."},
            {"name": "Skill Increase", "type": "info", "desc": "Standard skill increase."},
            {"name": "Sneak Attacker (improved)", "type": "info", "desc": "Sneak attack damage scales: 2d6 at L5, 3d6 at L11, 4d6 at L17."},
            {"name": "Racket-specific Bonus (Ruffian)", "type": "info", "subclass": ["Ruffian"],
             "desc": "When you sneak attack with medium armor + martial-weapon Strikes, the target also takes flat damage equal to your level/2."},
            {"name": "Racket-specific Bonus (Scoundrel)", "type": "info", "subclass": ["Scoundrel"],
             "desc": "When you Feint successfully, the target is off-guard to your next Strike before the end of your turn."},
            {"name": "Racket-specific Bonus (Thief)", "type": "info", "subclass": ["Thief"],
             "desc": "Your Dex-based finesse Strike now adds Dex to damage in place of Str (scales with class)."},
            {"name": "Racket-specific Bonus (Eldritch Trickster)", "type": "info", "subclass": ["Eldritch Trickster"],
             "desc": "You gain a free dabbler-tier focus spell from your chosen tradition."},
            {"name": "Racket-specific Bonus (Mastermind)", "type": "info", "subclass": ["Mastermind"],
             "desc": "Successful Recall Knowledge against a creature makes it off-guard to your next Strike."}],
        5: [{"name": "Skill Mastery (Rogue)", "type": "info",
             "desc": "Pick a trained skill — advance to Expert. (Rogue gets this earlier than other classes.)"},
            {"name": "Weapon Tricks", "type": "info", "desc": "Your sneak attack now applies to all martial weapons + crit specialization adds to the rider."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Evasion", "type": "info", "desc": "Reflex save reaches Master + auto-crit on success."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Debilitating Strike", "type": "info",
             "desc": "Sneak attacks can apply enfeebled, slowed, or off-guard for 1 round. Pick effect at strike time."},
            {"name": "Great Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        11: [{"name": "Rogue Expertise", "type": "info", "desc": "Class DC reaches Expert."},
             {"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."}],
        13: [{"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."},
             {"name": "Improved Evasion", "type": "info", "desc": "Reflex auto-crit threshold improves; failed Reflex saves take half damage."},
             {"name": "Master Tricks", "type": "info", "desc": "Master with all simple + several martial weapons (rapier, shortbow, shortsword, etc.)."}],
        15: [{"name": "Greater Debilitations", "type": "info", "desc": "Debilitating Strike can apply more severe versions (clumsy 1, drained 1, immobilized for 1 turn)."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Slippery Mind", "type": "info", "desc": "Will save reaches Master."},
             {"name": "Light Armor Mastery", "type": "info", "desc": "Master in unarmored + light armor."}],
        19: [{"name": "Master Strike", "type": "info",
              "desc": "Capstone — three times per day, declare a sneak-attack Strike that on a hit forces a Fortitude save (DC 11+sneak attack) or be paralyzed/asleep/killed."}],
    },

    "sorcerer": {
        3: [{"name": "Signature Spells", "type": "info",
             "desc": "Pick 1 spell per rank as Signature — auto-heightens to any rank slot you spend."},
            {"name": "Bloodline Spell (rank 2 known)", "type": "info",
             "desc": "Your bloodline grants its rank-2 spell as a known spell — always available, doesn't count against repertoire."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."},
            {"name": "Bloodline Spell (rank 3)", "type": "info", "desc": "Bloodline grants its rank-3 spell automatically."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Bloodline Spell (rank 4)", "type": "info", "desc": "Bloodline grants its rank-4 spell automatically."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Bloodline Spell (rank 5)", "type": "info", "desc": "Bloodline grants its rank-5 spell automatically."}],
        11: [{"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."},
             {"name": "Weapon Expertise", "type": "info", "desc": "Expert in simple weapons + crossbow."},
             {"name": "Bloodline Spell (rank 6)", "type": "info", "desc": "Bloodline grants its rank-6 spell automatically."}],
        13: [{"name": "Defensive Robes", "type": "info", "desc": "Expert in unarmored defense."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."},
             {"name": "Bloodline Spell (rank 7)", "type": "info", "desc": "Bloodline grants its rank-7 spell automatically."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
             {"name": "Bloodline Spell (rank 8)", "type": "info", "desc": "Bloodline grants its rank-8 spell automatically."}],
        17: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
             {"name": "Bloodline Spell (rank 9)", "type": "info", "desc": "Bloodline grants its rank-9 spell automatically."}],
        19: [{"name": "Bloodline Paragon", "type": "info", "desc": "Capstone — your bloodline grants you a unique signature ability tied to its theme."},
             {"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."}],
    },

    "wizard": {
        3: [{"name": "Drain Bonded Item improvement", "type": "info",
             "desc": "Your bonded item now lets you re-cast a prepared spell once per day per rank you can cast."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."},
            {"name": "Arcane School Bonus (Abjuration)", "type": "info", "subclass": ["Abjuration"], "desc": "Add +2 status bonus to saves vs your school's spells (Dispel Magic, etc.)."},
            {"name": "Arcane School Bonus (Conjuration)", "type": "info", "subclass": ["Conjuration"], "desc": "When you Cast a Spell of your school, summoned creatures gain +1 status to attack rolls for 1 round."},
            {"name": "Arcane School Bonus (Divination)", "type": "info", "subclass": ["Divination"], "desc": "Once per day, you can re-roll a Perception or initiative check (your choice)."},
            {"name": "Arcane School Bonus (Enchantment)", "type": "info", "subclass": ["Enchantment"], "desc": "Targets of your school's spells take a -1 status to saves vs your enchantment spells for 1 round."},
            {"name": "Arcane School Bonus (Evocation)", "type": "info", "subclass": ["Evocation"], "desc": "Your school's damage spells deal +1 damage per spell rank."},
            {"name": "Arcane School Bonus (Illusion)", "type": "info", "subclass": ["Illusion"], "desc": "Disbelievers of your illusions take a -1 status penalty to checks against the illusion."},
            {"name": "Arcane School Bonus (Necromancy)", "type": "info", "subclass": ["Necromancy"], "desc": "When you reduce a creature to 0 HP with a school spell, you gain temporary HP equal to your level."},
            {"name": "Arcane School Bonus (Transmutation)", "type": "info", "subclass": ["Transmutation"], "desc": "Polymorph and metamorph spells last 1 round longer."},
            {"name": "Universalist Bonus", "type": "info", "subclass": ["Universalist"], "desc": "You gain an extra Drain Bonded Item per day at L3 (and another at L9, L15)."}],
        5: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        9: [{"name": "Wizard Weapon Expertise", "type": "info", "desc": "Expert in club, crossbow, dagger, heavy crossbow, and staff."},
            {"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."}],
        11: [{"name": "Wizard Expertise", "type": "info", "desc": "Class DC reaches Expert."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Defensive Robes", "type": "info", "desc": "Expert in unarmored defense."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Prodigious Will", "type": "info", "desc": "Will auto-success on first failed save per day."},
             {"name": "School Mastery", "type": "info", "desc": "Your arcane-school capstone activates (varies by school — see your school feature description)."}],
        19: [{"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."},
             {"name": "Archwizard's Spellcraft", "type": "info", "desc": "Capstone — gain a 10th-rank spell slot you can fill with any wizard spell prepared rituals."}],
    },

    "investigator": {
        3: [{"name": "Skill Mastery (Investigator)", "type": "info",
             "desc": "Pick a trained skill — advance to Expert. Investigator gains a skill feat at every level (already granted)."},
            {"name": "Methodology Feature (Alchemical Sciences)", "type": "info", "subclass": ["Alchemical Sciences"], "desc": "You gain Quick Alchemy with a small reagent pool, scaling with class."},
            {"name": "Methodology Feature (Empiricism)", "type": "info", "subclass": ["Empiricism"], "desc": "When you successfully Recall Knowledge, the target is also off-guard to your next Strike."},
            {"name": "Methodology Feature (Interrogation)", "type": "info", "subclass": ["Interrogation"], "desc": "Your Pursue a Lead extends to social manipulation — Diplomacy/Intimidate vs the lead is +1."},
            {"name": "Methodology Feature (Forensic Medicine)", "type": "info", "subclass": ["Forensic Medicine"], "desc": "Treat Wounds is 1 action faster + Battle Medicine recharges in 10 minutes."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons."}],
        7: [{"name": "Trapfinder", "type": "info", "desc": "Find traps automatically when adjacent. +1 status to disable traps."},
            {"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."}],
        9: [{"name": "Deductive Improvisation", "type": "info", "desc": "When you Pursue a Lead, you treat all skills as Trained for the duration."},
            {"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."}],
        11: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
             {"name": "Investigator Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        13: [{"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Greater Methodology", "type": "info", "desc": "Methodology's mid-tier upgrade — see your methodology entry."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Light Armor Mastery", "type": "info", "desc": "Master in unarmored + light armor."},
             {"name": "Master Methodology", "type": "info", "desc": "Methodology's master-tier feature unlocks."}],
        19: [{"name": "Master Detective", "type": "info", "desc": "Capstone — Pursue a Lead grants insights even on failure; you can keep two leads concurrently."}],
    },

    "magus": {
        3: [{"name": "Hybrid Study Upgrade (Inexorable Iron)", "type": "info", "subclass": ["Inexorable Iron"],
             "desc": "Your two-handed Spellstrike Strike adds a die of your weapon's damage on the spell's damage."},
            {"name": "Hybrid Study Upgrade (Laughing Shadow)", "type": "info", "subclass": ["Laughing Shadow"],
             "desc": "When you Spellstrike, you can also Step or Stride as part of the action."},
            {"name": "Hybrid Study Upgrade (Sparkling Targe)", "type": "info", "subclass": ["Sparkling Targe"],
             "desc": "Your shield-based Spellstrike adds your shield's Hardness as bonus damage on the spell."},
            {"name": "Hybrid Study Upgrade (Starlit Span)", "type": "info", "subclass": ["Starlit Span"],
             "desc": "Your ranged Spellstrike costs only 1 action (was 2) on prepared shots."},
            {"name": "Hybrid Study Upgrade (Twisting Tree)", "type": "info", "subclass": ["Twisting Tree"],
             "desc": "Your two-handed melee Strike + Spellstrike grants a free Step after the strike."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Conflux Spells", "type": "info", "desc": "You learn one Conflux spell — a focus spell tied to your study."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Magus Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons + study weapon."}],
        9: [{"name": "Magus Expertise", "type": "info", "desc": "Class DC reaches Expert."},
            {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        11: [{"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Expert."}],
        13: [{"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."},
             {"name": "Magus Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons + study weapon."}],
        15: [{"name": "Greater Spellstrike", "type": "info", "desc": "Spellstrike now uses your weapon's crit specialization on a successful spell + Strike."},
             {"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Greater Conflux", "type": "info", "desc": "Conflux spell heightens to your highest spell rank automatically."}],
        19: [{"name": "Legendary Spellstrike", "type": "info", "desc": "Capstone — Spellstrike auto-criticals if you spend an extra action; spell rank reaches Legendary."}],
    },

    "monk": {
        3: [{"name": "Mystic Strikes", "type": "info", "desc": "Your unarmed Strikes count as magical for resistance/weakness. (Always-on.)"},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Path to Perfection (Save Choice)", "type": "choice",
             "desc": "Pick ONE save to advance to Master.",
             "choices": [
                {"name": "Fortitude", "desc": "Master Fortitude — resilient to body-affecting effects."},
                {"name": "Reflex", "desc": "Master Reflex — superb at avoiding traps and AOEs."},
                {"name": "Will", "desc": "Master Will — clear-minded against mental effects."},
             ]},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Path to Perfection — second save", "type": "info",
             "desc": "(Existing app.py logic) Advance ONE additional save to Master."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons + unarmed."}],
        9: [{"name": "Metal Strikes", "type": "info", "desc": "Unarmed Strikes count as cold iron + silver for resistance/weakness."},
            {"name": "Monk Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        11: [{"name": "Third Path to Perfection", "type": "info", "desc": "(Existing app.py logic) Advance the LAST save to Master, OR re-pick the first to add Legendary potential."},
             {"name": "Alertness", "type": "info", "desc": "Perception reaches Master."}],
        13: [{"name": "Graceful Mastery", "type": "info", "desc": "Reflex auto-crit threshold improves — failed Reflex saves take half damage."},
             {"name": "Master Strikes", "type": "info", "desc": "Master in unarmed + martial weapons."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Adamantine Strikes", "type": "info", "desc": "Unarmed Strikes count as adamantine for resistance/weakness."},
             {"name": "Graceful Legend", "type": "info", "desc": "All save proficiencies become Legendary."}],
        19: [{"name": "Perfected Form", "type": "info", "desc": "Capstone — once per day, treat any Strike or save's d20 as a 10 (auto-success against most checks)."}],
    },

    "ranger": {
        3: [{"name": "Iron Will", "type": "info", "desc": "Will save reaches Expert."},
            {"name": "Hunter's Edge upgrade (Flurry)", "type": "info", "subclass": ["Flurry"],
             "desc": "Your second/third attacks per round vs your hunted prey suffer reduced MAP — -3/-6 instead of -5/-10."},
            {"name": "Hunter's Edge upgrade (Outwit)", "type": "info", "subclass": ["Outwit"],
             "desc": "+2 circumstance to Deception, Intimidation, Stealth, and Recall Knowledge against your hunted prey."},
            {"name": "Hunter's Edge upgrade (Precision)", "type": "info", "subclass": ["Precision"],
             "desc": "Your first Strike against your hunted prey each round deals additional precision damage (1d8 at L3)."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ranger Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons."},
            {"name": "Trackless Step", "type": "info", "desc": "Stride does not leave a trail through normal terrain (counted as if you'd Sneaked)."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Nature's Edge", "type": "info", "desc": "Your hunted prey is treated as off-guard against you in difficult / greater terrain."},
            {"name": "Ranger Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        11: [{"name": "Juggernaut", "type": "info", "desc": "Fort save reaches Master."},
             {"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Wild Stride", "type": "info", "desc": "Ignore difficult terrain caused by natural sources."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons."},
             {"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."}],
        15: [{"name": "Improved Evasion", "type": "info", "desc": "Reflex auto-crit threshold improves; failed Reflex saves take half damage."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Masterful Hunter", "type": "info", "desc": "Hunter's Edge effect doubles — Flurry MAP becomes -2/-4, Outwit bonus +4, Precision die +1d10."},
             {"name": "Medium Armor Mastery", "type": "info", "desc": "Master in light + medium armor."}],
        19: [{"name": "Swift Prey", "type": "info", "desc": "Hunt Prey becomes a free action once per round."},
             {"name": "Capstone — Trackless Master", "type": "info", "desc": "Capstone — your tracks vanish entirely; you cannot be Tracked."}],
    },

    "summoner": {
        3: [{"name": "Eidolon Boost", "type": "info",
             "desc": "Your eidolon gains its first major upgrade: increase its size category, gain a new natural attack, OR gain a new defensive ability based on type."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Eidolon's Wrath", "type": "info", "desc": "Your eidolon's Strikes deal 1d6 extra damage of its type when you act in tandem."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Eidolon Master Strikes", "type": "info", "desc": "Eidolon's natural attacks reach Master in their proficiency progression."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Tandem Movement", "type": "info", "desc": "When you Stride, your eidolon may also Stride as a free action (or vice versa)."}],
        11: [{"name": "Eidolon Specialization", "type": "info", "desc": "Eidolon gains a unique trait based on type: ace flier, agile crawler, etc."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons + eidolon's natural attacks."},
             {"name": "Eidolon Defensive Aura", "type": "info", "desc": "Eidolon emits a 10 ft aura granting allies +1 status to AC."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Eidolon Mastery", "type": "info", "desc": "Eidolon's natural attacks reach Legendary."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Greater Specialization", "type": "info", "desc": "Eidolon's L11 specialization upgrades to a more powerful tier."}],
        19: [{"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."},
             {"name": "Eidolon Apex", "type": "info", "desc": "Capstone — your bond grants the eidolon a unique signature ability based on type (e.g., true sight, regeneration, time-skip)."}],
    },

    "swashbuckler": {
        3: [{"name": "Swashbuckler Style upgrade (Battledancer)", "type": "info", "subclass": ["Battledancer"], "desc": "Your bardic flair converts: Performance gives you Panache + grants allies a +1 status to attacks for 1 round."},
            {"name": "Swashbuckler Style upgrade (Braggart)", "type": "info", "subclass": ["Braggart"], "desc": "Demoralize doesn't take a -4 penalty for not sharing a language; criticals on Demoralize grant Panache."},
            {"name": "Swashbuckler Style upgrade (Fencer)", "type": "info", "subclass": ["Fencer"], "desc": "Feint becomes a 1-action activity; criticals on Feint grant Panache."},
            {"name": "Swashbuckler Style upgrade (Gymnast)", "type": "info", "subclass": ["Gymnast"], "desc": "Athletics maneuvers (Trip, Disarm, Grapple, Shove) trigger Panache on success."},
            {"name": "Swashbuckler Style upgrade (Wit)", "type": "info", "subclass": ["Wit"], "desc": "Bon Mot is a 1-action; criticals on Bon Mot grant Panache."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons."}],
        7: [{"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Evasion", "type": "info", "desc": "Reflex save reaches Master + auto-crit on success."},
            {"name": "Exemplary Finisher", "type": "info", "desc": "Style-specific Finisher upgrade — see style entry."}],
        11: [{"name": "Continuous Flair", "type": "info", "desc": "While you have Panache, your speed increases 5 ft."},
             {"name": "Vivacious Speed", "type": "info", "desc": "+10 ft Speed when wielding 1 finesse weapon (and a free hand)."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons + crit specialization."},
             {"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Improved Evasion", "type": "info", "desc": "Failed Reflex saves take half damage."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Style Mastery", "type": "info", "desc": "Style's master-tier ability unlocks — varies by style (see your style entry)."}],
        19: [{"name": "Eternal Confidence", "type": "info", "desc": "Capstone — Panache cannot be lost involuntarily; finisher Strikes auto-stagger their target."}],
    },

    "thaumaturge": {
        3: [{"name": "Implement upgrade (Amulet)", "type": "info", "subclass": ["Amulet"], "desc": "Your amulet's protection scales — gain +2 status to saves vs cursed/divine effects."},
            {"name": "Implement upgrade (Bell)", "type": "info", "subclass": ["Bell"], "desc": "Your Strike-rooted bell ringing now applies Frightened 1 on a successful Diplomacy/Intimidation."},
            {"name": "Implement upgrade (Chalice)", "type": "info", "subclass": ["Chalice"], "desc": "Your chalice grants 1 extra heal/day equal to your level."},
            {"name": "Implement upgrade (Lantern)", "type": "info", "subclass": ["Lantern"], "desc": "Your lantern's reveal-truth aura extends 10 ft; concealment ends in the area."},
            {"name": "Implement upgrade (Mirror)", "type": "info", "subclass": ["Mirror"], "desc": "Your mirror image scales — gain 2 mirror images instead of 1, and they last 1 minute."},
            {"name": "Implement upgrade (Regalia)", "type": "info", "subclass": ["Regalia"], "desc": "Your regalia's authority aura grants +1 status to allies' saves vs fear in 30 ft."},
            {"name": "Implement upgrade (Tome)", "type": "info", "subclass": ["Tome"], "desc": "Your tome lets you Recall Knowledge as a free action once per round."},
            {"name": "Implement upgrade (Wand)", "type": "info", "subclass": ["Wand"], "desc": "Your wand can cast a chosen cantrip at +1 spell rank for 1 round."},
            {"name": "Implement upgrade (Weapon)", "type": "info", "subclass": ["Weapon"], "desc": "Your weapon Strike adds your Implement Adept's bonus damage equal to half your level (rounded up)."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Second Implement", "type": "info", "desc": "Choose another implement to wield (you can have two implements active)."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Esoteric Lore", "type": "info", "desc": "Bonus skill — Esoteric Lore reaches Expert."}],
        7: [{"name": "Implement Adept", "type": "info", "desc": "Your primary implement's effect upgrades to its mid-tier benefit."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        9: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
            {"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."}],
        11: [{"name": "Third Implement", "type": "info", "desc": "Choose a third implement (max 3 active)."},
             {"name": "Intensify Vulnerability", "type": "info", "desc": "Exploit Vulnerability now applies +1 die of damage on your next Strike against the target."}],
        13: [{"name": "Weapon Mastery / Implement Paragon", "type": "info", "desc": "Master in simple + martial weapons; primary implement reaches Paragon tier."}],
        15: [{"name": "Greater Implement", "type": "info", "desc": "All implements' main features upgrade to greater tier."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Implement Paragon (all implements)", "type": "info", "desc": "All implements reach Paragon — strongest tier across the board."}],
        19: [{"name": "Esoteric Capstone", "type": "info", "desc": "Capstone — once per day, automatically Exploit Vulnerability against any target you can perceive without rolling."}],
    },

    "witch": {
        3: [{"name": "Lesson", "type": "info",
             "desc": "Your patron grants a Lesson — pick a thematic mini-feature from your patron's lesson list (see Witch's Patron section)."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."},
            {"name": "Patron Familiar Boost", "type": "info", "desc": "Your familiar gains 1 additional ability of your choice (e.g., Speech, Manual Dexterity, Skilled)."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Familiar Boost", "type": "info", "desc": "Familiar gains another ability."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Lesson (second)", "type": "info", "desc": "Pick a second Lesson from your patron's list."}],
        11: [{"name": "Witch Weapon Expertise", "type": "info", "desc": "Expert in simple weapons."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Defensive Robes", "type": "info", "desc": "Expert in unarmored defense."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Greater Lesson", "type": "info", "desc": "A new Master-tier lesson unlocks from your patron's repertoire."}],
        19: [{"name": "Patron's Truth", "type": "info", "desc": "Capstone — your patron grants you a 10th-rank lesson and a unique patron-themed signature ability."},
             {"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."}],
    },

    "psychic": {
        3: [{"name": "Subconscious Mind / Conscious Mind advance", "type": "info",
             "desc": "Your subconscious-mind feature scales (e.g., Distant Grasp manifests further; Oscillating Wave gains the rank-1 wave)."},
            {"name": "Conscious Mind upgrade (Distant Grasp)", "type": "info", "subclass": ["Distant Grasp"], "desc": "Your telekinetic Strike adds your Int/Cha modifier to damage at range."},
            {"name": "Conscious Mind upgrade (Infinite Eye)", "type": "info", "subclass": ["Infinite Eye"], "desc": "True Strike-tier divination effects — first attack roll per round can re-roll once."},
            {"name": "Conscious Mind upgrade (Silent Whisper)", "type": "info", "subclass": ["Silent Whisper"], "desc": "Your silent-mind cantrips affect creatures even when they can't perceive you."},
            {"name": "Conscious Mind upgrade (Tangent Strike)", "type": "info", "subclass": ["Tangent Strike"], "desc": "Your Spellstrike-style impulses gain +1 status to attack rolls."},
            {"name": "Conscious Mind upgrade (Unbound Step)", "type": "info", "subclass": ["Unbound Step"], "desc": "+10 ft Speed; you can teleport-step 5 ft as a free action once per round."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        9: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
            {"name": "Conscious Mind advance", "type": "info", "desc": "Your conscious-mind feature scales — see your conscious-mind table."}],
        11: [{"name": "Psychic Weapon Expertise", "type": "info", "desc": "Expert in simple weapons."}],
        13: [{"name": "Defensive Robes", "type": "info", "desc": "Expert in unarmored defense."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Subconscious Master", "type": "info", "desc": "Your subconscious mind ability scales to its master tier."}],
        19: [{"name": "Conscious Mind Mastery", "type": "info", "desc": "Capstone — your conscious-mind unleashes once per day at maximum tier."},
             {"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."}],
    },

    "oracle": {
        3: [{"name": "Mystery Bonus / Curse Tier", "type": "info",
             "desc": "Your mystery feature scales; your curse tier advances by your spellcasting progression."},
            {"name": "Mystery Spell (Ancestors)", "type": "info", "subclass": ["Ancestors"], "desc": "You learn an Ancestors mystery focus spell tied to commune-with-spirits."},
            {"name": "Mystery Spell (Battle)", "type": "info", "subclass": ["Battle"], "desc": "You learn a Battle mystery focus spell — bless yourself with a bonus to attack."},
            {"name": "Mystery Spell (Bones)", "type": "info", "subclass": ["Bones"], "desc": "You learn a Bones mystery focus spell — necromantic strike or summon."},
            {"name": "Mystery Spell (Cosmos)", "type": "info", "subclass": ["Cosmos"], "desc": "You learn a Cosmos mystery focus spell — light/cold variant of Fireball."},
            {"name": "Mystery Spell (Flames)", "type": "info", "subclass": ["Flames"], "desc": "You learn a Flames mystery focus spell — fire-themed offensive cantrip."},
            {"name": "Mystery Spell (Life)", "type": "info", "subclass": ["Life"], "desc": "You learn a Life mystery focus spell — additional area heal."},
            {"name": "Mystery Spell (Lore)", "type": "info", "subclass": ["Lore"], "desc": "You learn a Lore mystery focus spell — divination + insight bonus to allies."},
            {"name": "Mystery Spell (Tempest)", "type": "info", "subclass": ["Tempest"], "desc": "You learn a Tempest mystery focus spell — wind-shock area effect."},
            {"name": "Mystery Spell (Time)", "type": "info", "subclass": ["Time"], "desc": "You learn a Time mystery focus spell — slow/haste targeted manipulation."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."},
            {"name": "Mystery Skill", "type": "info", "desc": "A skill tied to your mystery becomes Expert (e.g., Religion for many mysteries)."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Major Curse", "type": "info", "desc": "Your curse advances to its mid-tier — bigger penalties but bigger benefits."}],
        11: [{"name": "Oracle Weapon Expertise", "type": "info", "desc": "Expert in simple weapons."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Extreme Curse", "type": "info", "desc": "Your curse reaches its highest tier — most severe drawback, but most powerful boon."}],
        19: [{"name": "Oracular Clarity", "type": "info", "desc": "Capstone — once per day you can ignore your curse entirely for 1 round at no cost."},
             {"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."}],
    },

    "gunslinger": {
        3: [{"name": "Way Skill / Slinger's Reload", "type": "info", "desc": "Way-specific feature: Drifter, Pistolero, Sniper, Vanguard, or Spellshot ability scales."},
            {"name": "Way Bonus (Drifter)", "type": "info", "subclass": ["Drifter"], "desc": "Drifter's Cover + Reload becomes 1 action (was 2). You can fire from cover without losing it."},
            {"name": "Way Bonus (Pistolero)", "type": "info", "subclass": ["Pistolero"], "desc": "Pistolero's quick-draw fan-fire — once per round, fire two pistols at the same target with reduced MAP."},
            {"name": "Way Bonus (Sniper)", "type": "info", "subclass": ["Sniper"], "desc": "Sniper's One Shot, One Kill — first ranged Strike per round adds your Wis modifier as bonus damage."},
            {"name": "Way Bonus (Vanguard)", "type": "info", "subclass": ["Vanguard"], "desc": "Vanguard's Spreadshot — your scattergun strikes hit two adjacent targets in a 15 ft cone."},
            {"name": "Way Bonus (Spellshot)", "type": "info", "subclass": ["Spellshot"], "desc": "Spellshot grants you 1 spell slot per round of an arcane Cantrip you choose, fired through your firearm."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Singular Expertise", "type": "info", "desc": "Expert in simple firearms + your Way's signature firearm."}],
        7: [{"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Slinger's Reflex", "type": "info", "desc": "Reflex save reaches Expert; first Strike per turn ignores cover from your Way's signature weapon."},
            {"name": "Way's Tactic", "type": "info", "desc": "Way-specific mid-tier ability unlocks — see Way table."}],
        11: [{"name": "Gunslinger Expertise", "type": "info", "desc": "Class DC reaches Expert."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."},
             {"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial firearms."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Way Mastery", "type": "info", "desc": "Way's master-tier ability unlocks."}],
        19: [{"name": "Gunslinger Capstone", "type": "info", "desc": "Capstone — once per day, automatically score a critical hit on a Strike with your Way's signature firearm."}],
    },

    # ── PC2 / Remaster classes — full tagging ─────────────────────────────
    "animist": {
        3: [{"name": "Apparition Attunement upgrade", "type": "info",
             "desc": "Your apparition's first feature scales — granting an additional advanced effect (varies by apparition type)."},
            {"name": "Vessel Spell (extra)", "type": "info",
             "desc": "Your animist spellcasting now includes an additional vessel spell from your primary apparition."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Magical Fortitude", "type": "info", "desc": "Fort save reaches Expert."}],
        7: [{"name": "Expert Spellcaster", "type": "info", "desc": "Spell DC + attack reach Expert."}],
        9: [{"name": "Lightning Reflexes", "type": "info", "desc": "Reflex save reaches Expert."},
            {"name": "Apparition Mastery", "type": "info", "desc": "Your primary apparition gains its mid-tier benefit."}],
        11: [{"name": "Animist Weapon Expertise", "type": "info", "desc": "Expert in simple weapons."},
             {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        13: [{"name": "Light Armor Expertise", "type": "info", "desc": "Expert in unarmored + light armor."},
             {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        15: [{"name": "Master Spellcaster", "type": "info", "desc": "Spell DC + attack reach Master."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Apparition Apex", "type": "info", "desc": "Your primary apparition's master-tier feature unlocks."}],
        19: [{"name": "Communion Capstone", "type": "info", "desc": "Capstone — your apparition's full power manifests once per day for 1 minute, granting all its tier benefits at once."},
             {"name": "Legendary Spellcaster", "type": "info", "desc": "Spell DC + attack reach Legendary."}],
    },

    "commander": {
        3: [{"name": "Tactical Insight", "type": "info",
             "desc": "When you Direct an Ally as a Free Action, that ally also gains a +1 status bonus to AC against the next attack until your next turn."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Banner Tactics", "type": "info", "desc": "Your Banner's range extends to 30 ft (was 15 ft) and you can issue tactics from it."},
            {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        7: [{"name": "Tactical Expertise", "type": "info", "desc": "Class DC reaches Expert."},
            {"name": "Commander Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons."}],
        9: [{"name": "Resolve", "type": "info", "desc": "Will save reaches Master."},
            {"name": "Greater Tactics", "type": "info", "desc": "All your tactics gain a secondary effect (movement bonus, +1 status to a save, etc.)."}],
        11: [{"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons."},
             {"name": "Greater Banner", "type": "info", "desc": "Banner range extends to 60 ft + grants resistance equal to your level/2 to allies in the area."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Master Tactician", "type": "info", "desc": "Class DC reaches Master; you can issue 2 tactics per round."}],
        19: [{"name": "Commander Capstone", "type": "info", "desc": "Capstone — once per day, your banner's range extends to 120 ft for 10 minutes; allies in the area cannot be Frightened."}],
    },

    "exemplar": {
        3: [{"name": "Ikon Boost", "type": "info",
             "desc": "Your bonded Ikon (sword, javelin, shield, etc.) gains its first divine boon — see Ikon entry for the boost detail."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Exemplar Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons."}],
        7: [{"name": "Iron Will", "type": "info", "desc": "Will save reaches Expert."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Greater Ikon", "type": "info", "desc": "Your Ikon gains a second divine boon."},
            {"name": "Resolve", "type": "info", "desc": "Will save reaches Master."}],
        11: [{"name": "Medium Armor Expertise", "type": "info", "desc": "Expert in light + medium armor."},
             {"name": "Class DC Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons + crit specialization."},
             {"name": "Ikon Mastery", "type": "info", "desc": "Your Ikon's primary boon scales to mid-tier."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Apex Ikon", "type": "info", "desc": "Your Ikon reaches its apex tier — strongest divine boon."}],
        19: [{"name": "Exemplar Capstone", "type": "info", "desc": "Capstone — once per day, channel your Ikon's apex tier to a 10th-rank effect."}],
    },

    "guardian": {
        3: [{"name": "Bulwark Stance", "type": "info",
             "desc": "When you take the Raise Shield action, you also gain damage reduction equal to your level/2 against the next attack."},
            {"name": "General Feat + Skill Increase", "type": "info", "desc": "Standard L3 odd-level feat + skill increase."}],
        5: [{"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."},
            {"name": "Guardian Weapon Expertise", "type": "info", "desc": "Expert in simple + martial weapons + shield bash."}],
        7: [{"name": "Vigilant Senses", "type": "info", "desc": "Perception reaches Master."},
            {"name": "Weapon Specialization", "type": "info", "desc": "+2 damage with expert weapons."}],
        9: [{"name": "Greater Bulwark", "type": "info", "desc": "Bulwark Stance damage reduction extends to allies adjacent to you."},
            {"name": "Juggernaut", "type": "info", "desc": "Fort save reaches Master."}],
        11: [{"name": "Heavy Armor Expertise", "type": "info", "desc": "Expert in light, medium, and heavy armor."},
             {"name": "Guardian Expertise", "type": "info", "desc": "Class DC reaches Expert."}],
        13: [{"name": "Weapon Mastery", "type": "info", "desc": "Master in simple + martial weapons + crit specialization."}],
        15: [{"name": "Greater Weapon Specialization", "type": "info", "desc": "Specialization damage doubles."},
             {"name": "Ability Boosts (4)", "type": "info", "desc": "Gain 4 free ability boosts."}],
        17: [{"name": "Iron Bulwark", "type": "info", "desc": "Bulwark Stance damage reduction increases to your level."},
             {"name": "Heavy Armor Mastery", "type": "info", "desc": "Master in light, medium, and heavy armor."}],
        19: [{"name": "Guardian Capstone", "type": "info", "desc": "Capstone — once per day, when an ally in 30 ft would be reduced to 0 HP, drop to 1 HP instead."}],
    },
}

# =============================================================================
# FEAT PREREQUISITE VALIDATION
# =============================================================================
# Skill feat → required skill + rank. Auto-generated from compendium + manual additions.
RANK_VALUES = {'untrained': 0, 'trained': 2, 'expert': 4, 'master': 6, 'legendary': 8}

SKILL_FEAT_PREREQS = {
    # Common L1 skill feats
    "Assurance": {"skill": "*", "rank": "trained"},  # any trained skill
    "Battle Medicine": {"skill": "medicine", "rank": "trained"},
    "Cat Fall": {"skill": "acrobatics", "rank": "trained"},
    "Charming Liar": {"skill": "deception", "rank": "trained"},
    "Combat Climber": {"skill": "athletics", "rank": "trained"},
    "Courtly Graces": {"skill": "society", "rank": "trained"},
    "Experienced Tracker": {"skill": "survival", "rank": "trained"},
    "Forager": {"skill": "survival", "rank": "trained"},
    "Group Coercion": {"skill": "intimidation", "rank": "trained"},
    "Group Impression": {"skill": "diplomacy", "rank": "trained"},
    "Hefty Hauler": {"skill": "athletics", "rank": "trained"},
    "Hobnobber": {"skill": "diplomacy", "rank": "trained"},
    "Intimidating Glare": {"skill": "intimidation", "rank": "trained"},
    "Intimidating Prowess": {"skill": "intimidation", "rank": "trained"},
    "Lie to Me": {"skill": "deception", "rank": "trained"},
    "Multilingual": {"skill": "society", "rank": "trained"},
    "Natural Medicine": {"skill": "medicine", "rank": "trained"},
    "Pickpocket": {"skill": "thievery", "rank": "trained"},
    "Quick Coercion": {"skill": "intimidation", "rank": "trained"},
    "Quick Identification": {"skill": "arcana", "rank": "trained"},  # or nature/occultism/religion
    "Quick Jump": {"skill": "athletics", "rank": "trained"},
    "Quick Repair": {"skill": "crafting", "rank": "trained"},
    "Read Lips": {"skill": "society", "rank": "trained"},
    "Robust Recovery": {"skill": "medicine", "rank": "trained"},
    "Snare Crafting": {"skill": "crafting", "rank": "trained"},
    "Specialty Crafting": {"skill": "crafting", "rank": "trained"},
    "Steady Balance": {"skill": "acrobatics", "rank": "trained"},
    "Streetwise": {"skill": "society", "rank": "trained"},
    "Subtle Theft": {"skill": "thievery", "rank": "trained"},
    "Survey Wildlife": {"skill": "survival", "rank": "trained"},
    "Terrain Expertise": {"skill": "survival", "rank": "trained"},
    "Terrain Stalker": {"skill": "stealth", "rank": "trained"},
    "Titan Wrestler": {"skill": "athletics", "rank": "trained"},
    "Trick Magic Item": {"skill": "arcana", "rank": "trained"},
    "Trip Acumen": {"skill": "athletics", "rank": "trained"},
    "Virtuosic Performer": {"skill": "performance", "rank": "trained"},
    "Ward Medic": {"skill": "medicine", "rank": "trained"},
    # Common L2+ skill feats
    "Bon Mot": {"skill": "diplomacy", "rank": "trained"},
    "Glad-Hand": {"skill": "diplomacy", "rank": "trained"},
    "Continual Recovery": {"skill": "medicine", "rank": "expert"},
    "Magical Crafting": {"skill": "crafting", "rank": "expert"},
    "Impeccable Crafting": {"skill": "crafting", "rank": "master"},
    "Unified Theory": {"skill": "arcana", "rank": "legendary"},
    "Legendary Medic": {"skill": "medicine", "rank": "legendary"},
    "Legendary Negotiation": {"skill": "diplomacy", "rank": "legendary"},
    "Legendary Sneak": {"skill": "stealth", "rank": "legendary"},
    "Cloud Jump": {"skill": "athletics", "rank": "legendary"},
    "Scare to Death": {"skill": "intimidation", "rank": "legendary"},
    "Shameless Request": {"skill": "diplomacy", "rank": "expert"},
    "Terrified Retreat": {"skill": "intimidation", "rank": "expert"},
    "Powerful Leap": {"skill": "athletics", "rank": "expert"},
    "Rapid Mantel": {"skill": "athletics", "rank": "expert"},
    "Quick Squeeze": {"skill": "acrobatics", "rank": "trained"},
    "Kip Up": {"skill": "acrobatics", "rank": "trained"},
    "Confabulator": {"skill": "deception", "rank": "expert"},
    "Slippery Secrets": {"skill": "deception", "rank": "expert"},
    "Quick Disguise": {"skill": "deception", "rank": "master"},
}

def check_feat_prereqs(feat_name, character_proficiencies):
    """
    Check if a character meets prerequisites for a feat.
    Returns: {'met': True/False, 'reason': 'explanation'} or None if no prereqs known.
    """
    prereq = SKILL_FEAT_PREREQS.get(feat_name)
    if not prereq:
        return None  # No prereqs known — don't block
    
    required_skill = prereq['skill']
    required_rank = prereq['rank']
    required_val = RANK_VALUES.get(required_rank, 2)
    
    if required_skill == '*':
        # Any trained skill (e.g., Assurance)
        return {'met': True, 'reason': f'Requires {required_rank} in any skill'}
    
    current_val = character_proficiencies.get(required_skill, 0)
    met = current_val >= required_val
    
    return {
        'met': met,
        'reason': f'{required_rank.title()} in {required_skill.title()}',
        'skill': required_skill,
        'required_rank': required_rank,
    }
