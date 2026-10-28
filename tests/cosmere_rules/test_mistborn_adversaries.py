"""MB-07: Mistborn adversaries from the pinned catalog into the bestiary and tracker.

The unit tests use synthetic placeholder records (no rules content); the page
tests run against the installed private bundle and skip without it.
"""
import pytest

from systems.cosmere import CosmereActor
from systems.cosmere.mistborn_adversaries import (actor_document, bestiary_rows,
                                                  find_actor_document, is_catalog_adversary_id)
from systems.cosmere.rules.paths import registry_path

NEEDS_PRIVATE_DATA = pytest.mark.skipif(not registry_path().is_file(),
                                        reason='private Cosmere rules data is not installed')


def _record(rid='mistborn.adversary.test-thug', **data):
    base = {'actions': [f'{rid}.action.club', f'{rid}.action.missing'],
            'deflect': 1, 'features': ['Tough'], 'focus': 3,
            'health': {'default': 30, 'range': {'min': 25, 'max': 35}}, 'investiture': 2,
            'movement_ft': 25, 'role': 'rival', 'senses_ft': 20, 'size': 'medium',
            'statistics': {'strength': 3, 'speed': 2, 'intellect': 1, 'willpower': 2,
                           'awareness': 1, 'presence': 0, 'physical_defense': 15,
                           'cognitive_defense': 13, 'spiritual_defense': 11},
            'tier': 1, 'type': 'humanoid'}
    base.update(data)
    return {'id': rid, 'kind': 'adversary', 'name': 'Test Thug', 'parameters': {'data': base}}


def _action(aid, name, cost):
    return {'id': aid, 'kind': 'action', 'name': name, 'summary': f'{name} placeholder.',
            'parameters': {'data': {'action_cost': cost, 'kind': 'attack'}}}


def _catalog():
    rid = 'mistborn.adversary.test-thug'
    return {'catalogs': {
        'adversary': [_record(), _record('mistborn.adversary.a-boss', tier=3, role='boss')],
        'action': [_action(f'{rid}.action.club', 'Strike: Club', 'one')]}}


def test_catalog_adversary_ids_exclude_their_actions_and_other_systems():
    assert is_catalog_adversary_id('mistborn.adversary.test-thug')
    assert not is_catalog_adversary_id('mistborn.adversary.test-thug.action.club')
    assert not is_catalog_adversary_id('a1b2c3d4e5f6')
    assert not is_catalog_adversary_id(None)


def test_bestiary_rows_sort_by_tier_then_name():
    rows = bestiary_rows(_catalog())
    assert [r['id'] for r in rows] == ['mistborn.adversary.test-thug', 'mistborn.adversary.a-boss']
    assert rows[1] == {'id': 'mistborn.adversary.a-boss', 'name': 'Test Thug',
                       'tier': 3, 'role': 'boss', 'size': 'medium'}


def test_actor_document_reads_into_a_tracker_actor():
    doc = find_actor_document(_catalog(), 'mistborn.adversary.test-thug')
    actor = CosmereActor(doc)
    assert actor.name == 'Test Thug'
    assert actor.health_max == 30 and actor.focus_max == 3 and actor.investiture_max == 2
    summary = actor.to_summary()
    assert summary['defenses']['phy'] == 15
    assert summary['attributes']['str'] == 3
    names = [a['name'] for a in actor.actions]
    assert names == ['Strike: Club', 'Missing']
    assert actor.traits == ['Tough']
    club = actor.actions[0]
    assert club['activation']['cost'] == {'type': 'action', 'value': 1}
    assert doc['items'][2]['system']['description']['short'] == 'Not available in the installed rules.'


def test_actor_document_never_guesses_a_strike_from_a_described_attack():
    # The catalog states no attack bonus or damage formula, so the action stays
    # an ability rather than becoming a strike with invented numbers.
    actor = CosmereActor(find_actor_document(_catalog(), 'mistborn.adversary.test-thug'))
    assert actor.strikes == []


def test_flat_or_missing_values_fall_back_safely():
    doc = actor_document(_record(health=12, statistics={}, focus='x', actions=None), {})
    actor = CosmereActor(doc)
    assert actor.health_max == 12 and actor.focus_max == 0
    assert doc['items'] == [{'_id': 'mistborn.adversary.test-thug.feature.0', 'type': 'trait',
                             'name': 'Tough', 'system': {}}]


def test_unknown_or_foreign_ids_find_nothing():
    assert find_actor_document(_catalog(), 'mistborn.adversary.nobody') is None
    assert find_actor_document(_catalog(), 'some-stormlight-id') is None


@pytest.fixture
def mistborn_tracker(tmp_path, monkeypatch):
    import app as host
    from systems.cosmere.rules.profiles import mistborn_campaign_binding
    from systems.cosmere.rules.schema import to_dict
    monkeypatch.setattr(host, 'CAMPAIGN_FILE', str(tmp_path / 'campaign.json'))
    monkeypatch.setattr(host, 'COSMERE_PC_DIR', str(tmp_path / 'cosmere_pcs'))
    monkeypatch.setattr(host, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(host, 'ACTIVE_CAMPAIGN_ID', None)
    monkeypatch.setattr(host, 'ACTIVE_ENCOUNTER', [])
    monkeypatch.setattr(host, '_is_gm', lambda: True)
    monkeypatch.setattr(host, '_account_mode', lambda: False)
    monkeypatch.setattr(host, '_active_system', lambda: 'cosmere')
    monkeypatch.setattr(host, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(host, '_broadcast_encounter_state', lambda *a, **k: None)

    def bind(rules):
        if rules == 'stormlight':
            host._save_campaign_config({}, remove=('rules_binding',))
        else:
            host._save_campaign_config({'rules_binding': to_dict(mistborn_campaign_binding(rules)),
                                        'cosmere_theme': 'mistborn', 'cosmere_world': 'mistborn'})
        return host, host.app.test_client()
    return bind


@NEEDS_PRIVATE_DATA
@pytest.mark.parametrize('era', ['era-1', 'era-2'])
def test_mistborn_bestiary_lists_only_the_era_adversaries(mistborn_tracker, era):
    host, client = mistborn_tracker(era)
    rows = host._cosmere_adversary_rows()
    assert rows and all(r['id'].startswith('mistborn.adversary.') for r in rows)
    html = client.get('/cosmere/bestiary').get_data(as_text=True)
    assert 'Mistborn Assassin' in html if era == 'era-1' else True
    stormlight_ids = {d.get('_id') for d in __import__('systems.cosmere', fromlist=['x']).adversary_docs()}
    assert not ({r['id'] for r in rows} & stormlight_ids)


@NEEDS_PRIVATE_DATA
def test_stormlight_campaign_keeps_the_stormlight_bestiary(mistborn_tracker):
    import systems.cosmere as cosmere
    host, client = mistborn_tracker('stormlight')
    assert {r['id'] for r in host._cosmere_adversary_rows()} == \
        {d.get('_id') for d in cosmere.adversary_docs()}
    assert host._cosmere_doc_by_id('mistborn.adversary.mistborn-assassin') is None


@NEEDS_PRIVATE_DATA
def test_mistborn_adversary_statblock_and_tracker_add(mistborn_tracker):
    host, client = mistborn_tracker('era-1')
    sheet = client.get('/cosmere/sheet/mistborn.adversary.mistborn-assassin')
    assert sheet.status_code == 200
    html = sheet.get_data(as_text=True)
    assert 'Burn Iron/Steel' in html and 'Ironpull/Steelpush' in html
    response = client.post('/api/add_combatant', json={
        'type': 'cosmere', 'path': 'mistborn.adversary.mistborn-assassin'})
    assert response.status_code == 200
    added = host.ACTIVE_ENCOUNTER[-1]
    assert added.name == 'Mistborn Assassin' and added.restore_id == 'mistborn.adversary.mistborn-assassin'
    assert added.max_hp == added.current_hp > 0
