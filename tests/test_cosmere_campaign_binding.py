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
        assert ar.status_code == 302 and ar.headers['Location'].endswith('/cosmere/gm'), ar.headers.get('Location')
        assert A.ACTIVE_CAMPAIGN_ID == cos_cid
        # the Cosmere GM dashboard renders as the command center
        gmhub = c.get('/cosmere/gm')
        assert gmhub.status_code == 200 and b'Encounter Tracker' in gmhub.data and b'Bestiary' in gmhub.data
        # the Cosmere GM Screen (rules reference) renders
        gmscr = c.get('/cosmere/gmscreen')
        assert gmscr.status_code == 200 and b'Plot Die' in gmscr.data and b'Conditions' in gmscr.data
        # the Cosmere generators page renders + a card rerolls via the API
        genp = c.get('/cosmere/generator')
        assert genp.status_code == 200 and b'Cosmere Generators' in genp.data
        assert c.post('/api/cosmere/generate/name').get_json()['html']
        assert A.COSMERE_PC_DIR == storage.cosmere_pc_dir(cos_cid)   # store follows the live campaign

        # the front door sends a logged-in user to /me (the chooser); the
        # system-aware landing happens on activate (asserted above). The nav is Cosmere.
        home = c.get('/')
        assert home.status_code == 302 and home.headers['Location'].endswith('/me')
        pcs = c.get('/cosmere/pcs')
        assert pcs.status_code == 200 and b'COSMERE' in pcs.data    # nav brand flipped
        assert b'href="/party"' not in pcs.data and b'href="/generator"' not in pcs.data   # no PF2e GM-nav bleed (Cosmere has its own nav)
        # the PF2e GM command center redirects to the Cosmere GM hub in Cosmere mode
        gm = c.get('/gm')
        assert gm.status_code == 302 and gm.headers['Location'].endswith('/cosmere/gm')

        # build a Cosmere PC -> lands UNDER the campaign, stamped campaign + owner
        br = c.post('/cosmere/builder', json={'build': {'name': 'Kaladin'}})
        assert br.status_code == 200 and br.get_json()['ok']
        pid = br.get_json()['id']
        pdir = storage.cosmere_pc_dir(cos_cid)
        assert os.path.isfile(os.path.join(pdir, pid + '.json')), os.listdir(pdir)
        doc = storage.load_json(os.path.join(pdir, pid + '.json'))
        # GM-built -> campaign-bound but UNCLAIMED (assignable via a join link)
        assert doc['campaign_id'] == cos_cid and doc['owner_user_id'] is None and doc['name'] == 'Kaladin'
        # NOT written to the legacy flat store
        assert not os.path.isfile(os.path.join(os.environ['DATA_DIR'], 'cosmere_pcs', pid + '.json'))

        # unclaimed -> not in anyone's 'My Characters' yet, but on the roster
        assert not any(m['name'] == 'Kaladin' for m in campaigns.characters_for_user(gm_id))
        assert b'Kaladin' in c.get('/cosmere/pcs').data

        # the tracker is Cosmere-aware: the add UI shows the adversary/PC pickers
        # (not the PF2e monster search), and Cosmere combatants add + carry is_pc.
        tr = c.get('/tracker')
        assert tr.status_code == 200 and b'cos-adv-select' in tr.data and b'Type to search monsters' not in tr.data
        assert c.post('/api/add_combatant', json={'type': 'cosmere', 'path': pid}).status_code == 200
        assert c.post('/api/cosmere/add_party', json={}).status_code == 200
        cstate = c.get('/api/tracker_state').get_json()
        assert cstate['combatants'] and all(cb['system'] == 'cosmere' for cb in cstate['combatants'])
        assert any(cb['name'] == 'Kaladin' and cb['is_pc'] for cb in cstate['combatants'])
        c.post('/api/clear_encounter', json={})

        # switch to the PF2e campaign -> activate lands on /gm, no Cosmere bleed
        pr = c.post('/campaign/' + pf_cid + '/activate')
        assert pr.status_code == 302 and pr.headers['Location'].endswith('/gm')
        assert c.get('/cosmere/gm').status_code == 302     # Cosmere dashboard redirects out in PF2e mode
        assert c.get('/cosmere/gmscreen').status_code == 302   # GM Screen redirects out in PF2e mode too
        assert c.get('/cosmere/generator').status_code == 302   # generators redirect out in PF2e mode too
        assert b'Type to search monsters' in c.get('/tracker').data   # PF2e tracker keeps its monster search (no Cosmere bleed)
        assert A.COSMERE_PC_DIR == storage.cosmere_pc_dir(pf_cid)
        ph = c.get('/')
        assert ph.status_code == 302 and ph.headers['Location'].endswith('/me')   # front door -> /me
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


def test_cosmere_gm_build_then_handoff():
    """The GM builds a character for the party (it stays UNCLAIMED), a player
    claims it via the minted link, and the GM can hand it off again (release)
    to free a fresh link. The clean GM-prep workflow."""
    r = _run('''
        import tempfile, os, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        gm = A.app.test_client()
        gm.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'})
        gm.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'})
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Roshar'][0]
        gm.post('/campaign/' + cid + '/activate')

        def pc_owner():
            return storage.load_json(os.path.join(storage.cosmere_pc_dir(cid), pid + '.json')).get('owner_user_id')

        # GM builds a PC for a player -> UNCLAIMED (assignable), and an edit keeps it that way
        pid = gm.post('/cosmere/builder', json={'build': {'name': 'Shallan', 'radiant_order': 'lightweavers'}}).get_json()['id']
        assert pc_owner() is None
        gm.post('/cosmere/builder', json={'id': pid, 'build': {'name': 'Shallan', 'radiant_order': 'lightweavers', 'level': 2}})
        assert pc_owner() is None                                  # edit preserves (un)ownership

        # invites page lists it WITH a join code
        page = gm.get('/campaign/' + cid + '/invites').data
        assert b'Shallan' in page
        code = re.findall(rb'code=([A-Z2-9]{4}-[A-Z2-9]{4})', page)[0].decode()

        # a player claims it -> owner set; invites now shows it claimed by that player
        p = A.app.test_client()
        assert p.post('/join', data={'code': code, 'username': 'shln', 'password': 'pw12345', 'display_name': 'ShallanP'}).status_code == 302
        puid = auth.get_user_by_username('shln')['id']
        assert pc_owner() == puid
        page2 = gm.get('/campaign/' + cid + '/invites').data
        assert b'claimed' in page2 and b'ShallanP' in page2

        # a non-GM cannot hand off
        assert p.post('/cosmere/pc/' + pid + '/release').status_code == 403
        # the GM hands it off -> ownership cleared, a fresh join code is minted
        assert gm.post('/cosmere/pc/' + pid + '/release').status_code in (302, 200)
        assert pc_owner() is None
        assert re.findall(rb'code=([A-Z2-9]{4}-[A-Z2-9]{4})', gm.get('/campaign/' + cid + '/invites').data)

        print('COSMERE_HANDOFF_OK')
    ''')
    assert 'COSMERE_HANDOFF_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_active_system_follows_user_not_global_live_slot():
    """Regression: a user's active campaign/system must follow THEIR own selection
    (this session, or the campaign they last activated), and must NEVER be dictated
    by whichever campaign happens to hold the server-wide live slot.

    The bug: `_active_campaign_doc()` fell back to the global live slot for any
    logged-in user with no session selection -- so a Cosmere game running on the
    single live slot flipped every other user (and the same user on a fresh login)
    onto the Cosmere side, regardless of their own campaigns.
    """
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns

        def active_system(client):
            gm = client.get('/gm')
            if gm.status_code == 302 and '/cosmere' in gm.headers.get('Location', ''):
                return 'cosmere'
            return 'pf2e' if gm.status_code == 200 else '?%s' % gm.status_code

        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        gm_id = auth.get_user_by_username('gm')['id']
        assert c.post('/campaigns/new', data={'name': 'Roshar', 'system': 'cosmere'}).status_code == 302
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'pf2e'}).status_code == 302
        by = {campaigns.get_campaign(cid)['system']: cid for cid in storage.list_campaign_ids()}
        cos, pf = by['cosmere'], by['pf2e']

        # the admin selects their Pathfinder game -> sees pf2e, choice remembered
        assert c.post('/campaign/' + pf + '/activate').status_code == 302
        assert active_system(c) == 'pf2e'
        assert auth.get_user(gm_id).get('last_campaign_id') == pf

        # a DIFFERENT table puts Cosmere on the single server-wide live slot
        storage.set_live_campaign_id(cos)
        assert storage.get_live_campaign_id() == cos

        # the admin on a FRESH session (expired cookie / new device) resumes THEIR
        # own pf2e game -- not the globally-live Cosmere campaign
        fresh = A.app.test_client()
        assert fresh.post('/login', data={'username': 'gm', 'password': 'secret1'}).status_code == 302
        assert active_system(fresh) == 'pf2e', 'fresh login bled onto the live cosmere slot'

        # a player who belongs ONLY to the Pathfinder game, with Cosmere live, is
        # never adopted onto the campaign they are not a member of
        bob = A.app.test_client()
        assert bob.post('/register', data={'username': 'bob', 'password': 'pw12345', 'display_name': 'Bob'}).status_code == 302
        bob_id = auth.get_user_by_username('bob')['id']
        campaigns.add_member(pf, bob_id, 'player')
        storage.set_live_campaign_id(cos)
        with A.app.test_request_context('/'):
            from flask import session as s
            s['user_id'] = bob_id                       # logged in, never selected anything
            assert A._active_system() == 'pf2e', 'player bled onto the live cosmere slot'
            assert A._active_campaign_doc() is None      # not a member of the live campaign -> not adopted
            s['active_campaign_id'] = pf                 # bob picks his own game
            assert A._active_system() == 'pf2e'

        print('ACTIVE_SYSTEM_PER_USER_OK')
    ''')
    assert 'ACTIVE_SYSTEM_PER_USER_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_admin_can_repair_mis_stamped_campaign_system():
    """Admin tool: a campaign mistakenly stored as the wrong system can be flipped
    back, which fixes the routing (a Pathfinder game saved as 'cosmere' stops
    routing to the Cosmere side once corrected)."""
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()
        assert c.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302

        # a Pathfinder game that got mis-stamped as Cosmere
        assert c.post('/campaigns/new', data={'name': 'Golarion', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        assert campaigns.get_campaign(cid)['system'] == 'cosmere'

        # selecting it routes to the Cosmere side (the reported symptom)
        ar = c.post('/campaign/' + cid + '/activate')
        assert ar.status_code == 302 and '/cosmere' in ar.headers['Location'], ar.headers.get('Location')

        # the admin page lists it and shows its stored system
        page = c.get('/admin/campaigns')
        assert page.status_code == 200 and b'Golarion' in page.data and b'COSMERE' in page.data

        # repair it to Pathfinder
        fix = c.post('/admin/campaigns/' + cid + '/system', data={'system': 'pf2e'})
        assert fix.status_code == 302
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'

        # now selecting it routes to the Pathfinder GM hub, not Cosmere
        ar2 = c.post('/campaign/' + cid + '/activate')
        assert ar2.status_code == 302 and ar2.headers['Location'].endswith('/gm'), ar2.headers.get('Location')

        # non-admins are refused (register on a FRESH client -> logs bob in)
        nb = A.app.test_client()
        assert nb.post('/register', data={'username': 'bob', 'password': 'pw12345', 'display_name': 'Bob'}).status_code == 302
        assert nb.get('/admin/campaigns').status_code == 403
        assert nb.post('/admin/campaigns/' + cid + '/system', data={'system': 'cosmere'}).status_code == 403
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'   # unchanged by the non-admin

        print('ADMIN_CAMPAIGN_REPAIR_OK')
    ''')
    assert 'ADMIN_CAMPAIGN_REPAIR_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
