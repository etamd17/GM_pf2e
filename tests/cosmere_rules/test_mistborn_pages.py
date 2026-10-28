"""MB-07: Mistborn-rules characters on every Cosmere page, and the Mistborn builder."""
from copy import deepcopy
import json
import uuid

import pytest

from systems.cosmere.mistborn_build import ERA_METALS, MistbornBuild
from systems.cosmere.mistborn_display import (MISTBORN_ACCENT, card_identity, is_mistborn_build,
                                              metalborn_summary, metallic_arts_panel)
from systems.cosmere.rules.paths import registry_path

NEEDS_PRIVATE_DATA = pytest.mark.skipif(not registry_path().is_file(),
                                        reason='private Cosmere rules data is not installed')

ATTRS = {'str': 1, 'spd': 2, 'int': 2, 'wil': 2, 'awa': 3, 'pre': 2}


def _build(kind, era='era-1', **changes):
    allomantic = kind in ('misting', 'mistborn', 'twinborn')
    feruchemical = kind in ('feruchemist', 'ferring', 'twinborn')
    key = 'mistborn.path.key.' + (f'{kind}-snap' if kind in ('misting', 'mistborn') else f'{kind}-heritage')
    skills = {'stl': 1}
    if allomantic:
        skills['allomancy'] = 2
    if feruchemical:
        skills['feruchemy'] = 2
    data = {
        'name': f'Test {kind}', 'level': 1, 'origin_world': 'scadrial', 'era': era,
        'ancestry': 'Human', 'starting_path': 'agent', 'heroic_paths': ['agent'],
        'metalborn_keys': [key], 'attributes': dict(ATTRS), 'skills': skills,
        'talents': [], 'expertises': [],
        'allomantic_powers': ({m: 'nascent' for m in ERA_METALS[era]} if kind == 'mistborn'
                              else {'tin': 'nascent'} if allomantic else {}),
        'feruchemical_powers': ({m: 'nascent' for m in ERA_METALS[era]} if kind == 'feruchemist'
                                else {'gold': 'nascent'} if feruchemical else {}),
    }
    if kind == 'mistborn' and era == 'era-1':
        data['allomantic_powers']['atium'] = 'unlocked'
    data.update(changes)
    return data


class _Facade:
    """Stands in for the CosmereBuild facade: the engine sits behind _implementation."""

    def __init__(self, inner):
        self._implementation = inner


def test_is_mistborn_build_sees_through_the_facade_and_rejects_other_engines():
    inner = MistbornBuild(_build('misting'))
    assert is_mistborn_build(inner) and is_mistborn_build(_Facade(inner))
    assert not is_mistborn_build(object()) and not is_mistborn_build(_Facade(object()))


def test_misting_summary_lists_its_one_allomantic_metal():
    summary = metalborn_summary(MistbornBuild(_build('misting')))
    assert summary['label'] == 'Misting' and summary['era_label'] == 'Era 1'
    assert [row['metal'] for row in summary['allomancy']] == ['tin']
    assert summary['allomancy'][0]['effect'] == 'Heighten the senses'
    assert summary['feruchemy'] == [] and summary['headline'] == 'Misting · Tin'


def test_feruchemist_rows_say_what_each_metalmind_stores_not_the_allomantic_effect():
    summary = metalborn_summary(MistbornBuild(_build('feruchemist')))
    rows = {row['metal']: row for row in summary['feruchemy']}
    assert set(rows) == set(ERA_METALS['era-1'])
    assert rows['brass']['effect'] == 'Stores warmth'
    assert rows['gold']['effect'] == 'Stores health'
    assert summary['allomancy'] == []
    # Thirteen metals do not fit a card headline; the label alone is shown.
    assert summary['headline'] == 'Feruchemist'


def test_twinborn_summary_has_one_metal_per_art():
    summary = metalborn_summary(MistbornBuild(_build('twinborn', era='era-2')))
    assert summary['label'] == 'Twinborn' and summary['era_label'] == 'Era 2'
    assert [r['metal'] for r in summary['allomancy']] == ['tin']
    assert [r['metal'] for r in summary['feruchemy']] == ['gold']


def test_card_identity_keeps_the_shared_card_keys_with_empty_radiant_fields():
    identity = card_identity(MistbornBuild(_build('misting')))
    assert identity['is_radiant'] is False
    assert identity['order'] == identity['spren'] == '' and identity['surges'] == []
    assert identity['accent'] == MISTBORN_ACCENT
    assert identity['metalborn']['label'] == 'Misting'


def test_arts_panel_reads_saved_runtime_without_mutating_it():
    build = MistbornBuild(_build('twinborn', era='era-2'))
    play_state = {'investiture': 1, 'mistborn': {
        'allomancy': {'current_investiture': 3, 'depleted_metals': [],
                      'active_burns': [{'id': 'e1', 'parameters': {'metal': 'tin'}}],
                      'vial_inventory': {'v1': {'count': 2, 'metals': ['tin', 'steel']}}},
        'feruchemy': {'by_metal': {'gold': {'charge_current': 3, 'charge_max': 4, 'mode': 'storing'}}}}}
    before = deepcopy(play_state)
    panel = metallic_arts_panel(build, play_state)
    assert play_state == before
    tin = panel['allomancy'][0]
    assert tin['burning'] is True and tin['depleted'] is False and tin['vials'] == 2
    gold = panel['feruchemy'][0]
    assert gold['charges'] == 3 and gold['charge_max'] == build.feruchemy_charge_max()
    assert gold['mode'] == 'storing'
    assert panel['investiture'] == 3 and panel['investiture_max'] == build.investiture_max()


def test_arts_panel_clamps_bad_runtime_values_and_defaults_to_full():
    build = MistbornBuild(_build('twinborn', era='era-2'))
    panel = metallic_arts_panel(build, {'mistborn': {
        'allomancy': {'vial_inventory': {'v1': {'count': 'x', 'metals': ['tin']}}},
        'feruchemy': {'by_metal': {'gold': {'charge_current': 99, 'mode': 'sideways'}}}}})
    assert panel['allomancy'][0]['vials'] == 0
    assert panel['feruchemy'][0]['charges'] == build.feruchemy_charge_max()
    assert panel['feruchemy'][0]['mode'] == 'idle'
    assert panel['investiture'] == build.investiture_max()
    assert metallic_arts_panel(build, None)['investiture'] == build.investiture_max()


def test_display_helpers_refuse_a_non_mistborn_build():
    for helper in (metalborn_summary, card_identity):
        with pytest.raises(TypeError):
            helper(object())
    with pytest.raises(TypeError):
        metallic_arts_panel(object(), {})


# --- Pages, against the installed private bundle -------------------------------

@pytest.fixture
def mistborn_campaign(tmp_path, monkeypatch):
    import app as host
    from systems.cosmere.rules.profiles import mistborn_campaign_binding
    from systems.cosmere.rules.schema import to_dict

    def make(era):
        campaign_file = tmp_path / era / 'campaign.json'
        pcs = tmp_path / era / 'cosmere_pcs'
        pcs.mkdir(parents=True)
        monkeypatch.setattr(host, 'CAMPAIGN_FILE', str(campaign_file))
        monkeypatch.setattr(host, 'COSMERE_PC_DIR', str(pcs))
        monkeypatch.setattr(host, 'DATA_DIR', str(tmp_path / era))
        monkeypatch.setattr(host, 'ACTIVE_CAMPAIGN_ID', None)
        monkeypatch.setattr(host, 'ACTIVE_ENCOUNTER', [])
        monkeypatch.setattr(host, '_is_gm', lambda: True)
        monkeypatch.setattr(host, '_account_mode', lambda: False)
        monkeypatch.setattr(host, '_active_system', lambda: 'cosmere')
        monkeypatch.setattr(host, '_cosmere_can_act_on', lambda _doc: True)
        binding = to_dict(mistborn_campaign_binding(era))
        host._save_campaign_config({'name': f'Scadrial {era}', 'rules_binding': binding,
                                    'cosmere_theme': 'mistborn', 'cosmere_world': 'mistborn'})
        monkeypatch.setattr(host, '_cosmere_world', lambda: 'mistborn')
        ids = []
        for kind in ('misting', 'feruchemist' if era == 'era-1' else 'twinborn'):
            doc = host._build_cosmere_document(
                {'build': dict(_build(kind, era=era), rules_binding=binding,
                               cosmere_theme='mistborn'), 'force': True}, None)
            doc.update(id=uuid.uuid4().hex, owner_user_id=None)
            host._save_cosmere_pc(doc)
            ids.append(doc['id'])
        return host, host.app.test_client(), ids, pcs
    return make


@NEEDS_PRIVATE_DATA
@pytest.mark.parametrize('era', ['era-1', 'era-2'])
def test_every_cosmere_page_loads_for_mistborn_characters(mistborn_campaign, era):
    host, client, ids, pcs = mistborn_campaign(era)
    before = {p.name: p.read_bytes() for p in pcs.iterdir()}
    urls = ['/cosmere/builder', '/cosmere/pcs', '/cosmere/player', '/cosmere/bestiary',
            '/cosmere/combat', '/cosmere/gm', '/cosmere/gm/vitals', '/cosmere/gmscreen',
            '/cosmere/generator', '/cosmere/loot', '/cosmere/homebrew']
    for pid in ids:
        urls += [f'/cosmere/pc/{pid}', f'/cosmere/builder?pc={pid}',
                 f'/cosmere/builder?pc={pid}&levelup=1']
    failures = {url: client.get(url).status_code for url in urls}
    assert {url: code for url, code in failures.items() if code != 200} == {}
    # Opening pages never rewrites a character file.
    assert {p.name: p.read_bytes() for p in pcs.iterdir()} == before


@NEEDS_PRIVATE_DATA
def test_mistborn_sheet_shows_metallic_arts_and_hides_radiant_sections(mistborn_campaign):
    host, client, ids, pcs = mistborn_campaign('era-1')
    html = client.get(f'/cosmere/pc/{ids[1]}').get_data(as_text=True)
    assert 'role="tab" data-tab="arts"' in html and 'data-panel="arts"' in html
    assert 'Feruchemist' in html and 'Stores warmth' in html
    assert 'role="tab" data-tab="radiant"' not in html and 'id="btn-shardblade"' not in html
    assert 'cos-workshop' not in html
    roster = client.get('/cosmere/pcs').get_data(as_text=True)
    assert 'Misting · Tin' in roster


@NEEDS_PRIVATE_DATA
def test_builder_serves_the_mistborn_page_and_saves_a_valid_build(mistborn_campaign):
    host, client, ids, pcs = mistborn_campaign('era-1')
    html = client.get('/cosmere/builder').get_data(as_text=True)
    assert 'New Mistborn character' in html and 'Mistborn rules' in html
    build = _build('misting')
    build['metal_access'] = {'allomancy': ['tin'], 'feruchemy': []}
    build['name'] = 'Round trip'
    preview = client.post('/cosmere/builder/preview', json={'build': build}).get_json()
    assert preview['hard'] == [] and preview['origin_world'] == 'scadrial'
    saved = client.post('/cosmere/builder', json={'build': build}).get_json()
    assert saved['ok'] is True
    doc = json.loads((pcs / f"{saved['id']}.json").read_text(encoding='utf-8'))
    assert doc['build']['era'] == 'era-1'
    assert doc['build']['allomantic_powers'] == {'tin': 'nascent'}


@NEEDS_PRIVATE_DATA
def test_builder_rejects_a_build_whose_metal_access_disagrees_with_its_powers(mistborn_campaign):
    host, client, ids, pcs = mistborn_campaign('era-1')
    build = _build('misting')
    build['metal_access'] = {'allomancy': ['steel'], 'feruchemy': []}
    response = client.post('/cosmere/builder', json={'build': build})
    assert response.status_code == 400
    assert any('metal access' in issue for issue in response.get_json()['hard'])


@NEEDS_PRIVATE_DATA
def test_sheet_offers_only_deterministic_metal_actions_and_a_burn_round_trips(mistborn_campaign):
    host, client, ids, pcs = mistborn_campaign('era-1')
    misting, feruchemist = ids
    html = client.get(f'/cosmere/pc/{misting}').get_data(as_text=True)
    assert 'mbAction("mistborn.allomancy.tin.action.burn", "spend"' in html
    # Investiture changes through burns, so the Stormlight orb controls are gone.
    assert 'Breathe Stormlight' not in html.split('<script')[0]
    ferring_html = client.get(f'/cosmere/pc/{feruchemist}').get_data(as_text=True)
    assert 'mistborn.feruchemy.gold.action.store-health", "store_start"' in ferring_html
    doc = host._load_cosmere_pc(misting)
    before = doc['runtime_state']['mistborn']['allomancy']['current_investiture']
    response = client.post(f'/api/cosmere/pc/{misting}/actions/mistborn.allomancy.tin.action.burn',
                           json={'expected_revision': doc['revision'],
                                 'request_id': str(uuid.uuid4()),
                                 'operation': {'kind': 'spend', 'amount': 1}})
    assert response.status_code == 200, response.get_json()
    html = client.get(f'/cosmere/pc/{misting}').get_data(as_text=True)
    assert 'is-burning' in html
    after = host._load_cosmere_pc(misting)['runtime_state']['mistborn']['allomancy']['current_investiture']
    assert after == before - 1
    assert f'Investiture {after}/' in html


# --- The builder's collector keeps what the form does not edit (MB-07 review) ---

def _merge_source():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / 'templates' /
              'cosmere_builder_mistborn.html').read_text(encoding='utf-8')
    return source.split('// MERGE START', 1)[1].split('// MERGE END', 1)[0]


def _run_merge(base, edits):
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    script = _merge_source() + (
        '\nprocess.stdout.write(JSON.stringify(mergeCollected('
        + json.dumps(base) + ', ' + json.dumps(edits) + ')));')
    done = subprocess.run([node, '-e', script], capture_output=True, text=True,
                          encoding='utf-8', timeout=60, check=True)
    return json.loads(done.stdout)


UNEDITED = {'occupation': 'Smith', 'relationships': 'Owes a lord', 'loyalties': 'The crew',
            'personality': 'Wry', 'notes': 'Keep this', 'goal_entitlements': [],
            'inventory': [{'name': 'Rope'}], 'currency': {'clip': 3}, 'epic_choices': []}


def test_collector_keeps_unedited_fields_and_drops_server_derived_ones():
    base = dict(_build('misting'), **UNEDITED, heroic_paths=['agent', 'misting', 'scholarly-extra'],
                allomancy_rank=2, feruchemy_rank=0, advancement_bonus_grants={'talents': 1},
                rules_binding={'stale': True}, cosmere_theme='mistborn',
                metal_access={'allomancy': ['steel'], 'feruchemy': []})
    edits = {'name': 'Edited', 'starting_path': 'scholar', 'skills': {'allomancy': 3},
             'allomantic_powers': {'tin': 'nascent'}, 'feruchemical_powers': {}}
    out = _run_merge(base, edits)
    for key, value in UNEDITED.items():
        assert out[key] == value, key
    assert out['name'] == 'Edited' and out['skills'] == {'allomancy': 3}
    # The old starting path and metalborn paths go; an earned path stays.
    assert out['heroic_paths'] == ['scholar', 'scholarly-extra']
    for key in ('allomancy_rank', 'feruchemy_rank', 'advancement_bonus_grants',
                'rules_binding', 'cosmere_theme'):
        assert key not in out
    assert out['metal_access'] == {'allomancy': ['tin'], 'feruchemy': []}


def test_collector_does_not_mutate_its_base():
    base = dict(_build('misting'), **UNEDITED)
    before = json.loads(json.dumps(base))
    _run_merge(base, {'name': 'x', 'starting_path': 'agent'})
    assert base == before


@NEEDS_PRIVATE_DATA
def test_collector_output_saves_and_reloads_without_losing_fields(mistborn_campaign):
    import systems.cosmere.build as cosmere_build
    host, client, ids, pcs = mistborn_campaign('era-1')
    pid = ids[0]
    doc = host._load_cosmere_pc(pid)
    doc['build'].update(occupation='Smith', relationships='Owes a lord',
                        loyalties='The crew', personality='Wry')
    host._save_cosmere_pc(doc)
    existing = host._load_cosmere_pc(pid)
    # What the page embeds as BUILD, then one edit through the real collector.
    page_build = cosmere_build.CosmereBuild(existing['build']).to_dict()
    payload = _run_merge(page_build, {
        'name': 'Vin Edited', 'level': 1, 'origin_world': 'scadrial', 'era': 'era-1',
        'ancestry': existing['build']['ancestry'], 'culture': '', 'starting_path': 'agent',
        'metalborn_keys': existing['build']['metalborn_keys'],
        'attributes': existing['build']['attributes'], 'skills': existing['build']['skills'],
        'talents': [], 'expertises': [],
        'allomantic_powers': existing['build']['allomantic_powers'],
        'feruchemical_powers': existing['build']['feruchemical_powers']})
    # Draft submission: publication builds the document through this function.
    drafted = host._build_cosmere_document({'build': payload}, existing)
    # Direct save through the builder route, then reload from disk.
    saved = client.post('/cosmere/builder', json={'id': pid, 'build': payload}).get_json()
    assert saved['ok'] is True, saved
    reloaded = host._load_cosmere_pc(pid)
    for result in (drafted['build'], reloaded['build']):
        assert result['occupation'] == 'Smith' and result['relationships'] == 'Owes a lord'
        assert result['loyalties'] == 'The crew' and result['personality'] == 'Wry'
    assert reloaded['build']['name'] == 'Vin Edited'


@NEEDS_PRIVATE_DATA
def test_store_uses_the_cards_charges_box_not_a_browser_prompt(mistborn_campaign):
    import re
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is unavailable')
    host, client, ids, pcs = mistborn_campaign('era-1')
    html = client.get(f'/cosmere/pc/{ids[1]}').get_data(as_text=True)
    assert 'class="cs-metal-amt"' in html
    assert re.search(r'"store_start", "Store Health", 4, this\)', html)
    script = next(s for s in re.findall(r'<script>(.*?)</script>', html, re.S)
                  if 'window.mbAction' in s)
    harness = r'''
const assert = require('node:assert/strict');
global.window = global; window.crypto = {randomUUID: () => '0b5e7a1c-1d2e-4f3a-8b4c-5d6e7f8a9b0c'};
global.prompt = () => { throw new Error('prompt must not be used'); };
const out = {textContent: ''}; let sent = null;
global.document = {getElementById: id => id === 'mb-act-result' ? out : null};
global.fetch = (url, opts) => { sent = {url, body: JSON.parse(opts.body)};
  return Promise.resolve({json: () => Promise.resolve({ok: false, error: 'stub'})}); };
global.location = {reload() {}};
''' + script + r'''
const box = {value: '3'};
const button = {closest: () => ({querySelector: () => box})};
window.mbAction('mistborn.feruchemy.gold.action.store-health', 'store_start', 'Store Health', 4, button);
assert.equal(sent.body.operation.kind, 'store_start');
assert.equal(sent.body.operation.amount, 3);
box.value = '9'; sent = null;
window.mbAction('mistborn.feruchemy.gold.action.store-health', 'store_start', 'Store Health', 4, button);
assert.equal(sent, null); assert.match(out.textContent, /between 1 and 4/);
'''
    done = subprocess.run([node, '-e', harness], capture_output=True, text=True,
                          encoding='utf-8', timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
