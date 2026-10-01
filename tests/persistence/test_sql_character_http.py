"""SQL grants and retained-file transactions through real character routes."""

import pytest

from tests.persistence.test_runtime_http import run_sql


@pytest.mark.parametrize("system", ["pf2e", "cosmere"])
def test_character_mutations_use_sql_owner_and_editor_not_payload_grants(tmp_path, system):
    run_sql(tmp_path, "SYSTEM = " + repr(system) + "\n" + '''
from core.persistence import CharacterAssignment, TransactionalStore, runtime

gm = auth.create_first_admin('gm', 'secret1')
users = {role: auth.create_user(role, 'secret1')
         for role in ('owner', 'editor', 'viewer', 'forged')}
camp = campaigns.create_campaign('Table', SYSTEM, gm['id'])
cid = camp['id']
for user in users.values():
    campaigns.add_member(cid, user['id'], 'player')
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
    route = '/api/save_notes/Hero'
    request_field = 'notes'
else:
    document = {'name': 'Hero', 'build': {'name': 'Hero', 'level': 1},
                'system': 'cosmere', 'owner_user_id': None}
    pid = application._save_cosmere_pc(document)
    path = Path(storage.cosmere_pc_dir(cid)) / (pid + '.json')
    document = json.loads(path.read_text(encoding='utf-8'))
    route = '/cosmere/pc/' + pid + '/notes'
    request_field = 'text'

TransactionalStore(runtime.database().session_factory).claim_character(
    campaign_id=cid, character_id=document['id'], user_id=users['owner']['id'])
with runtime.database().transaction() as db:
    for role in ('editor', 'viewer'):
        db.add(CharacterAssignment(campaign_id=cid, character_id=document['id'],
                                   user_id=users[role]['id'], role=role))

for role, expected_status in [('owner', 200), ('editor', 200),
                              ('viewer', 403), ('forged', 403)]:
    # Reinstate hostile file grants before every request: prior authorized saves
    # correctly replace those fields with current SQL assignments.
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload.update(owner_user_id=users['forged']['id'],
                   editor_user_ids=[users['forged']['id']],
                   viewer_user_ids=[users['forged']['id']])
    storage.atomic_write_json(str(path), payload)
    previous_bytes = path.read_bytes()
    login(users[role])
    response = client.post(route, json={request_field: role + ' wrote this'})
    assert response.status_code == expected_status, (role, response.status_code, response.data)
    saved = json.loads(path.read_text(encoding='utf-8'))
    if expected_status == 200:
        assert saved['build']['notes'] == role + ' wrote this'
        assert saved['owner_user_id'] == users['owner']['id']
        assert saved['editor_user_ids'] == [users['editor']['id']]
        assert saved['viewer_user_ids'] == [users['viewer']['id']]
    else:
        assert path.read_bytes() == previous_bytes

if SYSTEM == 'cosmere':
    login(users['editor'])
    response = client.post('/cosmere/pc/' + pid + '/delete', json={})
    assert response.status_code == 403, response.data
    assert path.exists()
    login(gm)
    response = client.post('/cosmere/pc/' + pid + '/release', json={})
    assert response.status_code == 200, response.data
    login(users['owner'])
    response = client.post(route, json={request_field: 'former owner'})
    assert response.status_code == 403, response.data
    login(users['editor'])
    response = client.post(route, json={request_field: 'editor after release'})
    assert response.status_code == 200, response.data
    assert json.loads(path.read_text(encoding='utf-8'))['owner_user_id'] is None
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
