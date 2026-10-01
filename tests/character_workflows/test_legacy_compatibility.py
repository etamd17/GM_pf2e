"""Old GM routes stay GM-only and resolve newly ID-named characters."""

import pytest
from tests.persistence.test_runtime_http import run_sql
from test_http import SETUP


@pytest.mark.parametrize('backend', ['json', 'sql'])
def test_gm_reimport_resolves_id_filename(tmp_path, backend):
    run_sql(tmp_path, "SYSTEM = 'pf2e'\n" + SETUP + '''
draft = create()
published = client.post(BASE + '/' + draft['id'] + '/publish',
    json={'expected_revision': 0, 'request_key': 'publish'}, headers=HEADERS)
assert published.status_code == 200, published.data
result = published.json
directory = Path(storage.party_dir(cid))
before = {p.name for p in directory.glob('*.json')}
response = gm_client.post('/api/import_pathbuilder', json={'id': 'forged', 'owner_user_id': 'forged',
    'build': {'name': 'Workflow Hero', 'class': 'Fighter', 'ancestry': 'Human', 'level': 2}}, headers=HEADERS)
assert response.status_code == 200, response.data
assert {p.name for p in directory.glob('*.json')} == before
document = json.loads((directory / (result['character_id'] + '.json')).read_text(encoding='utf-8'))
assert document['build']['level'] == 2
assert document['id'] == result['character_id'] and document['owner_user_id'] == owner['id']
''', backend=backend)


def test_legacy_open_builder_contract_unchanged(tmp_path):
    run_sql(tmp_path, '''
client = application.app.test_client()
response = client.post('/api/import_pathbuilder', json={'build': {
    'name': 'Legacy Hero', 'class': 'Fighter', 'ancestry': 'Human', 'level': 1}})
assert response.status_code == 200 and response.json['success'], response.data
assert (Path(application.PARTY_DIR) / 'Legacy_Hero.json').exists()
''', backend='json')
