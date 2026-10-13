"""Golden characters for the D&D 5e engine, every number checked by hand
against the PHB (2014) / XPHB (2024). If one of these changes, the engine or
the data changed -- work out which before touching the expectation."""
import pytest

from systems.dnd5e import content
from systems.dnd5e.rules.character import compute

pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')


def _doc(build, state=None):
    return {'name': 'Golden', 'build': build, 'state': state or {}}


def test_fighter_1_2024_human_soldier():
    ch = {
        'race:skill:any': ['perception'], 'race:feat:cat0': ['alert|xphb'], 'race:size': ['M'],
        'bg:ability#alt': [0], 'bg:ability#0': {'str': 2, 'con': 1},
        'bg:tool:anyGamingSet': ['dice set'], 'cls:fighter|xphb:skill:c0': ['acrobatics', 'survival'],
        'featprog:fighter|xphb:Fighting Style:0': ['defense|xphb'],
        'mastery:fighter|xphb': ['longsword', 'greatsword', 'longbow'],
        'origin:language:anyStandard': ['dwarvish', 'elvish'],
    }
    s = compute(_doc({
        'edition': 'one', 'race': 'human|xphb', 'background': 'soldier|xphb', 'ability_method': 'standard',
        'base_scores': {'str': 15, 'dex': 14, 'con': 13, 'int': 8, 'wis': 12, 'cha': 10},
        'levels': [{'class': 'fighter|xphb'}], 'choices': ch,
    }, {'inventory': [{'id': 'chain mail|xphb', 'equipped': True},
                      {'id': 'longsword|xphb', 'equipped': True},
                      {'id': 'shield|xphb', 'equipped': True}]}))
    assert s['errors'] == [] and s['pending'] == []
    assert s['abilities']['str']['score'] == 17 and s['abilities']['con']['score'] == 14
    assert s['hp_max'] == 12                      # d10 max + CON 2
    assert s['ac']['value'] == 19                 # chain 16 + shield 2 + Defense 1
    assert s['initiative'] == 4                   # DEX 2 + Alert (2024: + PB)
    sword = s['attacks'][0]
    # shield on the other arm: no two-handed (versatile) use
    assert (sword['to_hit'], sword['damage'], sword['versatile'], sword['mastery']) == (5, '1d8+3', None, 'Sap')
    assert s['abilities']['str']['save'] == 5 and s['abilities']['dex']['save'] == 2
    assert s['passive']['perception'] == 13
    assert {r['key']: r['max'] for r in s['resources']} == {'second_wind': 2}
    assert set(s['proficiencies']['languages']) == {'common', 'dwarvish', 'elvish'}
    assert {f['name'] for f in s['feats']} == {'Alert', 'Savage Attacker', 'Defense'}


def test_wizard_5_2014_high_elf_point_buy():
    s = compute(_doc({
        'edition': 'classic', 'race': 'elf (high)|phb', 'background': 'sage|phb', 'ability_method': 'pointbuy',
        'base_scores': {'str': 8, 'dex': 14, 'con': 14, 'int': 15, 'wis': 12, 'cha': 8},
        'levels': [{'class': 'wizard|phb'}] + [{'class': 'wizard|phb', 'hp': None}] * 4,
        'choices': {'asi:wizard|phb:4': {'asi': {'int': 2}}},
    }))
    assert s['errors'] == []
    assert s['abilities']['int']['score'] == 18 and s['abilities']['dex']['score'] == 16
    assert s['hp_max'] == 32                      # 6+2, then 4 x (4+2)
    w = s['spellcasting']['casters'][0]
    assert (w['dc'], w['attack'], w['cantrips_max'], w['prepared_max'], w['known_max']) == (15, 7, 4, 9, 14)
    assert s['spellcasting']['slots'] == {'1': 4, '2': 3, '3': 2}
    # Tasha's optional class features are opt-in
    assert 'Cantrip Formulas' not in {f['name'] for f in s['features']}


def test_paladin6_warlock5_2014_multiclass_slots_and_pact():
    lv = [{'class': 'paladin|phb'}] * 6 + [{'class': 'warlock|phb'}] * 5
    s = compute(_doc({
        'edition': 'classic', 'race': 'human|phb', 'background': 'acolyte|phb', 'ability_method': 'standard',
        'base_scores': {'str': 15, 'dex': 10, 'con': 14, 'int': 8, 'wis': 12, 'cha': 13}, 'levels': lv,
        'choices': {'subclass:paladin|phb': ['oath of devotion|phb|paladin|phb'],
                    'subclass:warlock|phb': ['the fiend|phb|warlock|phb']},
    }))
    assert s['errors'] == []
    assert s['level'] == 11 and s['proficiency_bonus'] == 4
    assert s['hp_max'] == 87                      # 12 + 5x8 + 5x7
    sc = s['spellcasting']
    assert sc['slots'] == {'1': 4, '2': 2}        # caster level 3 (paladin 6 // 2, 2014 rounds down)
    assert sc['pact'] == {'count': 2, 'level': 3}
    pal = next(c for c in sc['casters'] if c['name'] == 'Paladin')
    assert pal['prepared_max'] == 5 and pal['max_spell_level'] == 2
    always = {a['name'] for a in sc['always']}
    assert {'Sanctuary', 'Zone of Truth'} <= always
    assert 'Fireball' not in always               # Fiend list is EXPANDED, not known
    assert any(c['id'] == 'opt:warlock|phb:Eldritch Invocations' and c['count'] == 3 for c in s['choices'])


def test_monk_20_2024_capstone_and_unarmored():
    s = compute(_doc({
        'edition': 'one', 'race': 'dwarf|xphb', 'background': 'hermit|xphb', 'ability_method': 'standard',
        'base_scores': {'str': 10, 'dex': 15, 'con': 13, 'int': 8, 'wis': 14, 'cha': 12},
        'levels': [{'class': 'monk|xphb'}] * 20,
        'choices': {'subclass:monk|xphb': ['warrior of the open hand|xphb|monk|xphb'],
                    'bg:ability#alt': [0], 'bg:ability#0': {'wis': 2, 'con': 1},
                    'asi:monk|xphb:4': {'asi': {'dex': 2}}, 'asi:monk|xphb:8': {'asi': {'dex': 2}},
                    'asi:monk|xphb:12': {'asi': {'wis': 2}}, 'asi:monk|xphb:16': {'asi': {'wis': 2}}},
    }, {'inventory': [{'id': 'quarterstaff|xphb', 'equipped': True}]}))
    assert s['errors'] == []
    assert s['abilities']['dex']['score'] == 23 and s['abilities']['wis']['score'] == 24  # Body and Mind
    assert s['hp_max'] == 163                     # 10 + 19x7 + Dwarven Toughness 20
    assert s['ac']['value'] == 23                 # 10 + DEX 6 + WIS 7
    assert s['speed']['walk'] == 60
    assert all(a['save_proficient'] for a in s['abilities'].values())  # Disciplined Survivor
    ua = next(a for a in s['attacks'] if a['name'] == 'Unarmed Strike')
    assert ua['damage'] == '1d12+6' and ua['to_hit'] == 12
    # Focus from the class table; the rest detected from feature text:
    # Stonecunning = PB (6), Uncanny Metabolism = 1, Wholeness of Body = WIS mod (7).
    assert {r['key']: r['max'] for r in s['resources']} == {
        'focus': 20, 'feature:stonecunning': 6, 'feature:uncanny metabolism': 1,
        'feature:wholeness of body': 7}


def test_invalid_picks_are_errors_not_silently_dropped():
    s = compute(_doc({
        'edition': 'one', 'race': 'human|xphb', 'background': 'soldier|xphb', 'ability_method': 'standard',
        'base_scores': {'str': 15, 'dex': 15, 'con': 13, 'int': 8, 'wis': 12, 'cha': 10},
        'levels': [{'class': 'fighter|xphb'}],
        'choices': {'cls:fighter|xphb:skill:c0': ['arcana', 'athletics']},
    }))
    assert any('Standard array' in e for e in s['errors'])
    assert any("'arcana' is not an option" in e for e in s['errors'])


def test_asi_must_be_plus2_or_plus1_plus1():
    s = compute(_doc({
        'edition': 'classic', 'race': 'human|phb', 'background': 'acolyte|phb', 'ability_method': 'manual',
        'base_scores': {a: 10 for a in ('str', 'dex', 'con', 'int', 'wis', 'cha')},
        'levels': [{'class': 'fighter|phb'}] * 4,
        'choices': {'asi:fighter|phb:4': {'asi': {'str': 3}}},
    }))
    assert any('+2 to one score or +1 to two' in e for e in s['errors'])


def test_every_class_levels_to_20_without_engine_errors():
    """Smoke: every core class in both editions computes 1-20 cleanly."""
    for src in ('PHB', 'XPHB'):
        for name in ('Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter', 'Monk', 'Paladin', 'Ranger',
                     'Rogue', 'Sorcerer', 'Warlock', 'Wizard'):
            cid = f'{name}|{src}'.lower()
            for n in (1, 5, 11, 20):
                s = compute(_doc({
                    'edition': 'one' if src == 'XPHB' else 'classic', 'ability_method': 'manual',
                    'base_scores': {a: 13 for a in ('str', 'dex', 'con', 'int', 'wis', 'cha')},
                    'levels': [{'class': cid}] * n, 'choices': {},
                }))
                assert s['errors'] == [], (cid, n, s['errors'])
                assert s['level'] == n
