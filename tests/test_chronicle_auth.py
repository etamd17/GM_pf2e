"""Chronicle player-scope auth: recipient visibility + handout-leak fix.

Recipients key on ACCOUNT OWNERSHIP (owner_user_id), never the self-asserted
session['player_name'] (app.py:6050). Chronicle pages address a pc SLUG; live
handouts address a pc NAME. Legacy-open (unauthenticated identity) => non-'all'
content is GM-only.
"""
import app


def test_page_all_is_public():
    # A public page shows to anyone, even an anonymous non-GM.
    assert app._chronicle_page_visible({'recipients': 'all'}, user=None, is_gm=False)
    assert app._chronicle_page_visible({'recipients': ['all']}, user=None, is_gm=False)
    # Missing recipients defaults to public (author didn't scope it).
    assert app._chronicle_page_visible({}, user=None, is_gm=False)


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
