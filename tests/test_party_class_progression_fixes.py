"""Pinned regression guards for the party-class proficiency-progression fixes
(PF2e engine audit, 2026-06-09). Each assertion cites the rulebook level it
enforces. These complement the snapshot tests with explicit values so a future
class_matrix.py edit that re-introduces a bug fails with a clear message.

get_class_proficiency_at_level returns the CUMULATIVE proficiency BUMPS up to a
level (a key absent => the base_proficiencies value still applies). Proficiency
ranks: 2=Trained, 4=Expert, 6=Master, 8=Legendary.
"""
from class_matrix import get_class_proficiency_at_level as P


# --- Kineticist (Rage of Elements advancement table p.16) ------------------
def test_kineticist_saves():
    assert P('kineticist', 6).get('fortitude') is None        # Fort Expert (base) until L7
    assert P('kineticist', 7)['fortitude'] == 6               # Kinetic Durability 7th -> Master
    assert P('kineticist', 15)['fortitude'] == 8              # Greater Kinetic Durability 15th -> Legendary
    assert P('kineticist', 20).get('will', 4) == 4            # Will Expertise 3rd is the only Will bump (no Master)
    assert P('kineticist', 11)['reflex'] == 6                 # Kinetic Quickness 11th -> Master
    assert P('kineticist', 20)['reflex'] == 6                 # Reflex caps at Master (no phantom Legendary @19)


def test_kineticist_perception_weapons_armor_classdc():
    assert P('kineticist', 8).get('perception') is None       # Perception Expertise is 9th, not 7th
    assert P('kineticist', 9)['perception'] == 4
    assert P('kineticist', 10).get('simple') is None          # Weapon Expertise is 11th, not 5th
    assert P('kineticist', 11)['simple'] == 4 and P('kineticist', 11)['unarmed'] == 4
    assert P('kineticist', 20)['simple'] == 4                 # weapons cap at Expert (no phantom Master @13)
    assert P('kineticist', 17)['light'] == 4                  # Light Armor Expertise 13th; Mastery is 19th
    assert P('kineticist', 19)['light'] == 6 and P('kineticist', 19)['unarmored'] == 6
    assert P('kineticist', 7)['class_dc'] == 4                # Kinetic Expertise 7th
    assert P('kineticist', 15)['class_dc'] == 6               # Kinetic Mastery 15th
    assert P('kineticist', 19)['class_dc'] == 8               # Kinetic Legend 19th


# --- Druid (Player Core p.134 advancement table) ---------------------------
def test_druid_progression():
    assert P('druid', 3)['fortitude'] == 4                    # Fortitude Expertise 3rd (not 5th)
    assert P('druid', 11)['simple'] == 4 and P('druid', 11)['unarmed'] == 4   # Weapon Expertise 11th
    assert P('druid', 13)['medium'] == 4 and P('druid', 13)['light'] == 4     # Medium Armor Expertise 13th
    assert P('druid', 11)['will'] == 6                        # Wild Willpower 11th -> Master
    assert P('druid', 20)['will'] == 6                        # caps at Master (no phantom Legendary @17)
    assert P('druid', 20)['fortitude'] == 4                   # caps at Expert (no phantom Master @17)


# --- Cleric: Cloistered (Player Core p.130 doctrine table) ------------------
def test_cloistered_cleric_progression():
    assert P('cleric', 10).get('simple') is None             # 4th Doctrine weapon Expertise is 11th
    assert P('cleric', 11)['simple'] == 4 and P('cleric', 11)['unarmed'] == 4
    assert P('cleric', 20)['will'] == 6                       # Resolute Faith 9th -> Master, caps there
    assert P('cleric', 20).get('fortitude', 4) == 4          # 2nd Doctrine Fort Expert, caps there


# --- Cleric: Warpriest (Player Core p.130 doctrine table) ------------------
def test_warpriest_progression():
    W = 'Warpriest'
    assert P('cleric', 15, W)['fortitude'] == 6              # 5th Doctrine -> Fort Master (NOT spell)
    assert P('cleric', 14, W).get('fortitude') is None       # Fort Expert (base/First Doctrine) until L15
    assert P('cleric', 11, W)['spell_dc'] == 4               # 4th Doctrine -> spell Expert
    assert P('cleric', 19, W)['spell_dc'] == 6               # Final Doctrine -> spell Master
    assert P('cleric', 20, W)['spell_dc'] == 6               # caps at Master (Warpriest, not Legendary)
    assert P('cleric', 20, W)['will'] == 6                   # Resolute Faith Master, caps there


# --- Champion (unchanged by the audit — regression guard) ------------------
def test_champion_unchanged():
    assert P('champion', 9)['fortitude'] == 6                # Juggernaut (Master) @9
    assert P('champion', 5)['simple'] == 4                   # Weapon Expertise @5
    assert P('champion', 13)['reflex'] == 4                  # Greater Reflex @13
