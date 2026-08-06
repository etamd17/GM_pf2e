"""Official Paizo PF2e sheet PDF -> Pathbuilder build -> Character.

The importer reads the sheet's attribute mods + proficiency-rank checkboxes (the
deterministic INPUTS to PF2e stat math) and lets Character re-derive AC / saves /
skills to the sheet's exact numbers -- so an imported PC stays fully interactive
rather than a frozen snapshot. Driven by a synthetic AcroForm field dict (the
real field names) so it needs no committed PDF. The `_kyle_fields` subset is a
real L10 Druid whose sheet states AC 26 / HP 136 / Will 19 / Medicine 21; the
derivation must reproduce those.
"""
import os
import re
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
os.environ.setdefault('GM_PASSWORD', '')

import pf2e_pdf_import as P


def _kyle_fields():
    f = {
        'Character Name': 'KyleTest', 'LEVEL': '10', 'Class': 'Druid',
        'Ancestry': 'Awakened Animal', 'Heritage and Traits': 'Climbing Animal',
        'Background': 'Stargazer (Nature)',
        'STRENGTH STAT': '1', 'DEXTERITY STAT': '4', 'CONSTITUTION STAT': '4',
        'INTELLIGENCE STAT': '2', 'WISDOM STAT': '5', 'CHARISMA STAT': '0',
        'AC': '26', 'MAXIMUM HIT POINTS': '136',
        'FORTITUDE': '18', 'REFLEX': '18', 'WILL': '19', 'PERCEPTION': '19',
        'MEDICINE': '21', 'RELIGION': '21', 'NATURE': '17', 'ACROBATICS': '16',
        'SPELL SAVE DC': '29', 'CLASS DC': '27', 'CLASS DC PROFICIENCY': '12', 'CLASS DC KEY': '5',
        'SPELL ATTACK KEY': '5', 'SPEED': '20',
        'WORN 1': 'Padded Armor',
        # proficiency-rank checkboxes
        'FORTITUDE EXPERT': '/Yes', 'REFLEX EXPERT': '/Yes', 'WILL EXPERT': '/Yes',
        'PERCEPTION EXPERT': '/Yes', 'LIGHT TRAINED': '/Yes', 'UNARMORED TRAINED': '/Yes',
        'SIMPLE WEAPONS TRAINED': '/Yes', 'UNARMED TRAINED': '/Yes',
        'MEDICINE MASTER': '/Yes', 'RELIGION MASTER': '/Yes', 'NATURE TRAINED': '/Yes',
        'ACROBATICS TRAINED': '/Yes', 'SPELL ATTACK EXPERT': '/Yes', 'SPELL SAVE DC EXPERT': '/Yes',
        'PRIMAL': '/Yes',
        # a strike + a lore + feats + a spell
        'MELEE STRIKE 1': 'Staff', 'MELEE STRIKE 1 ATTACK BONUS': '13',
        'MELEE STRIKE 1 DAMAGE': '1d4+1 B', 'MELEE STRIKE 1 TRAITS AND NOTES': 'Two-Hand d8',
        'LORE CATAGORY 1': 'Astronomy', 'LORE1 TRAINED': '/Yes',
        'ANCESTRY FEAT': 'Natural Senses', 'CLASS FEAT 1-1': 'Beastmaster Dedication',
        'SKILL FEAT 3-1': 'Toughness',
        'CANTRIP NAME 1': 'Electric Arc', 'SPELL 1': 'Heal', 'SPELL RANK 1': '1',
        'SPELLS PER DAY 1': '3',
    }
    return f


def test_parse_maps_ranks_abilities_and_strike():
    build, auth = P.parse_pf2e_pdf(_kyle_fields())
    assert build['abilities'] == {'str': 1, 'dex': 4, 'con': 4, 'int': 2, 'wis': 5, 'cha': 0}
    # proficiency rank checkboxes -> PB rank numbers (T2/E4/M6/L8)
    assert build['proficiencies']['medicine'] == 6 and build['proficiencies']['religion'] == 6
    assert build['proficiencies']['fortitude'] == 4 and build['proficiencies']['nature'] == 2
    assert build['proficiencies'].get('castingPrimal') == 4
    # strike parsing "1d4+1 B" -> die/bonus/type, attack carried
    w = next(w for w in build['weapons'] if w['name'] == 'Staff')
    assert w['die'] == 'd4' and w['damageBonus'] == 1 and w['damageType'] == 'B' and w['attack'] == 13
    # feats carry their level; lore + spellcaster present
    assert ['Toughness', None, 'Skill Feat', 3] in build['feats']
    assert build['lores'] == [['Astronomy', 2]]
    assert build['spellCasters'] and build['spellCasters'][0]['magicTradition'] == 'primal'
    assert auth['ac'] == 26 and auth['hp'] == 136


def test_derivation_reproduces_the_sheet_numbers():
    import app
    build, play = P.build_from_pdf(_kyle_fields(), character_factory=app.Character)
    pc = app.Character({'success': True, 'build': build})

    def num(x):
        return int(re.sub(r'[^0-9-]', '', str(x)) or 0)

    assert pc.ac == 26, ('AC', pc.ac)
    assert pc.fort == 18 and pc.ref == 18 and pc.will == 19, (pc.fort, pc.ref, pc.will)
    assert pc.perception == 19, pc.perception
    assert pc.hp == 136, ('HP not pinned', pc.hp)
    assert pc.spell_dc == 29, pc.spell_dc
    skills = {s['name'].lower(): num(s['total']) for s in pc.skills}
    assert skills['medicine'] == 21 and skills['religion'] == 21, skills
    assert skills.get('nature') == 17 and skills.get('acrobatics') == 16
    # lore came through as a skill; staff strike present
    assert 'lore: astronomy' in skills
    assert any(a.get('name') == 'Staff' for a in pc.attacks)


def test_play_state_seeds_current_hp():
    build, play = P.build_from_pdf(_kyle_fields(), character_factory=None)
    assert play['current_hp'] == 136
