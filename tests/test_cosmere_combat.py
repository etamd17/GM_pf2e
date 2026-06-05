"""Cosmere combat engine (Phase 5) — rulebook ground truth for deflect-aware
damage, the injury death-spiral, the Plot Die, and the 4-phase turn queue.
"""
from __future__ import annotations

from systems.cosmere import combat as C


# -- deflect-aware damage (Ch.9) --------------------------------------------

def test_deflect_reduces_only_impact_keen_energy():
    assert C.apply_damage(20, 8, 'impact', deflect=2)[:2] == (14, 6)   # 8 - 2
    assert C.apply_damage(20, 8, 'keen', deflect=3)[:2] == (15, 5)
    assert C.apply_damage(20, 8, 'energy', deflect=2)[:2] == (14, 6)
    # spirit + vital bypass Deflect entirely
    assert C.apply_damage(20, 8, 'spirit', deflect=5)[:2] == (12, 8)
    assert C.apply_damage(20, 8, 'vital', deflect=5)[:2] == (12, 8)


def test_damage_floors_and_zero_flag():
    assert C.apply_damage(20, 3, 'impact', deflect=10)[1] == 0          # deflect > damage -> 0
    new, taken, zero = C.apply_damage(5, 9, 'impact', deflect=0)
    assert new == 0 and taken == 9 and zero is True                    # reduced to 0
    # already at 0 -> not a *fresh* drop
    assert C.apply_damage(0, 4, 'impact')[2] is False


# -- injuries / death spiral (Ch.9) -----------------------------------------

def test_injury_severity_table_boundaries():
    assert C.injury_severity(-6) == 'death'
    assert C.injury_severity(-5) == 'permanent' and C.injury_severity(0) == 'permanent'
    assert C.injury_severity(1) == 'vicious' and C.injury_severity(5) == 'vicious'
    assert C.injury_severity(6) == 'shallow' and C.injury_severity(15) == 'shallow'
    assert C.injury_severity(16) == 'flesh_wound'


def test_injury_roll_formula():
    # d20 + Deflect + mods − 5×existing injuries.
    r = C.injury_roll(10, deflect=2, existing_injuries=1)       # 10 + 2 − 5 = 7
    assert r['total'] == 7 and r['severity'] == 'shallow'
    assert C.injury_roll(1, existing_injuries=2)['severity'] == 'death'      # 1 − 10 = −9
    assert C.injury_roll(20, deflect=4)['severity'] == 'flesh_wound'         # 24
    assert C.injury_roll(3)['severity'] == 'vicious' and C.injury_roll(3)['is_death'] is False


def test_injury_effect_d8_table():
    assert C.injury_effect(1) == 'Exhausted [-1]' and C.injury_effect(3) == 'Exhausted [-2]'
    assert C.injury_effect(4) == 'Slowed' and C.injury_effect(6) == 'Disoriented'
    assert C.injury_effect(7) == 'Surprised' and C.injury_effect(8) == 'Can only use one hand'


def test_roll_injury_is_consistent():
    for _ in range(50):
        r = C.roll_injury(deflect=2, existing_injuries=0)
        assert 1 <= r['d20'] <= 20 and r['severity'] in C.INJURY_DURATION
        assert ('effect' in r) == (r['severity'] != 'death')


# -- Plot Die (Ch.0/13) -----------------------------------------------------

def test_plot_die_faces():
    faces = [C.plot_die(i) for i in range(6)]
    types = [f['type'] for f in faces]
    assert types.count('blank') == 2 and types.count('opportunity') == 2 and types.count('complication') == 2
    # Complication faces add +2 / +4; everything else adds 0.
    comp_bonuses = sorted(f['bonus'] for f in faces if f['type'] == 'complication')
    assert comp_bonuses == [2, 4]
    assert all(f['bonus'] == 0 for f in faces if f['type'] != 'complication')


def test_plot_die_result_has_label_and_spend():
    for i in range(6):
        r = C.plot_die_result(i)
        assert r['type'] in ('blank', 'opportunity', 'complication') and r['label']
        assert isinstance(r['spend'], list)
        if r['type'] == 'opportunity':
            assert any('focus' in s for s in r['spend'])      # Collect Yourself
        if r['type'] == 'complication':
            assert '+' in r['label']                          # shows the +2/+4 bonus
        if r['type'] == 'blank':
            assert r['spend'] == []


# -- 4-phase turn queue (Ch.10) ---------------------------------------------

def test_fast_slow_actions():
    assert C.fast_slow_actions('fast') == 2 and C.fast_slow_actions('slow') == 3
    assert C.turn_phase(True, 'fast') == 'fast_pc' and C.turn_phase(False, 'slow') == 'slow_npc'


def test_order_is_fast_pc_then_npc_then_slow_then_speed():
    combatants = [
        {'name': 'SlowNPC', 'is_pc': False, 'choice': 'slow', 'speed': 3, 'tiebreak': 10},
        {'name': 'FastPC-lo', 'is_pc': True, 'choice': 'fast', 'speed': 1, 'tiebreak': 5},
        {'name': 'FastPC-hi', 'is_pc': True, 'choice': 'fast', 'speed': 4, 'tiebreak': 5},
        {'name': 'FastNPC', 'is_pc': False, 'choice': 'fast', 'speed': 5, 'tiebreak': 1},
        {'name': 'SlowPC', 'is_pc': True, 'choice': 'slow', 'speed': 2, 'tiebreak': 1},
    ]
    order = [c['name'] for c in C.order_combatants(combatants)]
    assert order == ['FastPC-hi', 'FastPC-lo', 'FastNPC', 'SlowPC', 'SlowNPC']
