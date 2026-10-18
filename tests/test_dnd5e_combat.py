"""D&D 5e combat rules (systems/dnd5e/combat.py) -- pure, no app import."""
import random

from systems.dnd5e import combat as K
from systems.dnd5e import content, dice


def test_temp_hp_absorbs_first_and_does_not_stack():
    v = K.new_vitals(20)
    K.set_temp_hp(v, 5)
    K.set_temp_hp(v, 3)               # lower: ignored
    assert v['temp_hp'] == 5
    K.apply_damage(v, 7, is_pc=True)
    assert (v['temp_hp'], v['hp']) == (0, 18)


def test_resistance_immunity_vulnerability():
    assert K.adjust_for_defenses(11, 'fire', resist=['fire']) == (5, 'resisted')
    assert K.adjust_for_defenses(11, 'fire', immune=['fire'], resist=['fire'])[0] == 0
    assert K.adjust_for_defenses(10, 'cold', vulnerable=['cold']) == (20, 'vulnerable')
    assert K.adjust_for_defenses(11, 'cold', resist=['cold'], vulnerable=['cold'])[0] == 10   # halve, then double


def test_concentration_dc_is_half_damage_min_10_capped_30_in_2024():
    v = K.new_vitals(200)
    v['concentration'] = 'Bless'
    ev = K.apply_damage(v, 14, is_pc=True)
    assert [e['dc'] for e in ev if e['type'] == 'concentration_check'] == [10]
    ev = K.apply_damage(v, 70, is_pc=True, edition='one')
    assert [e['dc'] for e in ev if e['type'] == 'concentration_check'] == [30]
    ev = K.apply_damage(v, 70, is_pc=True, edition='classic')
    assert [e['dc'] for e in ev if e['type'] == 'concentration_check'] == [35]


def test_pc_drops_to_zero_then_death_saves():
    v = K.new_vitals(10)
    v['concentration'] = 'Bless'
    ev = K.apply_damage(v, 12, is_pc=True)
    assert v['hp'] == 0 and 'unconscious' in v['conditions'] and not v['dead']
    assert v['concentration'] is None and any(e['type'] == 'dying' for e in ev)
    K.death_save(v, 12)
    K.death_save(v, 3)
    assert v['death_saves'] == {'success': 1, 'failure': 1}
    K.apply_damage(v, 1, is_pc=True, crit=True)       # crit at 0 HP = 2 failures -> 3 -> dead
    assert v['dead']


def test_massive_damage_kills_outright():
    v = K.new_vitals(10)
    K.apply_damage(v, 20, is_pc=True)                 # 10 overflow >= max 10
    assert v['dead']


def test_nat20_death_save_wakes_and_heal_clears_dying():
    v = K.new_vitals(10)
    K.apply_damage(v, 10, is_pc=True)
    K.death_save(v, 20)
    assert v['hp'] == 1 and 'unconscious' not in v['conditions']
    K.apply_damage(v, 5, is_pc=True)
    K.death_save(v, 1)                                # nat 1 = two failures
    assert v['death_saves']['failure'] == 2
    K.heal(v, 4)
    assert v['hp'] == 4 and v['death_saves'] == {'success': 0, 'failure': 0}


def test_three_successes_stabilize():
    v = K.new_vitals(10)
    K.apply_damage(v, 10, is_pc=True)
    for _ in range(3):
        K.death_save(v, 15)
    assert v['stable'] and not v['dead'] and v['hp'] == 0


def test_monster_at_zero_is_defeated():
    v = K.new_vitals(7)
    K.apply_damage(v, 9, is_pc=False)
    assert v['dead'] and v['hp'] == 0


def test_condition_duration_ticks_on_the_right_turn():
    enc = K.new_encounter()
    a = K.new_quick_combatant('A', hp=10, ac=10)
    b = K.new_quick_combatant('B', hp=10, ac=10)
    a['initiative'], b['initiative'] = 20, 10
    enc['combatants'] = [a, b]

    def vit(c):
        return c
    K.start(enc)
    # B is frightened until the end of A's next turn (A is the source's clock)
    K.set_condition(b, 'frightened', rounds=1, until='end', source_cid=a['cid'])
    K.advance(enc, vit)        # end of A's turn: 1 -> 0, expires
    assert 'frightened' not in b['conditions']
    K.set_condition(a, 'poisoned', rounds=2, until='end')
    K.advance(enc, vit)        # end of B's turn: A's own clock untouched
    assert a['conditions']['poisoned']['rounds'] == 2
    K.advance(enc, vit)        # end of A's turn -> 1
    K.advance(enc, vit)
    K.advance(enc, vit)        # end of A's turn -> 0
    assert 'poisoned' not in a['conditions']
    assert enc['round'] == 3


def test_turn_order_ties_and_skipping_the_dead():
    enc = K.new_encounter()
    cs = [K.new_quick_combatant(n, hp=5, ac=10) for n in 'ABC']
    for c, i, d in zip(cs, (15, 15, 3), (12, 16, 10)):
        c['initiative'], c['dex'] = i, d
    enc['combatants'] = list(cs)
    K.start(enc)
    assert [c['name'] for c in enc['combatants']] == ['B', 'A', 'C']   # tie -> higher DEX first
    K.apply_damage(cs[0], 99, is_pc=False)                               # A dies
    K.advance(enc, lambda c: c)
    assert enc['combatants'][enc['turn']]['name'] == 'C'


def test_legendary_actions_reset_on_own_turn_and_recharge_rolls():
    mon = {'name': 'Dragon', 'hp': {'average': 100}, 'ac': [18], 'dex': 10, 'cr': '10',
           'legendary': [{'name': 'Tail'}], 'trait': [{'name': 'Legendary Resistance (3/Day)'}],
           'action': [{'name': 'Fire Breath {@recharge 5}'}]}
    c = K.new_monster_combatant(mon, 'dragon|mm')
    assert c['legendary'] == {'max': 3, 'used': 0} and c['legendary_resistance']['max'] == 3
    assert c['recharge'] == {'Fire Breath': {'min': 5, 'ready': True}}
    enc = K.new_encounter()
    other = K.new_quick_combatant('Hero', hp=10, ac=10)
    c['initiative'], other['initiative'] = 10, 20
    enc['combatants'] = [c, other]
    K.start(enc)
    c['legendary']['used'] = 2
    c['recharge']['Fire Breath']['ready'] = False
    ev = K.advance(enc, lambda x: x, rng=random.Random(4))
    assert c['legendary']['used'] == 0
    assert any('Fire Breath' in e.get('text', '') for e in ev)


def test_remove_keeps_the_current_creature():
    enc = K.new_encounter()
    cs = [K.new_quick_combatant(n, hp=5, ac=10) for n in 'ABCD']
    for i, c in enumerate(cs):
        c['initiative'] = 20 - i
    enc['combatants'] = list(cs)
    K.start(enc)
    K.advance(enc, lambda c: c)
    K.advance(enc, lambda c: c)          # C's turn
    K.remove(enc, cs[0]['cid'])          # remove A (before C)
    assert enc['combatants'][enc['turn']]['name'] == 'C'
    K.remove(enc, cs[2]['cid'])          # remove C itself -> D's turn
    assert enc['combatants'][enc['turn']]['name'] == 'D'


def test_group_initiative_shares_one_roll():
    enc = K.new_encounter()
    a = K.new_quick_combatant('Gob 1', hp=5, ac=10)
    b = K.new_quick_combatant('Gob 2', hp=5, ac=10)
    a['group'] = b['group'] = 'goblin|mm'
    enc['combatants'] = [a, b]
    K.roll_initiative(enc, group=True, rng=random.Random(9))
    assert a['initiative'] == b['initiative'] is not None


def test_monster_combatant_from_real_data():
    if not content.available():
        return
    m = content.get('monster', 'goblin warrior|xmm')
    c = K.new_monster_combatant(m, 'goblin warrior|xmm')
    assert (c['hp'], c['ac'], c['init_bonus']) == (10, 15, 2)       # XMM: Initiative +2
    # XMM statblocks with initiative proficiency: DEX mod + N x PB(CR)
    dragon = K.new_monster_combatant(content.get('monster', 'adult red dragon|xmm'), 'x')
    lich = K.new_monster_combatant(content.get('monster', 'lich|xmm'), 'y')
    assert dragon['init_bonus'] == 12 and lich['init_bonus'] == 17       # as printed in the 2024 MM
    assert dragon['legendary']['max'] == 3 and dragon['legendary_resistance']['max'] >= 3


def test_dice():
    rng = random.Random(1)
    r = dice.roll('4d6kh3', rng)
    assert len(r['rolls']) == 4 and sum(1 for x in r['rolls'] if x[2]) == 3
    assert 3 <= r['total'] <= 18
    assert dice.average('3d6') == 10 and dice.average('2d10+4') == 15
    for bad in ('rm -rf', '1d20; x', '1000d1000'):
        try:
            dice.roll(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    adv = dice.d20(3, 'adv', random.Random(2))
    assert adv['natural'] == max(adv['dice']) and adv['total'] == adv['natural'] + 3
