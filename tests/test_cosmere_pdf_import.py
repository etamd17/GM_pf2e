"""PDF -> Cosmere character import: field mapping + authoritative overrides.

Driven by a synthetic AcroForm field dict (the real fillable sheet has these
exact field names) so it needs no committed PDF. Verifies: attributes, skill
ranks from the rank pips, heroic-path / radiant-order split, talents, and that
the sheet's authoritative totals (a Hardy +5 health the build can't derive from
the talent name, plus armor Deflect) are reproduced via stat_bonuses and survive
a to_dict round-trip.
"""
import os
import tempfile

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())
os.environ.setdefault('GM_PASSWORD', '')

from systems.cosmere.pdf_import import parse_cosmere_pdf, build_from_pdf
from systems.cosmere.build import CosmereBuild
from systems.cosmere.actor import CosmereActor


def _fields():
    f = {
        'char_name': 'Test Hunter', 'char_level': '5', 'char_ancestry': 'Human',
        'char_paths': 'Hunter',
        'char_strength': '4', 'char_speed': '3', 'char_intellect': '1',
        'char_willpower': '1', 'char_awareness': '3', 'char_presence': '1',
        'char_phys_def': '17', 'char_cog_def': '12', 'char_spirit_def': '14',
        'char_health_max': '39', 'char_health_current': '39',
        'char_focus_max': '3', 'char_focus_current': '3', 'char_deflect': '2',
        'char_light_weapon': '5', 'char_stealth': '5', 'char_perception': '5',
        'char_talent_name_1': 'Hardy', 'char_talent_name_2': 'Seek Quarry',
        'char_expertise': 'Shortbow (weapon), Kharbranthian (cultural)',
    }
    # rank pips: Light Weapon rank 2, Stealth rank 2, Perception rank 2
    for sk in ('light_weapon', 'stealth', 'perception'):
        f[f'char_{sk}_rank_1'] = '/Yes'
        f[f'char_{sk}_rank_2'] = '/Yes'
        for n in (3, 4, 5):
            f[f'char_{sk}_rank_{n}'] = '/Off'
    return f


def test_field_mapping_attributes_skills_path_talents():
    build, auth, extras = parse_cosmere_pdf(_fields())
    assert build['attributes'] == {'str': 4, 'spd': 3, 'int': 1, 'wil': 1, 'awa': 3, 'pre': 1}
    assert build['skills'].get('lwp') == 2 and build['skills'].get('stl') == 2 and build['skills'].get('prc') == 2
    assert build['path'] == 'hunter'
    assert {t['name'] for t in build['talents']} == {'Hardy', 'Seek Quarry'}
    assert 'Shortbow (weapon)' in build['expertises']
    assert auth['health'] == 39 and auth['deflect'] == 2


def test_authoritative_overrides_reproduce_sheet_exactly():
    build, play_state, extras = build_from_pdf(_fields())
    a = CosmereActor(CosmereBuild(build).to_actor_doc())
    # base build derives 34 HP (10+4 +5*4) / deflect 0; the sheet says 39 / 2.
    assert a.health_max == 39, ('health override failed', a.health_max, build.get('stat_bonuses'))
    assert (a.deflect or {}).get('value', 0) == 2, ('deflect override failed', a.deflect)
    assert a.defenses['phy'] == 17 and a.defenses['cog'] == 12 and a.defenses['spi'] == 14
    assert play_state['health'] == 39 and play_state['focus'] == 3
    # stat_bonuses survive a to_dict round-trip (so in-builder edits keep them)
    rt = CosmereBuild(build).to_dict()
    assert rt.get('stat_bonuses', {}).get('health') == 5
    a2 = CosmereActor(CosmereBuild(rt).to_actor_doc())
    assert a2.health_max == 39


def test_homebrew_weapon_becomes_custom_strike():
    from systems.cosmere.pdf_import import _parse_weapon_line
    p = _parse_weapon_line("Soravar's Gauntlet: +7 (1d10 impact damage) [Melee, Reach]")
    assert p['name'] == "Soravar's Gauntlet" and p['attack'] == 7 and p['damage'] == '1d10' and p['type'] == 'impact'

    f = _fields()
    # a homebrew weapon (no catalog match) + a catalog weapon (Knife)
    f['char_light_weapon'] = '4'
    f['char_weapons.0'] = "Soravar's Gauntlet: +7 (1d10 impact damage) [Melee, Reach]"
    f['char_weapons.1'] = 'Knife: +4 (1d4 keen damage) [Melee]'
    build, play_state, extras = build_from_pdf(f)
    assert any(w['name'] == "Soravar's Gauntlet" and w['attack'] == 7 for w in build.get('custom_weapons', [])), \
        ('homebrew weapon dropped', build.get('custom_weapons'))

    a = CosmereActor(CosmereBuild(build).to_actor_doc())
    by = {s['name']: s for s in a.strikes}
    assert "Soravar's Gauntlet" in by, ('custom strike missing', list(by))
    assert by["Soravar's Gauntlet"]['mod'] == 7 and by["Soravar's Gauntlet"]['damage'] == '1d10'
    # the catalog weapon keeps its SKILL-derived mod (not 0) -- the custom-attack
    # override must not collide with the reserved `attack` schema field.
    assert 'Knife' in by and by['Knife']['mod'] != 0, ('catalog weapon mod broke', by.get('Knife'))


def test_radiant_order_split_from_path():
    f = _fields()
    f['char_paths'] = 'Scholar, Truthwatcher'
    build, _, _ = parse_cosmere_pdf(f)
    assert build['path'] == 'scholar'
    assert build.get('radiant_order') == 'truthwatcher' and build.get('is_radiant') is True
