"""End-to-end (subprocess, isolated DATA_DIR) verification of the switching UX:
/api/my_campaigns, the validated `then` deep-link on activate, and the
owner-aware /player redirect. Runs in a subprocess because app.py binds DATA_DIR
+ account mode at import (same pattern as test_phase1).
"""
from __future__ import annotations

import os
import subprocess
import sys
import glob

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys, textwrap\nsys.path.insert(0, os.getcwd())\n" + body
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_switching_flows_end_to_end():
    if not glob.glob(os.path.join(_REPO, 'tests', 'fixtures', 'kyle_l10.json')):
        pytest.skip('no committed PC fixture to seed a campaign')
    r = _run('''
import tempfile, shutil, glob, json
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
pd = os.path.join(TMP, 'party_data'); os.makedirs(pd)
shutil.copy2(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', 'kyle_l10.json'),
             os.path.join(pd, 'Kyle.json'))
json.dump({'name': 'Test Campaign'}, open(os.path.join(TMP, 'campaign.json'), 'w'))

import app as A
from core import storage, auth
c = A.app.test_client()
assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
cid = storage.list_campaign_ids()[0]
gm_id = auth.get_user_by_username('gm')['id']

# guarantee the gm owns exactly the one migrated PC (migration ownership default
# is not what we are testing here)
party = os.path.join(storage.campaign_dir(cid), 'party_data')
fn = [f for f in os.listdir(party) if f.endswith('.json')][0]
p = os.path.join(party, fn); doc = json.load(open(p)); doc['owner_user_id'] = gm_id
json.dump(doc, open(p, 'w'))

# activate the campaign for the session
assert c.post('/campaign/%s/activate' % cid).status_code == 302

# 1) /api/my_campaigns lists the campaign, role GM, marked live
d = c.get('/api/my_campaigns').get_json()
camps = d['campaigns']
assert len(camps) == 1 and camps[0]['role'] == 'GM' and camps[0]['is_live'] is True, camps

# 2) owner-aware /player: gm owns exactly one PC -> deep-link to its sheet
r2 = c.get('/player')
assert r2.status_code == 302 and '/player/sheet/' in r2.headers['Location'], (r2.status_code, r2.headers.get('Location'))

# 3) activate honors a validated same-site `then` target
r3 = c.post('/campaign/%s/activate' % cid, data={'then': '/player/sheet/Kyle'})
assert r3.status_code == 302 and r3.headers['Location'].endswith('/player/sheet/Kyle'), r3.headers.get('Location')

# 4) an OPEN-REDIRECT `then` is ignored (falls back to the hub)
r4 = c.post('/campaign/%s/activate' % cid, data={'then': 'http://evil.example/x'})
assert 'evil.example' not in r4.headers.get('Location', ''), r4.headers.get('Location')
print('SWITCH_OK')
''')
    assert 'SWITCH_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
