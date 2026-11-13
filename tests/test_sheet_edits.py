"""SE-01: the PF2e sheet edit layer (build['sheet_edits']).

Spec: docs/superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md. The engine
keeps computing every stat; the layer only adds GM adjustments and meta
overrides, and it must survive a Pathbuilder re-import.
"""
import pytest

from core.sheet_edits import SheetEditError, merge_patch, validate_patch


# ---------------------------------------------------------------- Task 1: pure module

def test_adjust_requires_gm():
    with pytest.raises(SheetEditError) as e:
        validate_patch({'adjust': {'ac': 1}}, is_gm=False)
    assert e.value.status == 403


def test_owner_may_edit_meta():
    clean = validate_patch({'meta': {'deity': '  Ragathiel '}}, is_gm=False)
    assert clean == {'meta': {'deity': 'Ragathiel'}}


@pytest.mark.parametrize('bad', [
    {'adjust': {'ac': '2'}}, {'adjust': {'ac': 21}}, {'adjust': {'ac': -21}},
    {'adjust': {'nope': 1}}, {'adjust': {'ac': True}}, {'adjust': {'skill:': 1}},
    {'adjust': {'skill:flying': 1}}, {'adjust': []},
    {'meta': {'size': 'Colossal'}}, {'meta': {'languages': 'Common'}},
    {'meta': {'languages': ['x'] * 31}}, {'meta': {'languages': ['x' * 41]}},
    {'meta': {'deity': 'x' * 81}}, {'meta': {'deity': 3}}, {'meta': {'bogus': 'x'}},
    {'feats': {'added': [{'name': ''}]}}, {'feats': {'added': [{'name': 'X', 'level': 21}]}},
    {'feats': {'added': 'Toughness'}}, {'feats': {'bogus': []}}, {'features': {}},
    {'features': {'custom': [{'name': 'X', 'desc': 'y' * 2001}]}}, {'feats': {'removed': ['x' * 81]}},
    {'other': 1}, {},
    [], 'str', None,
])
def test_invalid_patches_400(bad):
    with pytest.raises(SheetEditError) as e:
        validate_patch(bad, is_gm=True)
    assert e.value.status == 400


def test_skill_and_lore_keys_accepted():
    clean = validate_patch({'adjust': {'skill:Athletics': 2, 'skill:lore:warfare': -1}}, is_gm=True)
    assert clean['adjust'] == {'skill:athletics': 2, 'skill:lore:warfare': -1}


def test_merge_clears_and_keeps():
    cur = {'meta': {'deity': 'Ragathiel', 'size': 'Small'}, 'adjust': {'ac': 1}}
    out = merge_patch(cur, validate_patch({'meta': {'deity': ''}, 'adjust': {'ac': 0}}, is_gm=True))
    assert out == {'meta': {'size': 'Small'}}
    assert cur['meta']['deity'] == 'Ragathiel'   # input not mutated


def test_merge_empty_list_clears_and_list_dedupes():
    out = merge_patch({'meta': {'languages': ['Common']}},
                      validate_patch({'meta': {'languages': [], 'senses': ['Darkvision', 'darkvision', ' ']}},
                                     is_gm=False))
    assert out == {'meta': {'senses': ['Darkvision']}}


def test_merge_from_none():
    assert merge_patch(None, validate_patch({'adjust': {'hp_max': 5}}, is_gm=True)) == {'adjust': {'hp_max': 5}}


# ---------------------------------------------------------------- Task 2: Character applies the layer

import copy
import json
from pathlib import Path

_FIXTURES = Path(__file__).parent / 'fixtures'

GETTERS = {
    'ac': lambda c: c.ac, 'fortitude': lambda c: c.fort, 'reflex': lambda c: c.ref,
    'will': lambda c: c.will, 'perception': lambda c: c.perception,
    'class_dc': lambda c: c.class_dc, 'speed': lambda c: c.speed, 'hp_max': lambda c: c.hp,
    'spell_dc': lambda c: c.spell_dc,
}


@pytest.fixture(scope='module')
def app_module():
    import app
    return app


@pytest.fixture
def goel_doc():
    return json.loads((_FIXTURES / 'goel_l10.json').read_text(encoding='utf-8'))


def _mk(app_module, doc, edits=None):
    doc = copy.deepcopy(doc)
    if edits is not None:
        doc['build']['sheet_edits'] = edits
    return app_module.Character(doc, '')


def _skills(c):
    return {s['name']: int(s['total']) for s in c.skills}


def _numbers(c):
    out = {k: g(c) for k, g in GETTERS.items()}
    out.update({'mods': dict(c.mods), 'skills': _skills(c)})
    return out


def test_no_edits_identical(app_module, goel_doc):
    base = _numbers(_mk(app_module, goel_doc))
    assert _numbers(_mk(app_module, goel_doc, {})) == base
    assert _numbers(_mk(app_module, goel_doc, {'meta': {}, 'adjust': {}})) == base


@pytest.mark.parametrize('key', sorted(GETTERS))
def test_each_adjust_moves_only_its_stat(app_module, goel_doc, key):
    base = _numbers(_mk(app_module, goel_doc))
    got = _numbers(_mk(app_module, goel_doc, {'adjust': {key: 2}}))
    for other in GETTERS:
        assert got[other] == base[other] + (2 if other == key else 0), other
    assert got['skills'] == base['skills'] and got['mods'] == base['mods']


def test_con_adjust_flows_to_hp_and_fort(app_module, goel_doc):
    base = _mk(app_module, goel_doc)
    c = _mk(app_module, goel_doc, {'adjust': {'con': 1}})
    assert c.mods['con'] == base.mods['con'] + 1
    assert c.fort == base.fort + 1
    assert c.hp == base.hp + base.level
    assert any(a['label'] == 'CON' and a['mod'].startswith('+4') for a in c.ability_display)


def test_skill_adjust(app_module, goel_doc):
    base = _skills(_mk(app_module, goel_doc))
    got = _skills(_mk(app_module, goel_doc, {'adjust': {'skill:religion': 3}}))
    assert got['Religion'] == base['Religion'] + 3
    assert {k: v for k, v in got.items() if k != 'Religion'} == {k: v for k, v in base.items() if k != 'Religion'}
    c = _mk(app_module, goel_doc, {'adjust': {'skill:religion': 3}})
    assert 'GM adj +3' in next(s['breakdown'] for s in c.skills if s['name'] == 'Religion')


def test_frightened_still_applies(app_module, goel_doc):
    doc = copy.deepcopy(goel_doc)
    base = _mk(app_module, doc).ac
    doc['build']['conditions'] = {'frightened': 1}
    assert _mk(app_module, doc, {'adjust': {'ac': 1}}).ac == base + 1 - 1


def test_spell_dc_adjust_needs_a_caster(app_module):
    doc = {'build': {'name': 'Plain Fighter', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1,
                     'abilities': {'str': 4, 'dex': 2, 'con': 2, 'int': 0, 'wis': 1, 'cha': 0}}}
    base = _mk(app_module, doc)
    assert base.spell_attack == 0
    c = _mk(app_module, doc, {'adjust': {'spell_dc': 2}})
    assert c.spell_attack == 0 and c.spell_dc == 0


def test_hp_floor(app_module):
    doc = {'build': {'name': 'Frail', 'class': 'Wizard', 'ancestry': 'Elf', 'level': 1,
                     'abilities': {'str': 0, 'dex': 2, 'con': -1, 'int': 4, 'wis': 1, 'cha': 0},
                     'current_hp': 9}}
    c = _mk(app_module, doc, {'adjust': {'hp_max': -20}})
    assert c.hp == 1 and c.current_hp == 1


def test_meta_overrides_and_clear(app_module, goel_doc):
    c = _mk(app_module, goel_doc, {'meta': {
        'deity': 'Ragathiel', 'sanctification': 'Holy', 'languages': ['Common', 'Empyrean'],
        'senses': ['Scent (imprecise) 30 feet'], 'size': 'Large', 'background': 'Acolyte'}})
    assert c.deity == 'Ragathiel' and c.sanctification == 'Holy'
    assert c.languages == ['Common', 'Empyrean'] and c.size == 'Large' and c.background == 'Acolyte'
    assert c.senses[0] == 'Darkvision' and 'Scent (imprecise) 30 feet' in c.senses
    assert _mk(app_module, goel_doc, {'meta': {'size': 'Large'}}).deity == 'Rathathiel'


def test_display_name_default_and_set(app_module, goel_doc):
    assert _mk(app_module, goel_doc).display_name == "Go'el Thrall"
    c = _mk(app_module, goel_doc, {'meta': {'display_name': "Go'el the Red"}})
    assert c.display_name == "Go'el the Red" and c.name == "Go'el Thrall"


def test_edits_survive_reimport(app_module, goel_doc):
    existing = copy.deepcopy(goel_doc)
    existing['build']['sheet_edits'] = {'meta': {'deity': 'Ragathiel'}, 'adjust': {'ac': 1}}
    stale = copy.deepcopy(goel_doc)
    assert stale['build']['deity'] == 'Rathathiel'
    merged = app_module._merge_pf2e_import(existing, stale)
    c = app_module.Character(merged, '')
    assert c.deity == 'Ragathiel'
    assert c.ac == _mk(app_module, goel_doc).ac + 1


def test_adjusted_keys_marker(app_module, goel_doc):
    assert _mk(app_module, goel_doc).adjusted_keys == frozenset()
    assert _mk(app_module, goel_doc, {'adjust': {'ac': 1}}).adjusted_keys == frozenset({'ac'})
    c = _mk(app_module, goel_doc, {'adjust': {'will': -1}})
    assert 'GM adj -1' in c.will_breakdown


# ---------------------------------------------------------------- Task 3: routes (isolated subprocess)

import os
import subprocess
import sys
import textwrap

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_ROUTE_PRELUDE = '''
import tempfile, os, json
from urllib.parse import quote
os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
import app as A
c = A.app.test_client(); J = {'X-Requested-With': 'XMLHttpRequest'}
pb = json.load(open('tests/fixtures/goel_l10.json', encoding='utf-8'))
assert c.post('/api/import_pathbuilder', json=pb, headers=J).status_code == 200
name = pb['build']['name']; url = '/api/sheet_edits/' + quote(name)
path = A.get_pc_file_path(name)
def raw(): return open(path, 'rb').read()
def build(): return json.loads(raw()).get('build', {})
'''


def _route(body):
    script = 'import sys, os\nsys.path.insert(0, os.getcwd())\n' + _ROUTE_PRELUDE + textwrap.dedent(body)
    r = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)
    assert 'ROUTE_OK' in r.stdout, 'stdout:\n%s\nstderr:\n%s' % (r.stdout, r.stderr[-3000:])


def test_route_meta_roundtrip():
    _route('''
        r = c.post(url, json={'meta': {'deity': 'Ragathiel'}}, headers=J)
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json()['sheet_edits'] == {'meta': {'deity': 'Ragathiel'}}
        assert build()['sheet_edits']['meta']['deity'] == 'Ragathiel'
        assert build()['deity'] == 'Rathathiel'            # Pathbuilder data untouched
        assert A.PARTY_LIBRARY[name].deity == 'Ragathiel'
        r = c.post(url, json={'meta': {'deity': ''}}, headers=J)
        assert r.status_code == 200 and 'sheet_edits' not in build()
        assert A.PARTY_LIBRARY[name].deity == 'Rathathiel'
        print('ROUTE_OK')
    ''')


def test_route_owner_adjust_403():
    _route('''
        A._is_gm = lambda: False      # GM_PASSWORD '' keeps @require_pc_self_or_gm open
        before = raw()
        r = c.post(url, json={'adjust': {'ac': 1}}, headers=J)
        assert r.status_code == 403, r.status_code
        assert r.get_json()['success'] is False and 'GM' in r.get_json()['error']
        assert raw() == before
        assert c.post(url, json={'meta': {'deity': 'Ragathiel'}}, headers=J).status_code == 200
        print('ROUTE_OK')
    ''')


def test_route_bad_input_400_file_unchanged():
    _route('''
        before = raw()
        for body in ({'meta': {'size': 'Colossal'}}, {'adjust': {'ac': '2'}}, {'nope': 1}, []):
            r = c.post(url, json=body, headers=J)
            assert r.status_code == 400, (body, r.status_code)
            assert r.get_json()['error'], body
        r = c.post(url, data='not json', headers=dict(J, **{'Content-Type': 'application/json'}))
        assert r.status_code == 400, r.status_code
        assert raw() == before
        print('ROUTE_OK')
    ''')


def test_route_unknown_pc_404():
    _route('''
        r = c.post('/api/sheet_edits/' + quote('Nobody Here'), json={'meta': {'deity': 'X'}}, headers=J)
        assert r.status_code == 404, r.status_code
        print('ROUTE_OK')
    ''')


def test_route_flushes_dirty_hp_first():
    _route('''
        A.PARTY_LIBRARY[name].current_hp = 5
        A._persist_pc_combat_state(name)                   # dirty, not yet flushed
        assert c.post(url, json={'meta': {'deity': 'Ragathiel'}}, headers=J).status_code == 200
        assert build()['current_hp'] == 5, build().get('current_hp')
        assert A.PARTY_LIBRARY[name].current_hp == 5
        print('ROUTE_OK')
    ''')


def test_route_gm_adjust_and_reset():
    _route('''
        base_ac = A.PARTY_LIBRARY[name].ac
        r = c.post(url, json={'adjust': {'ac': 2}, 'meta': {'deity': 'Ragathiel'}}, headers=J)
        assert r.status_code == 200
        assert A.PARTY_LIBRARY[name].ac == base_ac + 2
        r = c.post(url + '/reset', json={'section': 'adjust'}, headers=J)
        assert r.status_code == 200 and build()['sheet_edits'] == {'meta': {'deity': 'Ragathiel'}}
        assert A.PARTY_LIBRARY[name].ac == base_ac
        assert c.post(url + '/reset', json={'section': 'bogus'}, headers=J).status_code == 400
        assert c.post(url + '/reset', json={}, headers=J).status_code == 200 and 'sheet_edits' not in build()
        A._is_gm = lambda: False
        assert c.post(url + '/reset', json={}, headers=J).status_code == 403
        print('ROUTE_OK')
    ''')


# ---------------------------------------------------------------- Task 4: sheet UI

from urllib.parse import quote


def _render(app_module, monkeypatch, doc, *, gm=True):
    pc = app_module.Character(copy.deepcopy(doc), file_path='')
    monkeypatch.setattr(app_module, 'PARTY_LIBRARY', {pc.name: pc})
    monkeypatch.setattr(app_module, '_sync_party_from_disk', lambda: None)
    monkeypatch.setattr(app_module, '_active_system', lambda: 'pf2e')
    monkeypatch.setattr(app_module, '_account_mode', lambda: False)
    monkeypatch.setattr(app_module, '_is_gm', lambda: gm)
    r = app_module.app.test_client().get('/player/sheet/' + quote(pc.name))
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_sheet_has_edit_modal_and_entry_points(app_module, monkeypatch, goel_doc):
    html = _render(app_module, monkeypatch, goel_doc)
    assert 'id="se-modal"' in html
    assert html.count('data-sheet-edit-open') >= 2          # overflow menu + meta rail
    assert 'id="se-numbers"' in html


def test_numbers_tab_gm_only(app_module, monkeypatch, goel_doc):
    html = _render(app_module, monkeypatch, goel_doc, gm=False)
    assert 'id="se-modal"' in html and 'id="se-numbers"' not in html
    assert 'data-se-adjust=' not in html


def test_numbers_rows_show_computed_values(app_module, monkeypatch, goel_doc):
    doc = copy.deepcopy(goel_doc)
    doc['build']['sheet_edits'] = {'adjust': {'ac': 2}}
    html = _render(app_module, monkeypatch, doc)
    base_ac = _mk(app_module, goel_doc).ac
    assert f'data-se-adjust="ac" data-se-computed="{base_ac}" value="2"' in html
    assert 'data-se-adjust="skill:religion"' in html and 'data-se-adjust="str"' in html
    assert 'class="gm-adj-dot"' in html                     # AC carries the marker


def test_display_name_rendered_keys_unchanged(app_module, monkeypatch, goel_doc):
    doc = copy.deepcopy(goel_doc)
    doc['build']['sheet_edits'] = {'meta': {'display_name': 'Go\'el the <Red>', 'deity': 'Ragathiel'}}
    html = _render(app_module, monkeypatch, doc)
    assert 'Go&#39;el the &lt;Red&gt;' in html and '<Red>' not in html
    assert '/player/levelup/Go%27el%20Thrall' in html
    assert 'Ragathiel' in html


def test_meta_fields_prefilled_with_pathbuilder_placeholders(app_module, monkeypatch, goel_doc):
    doc = copy.deepcopy(goel_doc)
    doc['build']['sheet_edits'] = {'meta': {'deity': 'Ragathiel'}}
    html = _render(app_module, monkeypatch, doc, gm=False)
    assert 'name="deity" value="Ragathiel" placeholder="Rathathiel"' in html


# ---------------------------------------------------------------- Final review fixes

def test_lore_adjust_matches_builder_lore_keys(app_module, goel_doc):
    # The in-app builder writes 'lore: warfare' (space after the colon).
    doc = copy.deepcopy(goel_doc)
    doc['build'].setdefault('proficiencies', {})['lore: warfare'] = 2
    base = _skills(_mk(app_module, doc))['Lore: Warfare']
    key = validate_patch({'adjust': {'skill:lore: warfare': 3}}, is_gm=True)['adjust']
    assert key == {'skill:lore:warfare': 3}
    c = _mk(app_module, doc, {'adjust': key})
    assert _skills(c)['Lore: Warfare'] == base + 3
    row = next(r for r in app_module._sheet_edit_numbers(c) if r['label'] == 'Lore: Warfare')
    assert row['key'] == 'skill:lore:warfare' and row['adj'] == 3 and row['computed'] == base


def test_large_negative_spell_dc_adjust_keeps_a_spell_dc(app_module, goel_doc):
    c = _mk(app_module, goel_doc, {'adjust': {'spell_dc': -20}})
    assert c.spell_dc > 0
    assert any(r['key'] == 'spell_dc' for r in app_module._sheet_edit_numbers(c))


def test_route_save_broadcasts_pc_state():
    _route('''
        sent = []
        A._broadcast_pc_state = lambda n, *a, **k: sent.append(n)
        assert c.post(url, json={'adjust': {'ac': 1}}, headers=J).status_code == 200
        assert c.post(url + '/reset', json={}, headers=J).status_code == 200
        assert sent == [name, name], sent
        print('ROUTE_OK')
    ''')


def test_pathbuilder_meta_tolerates_a_bare_actor(app_module):
    # The sheet route's characterization test renders with a name-only stub.
    from types import SimpleNamespace
    meta = app_module._sheet_edit_pathbuilder_meta(SimpleNamespace(name='Viewed Character'))
    assert meta['display_name'] == 'Viewed Character' and meta['deity'] == '' and meta['senses'] == ''


# ---------------------------------------------------------------- SE-02 Task 1: feats and features

def _fighter(level=5, feats=None):
    return {'build': {'name': 'Test Fighter', 'class': 'Fighter', 'ancestry': 'Human', 'level': level,
                      'abilities': {'str': 4, 'dex': 2, 'con': 2, 'int': 0, 'wis': 1, 'cha': 0},
                      'feats': feats or [['Power Attack', None, 'Class', 1]]}}


def test_feat_patch_validation():
    clean = validate_patch({'feats': {'added': [{'name': ' Toughness ', 'type': 'General', 'level': 3}],
                                      'removed': ['Diehard'], 'order': ['Toughness', 'Diehard']},
                            'features': {'hidden': ['Raise Symbol'], 'custom': [{'name': 'Vow', 'desc': 'Text'}]}},
                           is_gm=False)
    assert clean['feats']['added'] == [['Toughness', None, 'General', 3]]
    assert clean['feats']['removed'] == ['Diehard'] and clean['features']['custom'] == [{'name': 'Vow', 'desc': 'Text'}]
    # Lists replace wholesale; an empty list clears.
    out = merge_patch({'feats': {'removed': ['Diehard'], 'order': ['A']}}, validate_patch({'feats': {'removed': []}}, is_gm=False))
    assert out == {'feats': {'order': ['A']}}


def test_added_feat_rule_applies(app_module):
    base = _mk(app_module, _fighter())
    c = _mk(app_module, _fighter(), {'feats': {'added': [['Toughness', None, 'General', 3]]}})
    assert c.hp == base.hp + 5
    assert next(f for f in c.feats if f['name'] == 'Toughness')['source'] == 'sheet'
    assert next(f for f in c.feats if f['name'] == 'Power Attack')['source'] == 'build'


def test_removed_pathbuilder_feat_gone_and_listed(app_module, goel_doc):
    c = _mk(app_module, goel_doc, {'feats': {'removed': ['diehard']}})
    assert 'Diehard' not in [f['name'] for f in c.feats]
    assert c.removed_feats == ['Diehard']
    assert _mk(app_module, goel_doc).removed_feats == []


def test_added_and_removed_same_name_shows_once(app_module, goel_doc):
    c = _mk(app_module, goel_doc, {'feats': {'removed': ['Toughness'],
                                             'added': [['Toughness', None, 'General', 3]]}})
    assert [f['name'] for f in c.feats].count('Toughness') == 1
    c = _mk(app_module, goel_doc, {'feats': {'added': [['Diehard', None, 'General', 1]]}})
    assert [f['name'] for f in c.feats].count('Diehard') == 1      # already in the build


def test_feat_order_applied(app_module, goel_doc):
    base = [f['name'] for f in _mk(app_module, goel_doc).feats]
    got = [f['name'] for f in _mk(app_module, goel_doc, {'feats': {'order': ['Toughness', 'Diehard']}}).feats]
    assert got[:2] == ['Toughness', 'Diehard']
    assert got[2:] == [n for n in base if n not in ('Toughness', 'Diehard')]


def test_feat_edits_survive_reimport(app_module, goel_doc):
    existing = copy.deepcopy(goel_doc)
    existing['build']['sheet_edits'] = {'feats': {'removed': ['Diehard'], 'added': [['Fleet', None, 'General', 3]]}}
    reimport = copy.deepcopy(goel_doc)
    reimport['build']['feats'].append(['Fleet', None, 'General', 3])   # Pathbuilder now has it too
    c = app_module.Character(app_module._merge_pf2e_import(existing, reimport), '')
    names = [f['name'] for f in c.feats]
    assert 'Diehard' not in names and names.count('Fleet') == 1


def test_hidden_feature_not_listed_and_custom_feature_appended(app_module, goel_doc):
    c = _mk(app_module, goel_doc, {'features': {'hidden': ['raise symbol', 'Orc Ferocity'],
                                                'custom': [{'name': 'Oath of Ash', 'desc': 'Homebrew vow.'}]}})
    cf = [f['name'] for f in c.class_features]
    assert 'Raise Symbol' not in cf and 'Divine Font' in cf and cf[-1] == 'Oath of Ash'
    assert 'Orc Ferocity' not in [f['name'] for f in c.ancestry_features]
    assert sorted(c.hidden_features) == ['Orc Ferocity', 'Raise Symbol']
