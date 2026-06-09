"""Cosmere closing scrapbook -- the Session Complete recap, at parity with PF2e.

The scrapbook's roster, highlights, MVP poll, and currency follow the active
system: a Cosmere campaign assembles spheres (not coins), Opportunities/
Complications (not crits/nat-1s), names an MVP without granting a Hero Point,
and draws its per-PC cards from the Cosmere party. Runs in a subprocess with a
throwaway DATA_DIR (CI-safe), mirroring the other Cosmere e2e tests.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_cosmere_scrapbook_end_to_end():
    r = _run('''
        import tempfile, os, json, uuid
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        gm_id = auth.get_user_by_username('gm')['id']
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302

        # a Cosmere PC in the party (so the scrapbook roster has a name)
        pid = uuid.uuid4().hex
        doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': gm_id,
               'build': {'name': 'Kaladin', 'level': 2}}
        with open(os.path.join(storage.cosmere_pc_dir(cid), pid + '.json'), 'w') as f:
            json.dump(doc, f)

        # start the session (resets highlights), then play:
        assert c.post('/api/session/begin', json={'bump_session': True}).status_code == 200

        # a loot award -> feeds the scrapbook (spheres, not coins)
        assert c.post('/api/cosmere/loot/add', json={'recipient': 'Kaladin',
            'items': [{'name': 'Sphere pouch', 'qty': 1}], 'spheres': {'broam': 2, 'mark': 1}}).status_code == 200
        # a nat-20 roll -> an Opportunity (Cosmere's crit) attributed to the PC
        assert c.post('/api/cosmere/roll', json={'pid': pid, 'action': 'Athletics',
            'result': '25', 'detail': 'Athletics test — Opportunity! (nat 20)'}).status_code == 200

        # the draft is Cosmere-shaped
        d = c.get('/api/session/scrapbook/draft', headers={'X-Requested-With': 'XMLHttpRequest'}).get_json()
        assert d['success']
        sb = d['scrapbook']
        assert sb['system'] == 'cosmere'
        assert 'Kaladin' in d['party_members'] and 'Kaladin' in sb['mvp']['candidates']
        assert sb['party']['total_spheres'] == {'broam': 2, 'mark': 1, 'chip': 0}
        assert sb['party']['crit_count'] == 1                      # the nat-20 Opportunity
        assert sb['players']['Kaladin']['crits']                   # on the PC's card

        # an RP moment, an MVP vote, then crown the MVP (no Hero Point in Cosmere)
        assert c.post('/api/session/scrapbook/rp_moment', json={'text': 'Swore the Second Ideal.', 'scope': 'Kaladin'}).status_code == 200
        assert c.post('/api/session/scrapbook/vote', json={'choice': 'Kaladin', 'voter': 'GM'}).get_json()['success']
        g = c.post('/api/session/scrapbook/grant_hero/Kaladin').get_json()
        assert g['success'] and g['hero_points'] is None, g           # crowned, not Hero-Pointed
        assert A.SESSION_HIGHLIGHTS['mvp_winner'] == 'Kaladin'

        # the dashboard exposes the Session Complete control; push works
        assert b'Session Complete' in c.get('/cosmere/gm').data
        pushed = c.post('/api/session/scrapbook/push', json={'feed_recap': False}).get_json()
        assert pushed['success'] and pushed['scrapbook']['system'] == 'cosmere'

        print('COSMERE_SCRAPBOOK_OK')
    ''')
    assert 'COSMERE_SCRAPBOOK_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_pf2e_scrapbook_unchanged_by_cosmere_genericization():
    """The roster-generic refactor must not change PF2e: with a PF2e campaign
    active, the scrapbook still reports coins and the PF2e party."""
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        assert c.post('/campaign/' + cid + '/activate').status_code == 302
        sb = A._assemble_scrapbook()
        assert sb['system'] == 'pf2e'
        assert 'total_coins' in sb['party'] and set(sb['party']['total_coins']) == {'pp', 'gp', 'sp', 'cp'}
        print('PF2E_SCRAPBOOK_OK')
    ''')
    assert 'PF2E_SCRAPBOOK_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
