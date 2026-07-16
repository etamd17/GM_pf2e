"""Chronicle player-scope auth: recipient visibility + handout-leak fix.

Recipients key on ACCOUNT OWNERSHIP (owner_user_id), never the self-asserted
session['player_name'] (app.py:6050). Chronicle pages address a pc SLUG; live
handouts address a pc NAME. Legacy-open (unauthenticated identity) => non-'all'
content is GM-only.
"""
import json

import app


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
