"""Campaign backup/export (PR3): a GM can download a full .zip of a campaign and
restore it (non-destructively) into a NEW campaign owned by them. Subprocess
account-mode e2e + a template-wiring guard.
"""
from __future__ import annotations

import os
import subprocess
import sys
import glob
import pathlib

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    return subprocess.run([sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
                          capture_output=True, text=True, cwd=_REPO)


def test_backup_export_import_roundtrip_clears_restored_character_ownership():
    if not glob.glob(os.path.join(_REPO, 'tests', 'fixtures', 'kyle_l10.json')):
        pytest.skip('no committed PC fixture')
    r = _run('''
import tempfile, shutil, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
pd = os.path.join(TMP, 'party_data'); os.makedirs(pd)
shutil.copy2(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', 'kyle_l10.json'), os.path.join(pd, 'Kyle.json'))
json.dump({'name': 'Shades of Blood'}, open(os.path.join(TMP, 'campaign.json'), 'w', encoding='utf-8'))

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
cid = storage.list_campaign_ids()[0]
assert c.post('/campaign/%s/activate' % cid).status_code == 302

# A normal claimed PF2e character keeps its stable id across restore, but the
# new campaign must not inherit account ownership/editor authority.
party_dir = storage.party_dir(cid)
claimed_path = next(
    os.path.join(party_dir, name)
    for name in os.listdir(party_dir)
    if name.endswith('.json')
)
claimed = storage.load_json(claimed_path)
assert storage.is_wrapped(claimed), claimed
claimed_id = claimed['id']
claimed['owner_user_id'] = 'source-owner'
claimed['editor_user_ids'] = ['source-editor']
storage.atomic_write_json(claimed_path, claimed, indent=2)

# Legacy backups can contain native PF2e documents from before campaign
# envelopes existed. They must become first-class, inviteable characters.
legacy = json.load(open(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', 'kyle_l10.json'), encoding='utf-8'))
legacy['build']['name'] = 'Legacy Hero'
legacy_path = os.path.join(party_dir, 'Legacy_Hero.json')
storage.atomic_write_json(legacy_path, legacy, indent=2)

# Cosmere identity is also campaign-scoped, and its filename must continue to
# agree with the stable character id used by /cosmere/pc/<pid> lookups.
cosmere_id = storage.new_id()
cosmere_doc = {
    'id': cosmere_id,
    'campaign_id': cid,
    'system': 'cosmere',
    'name': 'Kaladin',
    'owner_user_id': 'source-owner',
    'editor_user_ids': ['source-editor'],
    'build': {'name': 'Kaladin', 'level': 1},
    'play_state': {'health': 10},
}
storage.atomic_write_json(
    os.path.join(storage.cosmere_pc_dir(cid), 'legacy-kaladin.json'),
    cosmere_doc,
    indent=2,
)

# export -> a .zip with campaign.json + both character systems
r = c.get('/campaign/%s/export' % cid)
assert r.status_code == 200 and r.headers['Content-Type'].startswith('application/zip'), r.headers.get('Content-Type')
z = zipfile.ZipFile(io.BytesIO(r.data)); names = z.namelist()
assert 'campaign.json' in names and any(n.startswith('party_data/') for n in names), names
assert any(n.startswith('cosmere_pcs/') for n in names), names

# import the same backup -> a NEW campaign owned by the importer
imp = c.post('/campaign/import',
             data={'backup': (io.BytesIO(r.data), 'backup.zip')},
             content_type='multipart/form-data').get_json()
assert imp['ok'] and imp['id'] != cid and imp['name'].endswith('(restored)'), imp
newid = imp['id']

# The restored campaign has every PC, re-stamped to the new campaign. Valid
# character ids remain stable so scene/token references keep working, while
# ownership/editor authority is cleared because membership was reset.
newparty = os.path.join(storage.campaign_dir(newid), 'party_data')
fns = [f for f in os.listdir(newparty) if f.endswith('.json')]
assert len(fns) == 2, fns
pf2e_docs = [storage.load_json(os.path.join(newparty, fn)) for fn in fns]
pf2e_by_name = {(doc.get('build') or doc).get('name'): doc for doc in pf2e_docs}
restored_claimed = pf2e_by_name['Kyle']
restored_legacy = pf2e_by_name['Legacy Hero']
for character in (restored_claimed, restored_legacy):
    assert storage.is_wrapped(character), character
    assert character['campaign_id'] == newid, character
    assert character['system'] == 'pf2e', character
    assert character.get('owner_user_id') is None, character
    assert not character.get('editor_user_ids'), character
assert restored_claimed['id'] == claimed_id
assert restored_legacy['id'] != claimed_id
assert len(restored_legacy['id']) == 32

newcosmere = storage.cosmere_pc_dir(newid)
assert os.listdir(newcosmere) == [cosmere_id + '.json'], os.listdir(newcosmere)
restored_cosmere = storage.load_json(os.path.join(newcosmere, cosmere_id + '.json'))
assert restored_cosmere['id'] == cosmere_id, restored_cosmere
assert restored_cosmere['campaign_id'] == newid, restored_cosmere
assert restored_cosmere['system'] == 'cosmere', restored_cosmere
assert restored_cosmere.get('owner_user_id') is None, restored_cosmere
assert not restored_cosmere.get('editor_user_ids'), restored_cosmere

# The invite page must now see all restored characters as unclaimed and mint a
# usable character-specific claim code for each one.
invites = c.get('/campaign/%s/invites' % newid)
assert invites.status_code == 200, invites.get_data(as_text=True)
for character_id in (claimed_id, restored_legacy['id'], cosmere_id):
    assert A._auth.active_invite_for_character(newid, character_id), character_id

# it shows in My Campaigns, GM role, and the original is untouched
my = c.get('/api/my_campaigns').get_json()['campaigns']
assert any(x['id'] == newid and x['role'] == 'GM' for x in my)
assert any(x['id'] == cid for x in my)
restored = storage.load_json(storage.campaign_file(newid))
assert len(restored['members']) == 1, restored['members']
assert restored['members'][0]['user_id'] == restored['created_by']
assert restored['members'][0]['role'] == 'gm'
assert storage.load_json(claimed_path)['owner_user_id'] == 'source-owner'
assert storage.load_json(
    os.path.join(storage.cosmere_pc_dir(cid), 'legacy-kaladin.json')
)['owner_user_id'] == 'source-owner'
assert set(os.listdir(storage.CAMPAIGNS_DIR)) == {cid, newid}
print('BACKUP_OK')
''')
    assert 'BACKUP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)


def test_import_rejects_transaction_artifacts_before_creating_campaign():
    r = _run('''
import tempfile, os, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={
    'username': 'gm', 'password': 'secret1', 'display_name': 'GM'
}).status_code == 302
assert storage.list_campaign_ids() == []

artifacts = (
    'party_data/pc.json.batch-stage',
    'party_data/pc.json.batch-backup',
    'party_data/pc.json.tmp',
    'party_data/txn.character-batch-journal',
)
for artifact in artifacts:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('campaign.json', json.dumps({
            'id': '0' * 32,
            'name': 'Unsafe backup',
            'system': 'pf2e',
            'members': [],
        }))
        archive.writestr(artifact, 'incomplete transaction state')
    payload.seek(0)

    response = c.post(
        '/campaign/import',
        data={'backup': (payload, 'unsafe.zip')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400, (artifact, response.get_data(as_text=True))
    assert response.get_json() == {
        'ok': False,
        'error': 'campaign backup contains incomplete transaction files',
    }
    assert storage.list_campaign_ids() == [], artifact

assert not os.path.exists(storage.CAMPAIGNS_DIR) or os.listdir(storage.CAMPAIGNS_DIR) == []
print('ARTIFACT_IMPORT_REJECTED')
''')
    assert 'ARTIFACT_IMPORT_REJECTED' in r.stdout, (
        "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
    )


def test_import_rejects_oversized_expansion_without_leaving_artifacts():
    r = _run('''
import tempfile, os, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={
    'username': 'gm', 'password': 'secret1', 'display_name': 'GM'
}).status_code == 302
assert storage.list_campaign_ids() == []
A._CAMPAIGN_IMPORT_MAX_UNCOMPRESSED = 1

payload = io.BytesIO()
with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('campaign.json', json.dumps({
        'id': '0' * 32,
        'name': 'Oversized backup',
        'system': 'pf2e',
        'members': [],
    }))
payload.seek(0)
response = c.post(
    '/campaign/import',
    data={'backup': (payload, 'oversized.zip')},
    content_type='multipart/form-data',
)
assert response.status_code == 400, response.get_data(as_text=True)
assert response.get_json() == {
    'ok': False,
    'error': 'campaign backup is too large when decompressed',
}
assert storage.list_campaign_ids() == []
assert os.listdir(storage.CAMPAIGNS_DIR) == []
print('OVERSIZED_IMPORT_CLEAN')
''')
    assert 'OVERSIZED_IMPORT_CLEAN' in r.stdout, (
        "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
    )


def test_import_rejects_oversized_parsed_json_entries_without_artifacts():
    r = _run('''
import tempfile, os, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={
    'username': 'gm', 'password': 'secret1', 'display_name': 'GM'
}).status_code == 302
assert storage.list_campaign_ids() == []

campaign_doc = json.dumps({
    'id': '0' * 32,
    'name': 'Oversized entry backup',
    'system': 'pf2e',
    'members': [],
})
for mode in (
    'campaign',
    'character',
    'aliased_character',
    'uppercase_character',
    'root_json',
    'trailing_dot_json',
    'character_total',
    'character_count',
):
    A._CAMPAIGN_IMPORT_MAX_CAMPAIGN_JSON = (
        1 if mode == 'campaign' else 1024 * 1024
    )
    A._CAMPAIGN_IMPORT_MAX_CHARACTER_JSON = (
        1
        if mode in ('character', 'aliased_character', 'uppercase_character')
        else 1024 * 1024
    )
    A._CAMPAIGN_IMPORT_MAX_JSON = 1 if mode == 'root_json' else 1024 * 1024
    A._CAMPAIGN_IMPORT_MAX_CHARACTER_TOTAL = (
        1 if mode == 'character_total' else 1024 * 1024
    )
    A._CAMPAIGN_IMPORT_MAX_CHARACTER_FILES = (
        0 if mode == 'character_count' else 500
    )
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('campaign.json', campaign_doc)
        if mode in (
            'character',
            'aliased_character',
            'uppercase_character',
            'character_total',
            'character_count',
        ):
            character_path = {
                'character': 'party_data/hero.json',
                'aliased_character': 'decoy/../party_data/hero.json',
                'uppercase_character': 'PARTY_DATA/hero.json',
                'character_total': 'party_data/hero.json',
                'character_count': 'party_data/hero.json',
            }[mode]
            archive.writestr(character_path, json.dumps({
                'build': {'name': 'Hero'},
                'play_state': {},
            }))
        if mode in ('root_json', 'trailing_dot_json'):
            root_json_path = (
                'handouts.json.' if mode == 'trailing_dot_json' else 'handouts.json'
            )
            archive.writestr(root_json_path, json.dumps([{
                'title': 'large parsed metadata',
            }]))
    payload.seek(0)
    response = c.post(
        '/campaign/import',
        data={'backup': (payload, mode + '.zip')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400, (mode, response.get_data(as_text=True))
    expected_error = {
        'aliased_character': 'unsafe path in archive',
        'trailing_dot_json': 'unsafe path in archive',
        'character_total': 'campaign backup contains too much character data',
        'character_count': 'campaign backup has too many character files',
    }.get(mode, 'campaign backup contains an oversized entry')
    assert response.get_json() == {'ok': False, 'error': expected_error}
    assert storage.list_campaign_ids() == [], mode
    assert os.listdir(storage.CAMPAIGNS_DIR) == [], mode

print('OVERSIZED_JSON_IMPORT_CLEAN')
''')
    assert 'OVERSIZED_JSON_IMPORT_CLEAN' in r.stdout, (
        "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
    )


def test_import_staging_write_failure_never_publishes_partial_campaign():
    r = _run('''
import tempfile, os, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={
    'username': 'gm', 'password': 'secret1', 'display_name': 'GM'
}).status_code == 302
assert storage.list_campaign_ids() == []

payload = io.BytesIO()
with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('campaign.json', json.dumps({
        'id': '0' * 32,
        'name': 'Interrupted restore',
        'system': 'pf2e',
        'members': [],
    }))
    for name in ('First Hero', 'Second Hero'):
        archive.writestr(
            'party_data/' + name.replace(' ', '_') + '.json',
            json.dumps({'build': {'name': name}, 'play_state': {}}),
        )
payload.seek(0)

original_write = A._atomic_write_json
stage_writes = {'count': 0}
def fail_second_character_stage(path, value, *args, **kwargs):
    if str(path).endswith('.restored-character-stage'):
        stage_writes['count'] += 1
        if stage_writes['count'] == 2:
            raise OSError('injected restored-character staging failure')
    return original_write(path, value, *args, **kwargs)
A._atomic_write_json = fail_second_character_stage

response = c.post(
    '/campaign/import',
    data={'backup': (payload, 'interrupted.zip')},
    content_type='multipart/form-data',
)
assert response.status_code == 503, response.get_data(as_text=True)
assert response.get_json() == {
    'ok': False,
    'error': 'campaign backup could not be restored',
}
assert stage_writes['count'] == 2
assert storage.list_campaign_ids() == []
assert os.listdir(storage.CAMPAIGNS_DIR) == []
print('INTERRUPTED_IMPORT_CLEAN')
''')
    assert 'INTERRUPTED_IMPORT_CLEAN' in r.stdout, (
        "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
    )


def test_import_rejects_late_unsafe_or_duplicate_paths_without_publication():
    r = _run('''
import tempfile, os, json, io, zipfile, warnings
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={
    'username': 'gm', 'password': 'secret1', 'display_name': 'GM'
}).status_code == 302
assert storage.list_campaign_ids() == []

campaign_doc = json.dumps({
    'id': '0' * 32,
    'name': 'Unsafe backup',
    'system': 'pf2e',
    'members': [],
})
for mode in ('traversal', 'duplicate'):
    payload = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('campaign.json', campaign_doc)
            archive.writestr('party_data/safe.json', '{}')
            if mode == 'traversal':
                archive.writestr('../escape.txt', 'must never be extracted')
            else:
                archive.writestr('campaign.json', campaign_doc)
    payload.seek(0)

    response = c.post(
        '/campaign/import',
        data={'backup': (payload, mode + '.zip')},
        content_type='multipart/form-data',
    )
    assert response.status_code == 400, (mode, response.get_data(as_text=True))
    assert response.get_json() == {
        'ok': False,
        'error': 'unsafe path in archive',
    }
    assert storage.list_campaign_ids() == [], mode
    assert os.listdir(storage.CAMPAIGNS_DIR) == [], mode
    assert not os.path.exists(os.path.join(TMP, 'escape.txt'))

print('UNSAFE_IMPORT_CLEAN')
''')
    assert 'UNSAFE_IMPORT_CLEAN' in r.stdout, (
        "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
    )


def test_backup_ui_wired():
    h = pathlib.Path(_REPO, 'templates', 'account_home.html').read_text(encoding='utf-8')
    assert '/export' in h and 'restoreBackup' in h and '/campaign/import' in h
