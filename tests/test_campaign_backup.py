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


def test_backup_export_import_roundtrip():
    if not glob.glob(os.path.join(_REPO, 'tests', 'fixtures', 'kyle_l10.json')):
        pytest.skip('no committed PC fixture')
    r = _run('''
import tempfile, shutil, json, io, zipfile
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
pd = os.path.join(TMP, 'party_data'); os.makedirs(pd)
shutil.copy2(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', 'kyle_l10.json'), os.path.join(pd, 'Kyle.json'))
json.dump({'name': 'Shades of Blood'}, open(os.path.join(TMP, 'campaign.json'), 'w'))

import app as A
from core import storage
c = A.app.test_client()
assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
cid = storage.list_campaign_ids()[0]
assert c.post('/campaign/%s/activate' % cid).status_code == 302

# export -> a .zip with campaign.json + the party PC
r = c.get('/campaign/%s/export' % cid)
assert r.status_code == 200 and r.headers['Content-Type'].startswith('application/zip'), r.headers.get('Content-Type')
z = zipfile.ZipFile(io.BytesIO(r.data)); names = z.namelist()
assert 'campaign.json' in names and any(n.startswith('party_data/') for n in names), names

# import the same backup -> a NEW campaign owned by the importer
imp = c.post('/campaign/import',
             data={'backup': (io.BytesIO(r.data), 'backup.zip')},
             content_type='multipart/form-data').get_json()
assert imp['ok'] and imp['id'] != cid and imp['name'].endswith('(restored)'), imp
newid = imp['id']

# the restored campaign has the PC, re-stamped to the new id
newparty = os.path.join(storage.campaign_dir(newid), 'party_data')
fns = [f for f in os.listdir(newparty) if f.endswith('.json')]
assert fns, 'no PC restored'
cd = json.load(open(os.path.join(newparty, fns[0])))
assert cd.get('campaign_id') == newid, cd.get('campaign_id')

# it shows in My Campaigns, GM role, and the original is untouched
my = c.get('/api/my_campaigns').get_json()['campaigns']
assert any(x['id'] == newid and x['role'] == 'GM' for x in my)
assert any(x['id'] == cid for x in my)
print('BACKUP_OK')
''')
    assert 'BACKUP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)


def test_backup_ui_wired():
    h = pathlib.Path(_REPO, 'templates', 'account_home.html').read_text()
    assert '/export' in h and 'restoreBackup' in h and '/campaign/import' in h
