"""D&D 5e services: storage + engine + combat together (no Flask).

The key invariant under test: a PC's vitals exist once, in the character
document, so tracker damage and sheet edits can never drift apart."""
import random
import uuid

import pytest

from core import storage
from systems.dnd5e import content, service, store

pytestmark = pytest.mark.skipif(not content.available(), reason='dnd5e.db not built')


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    store.forget_cache()
    c = uuid.uuid4().hex
    yield c
    store.forget_cache()


def _fighter(cid, name='Brann', level=3):
    doc = store.new_pc(cid, name=name, edition='one')
    doc['build'].update({
        'race': 'dwarf|xphb', 'background': 'soldier|xphb', 'ability_method': 'standard',
        'base_scores': {'str': 15, 'dex': 14, 'con': 13, 'int': 8, 'wis': 12, 'cha': 10},
        'levels': [{'class': 'fighter|xphb', 'hp': None}] * level,
    })
    store.save_pc(cid, doc)
    return doc['id']


def test_tracker_damage_lands_on_the_character_sheet(cid):
    pid = _fighter(cid)
    service.combat_action(cid, 'add_pc', {'pc_id': pid})
    enc, _, _ = service.combat_action(cid, 'add_monster', {'monster_id': 'goblin warrior|xmm', 'count': 2})
    names = [c['name'] for c in enc['combatants']]
    assert names == ['Brann', 'Goblin Warrior', 'Goblin Warrior 2']
    pc_cid = enc['combatants'][0]['cid']
    s = service.sheet_for(store.load_pc(cid, pid))
    full = s['hp_max']
    _, ev, touched = service.combat_action(cid, 'damage', {'cid': pc_cid, 'amount': 7, 'type': 'slashing'})
    assert touched == [pid]
    doc = store.load_pc(cid, pid)
    assert doc['state']['hp'] == full - 7
    # ... and the sheet-side edit is visible to the tracker view
    service.vitals_action(cid, pid, 'heal', {'amount': 3})
    view = service.encounter_view(cid)
    assert view['combatants'][0]['hp'] == full - 4


def test_player_view_hides_monster_numbers_and_hidden_creatures(cid):
    service.combat_action(cid, 'add_monster', {'monster_id': 'goblin warrior|xmm'})
    service.combat_action(cid, 'add_monster', {'monster_id': 'goblin warrior|xmm', 'hidden': True})
    view = service.encounter_view(cid, for_player=True)
    assert len(view['combatants']) == 1
    row = view['combatants'][0]
    assert 'hp' not in row and 'ac' not in row and row['status'] == 'Unhurt'
    assert view['log'] == []


def test_initiative_start_and_turns(cid):
    pid = _fighter(cid)
    enc, _, _ = service.combat_action(cid, 'add_pc', {'pc_id': pid})
    service.combat_action(cid, 'add_monster', {'monster_id': 'goblin warrior|xmm'})
    with pytest.raises(service.ServiceError):             # PC initiative not entered
        service.combat_action(cid, 'start', {}, rng=random.Random(1))
    pc_cid = enc['combatants'][0]['cid']
    service.combat_action(cid, 'set_initiative', {'cid': pc_cid, 'initiative': 30})
    enc, ev, _ = service.combat_action(cid, 'start', {}, rng=random.Random(1))
    assert enc['started'] and enc['round'] == 1 and enc['combatants'][0]['name'] == 'Brann'
    enc, _, _ = service.combat_action(cid, 'next', {})
    enc, _, _ = service.combat_action(cid, 'next', {})
    assert enc['round'] == 2


def test_pc_dropping_to_zero_prompts_death_saves_on_its_turn(cid):
    pid = _fighter(cid, level=1)
    enc, _, _ = service.combat_action(cid, 'add_pc', {'pc_id': pid})
    pc_cid = enc['combatants'][0]['cid']
    service.combat_action(cid, 'set_initiative', {'cid': pc_cid, 'initiative': 10})
    service.combat_action(cid, 'start', {})
    service.combat_action(cid, 'damage', {'cid': pc_cid, 'amount': 12})
    doc = store.load_pc(cid, pid)
    assert doc['state']['hp'] == 0 and 'unconscious' in doc['state']['conditions']
    _, ev, _ = service.combat_action(cid, 'next', {})      # only combatant: its turn again
    assert any(e['type'] == 'death_save_due' for e in ev)


def test_slots_resources_and_rests(cid):
    doc = store.new_pc(cid, name='Mira', edition='classic')
    doc['build'].update({'race': 'human|phb', 'background': 'sage|phb', 'ability_method': 'manual',
                         'base_scores': {'str': 8, 'dex': 14, 'con': 14, 'int': 16, 'wis': 12, 'cha': 10},
                         'levels': [{'class': 'wizard|phb', 'hp': 4}] * 3})
    store.save_pc(cid, doc)
    pid = doc['id']
    live, _ = service.resource_action(cid, pid, 'slot', {'level': 1, 'delta': 1})
    live, _ = service.resource_action(cid, pid, 'slot', {'level': 1, 'delta': 9})
    assert live['slots']['1'] == {'max': 4, 'used': 4}            # clamped at max
    live, _ = service.resource_action(cid, pid, 'resource', {'key': 'arcane_recovery'})
    service.vitals_action(cid, pid, 'damage', {'amount': 10})
    live, _ = service.resource_action(cid, pid, 'hit_die', {'die': 'd6', 'roll': 4})
    assert live['hit_dice']['d6']['used'] == 1
    hp_after_hd = live['hp']
    assert hp_after_hd == live['hp_max'] - 10 + 4 + 2             # roll + CON
    live, _ = service.rest(cid, pid, 'short')
    assert live['slots']['1']['used'] == 4                        # wizard slots: long rest only
    live, _ = service.rest(cid, pid, 'long')
    assert live['slots']['1']['used'] == 0 and live['hp'] == live['hp_max']
    assert live['hit_dice']['d6']['used'] == 0                    # regains up to half level (min 1)
    assert next(r for r in live['resources'] if r['key'] == 'arcane_recovery')['used'] == 0


def test_build_rejects_invalid_but_accepts_incomplete(cid):
    pid = _fighter(cid)
    with pytest.raises(service.ServiceError) as e:
        service.update_build(cid, pid, {'choices': {'cls:fighter|xphb:skill:c0': ['arcana']}})
    assert e.value.status == 422
    doc, s = service.update_build(cid, pid, {'choices': {'cls:fighter|xphb:skill:c0': ['athletics']}})
    assert s['errors'] == [] and s['pending']                     # incomplete is fine


def test_level_up_and_multiclass_prereqs(cid):
    pid = _fighter(cid, level=1)
    doc, s = service.level_up(cid, pid, 'fighter|xphb', hp_roll=6)
    assert s['level'] == 2
    with pytest.raises(service.ServiceError) as e:                # INT 8: no Wizard
        service.level_up(cid, pid, 'wizard|xphb')
    assert 'prerequisites' in e.value.message
    doc, s = service.level_down(cid, pid)
    assert s['level'] == 1


def test_attunement_limit(cid):
    pid = _fighter(cid)
    for _ in range(4):
        service.inventory_action(cid, pid, 'add', {'id': 'cloak of protection|dmg'})
    for i in range(3):
        service.inventory_action(cid, pid, 'attune', {'index': i, 'on': True})
    with pytest.raises(service.ServiceError):
        service.inventory_action(cid, pid, 'attune', {'index': 3, 'on': True})


def test_encounter_difficulty_service():
    r = service.difficulty('one', [3, 3, 3, 3], ['goblin warrior|xmm'] * 4)
    assert r['base_xp'] == 200 and r['thresholds']['low'] == 600


def test_player_prompt_filter_never_names_hidden_creatures():
    from systems.dnd5e.routes import player_prompts
    enc = {'combatants': [
        {'cid': 'a', 'kind': 'pc', 'name': 'Ana'},
        {'cid': 'b', 'kind': 'monster', 'name': 'Goblin'},
        {'cid': 'c', 'kind': 'monster', 'name': 'Lurking Horror', 'hidden': True},
    ]}
    turn = lambda cid, name: {'type': 'turn', 'cid': cid, 'name': name}
    assert player_prompts({'events': [turn('c', 'Lurking Horror')]}, enc) is None
    assert player_prompts({'events': [turn('b', 'Goblin')]}, enc)['events'] == [turn('b', 'Goblin')]
    # a monster's save reminder stays with the GM; a PC's dying prompt is shared
    rem = {'type': 'save_ends_reminder', 'cid': 'b', 'name': 'Goblin', 'condition': 'prone', 'save': {}}
    dying = {'type': 'dying', 'name': 'Ana'}
    assert player_prompts({'events': [rem, dying]}, enc)['events'] == [dying]


def test_hp_changes_do_not_invalidate_an_open_builder(cid):
    pid = _fighter(cid)
    doc = store.load_pc(cid, pid)
    rev = doc.get('build_rev', 0)
    service.vitals_action(cid, pid, 'damage', {'amount': 3})       # combat in progress
    doc, s = service.update_build(cid, pid, {'name': 'Brann II'}, expected_revision=rev)
    assert doc['name'] == 'Brann II' and doc['build_rev'] == rev + 1
    with pytest.raises(service.ServiceError) as e:                 # but a stale BUILD is refused
        service.update_build(cid, pid, {'name': 'Brann III'}, expected_revision=rev)
    assert e.value.status == 409


def test_unknown_conditions_are_rejected(cid):
    pid = _fighter(cid)
    with pytest.raises(service.ServiceError):
        service.vitals_action(cid, pid, 'condition', {'condition': '<img src=x>', 'on': True})
    live, _ = service.vitals_action(cid, pid, 'condition', {'condition': 'Prone', 'on': True})
    assert 'prone' in live['conditions']
