"""Real builder/parser contracts: authority, rules parity, and live-state safety."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

import app
from core.character_workflows.types import WorkflowError


def pf_payload():
    return {'name': 'Workflow Hero', 'class_name': 'Fighter', 'ancestry': 'Human',
            'abilities': {'str': 4, 'dex': 2, 'con': 2, 'int': 0, 'wis': 1, 'cha': 0},
            'skills': ['Athletics'], 'feats': [], 'equipment': []}


def cos_payload():
    return {'build': {'name': 'Workflow Hero', 'level': 1, 'path': 'warrior',
            'attributes': {'str': 3, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 1, 'pre': 1},
            'skills': {'ath': 1, 'hwp': 1}}}


def adapters():
    from core.character_workflows.systems import build_system_adapters
    from pf2e_pdf_import import build_from_pdf as pf_pdf
    from systems.cosmere.pdf_import import build_from_pdf as cos_pdf
    return build_system_adapters(
        pf2e_build=app._build_new_pf2e_document,
        pf2e_validate=app._validate_new_character_feats,
        pf2e_merge=app._merge_pf2e_import,
        pf2e_pdf=lambda data: pf_pdf(data, character_factory=app.Character),
        cosmere_build=app._build_cosmere_document,
        cosmere_pdf=lambda data: cos_pdf(data, homebrew=app._cosmere_homebrew_store()))


def inputs(kind, submission):
    from core.character_workflows.types import DraftInput
    return DraftInput(kind, 1, deepcopy(submission), {'step': 2}, deepcopy(submission))


def test_legacy_builder_characterization(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'PARTY_DIR', str(tmp_path))
    response = app.app.test_client().post('/api/save_new_character', json=pf_payload())
    assert response.status_code == 200
    doc = json.loads((tmp_path / 'Workflow_Hero.json').read_text(encoding='utf-8'))
    build = doc['build']
    assert build['name'] == 'Workflow Hero' and build['level'] == 1
    assert build['proficiencies']['athletics'] == 2
    assert build['money'] == {'pp': 0, 'gp': 15, 'sp': 0, 'cp': 0}
    assert build['attributes']['ancestryhp'] == 8
    assert build['attributes']['classhp'] == 10


def test_extracted_builder_matches_legacy_document(tmp_path, monkeypatch):
    monkeypatch.setattr(app, 'PARTY_DIR', str(tmp_path))
    response = app.app.test_client().post('/api/save_new_character', json=pf_payload())
    assert response.status_code == 200
    legacy = json.loads((tmp_path / 'Workflow_Hero.json').read_text(encoding='utf-8'))
    normalized = adapters()['pf2e'].normalize(inputs('pf2e_builder', pf_payload()),
                                             None, override=False)
    # The legacy save also constructs the actor, which adds the default Fist.
    # Compare at that same boundary, not before vs. after actor construction.
    app._actors_from_character_doc(normalized, str(tmp_path / 'Workflow_Hero.json'))
    assert normalized == legacy


def test_import_preserves_runtime_fields():
    current = {'build': {'name': 'Hero', 'level': 1, 'current_hp': 3,
        'conditions': {'frightened': 2}, 'notes': 'Private',
        'session_notes': [{'date': 'today', 'text': 'Private session'}],
        'hero_points': 2,
        'money': {'gp': 98}, 'expended_slots': {'1': 2},
        'weapons': [{'name': 'Custom Sword'}, {'name': 'Fist'}],
        'equipment': [['Custom Rope', 2]]}}
    imported = {'build': {'name': 'Hero', 'class': 'Fighter', 'ancestry': 'Human',
        'level': 2, 'current_hp': 999, 'conditions': {}, 'notes': 'Replace?',
        'session_notes': [{'date': 'forged', 'text': 'Replace?'}],
        'weapons': [{'name': 'Longsword'}], 'equipment': [['Pack', 1]]}}
    before = deepcopy(current)
    merged = adapters()['pf2e'].normalize(inputs('pf2e_import', imported),
                                         current, override=False)
    for field in ('current_hp', 'conditions', 'notes', 'session_notes', 'hero_points',
                  'money', 'expended_slots'):
        assert merged['build'][field] == current['build'][field]
    assert merged['build']['level'] == 2
    assert merged['build']['weapons'] == [{'name': 'Longsword'}, {'name': 'Custom Sword'}]
    assert merged['build']['equipment'] == [['Pack', 1], ['Custom Rope', 2]]
    assert current == before


def test_cosmere_edit_preserves_wallet_and_play_state():
    current = {'id': 'trusted', 'owner_user_id': 'owner', 'campaign_id': 'campaign',
        'build': cos_payload()['build'], 'wallet': {'spheres': 22},
        'play_state': {'health': 3}, 'house_metal': 'steel'}
    data = cos_payload()
    data.update(id='forged', owner_user_id='intruder', wallet={'spheres': 999})
    data['build']['name'] = 'Renamed Hero'
    result = adapters()['cosmere'].normalize(inputs('cosmere_builder', data),
                                            current, override=False)
    assert result['name'] == 'Renamed Hero'
    assert result['wallet'] == current['wallet']
    assert result['play_state'] == current['play_state']
    assert result['house_metal'] == 'steel'
    assert 'id' not in result and 'owner_user_id' not in result


@pytest.mark.parametrize(
    'submitted_private',
    [
        {},
        {'notes': 'FORGED NOTE',
         'session_notes': [{'date': 'forged', 'text': 'FORGED SESSION'}]},
    ],
)
def test_cosmere_edit_preserves_owner_private_fields(submitted_private):
    current_build = deepcopy(cos_payload()['build'])
    current_build.update(
        notes='OWNER NOTE',
        session_notes=[{'date': 'today', 'text': 'OWNER SESSION'}],
    )
    current = {
        'id': 'trusted',
        'owner_user_id': 'owner',
        'campaign_id': 'campaign',
        'build': current_build,
    }
    data = cos_payload()
    data['build'].update(submitted_private)

    result = adapters()['cosmere'].normalize(
        inputs('cosmere_builder', data), current, override=False
    )

    assert result['build']['notes'] == 'OWNER NOTE'
    assert result['build']['session_notes'] == [
        {'date': 'today', 'text': 'OWNER SESSION'}
    ]


def test_cosmere_edit_cannot_create_owner_private_fields():
    current = {
        'id': 'trusted',
        'owner_user_id': 'owner',
        'campaign_id': 'campaign',
        'build': deepcopy(cos_payload()['build']),
    }
    data = cos_payload()
    data['build'].update(
        notes='FORGED NOTE',
        session_notes=[{'date': 'forged', 'text': 'FORGED SESSION'}],
    )

    result = adapters()['cosmere'].normalize(
        inputs('cosmere_builder', data), current, override=False
    )

    assert 'notes' not in result['build']
    assert 'session_notes' not in result['build']


@pytest.mark.parametrize('system,kind', [('pf2e', 'pf2e_import'), ('cosmere', 'cosmere_builder')])
def test_fingerprint_ignores_hp_but_detects_build_changes(system, kind):
    adapter = adapters()[system]
    original = {'build': {'name': 'Hero', 'level': 1, 'current_hp': 10},
                'play_state': {'health': 10}, 'wallet': {'spheres': 1}}
    hp_changed = deepcopy(original)
    hp_changed['build']['current_hp'] = 2
    hp_changed['play_state']['health'] = 2
    hp_changed['wallet']['spheres'] = 3
    if system == 'cosmere':  # HP resides in play_state, not the Cosmere build.
        hp_changed['build']['current_hp'] = 10
    feat_changed = deepcopy(original)
    feat_changed['build']['feats'] = [['New feat']]
    private_changed = deepcopy(original)
    private_changed['build']['notes'] = 'Owner changed a private note'
    private_changed['build']['session_notes'] = [
        {'date': 'today', 'text': 'Owner changed a private session note'}
    ]
    assert adapter.fingerprint(kind, hp_changed) == adapter.fingerprint(kind, original)
    assert adapter.fingerprint(kind, private_changed) == adapter.fingerprint(kind, original)
    assert adapter.fingerprint(kind, feat_changed) != adapter.fingerprint(kind, original)


def test_versioned_fingerprint_matches_legacy_cosmere_hash_without_weakening_conflicts():
    adapter = adapters()['cosmere']
    document = deepcopy(cos_payload())
    document['build']['notes'] = 'Legacy private note'
    document['build']['session_notes'] = [
        {'date': 'today', 'text': 'Legacy private session'}
    ]
    projection = {key: document.get(key) for key in ('build', 'name', 'house_metal')}
    legacy = sha256(json.dumps(
        projection, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')).hexdigest()

    current = adapter.fingerprint('cosmere_builder', document)

    assert current.startswith('v2:') and len(current) == 67
    assert adapter.matches_fingerprint('cosmere_builder', document, current)
    assert adapter.matches_fingerprint('cosmere_builder', document, legacy)
    assert not adapter.matches_fingerprint('cosmere_builder', document, 'v3:' + legacy)
    assert not adapter.matches_fingerprint('cosmere_builder', document, 'malformed')
    changed = deepcopy(document)
    changed['build']['level'] = 2
    assert not adapter.matches_fingerprint('cosmere_builder', changed, legacy)


def test_versioned_fingerprint_matches_legacy_pf2e_hash():
    adapter = adapters()['pf2e']
    document = {'build': {
        'name': 'Hero', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1,
        'notes': 'Legacy private note',
        'session_notes': [{'date': 'today', 'text': 'Legacy private session'}],
    }}
    projection = app._merge_pf2e_import({}, deepcopy(document))
    legacy = sha256(json.dumps(
        projection, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False,
    ).encode('utf-8')).hexdigest()

    current = adapter.fingerprint('pf2e_import', document)

    assert current.startswith('v2:') and len(current) == 67
    assert adapter.matches_fingerprint('pf2e_import', document, current)
    assert adapter.matches_fingerprint('pf2e_import', document, legacy)
    changed = deepcopy(document)
    changed['build']['level'] = 2
    assert not adapter.matches_fingerprint('pf2e_import', changed, legacy)


def test_import_discards_authority_envelope():
    payload = {'id': 'forged', 'campaign_id': 'elsewhere', 'system': 'cosmere',
               'owner_user_id': 'intruder', 'editor_user_ids': ['intruder'],
               'build': {'name': 'Hero', 'class': 'Fighter', 'ancestry': 'Human',
                         'owner_user_id': 'intruder'}}
    adapter = adapters()['pf2e']
    draft = adapter.parse_import(json.dumps(payload).encode(), 'hero.json')
    normalized = adapter.normalize(draft, None, override=False)
    for key in ('id', 'campaign_id', 'owner_user_id', 'editor_user_ids', 'system'):
        assert key not in normalized and key not in normalized['build']
    assert normalized['build']['class'] == 'Fighter'


@pytest.mark.parametrize('kind,payload', [
    ('pf2e_builder', []), ('pf2e_builder', {'name': 32}),
    ('pf2e_builder', dict(pf_payload(), feats=[4])),
    ('pf2e_builder', dict(pf_payload(), skills='Athletics')),
    ('pf2e_builder', dict(pf_payload(), spellCasters=[{'spells': 'bad'}])),
    ('pf2e_import', {'build': []}),
    ('pf2e_import', {'build': {'name': 'Hero', 'class': []}}),
    ('cosmere_builder', {'build': {'name': 'Hero', 'path': 'warrior', 'skills': []}}),
    ('cosmere_builder', {'build': {'name': 'Hero', 'path': [], 'level': 1}}),
])
def test_malformed_shapes_are_public_validation_errors(kind, payload):
    with pytest.raises(WorkflowError) as exc:
        adapters()[kind.split('_')[0]].normalize(inputs(kind, payload), None, override=False)
    assert exc.value.status == 422


def test_force_from_untrusted_payload_does_not_bypass_rules():
    data = cos_payload()
    data['build']['attributes']['str'] = 9
    data['force'] = True
    adapter = adapters()['cosmere']
    with pytest.raises(WorkflowError) as exc:
        adapter.normalize(inputs('cosmere_builder', data), None, override=False)
    assert exc.value.code == 'rules_violation' and exc.value.issues
    assert adapter.normalize(inputs('cosmere_builder', data), None, override=True)['build']['attributes']['str'] == 9


def test_pf2e_reimport_cannot_rename_target():
    with pytest.raises(WorkflowError) as exc:
        adapters()['pf2e'].normalize(inputs('pf2e_import', {
            'name': 'New Name', 'class': 'Fighter', 'ancestry': 'Human'}),
            {'build': {'name': 'Old Name'}}, override=True)
    assert exc.value.status == 409


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_import_parse_failure_is_safe(system):
    with pytest.raises(WorkflowError) as exc:
        adapters()[system].parse_import(b'not a document', 'hero.pdf')
    assert exc.value.status == 422


@pytest.mark.parametrize('fixture', ['kyle_l10.json', 'goel_l10.json', 'gavin_l11.json', 'amadeus_l11.json'])
def test_committed_pathbuilder_exports_still_import(fixture):
    adapter = adapters()['pf2e']
    raw = (Path(__file__).parents[1] / 'fixtures' / fixture).read_bytes()
    parsed = adapter.parse_import(raw, fixture)
    normalized = adapter.normalize(parsed, None, override=False)
    assert normalized['build'] == json.loads(raw)['build']


def test_normalizing_caster_does_not_mutate_saved_snapshot():
    data = pf_payload()
    data.update(class_name='Sorcerer', spellCasters=[{
        'magicTradition': 'Arcane', 'spells': [{'spellLevel': 1, 'list': ['Fear']}]}])
    draft = inputs('pf2e_builder', data)
    before = deepcopy(draft)
    adapters()['pf2e'].normalize(draft, None, override=False)
    assert draft == before


def test_cosmere_missing_path_retains_existing_soft_guidance():
    data = cos_payload()
    data['build']['path'] = ''
    result = adapters()['cosmere'].normalize(inputs('cosmere_builder', data), None, override=False)
    assert result['build']['path'] == ''
