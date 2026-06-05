"""Open registration + campaign member management.

Proves the multi-tenant goal: anyone can sign up (no invite), create and GM their
own campaign, and manage who's in it (invite players/co-GMs, promote/demote,
remove) -- with the last-GM and non-member guards. Subprocess + throwaway
DATA_DIR (like tests/test_phase1.py) so it's CI-safe and never touches the repo.
"""
import os
import sys
import textwrap
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, cwd=_REPO)


def test_open_registration_and_member_management():
    r = _run('''
        import tempfile, os, re
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        admin = A.app.test_client()
        admin.post('/setup', data={'username': 'admin', 'password': 'secret1', 'display_name': 'Admin'})

        # open registration: a friend signs up with NO invite, gets a regular account
        friend = A.app.test_client()
        assert friend.post('/register', data={'username': 'friend', 'password': 'pw12345', 'display_name': 'Friend'}).status_code == 302
        fuid = auth.get_user_by_username('friend')['id']
        assert auth.get_user(fuid)['is_admin'] is False
        # duplicate username is rejected
        dup = A.app.test_client()
        assert dup.post('/register', data={'username': 'friend', 'password': 'pw12345'}).status_code == 400

        # the friend creates + GMs their OWN campaign
        assert friend.post('/campaigns/new', data={'name': 'Friend Cosmere', 'system': 'cosmere'}).status_code == 302
        cid = [i for i in storage.list_campaign_ids() if campaigns.get_campaign(i)['name'] == 'Friend Cosmere'][0]
        assert campaigns.is_gm(campaigns.get_campaign(cid), fuid)

        # the friend (GM) mints a generic player join link; a player claims it
        assert friend.post('/campaign/' + cid + '/invite', data={'role': 'player'}).status_code == 302
        page = friend.get('/campaign/' + cid + '/invites').data
        code = re.findall(rb'code=([A-Z2-9]{4}-[A-Z2-9]{4})', page)[0].decode()
        player = A.app.test_client()
        assert player.post('/join', data={'code': code, 'username': 'pl', 'password': 'pw12345', 'display_name': 'Player One'}).status_code == 302
        puid = auth.get_user_by_username('pl')['id']
        assert campaigns.user_role(campaigns.get_campaign(cid), puid) == 'player'

        # the manage page lists both members
        page2 = friend.get('/campaign/' + cid + '/invites').data.decode()
        assert 'Friend' in page2 and 'Player One' in page2

        # a non-member cannot manage the campaign
        rando = A.app.test_client()
        rando.post('/register', data={'username': 'rando', 'password': 'pw12345'})
        assert rando.post('/campaign/' + cid + '/members/' + puid + '/remove').status_code == 403

        # GM promotes the player to co-GM, then demotes (allowed -- 2 GMs)
        friend.post('/campaign/' + cid + '/members/' + puid + '/role', data={'role': 'gm'})
        assert campaigns.user_role(campaigns.get_campaign(cid), puid) == 'gm'
        friend.post('/campaign/' + cid + '/members/' + puid + '/role', data={'role': 'player'})
        assert campaigns.user_role(campaigns.get_campaign(cid), puid) == 'player'

        # GM removes the player
        friend.post('/campaign/' + cid + '/members/' + puid + '/remove')
        assert campaigns.user_role(campaigns.get_campaign(cid), puid) is None

        # the last GM is protected -- the campaign can't be left without a GM
        friend.post('/campaign/' + cid + '/members/' + fuid + '/remove')
        assert campaigns.is_gm(campaigns.get_campaign(cid), fuid)

        print('REGISTRATION_MEMBERS_OK')
    ''')
    assert 'REGISTRATION_MEMBERS_OK' in r.stdout, "stdout:\\n%s\\nstderr:\\n%s" % (r.stdout, r.stderr)
