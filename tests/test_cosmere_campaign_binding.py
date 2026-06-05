"""Cosmere live-campaign binding -- the whole app switches systems off the
active campaign, with no PF2e/Cosmere bleed.

Proves the platform wiring end-to-end: a Cosmere campaign can be created and
activated, the active system follows it, Cosmere PCs are stored *under that
campaign* (not the legacy flat store) and stamped with their campaign + owner,
they surface in 'My Characters', and the system-aware chrome (home redirect +
nav brand) flips to Cosmere -- while a PF2e campaign still lands on the PF2e
lobby.

Runs in a SUBPROCESS with a throwaway DATA_DIR (app.py binds DATA_DIR + the
per-campaign path globals at import), mirroring tests/test_phase1.py, so it is
self-contained and never touches the repo or the gitignored runtime data.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_cosmere_campaign_binding_end_to_end():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        # bootstrap the admin/GM (empty DATA_DIR -> nothing to migrate)
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        gm_id = auth.get_user_by_username('gm')['id']

        # create a Cosmere campaign + a PF2e campaign through the real form
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        by_system = {campaigns.get_campaign(cid)['system']: cid
                     for cid in storage.list_campaign_ids()
                     if campaigns.get_campaign(cid)['name'] in ('Roshar', 'Golarion')}
        cos_cid, pf_cid = by_system['cosmere'], by_system['pf2e']

        # activate the Cosmere campaign -> GM takes the live slot, paths re-bind,
        # and the landing is the Cosmere hub (no PF2e GM-hub bleed)
        ar = c.post('/campaign/' + cos_cid + '/activate')
        assert ar.status_code == 302 and ar.headers['Location'].endswith('/cosmere/pcs'), ar.headers.get('Location')
        assert A.ACTIVE_CAMPAIGN_ID == cos_cid
        assert A.COSMERE_PC_DIR == storage.cosmere_pc_dir(cos_cid)   # store follows the live campaign

        # home + nav are now Cosmere
        home = c.get('/')
        assert home.status_code == 302 and home.headers['Location'].endswith('/cosmere/pcs')
        pcs = c.get('/cosmere/pcs')
        assert pcs.status_code == 200 and b'COSMERE' in pcs.data    # nav brand flipped
        assert b'GM Hub' not in pcs.data and b'Generators' not in pcs.data   # no PF2e bleed in the nav
        # the PF2e GM command center redirects out in Cosmere mode
        gm = c.get('/gm')
        assert gm.status_code == 302 and gm.headers['Location'].endswith('/cosmere/pcs')

        # build a Cosmere PC -> lands UNDER the campaign, stamped campaign + owner
        br = c.post('/cosmere/builder', json={'build': {'name': 'Kaladin'}})
        assert br.status_code == 200 and br.get_json()['ok']
        pid = br.get_json()['id']
        pdir = storage.cosmere_pc_dir(cos_cid)
        assert os.path.isfile(os.path.join(pdir, pid + '.json')), os.listdir(pdir)
        doc = storage.load_json(os.path.join(pdir, pid + '.json'))
        assert doc['campaign_id'] == cos_cid and doc['owner_user_id'] == gm_id and doc['name'] == 'Kaladin'
        # NOT written to the legacy flat store
        assert not os.path.isfile(os.path.join(os.environ['DATA_DIR'], 'cosmere_pcs', pid + '.json'))

        # it shows up in 'My Characters' tagged cosmere
        mine = campaigns.characters_for_user(gm_id)
        kal = [m for m in mine if m['name'] == 'Kaladin']
        assert kal and kal[0]['system'] == 'cosmere' and kal[0]['campaign_id'] == cos_cid, mine
        assert b'Kaladin' in c.get('/cosmere/pcs').data

        # switch to the PF2e campaign -> home is the PF2e lobby (no Cosmere bleed)
        pr = c.post('/campaign/' + pf_cid + '/activate')
        assert pr.status_code == 302 and pr.headers['Location'].endswith('/gm')
        assert A.COSMERE_PC_DIR == storage.cosmere_pc_dir(pf_cid)
        ph = c.get('/')
        assert ph.status_code == 200 and b'PF2E' in ph.data       # lobby, brand PF2E
        assert c.get('/gm').status_code == 200                    # PF2e GM hub renders normally
        assert b'Kaladin' not in c.get('/cosmere/pcs').data        # Cosmere store scoped per-campaign

        print('COSMERE_BINDING_OK')
    ''')
    assert 'COSMERE_BINDING_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_cosmere_player_hub_claim_flow():
    """A player claims a Cosmere character via a GM invite and lands on their own
    Cosmere player hub -- the Cosmere sibling of /player, no PF2e bleed."""
    r = _run('''
        import tempfile, os, re, json, uuid
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        gm = A.app.test_client()

        assert gm.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert gm.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        assert gm.post('/campaign/' + cid + '/activate').status_code == 302   # GM takes the live slot -> paths bind

        # GM prepares an UNCLAIMED Cosmere PC for a player (owner unset, in the campaign store)
        pid = uuid.uuid4().hex
        doc = {'id': pid, 'system': 'cosmere', 'name': 'Shallan',
               'build': {'name': 'Shallan', 'radiant_order': 'lightweavers', 'ideals_sworn': 1, 'level': 2}}
        with open(os.path.join(storage.cosmere_pc_dir(cid), pid + '.json'), 'w') as f:
            json.dump(doc, f)

        # the invites page lists the Cosmere PC + mints a join code
        page = gm.get('/campaign/' + cid + '/invites').data
        assert b'Shallan' in page
        code = re.findall(rb'code=([A-Z2-9]{4}-[A-Z2-9]{4})', page)[0].decode()

        # a brand-new player claims it -> owner stamped on the Cosmere PC doc
        p = A.app.test_client()
        assert p.post('/join', data={'code': code, 'username': 'vasher', 'password': 'pw12345'}).status_code == 302
        puid = auth.get_user_by_username('vasher')['id']
        claimed = storage.load_json(os.path.join(storage.cosmere_pc_dir(cid), pid + '.json'))
        assert claimed['owner_user_id'] == puid and claimed['campaign_id'] == cid

        # activating as the player lands on the Cosmere PLAYER hub (not the GM roster, not PF2e /player)
        ar = p.post('/campaign/' + cid + '/activate')
        assert ar.status_code == 302 and ar.headers['Location'].endswith('/cosmere/player'), ar.headers.get('Location')
        hub = p.get('/cosmere/player')
        assert hub.status_code == 200
        body = hub.data.decode()
        assert 'Shallan' in body and 'Full sheet' in body and 'Lightweavers' in body
        assert 'My Character' in body and 'GM Hub' not in body and 'Builder' not in body   # player nav, no GM/PF2e bleed
        # the PC shows in the player's 'My Characters'
        assert any(m['name'] == 'Shallan' and m['system'] == 'cosmere' for m in campaigns.characters_for_user(puid))

        print('COSMERE_PLAYER_HUB_OK')
    ''')
    assert 'COSMERE_PLAYER_HUB_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
