"""GM XP / level award (PR2). Subprocess account-mode e2e: per-campaign
advancement mode, PF2e XP award + rollover-to-ready, milestone mark/clear ready,
and the xp/ready fields on the live pc_state payload.
"""
from __future__ import annotations

import os
import subprocess
import sys
import glob

import pathlib

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_advancement_ui_wired():
    gm = pathlib.Path(_REPO, 'templates', 'gm_hub.html').read_text()
    assert 'setAdvMode' in gm and 'awardXp' in gm and 'markReady' in gm
    sheet = pathlib.Path(_REPO, 'templates', 'player_sheet.html').read_text()
    assert 'ready-to-level-banner' in sheet
    cos = pathlib.Path(_REPO, 'templates', 'cosmere_gm.html').read_text()
    assert 'cosMarkReady' in cos
    csheet = pathlib.Path(_REPO, 'templates', 'cosmere_sheet.html').read_text()
    assert 'Ready to level up' in csheet


def _run(body):
    return subprocess.run([sys.executable, '-c', "import os, sys\nsys.path.insert(0, os.getcwd())\n" + body],
                          capture_output=True, text=True, cwd=_REPO)


def test_gm_xp_and_milestone_flow():
    if not glob.glob(os.path.join(_REPO, 'tests', 'fixtures', 'kyle_l10.json')):
        pytest.skip('no committed PC fixture')
    r = _run('''
import tempfile, shutil, json
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
pd = os.path.join(TMP, 'party_data'); os.makedirs(pd)
shutil.copy2(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', 'kyle_l10.json'), os.path.join(pd, 'Kyle.json'))
json.dump({'name': 'T'}, open(os.path.join(TMP, 'campaign.json'), 'w'))

import app as A
from core import storage, auth
c = A.app.test_client()
assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
cid = storage.list_campaign_ids()[0]
gm_id = auth.get_user_by_username('gm')['id']
party = os.path.join(storage.campaign_dir(cid), 'party_data')
fn = [f for f in os.listdir(party) if f.endswith('.json')][0]
p = os.path.join(party, fn); doc = json.load(open(p)); doc['owner_user_id'] = gm_id; json.dump(doc, open(p, 'w'))
assert c.post('/campaign/%s/activate' % cid).status_code == 302
name = json.load(open(p))['build']['name']

# default mode is milestone
assert A._advancement_mode() == 'milestone'
# switch to XP mode
assert c.post('/api/gm/advancement_mode', json={'mode': 'xp'}).get_json()['ok']
assert A._advancement_mode() == 'xp'

# award 1200 XP -> added to existing xp, and rolls the ready flag on (>=1000)
start_xp = int(json.load(open(p))['build'].get('xp', 0) or 0)
aw = c.post('/api/gm/award_xp', json={'amount': 1200}).get_json()
assert aw['ok'] and aw['amount'] == 1200, aw
b = json.load(open(p))['build']
assert b['xp'] == start_xp + 1200 and b.get('ready_to_level') is True, b

# XP award is PF2e-only is enforced elsewhere; here confirm a bad mode is rejected
assert c.post('/api/gm/advancement_mode', json={'mode': 'nonsense'}).status_code == 400

# milestone: clearing ready works
assert c.post('/api/gm/mark_ready', json={'ready': 0}).get_json()['ok']
assert json.load(open(p))['build'].get('ready_to_level') is False

# the live pc_state payload carries xp + ready_to_level
ps = c.get('/api/pc_state/' + name).get_json()
assert ps['xp'] == start_xp + 1200 and ps['ready_to_level'] is False, ps
print('XP_OK')
''')
    assert 'XP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
