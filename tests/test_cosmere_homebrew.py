"""Cosmere homebrew (builder stage 3): the per-campaign content shelf, its
engine-applied structured bonuses, and the manager routes + builder surfacing.
"""
from __future__ import annotations

import json

import pytest

import app
import systems.cosmere.homebrew as hb
from systems.cosmere.build import CosmereBuild
from systems.cosmere.actor import CosmereActor


# --- module: schema + resolution ------------------------------------------
def test_normalize_coerces_type_fields_and_filters_effects():
    e = hb.normalize({'type': 'talent', 'name': '  Stone Skin ', 'path': 'warrior',
                      'effects': [{'target': 'def:phy', 'value': 2},
                                  {'target': 'bogus', 'value': 9},      # unknown target dropped
                                  {'target': 'health', 'value': 0}]})   # zero value dropped
    assert e['type'] == 'talent' and e['name'] == 'Stone Skin' and e['homebrew'] is True
    assert e['id'].startswith('hb:')
    assert e['effects'] == [{'target': 'def:phy', 'value': 2}]
    assert e['path'] == 'warrior'


def test_effect_targets_cover_stats_attrs_and_skills():
    keys = {t['key'] for t in hb.effect_targets()}
    assert {'def:phy', 'deflect', 'health', 'focus', 'investiture'} <= keys
    assert {'attr:str', 'attr:wil'} <= keys
    assert 'skill:ath' in keys                       # a skill target exists


def test_resolve_bonuses_sums_only_selected_and_flags_dangling():
    store = {
        'talent': [{'type': 'talent', 'name': 'Bulwark', 'id': 'hb:bulwark',
                    'effects': [{'target': 'def:phy', 'value': 2}]}],
        'ancestry': [{'type': 'ancestry', 'name': 'Tinmaker',
                      'effects': [{'target': 'health', 'value': 3}]}],
        'item': [{'type': 'item', 'name': 'Ring', 'id': 'hb:ring',
                  'effects': [{'target': 'def:cog', 'value': 1}]}],
    }
    build = {'ancestry': 'Tinmaker', 'talents': [{'id': 'hb:bulwark', 'name': 'Bulwark'},
                                                 {'id': 'hb:ghost', 'name': 'Ghost'}],
             'inventory': [{'id': 'hb:ring', 'equipped': True}]}
    bonus, sources, dangling = hb.resolve_bonuses(build, store)
    assert bonus == {'def:phy': 2, 'health': 3, 'def:cog': 1}
    assert set(sources) == {'Bulwark', 'Tinmaker', 'Ring'}
    assert dangling == ['Ghost']                     # selected hb: talent not in the store
    # an UNequipped homebrew item contributes nothing
    b2, _, _ = hb.resolve_bonuses({'inventory': [{'id': 'hb:ring', 'equipped': False}]}, store)
    assert 'def:cog' not in b2


def test_homebrew_armor_deflect_bridges_to_engine():
    """Homebrew armor is invisible to the canon-only Inventory, so its Deflect
    must be bridged into the bonus map when equipped."""
    store = {'item': [{'type': 'item', 'name': 'Aegis', 'id': 'hb:aegis', 'kind': 'armor',
                       'deflect': 3, 'effects': [{'target': 'def:spi', 'value': 1}]}]}
    bonus, _, _ = hb.resolve_bonuses({'inventory': [{'id': 'hb:aegis', 'equipped': True}]}, store)
    assert bonus == {'def:spi': 1, 'deflect': 3}
    b = CosmereBuild({'inventory': [{'id': 'hb:aegis', 'equipped': True}]}, homebrew=store)
    assert b.deflect_value() == 3


def test_homebrew_order_and_path_lookups():
    store = {'radiant_path': [{'type': 'radiant_path', 'name': 'Sandbearer', 'spren': 'Sandspren',
                               'surges': ['grv', 'adh'], 'philosophy': 'Endure.'}],
             'heroic_path': [{'type': 'heroic_path', 'name': 'Stormwarden',
                              'key_talent': 'Resolute Stance', 'start_skill': 'ath'}]}
    o = hb.radiant_order(store, 'sandbearer')
    assert o and o['spren'] == 'Sandspren' and o['surges'] == ('grv', 'adh')
    p = hb.heroic_path(store, 'stormwarden')
    assert p and p['key_talent'] == 'Resolute Stance' and p['start_skill'] == 'ath'


# --- engine: bonuses applied + round-trip ----------------------------------
def test_engine_applies_homebrew_bonuses_and_cascades():
    store = {'culture': [{'type': 'culture', 'name': 'Forgemark',
                          'effects': [{'target': 'attr:str', 'value': 1}]}],
             'talent': [{'type': 'talent', 'name': 'Bulwark', 'id': 'hb:bulwark',
                         'effects': [{'target': 'def:phy', 'value': 2}, {'target': 'health', 'value': 3},
                                     {'target': 'skill:ath', 'value': 1}]}]}
    data = {'level': 1, 'culture': 'Forgemark', 'attributes': {'str': 2, 'spd': 1},
            'skills': {'ath': 1}, 'talents': [{'id': 'hb:bulwark', 'name': 'Bulwark'}]}
    b = CosmereBuild(data, homebrew=store)
    # str 2 + culture attr:str 1 = 3 (cascades). phy = 10 + str3 + spd1 + def:phy2 = 16
    assert b.eff_attributes()['str'] == 3
    assert b.defenses()['phy'] == 16
    assert b.health_max() == 10 + 3 + 3            # 10+effSTR + homebrew health
    assert b.skill_mods()['ath'] == 1 + 3 + 1      # rank + effSTR + homebrew skill


def test_actor_doc_roundtrip_preserves_homebrew():
    store = {'item': [{'type': 'item', 'name': 'Aegis', 'id': 'hb:aegis', 'kind': 'equipment',
                       'effects': [{'target': 'def:spi', 'value': 2}, {'target': 'focus', 'value': 1}]}]}
    data = {'level': 3, 'attributes': {'str': 1, 'spd': 1, 'int': 1, 'wil': 2, 'awa': 1, 'pre': 1},
            'inventory': [{'id': 'hb:aegis', 'equipped': True}]}
    b = CosmereBuild(data, homebrew=store)
    a = CosmereActor({'system_data': b.to_actor_doc(), 'name': 'X', 'system': 'cosmere'})
    assert a.defenses == b.defenses()
    assert a.focus_max == b.focus_max()
    assert a.health_max == b.health_max()


def test_no_homebrew_is_byte_unchanged():
    data = {'level': 2, 'attributes': {'str': 2, 'spd': 1, 'int': 1, 'wil': 1}}
    plain = CosmereBuild(data)
    assert plain.homebrew_bonuses == {} and plain.homebrew_dangling == []
    assert plain.defenses() == {'phy': 13, 'cog': 12, 'spi': 10}


def test_homebrew_radiant_order_drives_surges():
    store = {'radiant_path': [{'type': 'radiant_path', 'name': 'Sandbearer', 'spren': 'Sandspren',
                               'surges': ['grv', 'adh'], 'philosophy': 'Endure.'}]}
    b = CosmereBuild({'level': 3, 'radiant_order': 'sandbearer', 'ideals_sworn': 1}, homebrew=store)
    assert b.is_radiant and b.order()['name'] == 'Sandbearer'
    assert set(b.surges_unlocked()) == {'grv', 'adh'}     # the homebrew order's surges unlock
    assert b.investiture_max() > 0


# --- routes + builder surfacing -------------------------------------------
@pytest.fixture
def cosmere_mode(tmp_path, monkeypatch):
    """Put the app in Cosmere mode with a throwaway homebrew file."""
    f = tmp_path / 'homebrew.json'
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(app, 'COSMERE_HOMEBREW_FILE', str(f))
    return f


def test_homebrew_save_list_delete(cosmere_mode):
    c = app.app.test_client()
    r = c.post('/cosmere/homebrew/save', json={'entry': {
        'type': 'talent', 'name': 'Resolute Stance', 'path': 'warrior',
        'effects': [{'target': 'def:phy', 'value': 1}], 'notes': 'Hold the line.'}})
    assert r.get_json()['ok']
    eid = r.get_json()['id']
    # persisted to the file
    assert json.loads(cosmere_mode.read_text())['talent'][0]['name'] == 'Resolute Stance'
    # manager page lists it
    body = c.get('/cosmere/homebrew').data.decode()
    assert 'Resolute Stance' in body and 'Homebrew' in body
    # delete
    assert c.post('/cosmere/homebrew/%s/delete' % eid, json={}).get_json()['ok']
    assert json.loads(cosmere_mode.read_text())['talent'] == []


def test_homebrew_surfaces_in_builder(cosmere_mode):
    cosmere_mode.write_text(json.dumps({'culture': [
        {'type': 'culture', 'name': 'Forgemark', 'effects': [{'target': 'attr:str', 'value': 1}]}]}))
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Forgemark' in body                # homebrew reached the builder's JS data
    assert 'mergeHomebrew' in body            # the picker-merge bootstrap is wired
