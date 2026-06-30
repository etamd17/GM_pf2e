"""Stop active campaign: park the session so switching systems can't bleed.

"Stop active campaign" parks the session with NO active table -- _active_campaign_id
returns None (it must NOT fall back to last_campaign_id, or the stop wouldn't
stick), so the lobby/chrome render system-neutral. A GM who held the server-wide
live slot releases it. Activating any campaign un-parks.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_stop_then_activate_round_trip():
    r = _run('''
        import tempfile, os, glob, json, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'x'
        import app as A
        c = A.app.test_client()
        c.post('/setup', data={'username': 'gm', 'password': 'pw12345', 'display_name': 'GM'})
        c.post('/campaigns/new', data={'name': 'PF', 'system': 'pf2e'})
        c.post('/campaigns/new', data={'name': 'Cos', 'system': 'cosmere'})
        DD = os.environ['DATA_DIR']
        camps = {json.load(open(f))['system']: json.load(open(f))['id']
                 for f in glob.glob(DD + '/campaigns/*/campaign.json')}
        pf, cos = camps['pf2e'], camps['cosmere']

        c.post('/campaign/%s/activate' % cos)
        assert A._storage.get_live_campaign_id() == cos, 'cosmere should hold the live slot'
        with c.session_transaction() as s:
            assert s.get('active_campaign_id') == cos

        # STOP
        r = c.post('/campaign/stop', headers={'X-Requested-With': 'XMLHttpRequest'})
        assert r.status_code == 200 and r.get_json().get('ok')
        with c.session_transaction() as s:
            assert s.get('campaign_stopped') is True
            assert not s.get('active_campaign_id')
        assert A._storage.get_live_campaign_id() in (None, ''), 'GM stop should release the live slot'

        # Parked: the lobby renders, neutral (no active campaign)
        assert c.get('/me').status_code == 200

        # ACTIVATE un-parks + switches cleanly to PF2e
        c.post('/campaign/%s/activate' % pf)
        with c.session_transaction() as s:
            assert s.get('active_campaign_id') == pf
            assert not s.get('campaign_stopped'), 'activate must clear the stop flag'
        body = re.search(r'<body class="([^"]*)"', c.get('/tracker').get_data(as_text=True))
        assert body and 'system-pf2e' in body.group(1), ('tracker should be pf2e after re-activate', body and body.group(1))
        print('STOP_ROUNDTRIP_OK')
    ''')
    assert 'STOP_ROUNDTRIP_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
