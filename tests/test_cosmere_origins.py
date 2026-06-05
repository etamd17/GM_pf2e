"""Canon non-Radiant creation grants (Phase 4) — heroic-path key talents +
starting skills, cultures, and talent prerequisites, derived from the Foundry
data and guarded against it.
"""
from __future__ import annotations

import app
import systems.cosmere as cos
from systems.cosmere import origins as O, load_pack
from systems.cosmere.build import CosmereBuild


def test_path_info_matches_foundry_data():
    talents = {d['_id']: d for d in load_pack('heroic-paths') if d.get('type') == 'talent'}
    assert set(O.PATH_INFO) == set(cos.PATHS)           # all 6 heroic paths
    for key, info in O.PATH_INFO.items():
        assert info['start_skill'] in cos.SKILL_ATTR     # a real skill code
        t = talents.get(info['key_talent_id'])
        assert t is not None, key                        # the key talent id is real
        assert t['system'].get('prerequisites') == {}    # key talents are tree roots
        assert (t['system'].get('path') or '').lower() == key


def test_starting_skills_are_canon():
    # Spot-checks from the Foundry path descriptions.
    assert O.path_start_skill('warrior') == 'ath'        # Athletics
    assert O.path_start_skill('scholar') == 'lor'        # Lore
    assert O.path_start_skill('hunter') == 'prc'         # Perception


def test_engine_path_grants_and_validation():
    # Missing the key talent + the starting-skill rank -> two guided warnings.
    b = CosmereBuild({'level': 1, 'path': 'warrior',
                      'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2},
                      'skills': {'hwp': 2}})
    issues = ' '.join(b.validate())
    assert 'Vigilant Stance' in issues                   # key talent reminder
    assert 'Athletics' in issues                         # starting skill reminder
    # With the key talent + a rank in Athletics, those warnings clear.
    kt = O.path_key_talent('warrior')
    good = CosmereBuild({'level': 1, 'path': 'warrior',
                         'attributes': {'str': 2, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 2, 'pre': 2},
                         'skills': {'ath': 1, 'hwp': 2}, 'talents': [kt]})
    assert good.has_key_talent() is True
    issues2 = ' '.join(good.validate())
    assert 'Vigilant Stance' not in issues2 and 'starting skill' not in issues2


def test_talent_prereq_summary():
    # A known talent with prerequisites renders a readable summary.
    s = app._talent_prereq_summary({'g': {'type': 'skill', 'skill': 'lor', 'rank': 2}})
    assert 'Lore rank 2' == s
    assert app._talent_prereq_summary({}) == ''
    t = app._talent_prereq_summary({'g': {'type': 'talent', 'talents': [{'label': 'Field Medicine'}]}})
    assert 'Field Medicine' in t


def test_builder_shows_path_grants():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    # The path-info wiring + key-talent data are present for the JS to use.
    assert 'Vigilant Stance' in body and 'starting skill' in body.lower()


def test_starting_kits_resolve_to_catalog():
    kits = {k['key']: k for k in app._cosmere_starting_kits()}
    assert set(kits) == set(O.STARTING_KITS)
    names = [i['name'] for i in kits['military']['items']]
    assert 'Chain' in names and 'Longsword' in names      # armor + weapon resolved to catalog
    assert kits['military']['marks'] == '2d6'
    assert kits['prisoner']['items'] == []                 # prisoner grants no gear items


def test_builder_offers_starting_kits():
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Starting kit' in body and 'Academic Kit' in body and 'Underworld Kit' in body
