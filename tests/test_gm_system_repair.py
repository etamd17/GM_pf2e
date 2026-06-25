"""GM-facing campaign-system repair -- a campaign's own GM (not just a site admin)
can fix a mis-stamped game system from their Manage page, so a Pathfinder game
saved as 'cosmere' stops routing to the Cosmere side without anyone needing
/admin access.

Mirrors tests/test_cosmere_campaign_binding.py's subprocess + throwaway-DATA_DIR
style (app.py binds DATA_DIR + per-campaign globals at import).
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_campaign_gm_can_repair_own_system():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns

        # admin bootstrap (first user). A SEPARATE, non-admin user will be the GM.
        admin = A.app.test_client()
        assert admin.post('/setup', data={'username': 'owner', 'password': 'secret1', 'display_name': 'Owner'}).status_code == 302

        gm = A.app.test_client()
        assert gm.post('/register', data={'username': 'gm2', 'password': 'pw12345', 'display_name': 'GM2'}).status_code == 302
        gm_id = auth.get_user_by_username('gm2')['id']
        assert not auth.get_user(gm_id).get('is_admin')      # genuinely NOT a site admin

        # the GM creates their own game but it gets mis-stamped as Cosmere
        assert gm.post('/campaigns/new', data={'name': 'Golarion', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        assert campaigns.get_campaign(cid)['system'] == 'cosmere'

        # selecting it routes to the Cosmere side (the reported symptom)
        ar = gm.post('/campaign/' + cid + '/activate')
        assert ar.status_code == 302 and '/cosmere' in ar.headers['Location'], ar.headers.get('Location')

        # the Manage page shows the current system + the repair control (no /admin needed)
        page = gm.get('/campaign/' + cid + '/invites')
        assert page.status_code == 200
        body = page.data.decode()
        assert 'Game system' in body and 'name="system"' in body

        # the GM repairs it to Pathfinder from their own Manage page
        fix = gm.post('/campaign/' + cid + '/system', data={'system': 'pf2e'})
        assert fix.status_code == 302
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'

        # now selecting it routes to the Pathfinder GM hub, not Cosmere
        ar2 = gm.post('/campaign/' + cid + '/activate')
        assert ar2.status_code == 302 and ar2.headers['Location'].endswith('/gm'), ar2.headers.get('Location')

        # a non-member is refused and cannot change someone else's system
        bob = A.app.test_client()
        assert bob.post('/register', data={'username': 'bob', 'password': 'pw12345', 'display_name': 'Bob'}).status_code == 302
        assert bob.post('/campaign/' + cid + '/system', data={'system': 'cosmere'}).status_code == 403
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'   # unchanged by the non-member

        # an invalid system value is rejected (no silent corruption)
        assert gm.post('/campaign/' + cid + '/system', data={'system': 'dnd5e'}).status_code in (302, 400)
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'

        print('GM_SYSTEM_REPAIR_OK')
    ''')
    assert 'GM_SYSTEM_REPAIR_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)


def test_repairing_live_campaign_rebinds_globals():
    """If the repaired campaign holds the live slot, the in-memory binding follows
    the corrected system immediately (mirrors the admin tool's rebind)."""
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns

        gm = A.app.test_client()
        assert gm.post('/setup', data={'username': 'gm', 'password': 'secret1', 'display_name': 'GM'}).status_code == 302
        assert gm.post('/campaigns/new', data={'name': 'Golarion', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Golarion'][0]
        gm.post('/campaign/' + cid + '/activate')          # GM takes the live slot
        assert storage.get_live_campaign_id() == cid
        # cosmere mode: the PF2e GM hub redirects out to the Cosmere hub
        assert gm.get('/gm').status_code == 302 and '/cosmere' in gm.get('/gm').headers.get('Location', '')
        assert A.ACTIVE_CAMPAIGN_ID == cid and A.COSMERE_PC_DIR == storage.cosmere_pc_dir(cid)

        gm.post('/campaign/' + cid + '/system', data={'system': 'pf2e'})
        assert campaigns.get_campaign(cid)['system'] == 'pf2e'
        # the live binding now reflects pf2e: /gm renders rather than redirecting to /cosmere/gm
        assert gm.get('/gm').status_code == 200

        print('GM_SYSTEM_LIVE_REBIND_OK')
    ''')
    assert 'GM_SYSTEM_LIVE_REBIND_OK' in r.stdout, "stdout:\n%s\nstderr:\n%s" % (r.stdout, r.stderr)
