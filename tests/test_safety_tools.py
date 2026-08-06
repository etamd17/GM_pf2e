"""Table safety tools: per-campaign Lines & Veils (GM-edited, table-visible) and
an anonymous X-Card any member can tap to pause the game. Subprocess + throwaway
DATA_DIR, account mode (so the GM gate + membership are exercised)."""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_safety_lines_veils_and_xcard():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        J = {'X-Requested-With': 'XMLHttpRequest'}

        gm = A.app.test_client()
        assert gm.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert gm.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        cid = storage.list_campaign_ids()[0]
        assert gm.post('/campaign/' + cid + '/activate').status_code == 302

        # GM saves lines & veils (lists or newline text both accepted)
        save = gm.post('/api/safety', json={'lines': ['harm to children'], 'veils': 'on-screen torture\\nromance', 'notes': 'check in at breaks'}, headers=J)
        assert save.status_code == 200, save.status_code
        # GET reflects them, and the GM can edit
        g = gm.get('/api/safety', headers=J).get_json()
        assert g['lines'] == ['harm to children']
        assert g['veils'] == ['on-screen torture', 'romance']
        assert g['notes'] == 'check in at breaks' and g['can_edit'] is True

        # a player joins the campaign
        player = A.app.test_client()
        assert player.post('/register', data={'username': 'kyle', 'password': 'pw12345', 'display_name': 'Kyle'}).status_code == 302
        kid = auth.get_user_by_username('kyle')['id']
        campaigns.add_member(cid, kid, 'player')
        assert player.post('/campaign/' + cid + '/activate').status_code == 302

        # the player SEES the lines/veils but cannot edit, and cannot save (403)
        pg = player.get('/api/safety', headers=J).get_json()
        assert pg['lines'] == ['harm to children'] and pg['can_edit'] is False
        assert player.post('/api/safety', json={'lines': ['nothing']}, headers=J).status_code == 403
        # ...and the GM's saved lines are untouched by the rejected write
        assert gm.get('/api/safety', headers=J).get_json()['lines'] == ['harm to children']

        # ANY member can tap the X-Card (anonymous pause signal)
        assert player.post('/api/safety/xcard', headers=J).status_code == 200
        assert gm.post('/api/safety/xcard', headers=J).status_code == 200

        # a logged-out stranger cannot (account mode)
        anon = A.app.test_client()
        assert anon.post('/api/safety/xcard', headers=J).status_code == 401

        print('SAFETY_OK')
    ''')
    assert 'SAFETY_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
