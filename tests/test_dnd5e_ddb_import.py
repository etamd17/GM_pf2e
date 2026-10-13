"""D&D Beyond import (systems/dnd5e/ddb_import.py).

The fixtures are synthetic characters written in D&D Beyond's character-service
v5 shape (checked against a real export while building this): one 2024
Paladin, one 2014 Rogue. Every expectation below is what the source sheet
shows, so a failure means the import lost or changed something."""
import json
import os

import pytest

from systems.dnd5e import content
from systems.dnd5e import ddb_import as D
from systems.dnd5e.rules.character import compute

pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')
FIX = os.path.join(os.path.dirname(__file__), 'fixtures')


def load(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return f.read()


def imported(name, **kw):
    r = D.import_character(load(name), **kw)
    s = compute({'name': r['name'], 'build': r['build'], 'state': r['state']})
    return r, s


def test_2024_paladin_round_trip():
    r, s = imported('dnd5e_ddb_paladin_2024.json')
    rep = r['report']
    assert rep['edition'] == 'one'                          # detected from "Epic Boon"
    assert rep['unmatched'] == [] and rep['mismatches'] == [] and rep['pending'] == []
    assert s['errors'] == []
    # ability scores: base + Soldier (+2 STR, +1 CON) + ASI (+2 CHA)
    assert {a: s['abilities'][a]['score'] for a in ('str', 'con', 'cha')} == {'str': 17, 'con': 14, 'cha': 16}
    assert s['race']['name'] == 'Human' and s['background']['name'] == 'Soldier'
    assert s['classes'][0]['subclass']['name'] == 'Oath of Devotion' and s['classes'][0]['source'] == 'XPHB'
    assert {f['name'] for f in s['feats']} == {'Alert', 'Savage Attacker', 'Defense'}
    assert set(s['proficiencies']['skills']) >= {'perception', 'insight', 'persuasion', 'athletics', 'intimidation'}
    assert r['build']['choices']['mastery:paladin|xphb'] == ['javelin', 'longsword']
    # HP: 42 from hit dice + CON 2 x 5; 5 damage taken, 3 temp
    assert s['hp_max'] == 52 and r['state']['hp'] == 47 and r['state']['temp_hp'] == 3
    assert s['ac']['value'] == 19                           # chain 16 + shield 2 + Defense 1
    prepared = r['build']['choices']['spells:paladin|xphb:prepared']
    assert set(prepared) == {'bless|xphb', 'cure wounds|xphb', 'aid|xphb'}   # not Shield of Faith (unprepared)
    st = r['state']
    assert st['slots_used'] == {'1': 2} and st['conditions'] == {'poisoned': {}} and st['exhaustion'] == 1
    assert st['inspiration'] is True and st['xp'] == 6600
    assert st['currency'] == {'cp': 3, 'sp': 14, 'ep': 0, 'gp': 57, 'pp': 1}
    inv = {(i.get('id') or i.get('name')): i['qty'] for i in st['inventory']}
    assert inv['bolts (20)|xphb'] == 2                      # 30 bolts -> two bundles of 20
    assert inv['hooded lantern|xphb'] == 1                  # "Lantern, Hooded"
    assert inv['Mystery Homebrew Trinket'] == 1 and inv['Letter from home'] == 1
    assert any('Mystery Homebrew Trinket' in w for w in rep['warnings'])
    assert r['details']['alignment'] == 'Lawful Good' and r['details']['backstory'] == 'Served on the border.'


def test_2014_rogue_round_trip():
    r, s = imported('dnd5e_ddb_rogue_2014.json')
    rep = r['report']
    assert rep['edition'] == 'classic' and rep['unmatched'] == [] and rep['mismatches'] == []
    assert s['race']['name'] == 'Halfling (Lightfoot)'      # "Lightfoot Halfling" on D&D Beyond
    assert s['abilities']['dex']['score'] == 19 and s['abilities']['cha']['score'] == 11
    assert set(s['proficiencies']['expertise']) == {'stealth', 'sleight of hand'}
    assert "thieves' cant" in s['proficiencies']['languages']
    assert s['hp_max'] == 31                                # 23 + CON 2 x 4
    # the one thing D&D Beyond's export didn't record is left for the player
    assert rep['pending'] == ['Background (Criminal): choose 1 tool']


def test_edition_override_and_bad_input():
    r = D.import_character(load('dnd5e_ddb_rogue_2014.json'), edition='one')
    assert r['build']['edition'] == 'one'
    for bad in ('not json', '[]', '{"name": "x"}'):
        with pytest.raises(D.ImportError_):
            D.import_character(bad)
    # both the full response and its "data" object are accepted
    wrapped = json.loads(load('dnd5e_ddb_paladin_2024.json'))
    assert D.import_character(wrapped['data'])['name'] == D.import_character(wrapped)['name']


def test_fetch_only_ever_targets_dnd_beyond():
    assert D._DDB_URL.match('https://www.dndbeyond.com/characters/12345678').group(1) == '12345678'
    assert D._DDB_URL.match('12345678').group(1) == '12345678'
    assert D._DDB_URL.match('https://www.dndbeyond.com/profile/someone/characters/1234567/builder').group(1) == '1234567'
    for bad in ('https://evil.example/characters/123', 'file:///etc/passwd', 'http://127.0.0.1/characters/12345'):
        with pytest.raises(D.ImportError_):
            D.fetch_public(bad)
