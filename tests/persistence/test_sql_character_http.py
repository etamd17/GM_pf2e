"""SQL grants and retained-file transactions through real character routes."""

import pytest

from tests.persistence.test_runtime_http import run_sql


@pytest.mark.parametrize("system", ["pf2e", "cosmere"])
def test_private_character_routes_use_sql_owner_not_editor_grants(tmp_path, system):
    run_sql(tmp_path, "SYSTEM = " + repr(system) + "\n" + '''
from core.persistence import CharacterAssignment, TransactionalStore, runtime

site_admin = auth.create_first_admin('admin', 'secret1')
gm = auth.create_user('gm', 'secret1')
users = {role: auth.create_user(role, 'secret1')
         for role in ('owner', 'editor', 'viewer', 'member', 'outsider')}
camp = campaigns.create_campaign('Table', SYSTEM, gm['id'])
cid = camp['id']
for role in ('owner', 'editor', 'viewer', 'member'):
    campaigns.add_member(cid, users[role]['id'], 'player')
application._bind_campaign_paths(cid)
client = application.app.test_client()

def login(user):
    with client.session_transaction() as state:
        state['user_id'] = user['id']
        state['auth_session_version'] = user.get('session_version', 0)
        state['active_campaign_id'] = cid

login(gm)
if SYSTEM == 'pf2e':
    response = client.post('/api/import_pathbuilder', json={'build': {
        'name': 'Hero', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1}})
    assert response.status_code == 200, response.data
    path = Path(application.get_pc_file_path('Hero'))
    document = json.loads(path.read_text(encoding='utf-8'))
else:
    document = {'name': 'Hero', 'build': {'name': 'Hero', 'level': 1},
                'system': 'cosmere', 'owner_user_id': None}
    pid = application._save_cosmere_pc(document)
    path = Path(storage.cosmere_pc_dir(cid)) / (pid + '.json')
    document = json.loads(path.read_text(encoding='utf-8'))

TransactionalStore(runtime.database().session_factory).claim_character(
    campaign_id=cid, character_id=document['id'], user_id=users['owner']['id'])
with runtime.database().transaction() as db:
    for role in ('editor', 'viewer'):
        db.add(CharacterAssignment(campaign_id=cid, character_id=document['id'],
                                   user_id=users[role]['id'], role=role))

def reset_private_file():
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload.update(owner_user_id=users['member']['id'],
                   editor_user_ids=[users['member']['id']],
                   viewer_user_ids=[users['member']['id']])
    payload['build']['notes'] = 'OWNER SECRET'
    payload['build']['session_notes'] = [
        {'date': 'private date', 'text': 'OWNER SESSION SECRET'}]
    storage.atomic_write_json(str(path), payload)

def private_requests():
    if SYSTEM == 'pf2e':
        return [
            ('export', lambda: client.get('/api/export_character/Hero')),
            ('notes', lambda: client.post('/api/save_notes/Hero',
                                           json={'notes': 'replacement'})),
            ('session-add', lambda: client.post('/api/save_session_note/Hero',
                                                 json={'text': 'replacement'})),
            ('session-delete', lambda: client.post(
                '/api/delete_session_note/Hero/0', json={})),
        ]
    return [('notes', lambda: client.post(
        '/cosmere/pc/' + pid + '/notes', json={'text': 'replacement'}))]

for role, user in [('owner', users['owner']), ('gm', gm), ('admin', site_admin)]:
    login(user)
    for operation, call in private_requests():
        reset_private_file()
        response = call()
        assert response.status_code == 200, (
            role, operation, response.status_code, response.data)
        if operation == 'export':
            exported = response.get_json()
            assert exported['build']['notes'] == 'OWNER SECRET'
            assert exported['build']['session_notes'][0]['text'] == 'OWNER SESSION SECRET'
        if role == 'owner' and operation == 'notes':
            saved = json.loads(path.read_text(encoding='utf-8'))
            assert saved['owner_user_id'] == users['owner']['id']
            assert saved['editor_user_ids'] == [users['editor']['id']]
            assert saved['viewer_user_ids'] == [users['viewer']['id']]

for role in ('editor', 'viewer', 'member', 'outsider'):
    login(users[role])
    for operation, call in private_requests():
        reset_private_file()
        previous_bytes = path.read_bytes()
        response = call()
        assert response.status_code == 403, (
            role, operation, response.status_code, response.data)
        expected_error = ('campaign_required' if role == 'outsider'
                          else 'character_owner_private_access_required')
        assert response.get_json()['error'] == expected_error, (
            role, operation, response.get_json())
        assert path.read_bytes() == previous_bytes, (role, operation, 'file changed')

reset_private_file()
login(users['editor'])
if SYSTEM == 'pf2e':
    state = client.get('/api/pc_state/Hero')
    assert state.status_code == 200, state.data
    assert 'notes' not in state.get_json() and 'session_notes' not in state.get_json()
    gameplay = client.post('/api/adjust_hero/Hero', data={'action': 'decrease'})
    assert gameplay.status_code == 200 and gameplay.get_json()['success'], gameplay.data
else:
    gameplay = client.post('/cosmere/pc/' + pid + '/state', json={'health': 7})
    assert gameplay.status_code == 200, gameplay.data
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['play_state']['health'] == 7
    assert saved['build']['notes'] == 'OWNER SECRET'
    builder = client.post('/cosmere/builder', json={'id': pid, 'build': {
        'name': 'Editor Rename', 'level': 1, 'path': 'warrior',
        'notes': 'FORGED BUILDER NOTE',
        'session_notes': [{'date': 'forged', 'text': 'FORGED BUILDER SESSION'}]}})
    assert builder.status_code == 200, builder.data
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['name'] == 'Editor Rename'
    assert saved['build']['notes'] == 'OWNER SECRET'
    assert saved['build']['session_notes'] == [
        {'date': 'private date', 'text': 'OWNER SESSION SECRET'}]

if SYSTEM == 'cosmere':
    response = client.post('/cosmere/pc/' + pid + '/delete', json={})
    assert response.status_code == 403, ('editor delete', response.status_code, response.data)
    assert path.exists()
    login(gm)
    response = client.post('/cosmere/pc/' + pid + '/release', json={})
    assert response.status_code == 200, ('gm release', response.status_code, response.data)
    login(users['owner'])
    response = client.post('/cosmere/pc/' + pid + '/notes', json={'text': 'former owner'})
    assert response.status_code == 403, ('former owner notes', response.status_code, response.data)
    login(users['editor'])
    response = client.post('/cosmere/pc/' + pid + '/notes',
                           json={'text': 'editor after release'})
    assert response.status_code == 403, ('editor notes', response.status_code, response.data)
    assert application._load_cosmere_pc(pid)['owner_user_id'] is None
''')


def test_party_batch_commits_checksums_and_rolls_back_sql_with_files(tmp_path):
    run_sql(tmp_path, '''
import hashlib
from core.persistence import Character, runtime
from sqlalchemy import select

gm = auth.create_first_admin('gm', 'secret1')
camp = campaigns.create_campaign('Table', 'pf2e', gm['id'])
cid = camp['id']
application._bind_campaign_paths(cid)
client = application.app.test_client()
with client.session_transaction() as state:
    state['user_id'] = gm['id']
    state['auth_session_version'] = gm.get('session_version', 0)
    state['active_campaign_id'] = cid

for name in ('Hero', 'Another'):
    response = client.post('/api/import_pathbuilder', json={'build': {
        'name': name, 'class': 'Fighter', 'ancestry': 'Human', 'level': 1,
        'hero_points': 3}})
    assert response.status_code == 200, response.data

response = client.post('/api/daily_prep_all', json={})
assert response.status_code == 200, response.data

def checksums():
    with runtime.database().session() as db:
        return {pc.legacy_file: pc.content_checksum
                for pc in db.scalars(select(Character))}

for filename, expected in checksums().items():
    path = Path(storage.party_dir(cid)) / filename
    payload = json.loads(path.read_text(encoding='utf-8'))
    actual = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    assert expected == actual
    assert payload['build']['hero_points'] == 1
    payload['build']['hero_points'] = 3
    application.save_and_reload_character(payload['build']['name'], payload, str(path))

before_checksums = checksums()
before_files = {path: path.read_bytes()
                for path in Path(storage.party_dir(cid)).glob('*.json')}
original_replace = application.os.replace
second_file = Path(application.get_pc_file_path(list(application.PARTY_LIBRARY)[1]))
failed = []

def fail_second_replacement(source, destination):
    if (not failed and str(source).endswith('.batch-stage')
            and Path(destination) == second_file):
        failed.append(True)
        raise OSError('simulated second file replacement failure')
    return original_replace(source, destination)

application.os.replace = fail_second_replacement
try:
    response = client.post('/api/daily_prep_all', json={})
finally:
    application.os.replace = original_replace
assert response.status_code == 503, response.data
assert failed
assert checksums() == before_checksums
assert {path: path.read_bytes() for path in before_files} == before_files
assert all(actor.hero_points == 3 for actor in application.PARTY_LIBRARY.values())
assert not list(Path(storage.party_dir(cid)).glob('*.character-batch-journal'))
''')
