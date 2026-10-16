"""PDF character-sheet import.

The sheets here are synthetic: a fillable PDF is generated in the test with
the field names D&D Beyond's "Export to PDF" uses (values sit on page widget
annotations, with no AcroForm, exactly like D&D Beyond's file). No real
player's sheet is committed."""
import io
import os

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from systems.dnd5e import content as _content  # noqa: E402
pytestmark = pytest.mark.skipif(not _content.available(), reason='dnd5e.db not built')

FIGHTER = {
    'CharacterName': 'Brannoc Vale',
    'CLASS  LEVEL': 'Fighter 3',
    'RACE': 'Human',
    'BACKGROUND': 'Soldier',
    'STR': '17', 'DEX': '12', 'CON': '15', 'INT': '10', 'WIS': '13', 'CHA': '8',
    'ProfBonus': '+2',
    'AcrobaticsProf': 'P', 'Acrobatics': '+3',
    'AthleticsProf': 'P', 'Athletics': '+5',
    'IntimidationProf': 'P', 'Intimidation': '+1',
    'PerceptionProf': 'P', 'Perception': '+3',
    'SurvivalProf': 'P', 'Survival': '+3',
    'ST Strength': '+5', 'ST Constitution': '+4', 'ST Dexterity': '+1',
    'MaxHP': '31', 'CurrentHP': '22', 'AC': '19',
    'ProficienciesLang': '=== ARMOR === \nHeavy Armor, Light Armor, Medium Armor, Shields \n\n'
                         '=== TOOLS === \nDice Set \n\n=== LANGUAGES === \nCommon, Dwarvish, Elvish',
    'FeaturesTraits1': '=== FIGHTER FEATURES === \n\n* Fighting Style • PHB-2024 91 \n'
                       'You gain a Fighting Style feat. \n\n   | Defense •  \n'
                       'While wearing armor, you gain +1 AC. \n\n* Second Wind • PHB-2024 91 \n'
                       'Regain hit points. \n\n* Improved Critical • PHB-2024 96 \n'
                       '* Remarkable Athlete • PHB-2024 96 \n\n=== FEATS ===\n\n* Savage Attacker • PHB-2024 \n'
                       '* Alert • PHB-2024 \n',
    'Eq Name0': 'Chain Mail', 'Eq Qty0': '1',
    'Eq Name1': 'Shield', 'Eq Qty1': '1',
    'Eq Name2': 'Longsword', 'Eq Qty2': '1',
    'Wpn Name': 'Longsword',
    'GP': '15',
    'ALIGNMENT': 'Lawful Good',
    'Backstory': 'Served on the northern wall.',
}


def make_pdf(fields: dict, acroform: bool = False) -> bytes:
    """A one-page PDF whose widgets carry these values."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject

    w = PdfWriter()
    page = w.add_blank_page(612, 792)
    annots = ArrayObject()
    for i, (k, v) in enumerate(fields.items()):
        a = DictionaryObject({
            NameObject('/Type'): NameObject('/Annot'),
            NameObject('/Subtype'): NameObject('/Widget'),
            NameObject('/FT'): NameObject('/Tx'),
            NameObject('/T'): TextStringObject(k),
            NameObject('/V'): TextStringObject(v),
            NameObject('/Rect'): ArrayObject([FloatObject(10), FloatObject(10 + i), FloatObject(50), FloatObject(20 + i)]),
        })
        annots.append(w._add_object(a))
    page[NameObject('/Annots')] = annots
    if acroform:
        w._root_object[NameObject('/AcroForm')] = DictionaryObject({NameObject('/Fields'): ArrayObject(list(annots))})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _sheet(res):
    from systems.dnd5e.rules.character import compute
    return compute({'build': res['build'], 'state': res['state']})


def test_read_fields_from_widgets_and_acroform():
    from systems.dnd5e.pdf_import import read_fields
    for acro in (False, True):
        f = read_fields(make_pdf({'CharacterName': 'X', 'CLASS  LEVEL': 'Wizard 1', 'Monk’s Focus': 'y'}, acro))
        assert f['charactername'] == 'X'
        assert f['class level'] == 'Wizard 1'           # whitespace collapsed, lower-cased


def test_bad_files_are_refused_cleanly():
    from systems.dnd5e.pdf_import import PdfImportError, import_pdf, read_fields
    for bad in (b'', b'not a pdf at all', b'%PDF-1.7\n garbage garbage'):
        with pytest.raises(PdfImportError):
            read_fields(bad)
    # a PDF with no form fields
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(100, 100)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PdfImportError, match='no form fields'):
        read_fields(buf.getvalue())
    # fields but no class/level
    with pytest.raises(PdfImportError, match='class and level'):
        import_pdf(make_pdf({'CharacterName': 'Nobody'}))


def test_2024_fighter_rebuilt_to_match_the_sheet():
    from systems.dnd5e.pdf_import import import_pdf
    res = import_pdf(make_pdf(FIGHTER))
    rep = res['report']
    assert res['name'] == 'Brannoc Vale'
    assert res['build']['edition'] == 'one'               # "PHB-2024" in the features text
    assert rep['source'] == 'PDF'
    s = _sheet(res)
    # every total on the sheet is reproduced
    assert {a: s['abilities'][a]['score'] for a in s['abilities']} == \
        {'str': 17, 'dex': 12, 'con': 15, 'int': 10, 'wis': 13, 'cha': 8}
    assert s['hp_max'] == 31 and res['state']['hp'] == 22
    # skills come from the P markers, not guessed from totals
    assert set(s['proficiencies']['skills']) == {'acrobatics', 'athletics', 'intimidation', 'perception', 'survival'}
    # the subclass is recognised from its features (no name on the sheet)
    assert res['build']['choices']['subclass:fighter|xphb'] == ['champion|xphb|fighter|xphb']
    assert not [p for p in rep['pending'] if 'skill' in p or 'Subclass' in p or 'tool' in p], rep['pending']
    # the option bullet under Fighting Style picks Defense; armor is worn
    assert 'Defense' in [f['name'] for f in s['feats']]
    assert s['ac']['value'] == 19 and not [m for m in rep['mismatches'] if m.startswith('AC')]
    names = {f['name'] for f in s['feats']}
    assert {'Alert', 'Savage Attacker'} <= names     # Human's Versatile feat + Soldier's origin feat
    assert rep['mismatches'] == [], rep['mismatches']
    assert res['details'].get('backstory', '').startswith('Served')


def test_edition_override_and_short_armor_names():
    from systems.dnd5e.pdf_import import _armor_name, import_fields, read_fields
    assert _armor_name('Leather') == 'Leather Armor'
    assert _armor_name('Half Plate') == 'Half Plate Armor'
    assert _armor_name('Chain Mail') == 'Chain Mail'
    assert _armor_name('Longsword') == 'Longsword'
    f = read_fields(make_pdf(FIGHTER))
    res = import_fields(f, edition='classic')
    assert res['build']['edition'] == 'classic'
    s = _sheet(res)
    assert s['abilities']['str']['score'] == 17 and s['hp_max'] == 31


def test_ac_mismatch_is_reported_not_hidden():
    from systems.dnd5e.pdf_import import import_pdf
    f = dict(FIGHTER, AC='21')
    rep = import_pdf(make_pdf(f))['report']
    assert any(m.startswith('AC: sheet 21') for m in rep['mismatches'])


# A 2024 Paladin 9 / Warlock 1 shaped like D&D Beyond's export of the
# rehearsal character: feat-granted skills, masteries listed as options,
# "Weapon Mastery" printed under FEATS, and armour carried but not worn.
PALADIN_WARLOCK = {
    'CharacterName': 'Ser Test Vow',
    'CLASS  LEVEL': 'Paladin 9 / Warlock 1',
    'RACE': 'Human',
    'BACKGROUND': 'Soldier',
    'STR': '14', 'DEX': '12', 'CON': '12', 'INT': '10', 'WIS': '10', 'CHA': '20',
    'ProfBonus': '+4',
    'AthleticsProf': 'P', 'InsightProf': 'P', 'IntimidationProf': 'P', 'MedicineProf': 'P',
    'PerceptionProf': 'P', 'PersuasionProf': 'E', 'StealthProf': 'P',
    'ST Wisdom': '+4', 'ST Charisma': '+9',
    'MaxHP': '80', 'AC': '11',
    'ProficienciesLang': '=== LANGUAGES === \nCommon, Elvish, Dwarvish',
    'FeaturesTraits1': '=== PALADIN FEATURES === \n\n* Lay On Hands • PHB-2024 109 \n'
                       '* Weapon Mastery • PHB-2024 110 \n* Fighting Style • PHB-2024 110 \n'
                       '* Vow of Enmity • PHB-2024 117 \n\n'
                       '=== WARLOCK FEATURES === \n\n* Eldritch Invocations • PHB-2024 153 \n\n'
                       '=== FEATS === \n\n* Alert • PHB-2024 \n* Savage Attacker • PHB-2024 \n'
                       '* Observant • PHB-2024 \n* Skill Expert • PHB-2024 \n'
                       '* Great Weapon Fighting • PHB-2024 \n* Weapon Mastery • PHB-2024 \n\n'
                       '=== OPTIONS === \n\n* Greatsword (Graze) • \n* Warhammer (Push) • \n',
    'Eq Name0': 'Chain Mail', 'Eq Qty0': '1',
    'Eq Name1': 'Shield', 'Eq Qty1': '1',
    'Eq Name2': 'Longsword', 'Eq Qty2': '1',
    'Wpn Name': 'Longsword',
}


def test_multiclass_feat_skills_masteries_and_unworn_armor():
    from systems.dnd5e.pdf_import import import_pdf
    res = import_pdf(make_pdf(PALADIN_WARLOCK))
    rep = res['report']
    s = _sheet(res)
    assert [lv['class'] for lv in res['build']['levels']].count('paladin|xphb') == 9
    assert [lv['class'] for lv in res['build']['levels']].count('warlock|xphb') == 1
    # skills granted by feats (Observant, Skill Expert) are placed, expertise too
    assert {'perception', 'stealth'} <= set(s['proficiencies']['skills'])
    assert 'persuasion' in s['proficiencies']['expertise']
    assert not [w for w in rep['warnings'] if 'skill proficiency' in w], rep['warnings']
    # the 2024 class feature listed under FEATS is not a missing feat
    assert not [w for w in rep['warnings'] if 'Weapon Mastery' in w], rep['warnings']
    assert res['build']['choices']['mastery:paladin|xphb'] == ['greatsword', 'warhammer']
    # AC 11 on the sheet: the chain mail and shield are carried, not worn
    assert s['ac']['value'] == 11
    assert not [m for m in rep['mismatches'] if m.startswith('AC')]
    assert any('carried, not worn' in w for w in rep['warnings'])
    assert s['hp_max'] == 80
    sc = s['spellcasting']
    assert sc['slots'] == {'1': 4, '2': 3, '3': 2} or sc['slots'] == {1: 4, 2: 3, 3: 2}, sc['slots']
