"""Numeric layers on the 5e engine: magic items, toggled effects, always-on
class features, in-feature option choices, detected limited uses."""
import pytest

from systems.dnd5e import content
from systems.dnd5e.rules.character import compute

pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')
ALL13 = {a: 13 for a in ('str', 'dex', 'con', 'int', 'wis', 'cha')}


def sheet(cls, n, *, edition=None, scores=None, choices=None, inventory=None, effects=None, race=None):
    ed = edition or ('one' if cls.endswith('xphb') else 'classic')
    b = {'edition': ed, 'ability_method': 'manual', 'base_scores': scores or dict(ALL13),
         'levels': [{'class': cls}] * n, 'choices': choices or {}}
    if race:
        b['race'] = race
    s = compute({'build': b, 'state': {'inventory': inventory or [], 'effects': effects or []}})
    assert s['errors'] == [], s['errors']
    return s


def test_items_set_and_raise_scores_and_add_save_bonus():
    s = sheet('fighter|xphb', 1, inventory=[
        {'id': 'belt of hill giant strength|xdmg', 'equipped': True, 'attuned': True},
        {'id': 'cloak of protection|xdmg', 'equipped': True, 'attuned': True}])
    assert s['abilities']['str']['score'] == 21             # static 21 beats 13
    assert s['abilities']['str']['save'] == 5 + 2 + 1       # mod 5 + PB 2 + cloak 1
    assert s['abilities']['wis']['save'] == 1 + 1           # cloak applies to every save
    assert s['ac']['value'] == 11 + 1                       # unarmored 10+1, cloak +1
    # unattuned: nothing
    s2 = sheet('fighter|xphb', 1, inventory=[
        {'id': 'belt of hill giant strength|xdmg', 'equipped': True},
        {'id': 'cloak of protection|xdmg', 'equipped': True}])
    assert s2['abilities']['str']['score'] == 13 and s2['ac']['value'] == 11


def test_rage_folds_damage_and_resistance_only_while_active():
    inv = [{'id': 'greataxe|xphb', 'equipped': True}]
    calm = sheet('barbarian|xphb', 5, inventory=inv)
    axe = calm['attacks'][0]
    assert axe['damage'] == '1d12+1' and 'slashing' not in calm['resist']
    rage = sheet('barbarian|xphb', 5, inventory=inv, effects=['rage'])
    assert rage['attacks'][0]['damage'] == '1d12+3'         # +2 rage damage at level 5
    assert {'bludgeoning', 'piercing', 'slashing'} <= set(rage['resist'])
    assert 'rage' in rage['effects']['active']
    # a non-barbarian can't toggle rage on
    assert 'rage' not in sheet('fighter|xphb', 5, effects=['rage'])['effects']['active']


def test_bear_totem_rage_resists_everything_but_psychic():
    s = sheet('barbarian|phb', 3, effects=['rage'], choices={
        'subclass:barbarian|phb': ['path of the totem warrior|phb|barbarian|phb'],
        'featopt:barbarian|phb:totem spirit|3': ['Bear']})
    assert 'psychic' not in s['resist'] and {'fire', 'necrotic', 'slashing'} <= set(s['resist'])
    assert 'Bear' in [f['name'] for f in s['features']]
    assert 'Eagle' not in [f['name'] for f in s['features']]   # only the chosen option


def test_ac_effects():
    base = sheet('wizard|xphb', 2)['ac']['value']          # 10 + DEX 1
    assert sheet('wizard|xphb', 2, effects=['mage_armor'])['ac']['value'] == 14
    assert sheet('wizard|xphb', 2, effects=['mage_armor', 'shield'])['ac']['value'] == 19
    assert sheet('wizard|xphb', 2, effects=['barkskin'])['ac']['value'] == 17   # 2024 floor
    assert sheet('wizard|phb', 2, effects=['barkskin'])['ac']['value'] == 16    # 2014 floor
    hasted = sheet('wizard|xphb', 2, effects=['haste'])
    assert hasted['ac']['value'] == base + 2 and hasted['speed']['walk'] == 60


def test_bladesong_adds_int_to_ac_and_speed():
    s = sheet('wizard|xphb', 3, scores=dict(ALL13, int=18), effects=['bladesong'], choices={
        'subclass:wizard|xphb': ['bladesinger|frhof|wizard|xphb']})
    assert s['ac']['value'] == 11 + 4 and s['speed']['walk'] == 40


def test_aura_of_protection_and_dread_ambusher():
    pal = sheet('paladin|xphb', 6, scores=dict(ALL13, cha=16))
    assert pal['abilities']['dex']['save'] == 1 + 3          # DEX mod 1 + CHA 3
    assert pal['abilities']['wis']['save'] == 1 + 3 + 3      # proficient: +PB 3
    gs = sheet('ranger|xphb', 3, scores=dict(ALL13, wis=16), choices={
        'subclass:ranger|xphb': ['gloom stalker|xphb|ranger|xphb']})
    assert gs['initiative'] == 1 + 3


def test_divine_order_thaumaturge_skill_bonus():
    s = sheet('cleric|xphb', 1, scores=dict(ALL13, wis=16), choices={
        'feature:cleric|xphb:divine order:1:0': ['Thaumaturge']})
    assert s['skills']['religion']['mod'] == 1 + 3           # INT 1 + WIS 3 (not proficient)
    assert s['skills']['arcana']['mod'] == 1 + 3


def test_radiant_strikes_and_dueling():
    inv = [{'id': 'longsword|xphb', 'equipped': True}, {'id': 'shield|xphb', 'equipped': True}]
    s = sheet('paladin|xphb', 11, scores=dict(ALL13, str=16), inventory=inv,
              choices={'featprog:paladin|xphb:Fighting Style:0': ['dueling|xphb']})
    sword = s['attacks'][0]
    assert sword['damage'] == '1d8+5'                         # STR 3 + Dueling 2 (shield is not a weapon)
    assert any('radiant' in x for x in sword['extra_damage'])


def test_pact_weapon_uses_charisma():
    inv = [{'id': 'longsword|xphb', 'equipped': True, 'pact': True}]
    s = sheet('warlock|xphb', 3, scores=dict(ALL13, cha=18), inventory=inv,
              choices={'opt:warlock|xphb:Eldritch Invocations': ['pact of the blade|xphb']})
    assert s['attacks'][0]['ability'] == 'cha' and s['attacks'][0]['to_hit'] == 4 + 2


def test_detected_and_innate_uses():
    orc = sheet('fighter|xphb', 1, race='orc|xphb')
    keys = {r['key']: (r['max'], r['recharge']) for r in orc['resources']}
    assert keys['feature:relentless endurance'] == (1, 'long')
    assert keys['feature:adrenaline rush'] == (2, 'short')     # PB at level 1
    tief = sheet('wizard|phb', 5, race='tiefling|phb')
    spells = {r['name']: r['max'] for r in tief['resources'] if r['key'].startswith('spell:')}
    assert spells.get('Hellish Rebuke (free cast)') == 1      # Infernal Legacy, from level 3


def test_circle_of_the_land_spell_list_is_a_choice():
    s = sheet('druid|phb', 3, choices={'subclass:druid|phb': ['circle of the land|phb|druid|phb']})
    alt = [c for c in s['choices'] if c['kind'] == 'alternative' and 'spell list' in c['label']]
    assert alt and {'Arctic', 'Coast', 'Desert'} <= {o['label'] for o in alt[0]['options']}
    assert not any(a['name'] == 'Hold Person' for a in s['spellcasting']['always'])  # nothing until chosen
    s2 = sheet('druid|phb', 3, choices={'subclass:druid|phb': ['circle of the land|phb|druid|phb'],
                                         alt[0]['id']: [0]})                       # Arctic
    assert any(a['name'] == 'Hold Person' for a in s2['spellcasting']['always'])


def test_spell_numbers_from_data():
    from systems.dnd5e.rules.spells import spell_numbers
    n = spell_numbers(content.get('spell', 'fireball|xphb'), char_level=5)
    assert (n['save'], n['dice'], n['type'], n['per_slot']) == ('DEX', '8d6', 'fire', '1d6')
    n = spell_numbers(content.get('spell', 'fire bolt|xphb'), char_level=11)
    assert (n['attack'], n['dice']) == ('ranged', '3d10')           # cantrip scaling at 11
    n = spell_numbers(content.get('spell', 'cure wounds|phb'), char_level=1, ability_mod=3)
    assert (n['kind'], n['dice'], n['bonus'], n['per_slot']) == ('healing', '1d8', 3, '1d8')
    n = spell_numbers(content.get('spell', 'sleep|phb'), char_level=1)
    assert n['kind'] is None                                         # 5d8 HP pool is not healing
    n = spell_numbers(content.get('spell', 'eldritch blast|xphb'), char_level=11)
    assert (n['dice'], n['count']) == ('1d10', 3)


def test_effect_toggle_and_custom_resource_services(tmp_path, monkeypatch):
    import uuid
    from core import storage
    from systems.dnd5e import service, store
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'c'))
    store.forget_cache()
    cid = uuid.uuid4().hex
    doc = store.new_pc(cid, name='Grog', edition='one')
    doc['build'].update({'ability_method': 'manual', 'base_scores': dict(ALL13),
                         'levels': [{'class': 'barbarian|xphb'}] * 3})
    store.save_pc(cid, doc)
    pid = doc['id']
    doc, s = service.toggle_effect(cid, pid, 'rage', True)
    assert 'slashing' in s['resist']
    live = service.pc_live(doc)
    assert 'rage' in live['effects']
    with pytest.raises(service.ServiceError):
        service.toggle_effect(cid, pid, 'bladesong', True)            # not a bladesinger
    doc, s = service.custom_resource(cid, pid, {'name': 'Wand charges', 'max': 7, 'recharge': 'long'})
    key = next(r['key'] for r in s['resources'] if r['name'] == 'Wand charges')
    live, _ = service.resource_action(cid, pid, 'resource', {'key': key, 'delta': 2})
    assert next(r for r in live['resources'] if r['key'] == key)['used'] == 2
    live, _ = service.rest(cid, pid, 'short')                         # rage ends on a rest
    assert 'rage' not in live['effects']
    live, _ = service.rest(cid, pid, 'long')
    assert next(r for r in live['resources'] if r['key'] == key)['used'] == 0
    store.forget_cache()


def test_versatile_is_hidden_with_a_shield():
    sword = [{'id': 'longsword|xphb', 'equipped': True}]
    assert sheet('fighter|xphb', 1, inventory=sword)['attacks'][0]['versatile'] == '1d10+1'
    s = sheet('fighter|xphb', 1, inventory=sword + [{'id': 'shield|xphb', 'equipped': True}])
    assert s['attacks'][0]['versatile'] is None


def test_item_spells_cost_charges():
    from systems.dnd5e.routes import _item_spells
    s = sheet('fighter|xphb', 1, inventory=[
        {'id': 'staff of healing|dmg', 'equipped': True, 'attuned': True, 'uid': 'abc'}])
    charges = {r['key']: r['max'] for r in s['resources']}
    assert charges['item:abc'] == 10
    rows = _item_spells(s, lambda rec, caster: {})
    costs = {r['name']: r['item']['cost'] for r in rows}
    assert costs == {'Cure Wounds': 1, 'Lesser Restoration': 2, 'Mass Cure Wounds': 5}
    assert all(r['item']['resource'] == 'item:abc' for r in rows)
    wand = sheet('fighter|xphb', 1, inventory=[
        {'id': 'wand of fireballs|dmg', 'equipped': True, 'attuned': True, 'uid': 'w'}])
    assert [(r['name'], r['dc']) for r in _item_spells(wand, lambda rec, caster: {})] == [('Fireball', 15)]
    # not attuned: the staff does nothing
    s2 = sheet('fighter|xphb', 1, inventory=[{'id': 'staff of healing|dmg', 'equipped': True, 'uid': 'abc'}])
    assert _item_spells(s2, lambda rec, caster: {}) == []


def test_tool_category_choices_offer_specific_tools():
    # Inheritor: "choose a musical instrument or a gaming set" -> the actual tools
    s = compute({'build': {'edition': 'classic', 'background': 'inheritor|scag', 'ability_method': 'manual',
                           'base_scores': dict(ALL13), 'levels': [{'class': 'fighter|phb'}], 'choices': {}},
                 'state': {}})
    tool = next(c for c in s['choices'] if c['id'].startswith('bg:tool'))
    assert 'dragonchess set' in tool['options'] and 'lute' in tool['options']
    assert 'gaming set' not in tool['options'] and 'musical instrument' not in tool['options']


def test_thieves_cant_language_by_edition():
    old = sheet('rogue|phb', 1)
    assert "thieves' cant" in old['proficiencies']['languages']
    assert not any('Thieves' in c['label'] for c in old['choices'])          # 2014: no extra pick
    new = sheet('rogue|xphb', 1)
    assert "thieves' cant" in new['proficiencies']['languages']
    assert any("Thieves' Cant" in c['label'] for c in new['choices'])        # 2024: one more language
