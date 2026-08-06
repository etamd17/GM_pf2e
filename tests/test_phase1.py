"""Phase 1 (multi-campaign platform) tests.

Each runs in a SUBPROCESS with a throwaway DATA_DIR, because app.py and
core/storage bind DATA_DIR at import time -- a subprocess keeps these tests from
colliding with the main app import (the other 238 tests) and from ever touching
the repo. Self-contained: the legacy game is seeded from the committed
tests/fixtures/*_l10.json PC builds, so this runs in CI without the gitignored
party_data.
"""
import os
import sys
import glob
import textwrap
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_phase1_auth_core():
    r = _run('''
        import tempfile
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        from core import auth, storage
        u = auth.create_user('gm', 'secret1', is_admin=True)
        assert auth.verify_credentials('gm', 'secret1') and not auth.verify_credentials('gm', 'nope')
        assert 'secret1' not in auth.get_user(u['id'])['password_hash']   # hashed, not plaintext
        auth.set_password(u['id'], 'newpass1'); assert auth.verify_credentials('gm', 'newpass1')
        code = auth.create_invite('c1', 'player', character_id='ch1', uses=1)
        assert auth.get_invite(code)['character_id'] == 'ch1'
        assert auth.consume_invite(code)['uses_left'] == 0 and auth.get_invite(code) is None  # one-time
        assert auth.get_invite(auth.create_invite('c1', 'gm', ttl_days=-1)) is None            # expired
        env = storage.wrap_character('id1', '0' * 32, 'pf2e', {'build': {'name': 'K'}})
        assert storage.is_wrapped(env) and env['build']['name'] == 'K' and env['owner_user_id'] is None
        assert not storage.is_wrapped({'success': True, 'build': {}})                          # legacy
        print('AUTH_OK')
    ''')
    assert 'AUTH_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_phase1_end_to_end():
    if not glob.glob(os.path.join(_REPO, 'tests', 'fixtures', '*_l10.json')):
        pytest.skip('no committed PC fixtures to seed a campaign')
    r = _run('''
        import tempfile, shutil, glob, json, re
        TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
        # seed a legacy flat game from the committed PC fixtures
        pd = os.path.join(TMP, 'party_data'); os.makedirs(pd)
        for fx in glob.glob(os.path.join(os.path.abspath('.'), 'tests', 'fixtures', '*_l10.json')):
            shutil.copy2(fx, os.path.join(pd, os.path.basename(fx)))
        json.dump({'name': 'Test Campaign'}, open(os.path.join(TMP, 'campaign.json'), 'w'))

        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        # /setup bootstraps the admin and auto-migrates the legacy game into Campaign #1
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        cids = storage.list_campaign_ids(); assert len(cids) == 1, cids
        cid = cids[0]
        assert A.ACTIVE_CAMPAIGN_ID == cid
        assert len(A.PARTY_LIBRARY) >= 1, sorted(A.PARTY_LIBRARY)
        gm_id = auth.get_user_by_username('gm')['id']
        assert campaigns.is_gm(campaigns.get_campaign(cid), gm_id)
        assert c.get('/me').status_code == 200
        assert c.get('/status').status_code == 200          # GM route via admin + live campaign

        # CSRF: cross-origin POST blocked; same-origin allowed
        assert c.post('/campaigns/new', data={'name': 'X'}, headers={'Origin': 'http://evil.example'}).status_code == 400
        assert c.post('/campaigns/new', data={'name': 'Second Game'}).status_code == 302
        assert len(storage.list_campaign_ids()) == 2

        # GM mints an invite; a brand-new player claims a character
        page = c.get('/campaign/' + cid + '/invites').data
        code = re.findall(rb'code=([A-Z2-9]{4}-[A-Z2-9]{4})', page)[0].decode()
        p = A.app.test_client()
        assert p.post('/join', data={'code': code, 'username': 'kyle', 'password': 'pw12345'}).status_code == 302
        pid = auth.get_user_by_username('kyle')['id']
        chars = [storage.load_json(os.path.join(storage.party_dir(cid), f))
                 for f in os.listdir(storage.party_dir(cid)) if f.endswith('.json')]
        assert any(d.get('owner_user_id') == pid for d in chars)
        assert campaigns.user_role(campaigns.get_campaign(cid), pid) == 'player'

        # a combat-state save preserves the ownership envelope (flat-additive)
        name = sorted(A.PARTY_LIBRARY)[0]; A._do_persist_pc_combat_state(name)
        saved = storage.load_json(A.get_pc_file_path(name))
        assert saved.get('schema_version') == 1 and 'owner_user_id' in saved and saved.get('id')

        # migration preserved the legacy originals as backup
        assert os.path.isdir(os.path.join(TMP, 'party_data'))
        print('PHASE1_OK')
    ''')
    assert 'PHASE1_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
