"""Fixed 5e rules tables, pinned against the printed books."""
from systems.dnd5e.rules import tables as T


def test_xp_by_cr_spot_values():
    assert T.xp_for_cr('0') == 10 and T.xp_for_cr('1/4') == 50 and T.xp_for_cr('5') == 1800
    assert T.xp_for_cr('20') == 25000 and T.xp_for_cr('30') == 155000
    assert T.xp_for_cr(None) is None and T.xp_for_cr('Unknown') is None


def test_proficiency_bonus():
    assert [T.proficiency_bonus(l) for l in (1, 4, 5, 8, 9, 12, 13, 16, 17, 20)] == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    assert [T.cr_proficiency_bonus(c) for c in ('1/2', '4', '5', '8', '9', '17', '21', '30')] == [2, 2, 3, 3, 4, 6, 7, 9]


def test_2014_encounter_four_level3_vs_goblins():
    # DMG p.82 worked shape: 4 PCs L3 -> thresholds 300/600/900/1600;
    # 6 goblins (50 XP each = 300) x2 multiplier = 600 -> medium.
    r = T.encounter_difficulty('classic', [3, 3, 3, 3], ['1/4'] * 6)
    assert r['thresholds'] == {'easy': 300, 'medium': 600, 'hard': 900, 'deadly': 1600}
    assert r['base_xp'] == 300 and r['multiplier'] == 2 and r['adjusted_xp'] == 600
    assert r['rating'] == 'medium'


def test_2014_party_size_shifts_multiplier():
    assert T.encounter_multiplier_2014(1, 2) == 1.5      # small party: one step up
    assert T.encounter_multiplier_2014(1, 6) == 0.5      # large party: one step down
    assert T.encounter_multiplier_2014(15, 2) == 5
    assert T.encounter_multiplier_2014(3, 4) == 2


def test_2024_budget_no_multiplier():
    r = T.encounter_difficulty('one', [5, 5, 5, 5], ['3', '3'])
    assert r['thresholds'] == {'low': 2000, 'moderate': 3000, 'high': 4400}
    assert r['adjusted_xp'] == 1400 and r['multiplier'] == 1.0 and r['rating'] == 'trivial'
    r = T.encounter_difficulty('one', [5, 5, 5, 5], ['8'])
    assert r['rating'] == 'moderate'              # 3900: >= moderate 3000, < high 4400
