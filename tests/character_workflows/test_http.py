"""Real account/CSRF/service boundaries in fresh app processes, both backends."""

import pytest
from tests.persistence.test_runtime_http import run_sql


SETUP = '''
from dataclasses import asdict
from core.character_workflows.types import DraftInput
admin = auth.create_first_admin('admin', 'secret1')
gm = auth.create_user('gm', 'secret1')
owner = auth.create_user('owner', 'secret1')
other = auth.create_user('other', 'secret1')
camp = campaigns.create_campaign('Table', SYSTEM, gm['id'])
cid = camp['id']
for user in (owner, other):
    campaigns.add_member(cid, user['id'], 'player')
storage.set_live_campaign_id(cid)
application._bind_campaign_paths(cid)
application.PARTY_LIBRARY = {}
application._PC_FILE_CACHE = {}

def client_for(user):
    client = application.app.test_client()
    with client.session_transaction() as state:
        state['user_id'] = user['id']
        state['auth_session_version'] = user.get('session_version', 0)
        state['active_campaign_id'] = cid
        state['_csrf'] = 'workflow-test-token'
    return client

client = client_for(owner)
gm_client = client_for(gm)
admin_client = client_for(admin)
other_client = client_for(other)
HEADERS = {'X-CSRF-Token': 'workflow-test-token'}
BASE = '/api/character-workflows/drafts'
if SYSTEM == 'pf2e':
    submission = {'name': 'Workflow Hero', 'class_name': 'Fighter', 'ancestry': 'Human',
                  'abilities': {'str': 4, 'dex': 2, 'con': 2, 'int': 0, 'wis': 1, 'cha': 0},
                  'skills': ['Athletics'], 'feats': [], 'equipment': []}
else:
    submission = {'build': {'name': 'Workflow Hero', 'level': 1, 'path': 'warrior',
                  'attributes': {'str': 3, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 1, 'pre': 1},
                  'skills': {'ath': 1, 'hwp': 1}}}
inputs = asdict(DraftInput(SYSTEM + '_builder', 1, submission, {'step': 2}, submission))
def assert_public_draft(draft):
    assert 'base_fingerprint' not in draft
    return draft

def create():
    response = client.post(BASE, json={'inputs': inputs, 'request_key': 'create'}, headers=HEADERS)
    assert response.status_code == 201, response.data
    return assert_public_draft(response.get_json()['draft'])
'''


@pytest.mark.parametrize('backend', ['json', 'sql'])
@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_player_completes_without_gm_endpoint(tmp_path, backend, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
draft = create()
assert draft['author_id'] == owner['id'] and draft['inputs'] == inputs
assert client.post(BASE, json={'inputs': inputs, 'request_key': 'create'}, headers=HEADERS).status_code == 200
from flask import session as flask_session
with application.app.test_request_context('/builder?draft_id=' + draft['id']):
    flask_session['user_id'] = owner['id']
    flask_session['auth_session_version'] = owner.get('session_version', 0)
    flask_session['active_campaign_id'] = cid
    workflow, _trusted_target = application._workflow_builder_bootstrap(SYSTEM)
    assert_public_draft(workflow['initialDraft'])
assert other_client.get(BASE + '/' + draft['id']).status_code == 404
assert gm_client.get(BASE + '/' + draft['id']).status_code == 404
assert admin_client.get(BASE + '/' + draft['id']).status_code == 404
assert client.post('/api/save_new_character', json={}, headers=HEADERS).status_code == 403
publish = BASE + '/' + draft['id'] + '/publish'
bad = client.post(publish, json={'expected_revision': 0, 'request_key': 'bad', 'force': 'false'}, headers=HEADERS)
assert bad.status_code == 422 and bad.json['error']['code'] == 'invalid_publication', bad.data
response = client.post(publish, json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert response.status_code == 200, response.data
result = response.get_json()
assert result['url'] == '/characters/' + result['character_id']
directory = Path(storage.party_dir(cid) if SYSTEM == 'pf2e' else storage.cosmere_pc_dir(cid))
path = directory / (result['character_id'] + '.json')
assert path.exists()
document = json.loads(path.read_text(encoding='utf-8'))
assert document['owner_user_id'] == owner['id']
sheet = client.get(result['url'])
assert sheet.status_code == 200 and b'Workflow Hero' in sheet.data, sheet.data[:400]
replay = client.post(publish, json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert replay.status_code == 200 and replay.json == result
assert client.get(BASE).json['drafts'] == []
''', backend=backend)


@pytest.mark.parametrize('backend', ['json', 'sql'])
def test_drafts_require_author_and_live_membership(tmp_path, backend):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
missing_csrf = client.post(BASE, json={'inputs': inputs, 'request_key': 'missing'})
assert missing_csrf.status_code == 400 and missing_csrf.json['error']['code'] == 'csrf_failed', missing_csrf.data
draft = create()
assert_public_draft(client.get(BASE + '/' + draft['id']).json['draft'])
for listed in client.get(BASE).json['drafts']:
    assert_public_draft(listed)
saved = client.patch(BASE + '/' + draft['id'], json={'inputs': inputs, 'expected_revision': 0}, headers=HEADERS)
assert saved.status_code == 200 and saved.json['draft']['revision'] == 1
assert_public_draft(saved.json['draft'])
stale = client.patch(BASE + '/' + draft['id'], json={'inputs': inputs, 'expected_revision': 0}, headers=HEADERS)
assert stale.status_code == 409 and stale.json['error']['code'] == 'draft_revision_conflict'
second = campaigns.create_campaign('Another', SYSTEM, gm['id'])
campaigns.add_member(second['id'], owner['id'], 'player')
with client.session_transaction() as state:
    state['active_campaign_id'] = second['id']
wrong_live = client.get(BASE)
assert wrong_live.status_code == 409 and wrong_live.json['error']['code'] == 'campaign_not_live', wrong_live.data
with client.session_transaction() as state:
    state['active_campaign_id'] = cid
campaigns.remove_member(cid, owner['id'])
assert client.get(BASE).status_code == 403
''', backend=backend)


def test_viewer_does_not_select_actor_or_receive_private_notes(tmp_path):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
from core.persistence import CharacterAssignment, runtime
draft = create()
result = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS).json
assert 'character_id' in result, result
with runtime.database().transaction() as db:
    db.add(CharacterAssignment(campaign_id=cid, character_id=result['character_id'],
                               user_id=other['id'], role='viewer'))
actor = application.PARTY_LIBRARY['Workflow Hero']
actor.notes = 'OWNER PRIVATE TEXT'
actor.session_notes = [{'date': 'SECRET DATE', 'text': 'PRIVATE SESSION NOTE'}]
page = other_client.get(result['url'])
assert page.status_code == 200, page.data[:400]
assert b'Workflow Hero' in page.data
for secret in (b'OWNER PRIVATE TEXT', b'SECRET DATE', b'PRIVATE SESSION NOTE'):
    assert secret not in page.data
assert b'loadAndMigrateLocalStorageState' not in page.data
assert b'Read-only' in page.data
with other_client.session_transaction() as state:
    assert 'player_name' not in state
denied = other_client.post('/api/save_notes/Workflow Hero', json={'notes': 'forged'}, headers=HEADERS)
assert denied.status_code == 403
''')


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_editor_keeps_game_access_without_owner_private_notes(tmp_path, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
from core.persistence import CharacterAssignment, runtime
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert published.status_code == 200, published.data
result = published.json
with runtime.database().transaction() as db:
    db.add(CharacterAssignment(campaign_id=cid, character_id=result['character_id'],
                               user_id=other['id'], role='editor'))
directory = Path(storage.party_dir(cid) if SYSTEM == 'pf2e' else storage.cosmere_pc_dir(cid))
path = directory / (result['character_id'] + '.json')
document = json.loads(path.read_text(encoding='utf-8'))
document['build']['notes'] = 'OWNER PRIVATE TEXT'
document['build']['session_notes'] = [{'date': 'SECRET DATE', 'text': 'PRIVATE SESSION NOTE'}]
storage.atomic_write_json(str(path), document)
if SYSTEM == 'pf2e':
    application.PARTY_LIBRARY['Workflow Hero'].notes = 'OWNER PRIVATE TEXT'
    application.PARTY_LIBRARY['Workflow Hero'].session_notes = document['build']['session_notes']
for url in [result['url'], '/player/sheet/Workflow Hero' if SYSTEM == 'pf2e' else '/cosmere/pc/' + result['character_id']]:
    response = other_client.get(url)
    assert response.status_code == 200, response.data[:400]
    assert b'Workflow Hero' in response.data
    for text in (b'OWNER PRIVATE TEXT', b'SECRET DATE', b'PRIVATE SESSION NOTE', b'id="pc-notes"'):
        assert text not in response.data, (url, text)
owner_page = client.get(result['url'])
assert b'OWNER PRIVATE TEXT' in owner_page.data
if SYSTEM == 'cosmere':
    from flask import session as flask_session
    with application.app.test_request_context('/cosmere/builder?pc=' + result['character_id']):
        flask_session['user_id'] = other['id']
        flask_session['auth_session_version'] = other.get('session_version', 0)
        flask_session['active_campaign_id'] = cid
        _workflow, trusted_target = application._workflow_builder_bootstrap('cosmere')
        assert trusted_target is not None
        assert 'notes' not in trusted_target['build']
        assert 'session_notes' not in trusted_target['build']
    builder_page = other_client.get('/cosmere/builder?pc=' + result['character_id'])
    assert builder_page.status_code == 200, builder_page.data[:400]
    for text in (b'OWNER PRIVATE TEXT', b'SECRET DATE', b'PRIVATE SESSION NOTE'):
        assert text not in builder_page.data
''')


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_json_private_routes_allow_owner_gm_and_nonmember_admin(tmp_path, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert published.status_code == 200, published.data
result = published.json
directory = Path(storage.party_dir(cid) if SYSTEM == 'pf2e' else storage.cosmere_pc_dir(cid))
path = directory / (result['character_id'] + '.json')

def reset_private_file():
    document = json.loads(path.read_text(encoding='utf-8'))
    document['build']['notes'] = 'OWNER SECRET'
    document['build']['session_notes'] = [
        {'date': 'private date', 'text': 'OWNER SESSION SECRET'}]
    storage.atomic_write_json(str(path), document)
    if SYSTEM == 'pf2e':
        assert application.reload_single_character(str(path))

def private_requests(http):
    if SYSTEM == 'pf2e':
        return [
            ('export', lambda: http.get('/api/export_character/Workflow Hero')),
            ('notes', lambda: http.post('/api/save_notes/Workflow Hero',
                                       json={'notes': 'replacement'}, headers=HEADERS)),
            ('session-add', lambda: http.post('/api/save_session_note/Workflow Hero',
                                             json={'text': 'replacement'}, headers=HEADERS)),
            ('session-delete', lambda: http.post(
                '/api/delete_session_note/Workflow Hero/0', json={}, headers=HEADERS)),
        ]
    return [('notes', lambda: http.post(
        '/cosmere/pc/' + result['character_id'] + '/notes',
        json={'text': 'replacement'}, headers=HEADERS))]

for role, http in [('owner', client), ('gm', gm_client), ('admin', admin_client)]:
    for operation, call in private_requests(http):
        reset_private_file()
        response = call()
        assert response.status_code == 200, (
            role, operation, response.status_code, response.data)
        if operation == 'export':
            exported = response.get_json()
            assert exported['build']['notes'] == 'OWNER SECRET'
            assert exported['build']['session_notes'][0]['text'] == 'OWNER SESSION SECRET'
''', backend='json')


@pytest.mark.parametrize('system', ['pf2e', 'cosmere'])
def test_json_forged_assignments_do_not_grant_private_routes(tmp_path, system):
    run_sql(tmp_path, 'SYSTEM = ' + repr(system) + '\n' + SETUP + '''
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert published.status_code == 200, published.data
result = published.json
directory = Path(storage.party_dir(cid) if SYSTEM == 'pf2e' else storage.cosmere_pc_dir(cid))
path = directory / (result['character_id'] + '.json')
document = json.loads(path.read_text(encoding='utf-8'))
document['build']['notes'] = 'JSON OWNER SECRET'
document['build']['session_notes'] = [{'date': 'private', 'text': 'JSON SESSION SECRET'}]
document['editor_user_ids'] = [other['id']]
document['viewer_user_ids'] = [other['id']]
storage.atomic_write_json(str(path), document)

if SYSTEM == 'pf2e':
    requests = [
        lambda: other_client.get('/api/export_character/Workflow Hero'),
        lambda: other_client.post('/api/save_notes/Workflow Hero',
                                  json={'notes': 'forged'}, headers=HEADERS),
        lambda: other_client.post('/api/save_session_note/Workflow Hero',
                                  json={'text': 'forged'}, headers=HEADERS),
        lambda: other_client.post('/api/delete_session_note/Workflow Hero/0',
                                  json={}, headers=HEADERS),
    ]
else:
    requests = [lambda: other_client.post(
        '/cosmere/pc/' + result['character_id'] + '/notes',
        json={'text': 'forged'}, headers=HEADERS)]

for call in requests:
    before = path.read_bytes()
    response = call()
    assert response.status_code == 403, response.data
    assert response.json['error'] == 'character_owner_private_access_required'
    assert path.read_bytes() == before
''', backend='json')


@pytest.mark.parametrize('backend', ['json', 'sql'])
def test_import_copy_discard_and_json_shape_contract(tmp_path, backend):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
bad = client.post(BASE, json=[], headers=HEADERS)
assert bad.status_code == 422 and bad.json['error']['code'] == 'invalid_workflow_request'
source = {'id': 'forged', 'owner_user_id': other['id'], 'build': {
          'name': 'Imported', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1}}
prepared = client.post('/api/character-workflows/imports',
    json={'source': source, 'request_key': 'import'}, headers=HEADERS)
assert prepared.status_code == 201, prepared.data
draft = prepared.json['draft']
assert_public_draft(draft)
assert 'owner_user_id' not in draft['inputs']['submission']
copied = client.post(BASE + '/' + draft['id'] + '/copy',
    json={'inputs': draft['inputs'], 'request_key': 'copy'}, headers=HEADERS)
assert copied.status_code == 201 and copied.json['draft']['id'] != draft['id'], copied.data
assert_public_draft(copied.json['draft'])
discarded = client.post(BASE + '/' + draft['id'] + '/discard',
    json={'expected_revision': 0}, headers=HEADERS)
assert discarded.status_code == 200
assert client.get(BASE + '/' + draft['id']).status_code == 404
late_save = client.patch(BASE + '/' + draft['id'],
    json={'inputs': draft['inputs'], 'expected_revision': 0}, headers=HEADERS)
assert late_save.status_code == 409
''', backend=backend)


def test_import_rejects_nonfinite_json_and_invalid_explicit_target(tmp_path):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
source = {'build': {'name': 'Imported', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1}}
bad_target = client.post('/api/character-workflows/imports',
    json={'source': source, 'request_key': 'bad-target', 'target_id': 'missing'}, headers=HEADERS)
assert bad_target.status_code == 404, bad_target.data
source['build']['level'] = float('nan')
invalid = client.post('/api/character-workflows/imports',
    json={'source': source, 'request_key': 'invalid'}, headers=HEADERS)
assert invalid.status_code == 422, invalid.data[:400]
assert invalid.json['error']['code'] == 'invalid_import'
''')


def test_duplicate_pf2e_name_cannot_render_cached_peer_private_notes(tmp_path):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS).json
path = Path(storage.party_dir(cid)) / (published['character_id'] + '.json')
document = json.loads(path.read_text())
document.update(id='d'*32, owner_user_id=other['id'])
document['build']['notes'] = 'PEER PRIVATE NOTES'
storage.atomic_write_json(str(path.parent / 'duplicate.json'), document)
application.PARTY_LIBRARY['Workflow Hero'] = application.Character(document, 'duplicate.json')
response = client.get(published['url'])
assert response.status_code == 409, response.data[:400]
assert b'PEER PRIVATE NOTES' not in response.data
''', backend='json')
