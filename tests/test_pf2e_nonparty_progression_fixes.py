"""Pinned guards for the non-party-class proficiency fixes + ABP (PF2e engine
audit, 2026-06-09), verified against Player Core / GM Core (remaster). Ranks:
2=Trained, 4=Expert, 6=Master, 8=Legendary. get_class_proficiency_at_level
returns cumulative BUMPS (absent key => base proficiency still applies).
"""
from class_matrix import get_class_proficiency_at_level as P, get_abp_bonus


def test_fighter_class_dc_at_11():
    assert P('fighter', 9).get('class_dc') is None   # Fighter Expertise is 11th
    assert P('fighter', 11)['class_dc'] == 4


def test_ranger():  # Player Core p.152
    assert P('ranger', 7)['reflex'] == 6             # Natural Reflexes 7th -> Master
    assert P('ranger', 11)['fortitude'] == 6         # Warden's Endurance 11th -> Master
    assert P('ranger', 15)['reflex'] == 8            # Greater Natural Reflexes 15th -> Legendary
    assert P('ranger', 15)['perception'] == 8        # Perception Legend 15th
    assert P('ranger', 17)['class_dc'] == 6          # Masterful Hunter 17th -> Master
    assert P('ranger', 17).get('medium', 4) == 4     # armor Mastery deferred to 19th
    assert P('ranger', 19)['medium'] == 6            # Medium Armor Mastery 19th


def test_rogue():  # Player Core p.156
    assert P('rogue', 9).get('class_dc') is None     # Rogue Expertise is 11th
    assert P('rogue', 11)['class_dc'] == 4
    assert P('rogue', 13)['perception'] == 8         # Incredible Senses 13th -> Legendary (not 19th)
    assert P('rogue', 13)['reflex'] == 8             # Improved Rogue Reflexes 13th
    assert P('rogue', 13)['unarmored'] == 4          # Light Armor Expertise 13th (not 11th)
    assert P('rogue', 15).get('will') is None        # Slippery Mind is 17th, not 15th
    assert P('rogue', 17)['will'] == 6
    assert P('rogue', 20)['fortitude'] == 4          # Fort caps at Expert (no phantom Master)


def test_bard():  # Player Core p.100
    assert P('bard', 7).get('fortitude') is None     # Fortitude Expertise is 9th, not 7th
    assert P('bard', 9)['fortitude'] == 4
    assert P('bard', 11)['simple'] == 4              # Bard Weapon Expertise 11th (not 5th)
    assert P('bard', 11)['perception'] == 6          # Vigilant Senses 11th -> Master (not 9th)
    assert P('bard', 13)['light'] == 4               # Light Armor Expertise 13th (not 11th)
    assert P('bard', 20)['reflex'] == 4              # Bard has no Reflex Master (caps Expert from L3)


def test_abp_perception_potency():  # GM Core p.83
    assert get_abp_bonus(6, 'perception_potency') == 0
    assert get_abp_bonus(7, 'perception_potency') == 1   # +1 at 7 (not 9)
    assert get_abp_bonus(13, 'perception_potency') == 2  # +2 at 13 (not 15)
    assert get_abp_bonus(19, 'perception_potency') == 3  # +3 at 19 (new)
    # other potency types unchanged
    assert get_abp_bonus(10, 'attack_potency') == 2
    assert get_abp_bonus(18, 'defense_potency') == 3
