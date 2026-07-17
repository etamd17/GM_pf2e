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

        # alice (a member) passes the gate; no reading route exists yet in this
        # slice, so Flask 404s AFTER the gate -> proves the gate let her through.
        with c.session_transaction() as s: s['user_id']=alice['id']; s['active_campaign_id']=cid
        assert c.get('/chronicle').status_code == 404, 'member must pass the gate (404 = route not built)'

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
        # refused; picking one (session player_name) lets them through (404=no route).
        assert c.get('/chronicle').status_code == 403
        with c.session_transaction() as s: s['player_name'] = 'Aria'
        assert c.get('/chronicle').status_code == 404
        # the GM (authenticated) always passes.
        with c.session_transaction() as s:
            s.clear(); s['gm_authenticated'] = True
        assert c.get('/chronicle').status_code == 404
        print('GATE_LEGACY_OK')
    ''')
    assert 'GATE_LEGACY_OK' in r.stdout, r.stdout + r.stderr
