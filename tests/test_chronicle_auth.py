"""Chronicle player-scope auth: recipient visibility + handout-leak fix.

Recipients key on ACCOUNT OWNERSHIP (owner_user_id), never the self-asserted
session['player_name'] (app.py:6050). Chronicle pages address a pc SLUG; live
handouts address a pc NAME. Legacy-open (unauthenticated identity) => non-'all'
content is GM-only.
"""
import json
import os
import subprocess
import sys
import textwrap

import app

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(body):
    script = "import os, sys\nsys.path.insert(0, os.getcwd())\n" + textwrap.dedent(body)
    return subprocess.run([sys.executable, '-c', script],
                          capture_output=True, text=True, cwd=_REPO)


def test_page_all_is_public():
    # A public page shows to anyone, even an anonymous non-GM.
    assert app._chronicle_page_visible({'recipients': 'all'}, user=None, is_gm=False)
    assert app._chronicle_page_visible({'recipients': ['all']}, user=None, is_gm=False)
    # Missing recipients defaults to public (author didn't scope it).
    assert app._chronicle_page_visible({}, user=None, is_gm=False)
    # Literal `recipients: None` (blank-YAML frontmatter shape) is public too --
    # the contract is 'all' / list-containing-'all' / absent OR None -> public.
    assert app._chronicle_page_visible({'recipients': None}, user=None, is_gm=False) is True


def test_page_targeted_gm_sees_all():
    assert app._chronicle_page_visible({'recipients': ['aria']}, user=None, is_gm=True)


def test_page_targeted_owner_sees_nonowner_hidden(monkeypatch):
    monkeypatch.setattr(app, '_account_mode', lambda: True)
    monkeypatch.setattr(app, '_chronicle_owned_pc_slugs',
                        lambda uid: {'aria'} if uid == 'u_alice' else set())
    assert app._chronicle_page_visible({'recipients': ['aria']},
                                       user={'id': 'u_alice'}, is_gm=False)
    assert not app._chronicle_page_visible({'recipients': ['aria']},
                                           user={'id': 'u_bob'}, is_gm=False)
    # 'all' in a mixed list is still public.
    assert app._chronicle_page_visible({'recipients': ['aria', 'all']},
                                       user={'id': 'u_bob'}, is_gm=False)


def test_page_targeted_legacy_open_is_gm_only(monkeypatch):
    # No accounts -> no trustworthy identity -> a scoped page never reaches a player.
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    assert not app._chronicle_page_visible({'recipients': ['aria']},
                                           user=None, is_gm=False)


def test_owned_pc_slugs_scans_both_dirs_by_owner(tmp_path, monkeypatch):
    """Direct test of the disk-scanning resolver itself (no stub) -- exercises
    listdir + load_json + owner_user_id match + slugify(_character_name(doc))
    across BOTH the pf2e party_dir and the cosmere_pc_dir."""
    party = tmp_path / 'party_data'
    cosmere = tmp_path / 'cosmere_pcs'
    party.mkdir()
    cosmere.mkdir()

    (party / 'aria.json').write_text(json.dumps({'owner_user_id': 'u1', 'build': {'name': 'Aria'}}))
    (party / 'bob.json').write_text(json.dumps({'owner_user_id': 'u2', 'build': {'name': 'Bob'}}))
    (cosmere / 'shallan.json').write_text(json.dumps({'owner_user_id': 'u1', 'build': {'name': 'Shallan'}}))

    # Robustness: a non-dict/garbage json file and a doc with no owner_user_id
    # must be skipped without raising, and must not be counted for anyone.
    (party / 'garbage.json').write_text('not valid json {{{')
    (cosmere / 'noowner.json').write_text(json.dumps({'build': {'name': 'Nobody'}}))

    monkeypatch.setattr(app, '_active_campaign_id', lambda: 'testcid')
    monkeypatch.setattr(app._storage, 'party_dir', lambda cid: str(party))
    monkeypatch.setattr(app._storage, 'cosmere_pc_dir', lambda cid: str(cosmere))

    # Confirm the exact slugify shape rather than assuming it.
    assert app._storage.slugify('Aria') == 'aria'
    assert app._storage.slugify('Bob') == 'bob'
    assert app._storage.slugify('Shallan') == 'shallan'

    assert app._chronicle_owned_pc_slugs('u1') == {'aria', 'shallan'}  # owns across BOTH dirs
    assert app._chronicle_owned_pc_slugs('u2') == {'bob'}
    assert app._chronicle_owned_pc_slugs('u3') == set()


def test_handout_player_filter_drops_targeted():
    # The player SSE frame is SHARED by every player (sse_broadcast has no
    # per-player identity), so a targeted handout must NOT fan out live.
    assert app._handout_player_filter({'recipients': ['all']}) is not None
    assert app._handout_player_filter({'recipients': ['all', 'Aria']}) is not None
    assert app._handout_player_filter({'recipients': ['Aria']}) is None
    assert app._handout_player_filter({'recipients': []}) is None
    assert app._handout_player_filter({}) is None


def test_chronicle_gate_account_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
        auth.create_user('alice','pw_alice12','Alice'); alice = auth.get_user_by_username('alice')
        auth.create_user('bob','pw_bob1234','Bob');     bob   = auth.get_user_by_username('bob')

        assert c.post('/campaigns/new', data={'name':'Golarion','system':'pf2e'}).status_code == 302
        cid = [x for x in storage.list_campaign_ids()
               if campaigns.get_campaign(x)['name']=='Golarion'][0]
        campaigns.add_member(cid, alice['id'], 'player')      # bob is NOT a member
        assert c.post('/campaign/'+cid+'/activate').status_code == 302

        # bob (not a member of this campaign) is refused at the gate.
        with c.session_transaction() as s: s['user_id']=bob['id']; s['active_campaign_id']=cid
        assert c.get('/chronicle').status_code == 403, 'non-member must be blocked'

        # alice (a member) passes the gate; the /chronicle route then renders the
        # empty state (no publish in this DATA_DIR) -> 200 proves the gate let her through.
        with c.session_transaction() as s: s['user_id']=alice['id']; s['active_campaign_id']=cid
        assert c.get('/chronicle').status_code == 200, 'member must pass the gate (200 = empty-state render)'

        # a logged-OUT caller in account mode is redirected to login, not 403.
        with c.session_transaction() as s: s.clear()
        rv = c.get('/chronicle')
        assert rv.status_code == 302 and '/login' in rv.headers['Location'], rv.headers.get('Location')
        print('GATE_ACCOUNT_OK')
    ''')
    assert 'GATE_ACCOUNT_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_gate_legacy_password_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = 'sekret'
        import app as A
        c = A.app.test_client()
        # legacy mode WITH a password: a player who has not picked a character is
        # refused; picking one (session player_name) lets them through (200 = empty-state render).
        assert c.get('/chronicle').status_code == 403
        with c.session_transaction() as s: s['player_name'] = 'Aria'
        assert c.get('/chronicle').status_code == 200
        # the GM (authenticated) always passes.
        with c.session_transaction() as s:
            s.clear(); s['gm_authenticated'] = True
        assert c.get('/chronicle').status_code == 200
        print('GATE_LEGACY_OK')
    ''')
    assert 'GATE_LEGACY_OK' in r.stdout, r.stdout + r.stderr


def test_handout_recipients_ownership_account_mode():
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp(); os.environ['GM_PASSWORD'] = ''
        import app as A
        from core import storage, auth, campaigns
        c = A.app.test_client()

        assert c.post('/setup', data={'username':'gm','password':'secret1','display_name':'GM'}).status_code == 302
        auth.create_user('alice','pw_alice12','Alice'); alice = auth.get_user_by_username('alice')
        auth.create_user('bob','pw_bob1234','Bob');     bob   = auth.get_user_by_username('bob')

        assert c.post('/campaigns/new', data={'name':'Golarion','system':'pf2e'}).status_code == 302
        cid = [x for x in storage.list_campaign_ids()
               if campaigns.get_campaign(x)['name']=='Golarion'][0]
        campaigns.add_member(cid, alice['id'], 'player')
        campaigns.add_member(cid, bob['id'], 'player')
        assert c.post('/campaign/'+cid+'/activate').status_code == 302

        # A claimed PC 'Aria' owned by alice, written straight to the party store.
        doc = storage.wrap_character(storage.new_id(), cid, 'pf2e',
                                     {'name':'Aria','build':{'name':'Aria'}},
                                     owner_user_id=alice['id'])
        storage.atomic_write_json(os.path.join(storage.party_dir(cid), 'Aria.json'), doc)

        # GM (the setup admin session) creates a handout targeted ONLY at Aria.
        assert c.post('/api/handouts',
                      json={'title':'For Aria','content':'secret','recipients':['Aria']}).status_code == 200
        # ...and a public one.
        assert c.post('/api/handouts',
                      json={'title':'Town Notice','content':'hi','recipients':['all']}).status_code == 200

        def titles(resp): return {h['title'] for h in resp.get_json()['handouts']}

        # alice (the OWNER) sees the targeted handout -- with NO ?player= param.
        with c.session_transaction() as s: s['user_id']=alice['id']; s['active_campaign_id']=cid
        assert 'For Aria' in titles(c.get('/api/handouts'))
        assert 'Town Notice' in titles(c.get('/api/handouts'))

        # bob (a member but NOT the owner) never sees it, and cannot conjure it
        # by passing ?player=Aria (the old trusted-param leak is closed).
        with c.session_transaction() as s: s['user_id']=bob['id']; s['active_campaign_id']=cid
        assert 'For Aria' not in titles(c.get('/api/handouts'))
        assert 'For Aria' not in titles(c.get('/api/handouts?player=Aria'))
        assert 'Town Notice' in titles(c.get('/api/handouts'))     # public still reaches everyone

        # the GM sees everything.
        with c.session_transaction() as s:
            s.clear(); s['user_id'] = auth.get_user_by_username('gm')['id']; s['active_campaign_id']=cid
        assert {'For Aria','Town Notice'} <= titles(c.get('/api/handouts'))
        print('HANDOUT_RECIPIENTS_OK')
    ''')
    assert 'HANDOUT_RECIPIENTS_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_publish_token_unlocks_only_chronicle():
    # GM_PASSWORD set + no GM session => a plain caller is a non-GM. The
    # CHRONICLE_PUBLISH_TOKEN header must unlock EXACTLY /api/chronicle*, and
    # nothing else, and only when the env token is non-empty and matches.
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        os.environ['GM_PASSWORD'] = 'sekret'
        os.environ['CHRONICLE_PUBLISH_TOKEN'] = 'tok-abc123'
        import app as A
        c = A.app.test_client()

        # No header -> the chronicle publish API is still GM-gated (403).
        assert c.post('/api/chronicle/publish').status_code == 403

        # Wrong token -> still 403.
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': 'nope'}).status_code == 403

        # Correct token -> NOT 403 (the gate let it through; the route itself
        # may 400 on a missing archive, but it is no longer auth-blocked).
        rv = c.post('/api/chronicle/publish',
                    headers={'X-Chronicle-Token': 'tok-abc123'})
        assert rv.status_code != 403, rv.status_code

        # The token does NOT unlock any OTHER GM prefix (scope check).
        assert c.post('/api/clear_encounter',
                      headers={'X-Chronicle-Token': 'tok-abc123'}).status_code == 403
        print('TOKEN_SCOPED_OK')
    ''')
    assert 'TOKEN_SCOPED_OK' in r.stdout, r.stdout + r.stderr


def test_chronicle_publish_token_inert_when_env_unset():
    # With no CHRONICLE_PUBLISH_TOKEN in the environment, the header is inert:
    # a matching-looking header cannot unlock anything (empty expected != any).
    r = _run('''
        import tempfile, os
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        os.environ['GM_PASSWORD'] = 'sekret'
        os.environ.pop('CHRONICLE_PUBLISH_TOKEN', None)
        import app as A
        c = A.app.test_client()
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': ''}).status_code == 403
        assert c.post('/api/chronicle/publish',
                      headers={'X-Chronicle-Token': 'anything'}).status_code == 403
        print('TOKEN_INERT_OK')
    ''')
    assert 'TOKEN_INERT_OK' in r.stdout, r.stdout + r.stderr
