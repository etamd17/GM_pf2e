"""Pinned guards for the Player Core 2 class fixes (PF2e audit, 2026-06-10):
alchemist, barbarian, monk, investigator, oracle, swashbuckler — verified vs
Player Core 2 (remaster). Ranks: 2=Trained,4=Expert,6=Master,8=Legendary.
get_class_proficiency_at_level returns cumulative BUMPS; merged() adds the L1 base.
"""
from class_matrix import get_class_proficiency_at_level as P, CLASS_MATRIX


def merged(cls, lvl, key):
    base = CLASS_MATRIX[cls]['base_proficiencies'].get(key, 0)
    return P(cls, lvl).get(key, base)


def test_monk():  # PC2
    assert CLASS_MATRIX['monk']['base_proficiencies']['unarmed'] == 2   # L1 Trained (was wrongly Expert)
    assert merged('monk', 5, 'unarmed') == 4        # Expert Strikes 5th
    assert merged('monk', 13, 'unarmed') == 6       # Master Strikes 13th
    assert merged('monk', 20, 'unarmed') == 6       # no Legendary weapons
    assert merged('monk', 13, 'unarmored') == 6     # Graceful Mastery 13th
    assert merged('monk', 17, 'unarmored') == 8     # Graceful Legend 17th (Legendary)
    assert merged('monk', 17, 'class_dc') == 6      # Graceful Legend (class DC Master)


def test_alchemist():  # PC2 remaster
    assert merged('alchemist', 11, 'fortitude') == 6   # Chemical Hardiness (Master)
    assert merged('alchemist', 20, 'fortitude') == 6   # caps Master (no Legendary)
    assert merged('alchemist', 17, 'class_dc') == 6    # Alchemical Mastery (Master)
    assert merged('alchemist', 20, 'will') == 4        # Will caps Expert
    assert merged('alchemist', 20, 'perception') == 4  # Perception caps Expert
    assert merged('alchemist', 7, 'simple') == 4       # Weapon Expertise 7th (not 5th)


def test_barbarian():  # PC2
    assert merged('barbarian', 7, 'fortitude') == 6    # Juggernaut 7th (Master)
    assert merged('barbarian', 13, 'fortitude') == 8   # Greater Juggernaut 13th (Legendary)
    assert merged('barbarian', 19, 'class_dc') == 6    # Devastator 19th (Master)
    assert merged('barbarian', 20, 'reflex') == 4      # Reflex caps Expert (L9)
    assert merged('barbarian', 17, 'perception') == 6  # Perception Mastery 17th


def test_investigator():  # PC2
    assert merged('investigator', 7, 'perception') == 6   # Vigilant Senses 7th
    assert merged('investigator', 11, 'will') == 6        # Dogged Will 11th
    assert merged('investigator', 13, 'perception') == 8  # Incredible Senses 13th
    assert merged('investigator', 17, 'will') == 8        # Greater Dogged Will 17th
    assert merged('investigator', 19, 'class_dc') == 6    # Master Detective 19th
    assert merged('investigator', 20, 'reflex') == 6      # Savvy Reflexes (Master), no Legendary


def test_oracle():  # PC2
    assert merged('oracle', 7, 'will') == 6        # Mysterious Resolve 7th (Master)
    assert merged('oracle', 9, 'fortitude') == 4   # Magical Fortitude 9th
    assert merged('oracle', 11, 'simple') == 4     # Weapon Expertise 11th
    assert merged('oracle', 13, 'reflex') == 4     # Premonition's Reflexes 13th
    assert merged('oracle', 17, 'will') == 8       # Greater Mysterious Resolve 17th
    assert merged('oracle', 20, 'spell_dc') == 8   # Legendary Spellcaster 19th


def test_swashbuckler():  # PC2
    assert merged('swashbuckler', 3, 'fortitude') == 4    # Fortitude Expertise 3rd
    assert merged('swashbuckler', 7, 'reflex') == 6       # Confident Evasion 7th
    assert merged('swashbuckler', 13, 'reflex') == 8      # Assured Evasion 13th
    assert merged('swashbuckler', 17, 'will') == 6        # Reinforced Ego 17th
    assert merged('swashbuckler', 19, 'class_dc') == 6    # Eternal Confidence 19th
    assert merged('swashbuckler', 20, 'fortitude') == 4   # Fort caps Expert (L3)
    assert merged('swashbuckler', 20, 'perception') == 6  # Perception caps Master (L11)
