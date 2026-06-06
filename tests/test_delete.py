"""Delete characters + campaigns -- the destructive ops remove the data and clean
up after themselves (the live slot, the campaign dir), gated to the GM/owner and
guarded by a typed-name confirmation. Subprocess + throwaway DATA_DIR (mirrors
tests/test_cosmere_campaign_binding.py), so it never touches the real data."""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_delete_character_and_campaign():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'})
        c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'})
        cid = [x for x in storage.list_campaign_ids()
               if campaigns.get_campaign(x)['name'] == 'Roshar'][0]
        c.post('/campaign/' + cid + '/activate')

        # build + delete a Cosmere PC -> file removed, gone from the roster
        pid = c.post('/cosmere/builder', json={'build': {'name': 'Kaladin'}}).get_json()['id']
        pcfile = os.path.join(storage.cosmere_pc_dir(cid), pid + '.json')
        assert os.path.isfile(pcfile)
        assert c.post('/cosmere/pc/' + pid + '/delete', json={}).status_code in (200, 302)
        assert not os.path.isfile(pcfile)
        assert b'Kaladin' not in c.get('/cosmere/pcs').data

        # a non-member cannot delete the campaign (gated)
        c2 = A.app.test_client()
        c2.post('/register', data={'username': 'rando', 'password': 'secret1', 'display_name': 'R'})
        assert c2.post('/campaign/' + cid + '/delete', data={'confirm_name': 'Roshar'}).status_code == 403
        assert os.path.isdir(storage.campaign_dir(cid))

        # campaign delete needs the name typed back: wrong name is a no-op
        assert storage.get_live_campaign_id() == cid
        c.post('/campaign/' + cid + '/delete', data={'confirm_name': 'nope'})
        assert os.path.isdir(storage.campaign_dir(cid))

        # correct name -> data dir removed + live slot freed
        ok = c.post('/campaign/' + cid + '/delete', data={'confirm_name': 'Roshar'})
        assert ok.status_code == 302
        assert not os.path.isdir(storage.campaign_dir(cid))
        assert storage.get_live_campaign_id() is None
        print('DELETE_OK')
    ''')
    assert 'DELETE_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
