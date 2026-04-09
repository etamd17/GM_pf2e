# class_matrix.py
# Comprehensive PF2E Remaster Class Data
# Proficiency values: 0=untrained, 2=trained, 4=expert, 6=master, 8=legendary

# =============================================================================
# AUTOMATIC BONUS PROGRESSION (ABP) - Variant Rule
# =============================================================================
ABP_TABLE = {
    1: {}, 2: {"attack_potency": 1}, 3: {}, 4: {"devastating_attacks": 2}, 5: {"defense_potency": 1},
    6: {}, 7: {}, 8: {"save_potency": 1}, 9: {"perception_potency": 1}, 10: {"attack_potency": 2},
    11: {"defense_potency": 2}, 12: {"devastating_attacks": 3}, 13: {}, 14: {"save_potency": 2},
    15: {"perception_potency": 2}, 16: {"attack_potency": 3}, 17: {}, 18: {"defense_potency": 3},
    19: {"devastating_attacks": 4}, 20: {"save_potency": 3}
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
        9:  {"fortitude": 6, "class_dc": 4},                                                # Battle Hardened + Combat Flexibility
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4},                          # Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6, "advanced": 4},                       # Weapon Legend
        15: {"reflex": 6},                                                                   # Tempered Reflexes
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6},                          # Armor Mastery
        19: {"simple": 8, "martial": 8, "unarmed": 8, "advanced": 6},                       # Versatile Legend
    },
    # -------------------------------------------------------------------------
    # RANGER (Player Core p.152)
    # -------------------------------------------------------------------------
    "ranger": {
        # L1 initial: perception=4, fort=4, ref=4, will=2, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Iron Will
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6},                                                               # Vigilant Senses
        9:  {"reflex": 6, "class_dc": 4},                                                   # Nature's Edge / Ranger Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Medium Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"fortitude": 6},                                                                # Greater Weapon Specialization-level Fort bump
        17: {"unarmored": 6, "light": 6, "medium": 6},                                      # Medium Armor Mastery
        19: {"perception": 8},                                                               # Masterful Ranger / Keen Senses
    },
    # -------------------------------------------------------------------------
    # BARBARIAN (Player Core p.118)
    # -------------------------------------------------------------------------
    "barbarian": {
        # L1 initial: perception=4, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # (general save bump in Deny Advantage area)
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Brutality
        7:  {"perception": 6, "will": 6},                                                    # Keen Senses + Juggernaut-era Will
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Barbarian Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Armor of Fury
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Fury
        15: {"reflex": 6, "will": 6},                                                       # Greater Juggernaut (Fort→legend level), Improved saves
        17: {"unarmored": 6, "light": 6, "medium": 6, "fortitude": 8},                      # Armor of Fury (master), Indomitable Will / Mighty Rage
        19: {"perception": 8},                                                               # Keen Senses upgrade
    },
    # -------------------------------------------------------------------------
    # CHAMPION (Player Core p.124)
    # -------------------------------------------------------------------------
    "champion": {
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, all armor=2, unarmored=2, class_dc=2
        3:  {"reflex": 4},                                                                  # Divine Ally era
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4},                                                               # Vigilant Senses
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Champion Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4, "heavy": 4},                          # Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"will": 6, "reflex": 6},                                                       # Greater saves
        17: {"unarmored": 6, "light": 6, "medium": 6, "heavy": 6},                          # Armor Mastery
        19: {"perception": 6, "fortitude": 8},                                               # Hero's Defenses
    },
    # -------------------------------------------------------------------------
    # MONK (Player Core p.146)
    # -------------------------------------------------------------------------
    "monk": {
        # L1 initial: perception=2, fort=4, ref=4, will=4, simple=2, unarmed=4, unarmored=4, class_dc=2
        3:  {"simple": 4, "unarmed": 4},                                                    # Mystic Strikes (already expert unarmed)
        5:  {"perception": 4},                                                               # Alertness
        7:  {},                                                                              # Path to Perfection (player choice: one save → master)
        9:  {"class_dc": 4},                                                                 # Monk Expertise
        11: {},                                                                              # Second Path to Perfection (another save → master)
        13: {"simple": 6, "unarmed": 6},                                                    # Graceful Mastery
        15: {},                                                                              # Third Path to Perfection (one of first two → legendary)
        17: {"unarmored": 6},                                                                # Perfected Form / Adamantine Strikes area
        19: {"simple": 8, "unarmed": 8},                                                    # Graceful Legend
    },
    # -------------------------------------------------------------------------
    # ROGUE (Player Core p.160)
    # -------------------------------------------------------------------------
    "rogue": {
        # L1 initial: perception=4, fort=2, ref=4, will=4, simple=2, martial=2(rapier/sap/shortbow/shortsword), unarmed=2, light=2, unarmored=2, class_dc=2
        3:  {"perception": 4, "will": 4},                                                   # Already expert, Deny Advantage
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Tricks
        7:  {"perception": 6, "reflex": 6},                                                  # Vigilant Senses + Evasion
        9:  {"fortitude": 4, "class_dc": 4},                                                # Great Fortitude + Rogue Expertise
        11: {"unarmored": 4, "light": 4},                                                   # Light Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6, "reflex": 8},                         # Weapon Mastery + Improved Evasion (Legendary Ref)
        15: {"will": 6},                                                                     # Slippery Mind
        17: {"unarmored": 6, "light": 6},                                                   # Light Armor Mastery
        19: {"perception": 8, "fortitude": 6},                                               # Master Strike area
    },
    # -------------------------------------------------------------------------
    # SWASHBUCKLER (Player Core 2 / APG)
    # -------------------------------------------------------------------------
    "swashbuckler": {
        # L1 initial: perception=4, fort=2, ref=4, will=4, simple=2, martial=2, unarmed=2, light=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Stylish Trick / Deny Advantage
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6, "reflex": 6},                                                  # Evasion + Vigilant Senses
        9:  {"fortitude": 4, "class_dc": 4},                                                # Great Fort + Swashbuckler Expertise
        11: {"unarmored": 4, "light": 4},                                                   # Light Armor Expertise
        13: {"simple": 6, "martial": 6, "unarmed": 6, "reflex": 8},                         # Weapon Mastery + Improved Evasion
        15: {"will": 6, "fortitude": 6},                                                     # Keen Flair + Greater saves
        17: {"unarmored": 6, "light": 6},                                                   # Light Armor Mastery
        19: {"perception": 8},                                                               # Eternal Confidence
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
        3:  {"perception": 4},                                                               # Keen Recollection
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 6, "will": 6},                                                    # Vigilant Senses + Resolve
        9:  {"fortitude": 4, "class_dc": 4},                                                # Great Fortitude + Investigator Expertise
        11: {"unarmored": 4, "light": 4, "reflex": 6},                                      # Light Armor Expertise + Evasion
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"will": 8},                                                                     # Greater Resolve (Legendary Will)
        17: {"unarmored": 6, "light": 6},                                                   # Light Armor Mastery
        19: {"fortitude": 6, "reflex": 8},                                                   # Master Fort + Improved Evasion
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
        3:  {"will": 4},                                                                    # Iron Will
        5:  {"simple": 4, "unarmed": 4},                                                    # Weapon Expertise (alchemist bombs)
        7:  {"perception": 4, "fortitude": 6},                                               # Alertness + Juggernaut
        9:  {"class_dc": 4, "reflex": 6},                                                   # Alchemist Expertise + Evasion-equivalent
        11: {"unarmored": 4, "light": 4, "medium": 4},                                      # Medium Armor Expertise
        13: {"simple": 6, "unarmed": 6},                                                    # Weapon Mastery (bombs)
        15: {"will": 6},                                                                     # Greater Will
        17: {"unarmored": 6, "light": 6, "medium": 6, "fortitude": 8},                      # Armor Mastery + Legendary Fort
        19: {"perception": 6},                                                               # Master Perception
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
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise (bard weapons)
        7:  {"spell_attack": 4, "spell_dc": 4, "fortitude": 4},                             # Expert Spellcaster + Expert Fort
        9:  {"will": 6, "perception": 6},                                                    # Resolve + Great Perception
        11: {"unarmored": 4, "light": 4, "reflex": 6},                                      # Light Armor Expertise + Evasion
        13: {},                                                                              # Weapon Spec increase
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
        3:  {"fortitude": 4},                                                                # Magical Fortitude
        5:  {"perception": 4},                                                               # Alertness
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        11: {"reflex": 4, "simple": 4, "unarmed": 4},                                       # Lightning Reflexes + Weapon Expertise
        13: {"unarmored": 4},                                                                # Defensive Robes
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 6, "fortitude": 6},                                                     # Resolve (Will → Master) + Greater Fortitude
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # ORACLE (Player Core 2)
    # -------------------------------------------------------------------------
    "oracle": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, light=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"fortitude": 4},                                                                # Expert Fort
        5:  {"perception": 4},                                                               # Alertness
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"will": 6},                                                                     # Resolve
        11: {"reflex": 4, "simple": 4, "unarmed": 4, "unarmored": 4, "light": 4},           # Lightning Reflexes + Weapon Expertise + Light Armor Expertise
        13: {},                                                                              #
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8},                                                                     # Legendary Will
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
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, martial=2, unarmed=2, light=2, medium=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # Weapon Expertise
        7:  {"perception": 4, "spell_attack": 4, "spell_dc": 4},                            # Alertness + Studious Spells
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Magus Expertise
        11: {"unarmored": 4, "light": 4, "medium": 4, "will": 6},                           # Armor Expertise + Resolve
        13: {"simple": 6, "martial": 6, "unarmed": 6},                                      # Weapon Mastery
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"unarmored": 6, "light": 6, "medium": 6, "fortitude": 8},                      # Armor Mastery + Greater saves
        19: {"reflex": 6, "perception": 6},                                                  # Greater saves + Master Perception
    },
    # -------------------------------------------------------------------------
    # SUMMONER (Secrets of Magic)
    # -------------------------------------------------------------------------
    "summoner": {
        # L1 initial: perception=2, fort=4, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"reflex": 4},                                                                  # Lightning Reflexes
        5:  {"perception": 4, "simple": 4, "unarmed": 4},                                   # Alertness + Weapon Expertise
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"fortitude": 6, "class_dc": 4},                                                # Juggernaut + Summoner Expertise
        11: {"unarmored": 4, "will": 6},                                                     # Expert Unarmored + Resolve
        13: {"simple": 6, "unarmed": 6},                                                    # Weapon Mastery
        15: {"spell_attack": 6, "spell_dc": 6, "reflex": 6},                                # Master Spellcaster + Greater Ref
        17: {"fortitude": 8, "unarmored": 6},                                               # Legendary Fort + Armor Mastery
        19: {"will": 8, "spell_attack": 8, "spell_dc": 8},                                  # Legendary Spellcaster + Legendary Will
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
    # WIZARD (Player Core p.190) — AoN verified
    # -------------------------------------------------------------------------
    "wizard": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"reflex": 4},                                                                  # Expert Reflex (Arcane Resilience area)
        5:  {"fortitude": 4},                                                                # Expert Fort (Magical Fortitude)
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"perception": 4, "will": 6},                                                    # Alertness (Expert Perception) + Resolve (Master Will)
        11: {"simple": 4, "unarmed": 4, "unarmored": 4, "reflex": 4},                         # Weapon Expertise + Defensive Robes + Lightning Reflexes
        13: {},                                                                              # Weapon Specialization
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8, "fortitude": 6},                                                     # Legendary Will + Greater Fortitude (Master Fort)
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # CLERIC - Cloistered Cleric doctrine (Player Core p.130)
    # Warpriest has its own SUBCLASS_PROGRESSION entry
    # -------------------------------------------------------------------------
    "cleric": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"fortitude": 4},                                                                # 2nd Doctrine: Expert Fort
        5:  {"perception": 4},                                                               # Alertness
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # 3rd Doctrine: Expert Spellcaster
        9:  {"will": 6},                                                                     # Resolute Faith (Master Will)
        11: {"reflex": 4},                                                                   # Lightning Reflexes
        13: {"unarmored": 4},                                                                # Divine Defense (Expert Unarmored)
        15: {"spell_attack": 6, "spell_dc": 6},                                             # 5th Doctrine: Master Spellcaster
        17: {"will": 8, "fortitude": 6},                                                     # Greater Will + Greater Fort
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Final Doctrine: Legendary Spellcaster
    },
    # -------------------------------------------------------------------------
    # DRUID (Player Core p.134) — AoN verified
    # -------------------------------------------------------------------------
    "druid": {
        # L1 initial: perception=2, fort=2, ref=2, will=4, simple=2, unarmed=2, light=2, medium=2, unarmored=2, spell_attack=2, spell_dc=2
        3:  {"perception": 4, "fortitude": 4},                                              # Alertness (L3) + Great Fortitude
        5:  {"reflex": 4},                                                                   # Lightning Reflexes
        7:  {"spell_attack": 4, "spell_dc": 4},                                             # Expert Spellcaster
        9:  {"simple": 4, "unarmed": 4},                                                    # Weapon Expertise (simple + unarmed)
        11: {"will": 6, "unarmored": 4, "light": 4, "medium": 4},                           # Druid's Resolve (Master Will) + Armor Expertise
        13: {},                                                                              # Weapon Specialization
        15: {"spell_attack": 6, "spell_dc": 6},                                             # Master Spellcaster
        17: {"will": 8, "fortitude": 6},                                                     # Legendary Will + Greater Fortitude (Master)
        19: {"spell_attack": 8, "spell_dc": 8, "unarmored": 6, "light": 6, "medium": 6},   # Legendary Spellcaster + Armor Mastery
    },
    # -------------------------------------------------------------------------
    # KINETICIST (Rage of Elements) — AoN verified
    # -------------------------------------------------------------------------
    "kineticist": {
        # L1 initial: perception=2, fort=4, ref=4, will=2, simple=2, unarmed=2, light=2, unarmored=2, class_dc=2
        3:  {"will": 4},                                                                    # Will of the Elements
        5:  {"simple": 4, "unarmed": 4},                                                    # Weapon Expertise (kinetic blasts count as unarmed)
        7:  {"perception": 4, "class_dc": 4},                                               # Alertness + Kinetic Expertise
        9:  {"fortitude": 6},                                                                # Elemental Resistance / Juggernaut
        11: {"unarmored": 4, "light": 4, "reflex": 6},                                      # Armor Expertise + Evasion
        13: {"simple": 6, "unarmed": 6},                                                    # Weapon Mastery / Gate Mastery
        15: {"will": 6},                                                                     # Greater Will
        17: {"unarmored": 6, "light": 6, "fortitude": 8},                                   # Armor Mastery + Legendary Fort
        19: {"reflex": 8},                                                                   # Improved Evasion (Legendary Ref)
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
        # L1 initial overrides handled by SUBCLASS_MATRIX (Fort Expert, light/medium armor)
        3:  {"martial": 2},                                                                  # 2nd Doctrine: Trained martial weapons
        5:  {"perception": 4},                                                               # Alertness (shared)
        7:  {"simple": 4, "martial": 4, "unarmed": 4},                                      # 3rd Doctrine: Expert weapons
        9:  {"will": 6},                                                                     # Resolute Faith (shared)
        11: {"reflex": 4, "spell_attack": 4, "spell_dc": 4},                                # Lightning Reflexes (shared) + 4th Doctrine: Expert Spellcaster
        13: {"unarmored": 4, "light": 4, "medium": 4},                                      # Divine Defense (shared) + Warpriest armor upgrade
        15: {"spell_attack": 6, "spell_dc": 6},                                             # 5th Doctrine: Master Spellcaster
        17: {"will": 8, "fortitude": 6},                                                     # Greater Will + Greater Fort (shared)
        19: {"spell_attack": 8, "spell_dc": 8},                                             # Final Doctrine: Legendary Spellcaster
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

# Investigator: standard skill feats, but skill increase at EVERY level starting L2
investigator_prog = {
    1: {"class_feat": 1, "ancestry_feat": 1},
    2: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    3: {"general_feat": 1, "skill_increase": 1},
    4: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    5: {"ancestry_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    6: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    7: {"general_feat": 1, "skill_increase": 1},
    8: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    9: {"ancestry_feat": 1, "skill_increase": 1},
    10: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    11: {"general_feat": 1, "skill_increase": 1},
    12: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    13: {"ancestry_feat": 1, "skill_increase": 1},
    14: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    15: {"general_feat": 1, "skill_increase": 1, "ability_boosts": 4},
    16: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    17: {"ancestry_feat": 1, "skill_increase": 1},
    18: {"class_feat": 1, "skill_feat": 1, "skill_increase": 1},
    19: {"general_feat": 1, "skill_increase": 1},
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
    "commander":    {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "druid":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "exemplar":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "fighter":      {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 4, "simple": 4, "martial": 4, "advanced": 2, "perception": 4, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "guardian":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 2, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "gunslinger":   {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 4, "martial": 4, "advanced": 2, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "investigator": {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": investigator_prog},
    "inventor":     {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4}, "progression": base_prog},
    "kineticist":   {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "magus":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "monk":         {"base_proficiencies": {"unarmored": 4, "light": 0, "medium": 0, "heavy": 0, "unarmed": 4, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 4}, "progression": base_prog},
    "oracle":       {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "psychic":      {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "ranger":       {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 2, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 4, "reflex": 4, "class_dc": 2, "will": 2}, "progression": base_prog},
    "rogue":        {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 4, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": rogue_prog},
    "sorcerer":     {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "summoner":     {"base_proficiencies": {"unarmored": 2, "light": 0, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 0, "advanced": 0, "perception": 2, "fortitude": 4, "reflex": 2, "class_dc": 2, "will": 4, "spell_attack": 2, "spell_dc": 2}, "progression": base_prog},
    "swashbuckler": {"base_proficiencies": {"unarmored": 2, "light": 2, "medium": 0, "heavy": 0, "unarmed": 2, "simple": 2, "martial": 2, "advanced": 0, "perception": 2, "fortitude": 2, "reflex": 4, "class_dc": 2, "will": 4}, "progression": base_prog},
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
SPELL_ACTIONS = {
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

# Helper function to get action cost
def get_action_cost(name):
    return SPELL_ACTIONS.get(name.lower(), '')
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
