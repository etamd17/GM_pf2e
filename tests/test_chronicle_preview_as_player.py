"""Previewing the Chronicle as a player.

The GM could not see what their table sees. Every /chronicle* route funnels
through _chronicle_visible_pages and _handout_visible_to_request, so the preview
is one identity override feeding both -- the same reason the two publishing
lanes merge in one place.

THE SECURITY PROPERTY IS THE POINT. /chronicle is a PUBLIC route: players reach
it. The `as` parameter chooses an identity, so a player who could set it would
be choosing somebody else's and reading their secrets. It is honoured only when
the REAL _is_gm() is true, checked before any override, and it only ever narrows
what the GM's own session shows.

There is deliberately no single "the players' view": `recipients` is per-PC, so
four players can each be shown something different. 'player' previews a generic
player who owns nothing; a user id previews that person.
"""
from __future__ import annotations

import os

import pytest

import app


_SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'app.py'), encoding='utf-8').read()


def _fn(name):
    start = _SRC.index('def %s(' % name)
    return _SRC[start:_SRC.index('\ndef ', start + 1)]


# --- the gate ---------------------------------------------------------------

def test_the_parameter_is_ignored_unless_the_caller_is_really_a_gm():
    """/chronicle is public. A player setting ?as=<someone> must get nothing."""
    body = _fn('_chronicle_preview_target')
    assert 'if not key or not _is_gm():' in body
    assert 'return None' in body


def test_the_gm_check_happens_before_any_override():
    """If the override were applied first, the function would be asking whether
    the IMPERSONATED identity is a GM."""
    body = _fn('_chronicle_preview_target')
    assert body.index('_is_gm()') < body.index("key == 'player'")


def test_an_unknown_identity_previews_nothing_rather_than_everything():
    """Falling back to the GM's own view on a bad id would silently show the GM
    their own privileged view while the banner claimed otherwise."""
    body = _fn('_chronicle_preview_target')
    assert 'if not user:' in body and body.count('return None') >= 2


def test_previewing_drops_gm_privilege_rather_than_faking_a_user():
    body = _fn('_chronicle_view_identity')
    assert "return preview['user'], False" in body


# --- both visibility surfaces share it --------------------------------------

def test_pages_and_handouts_share_one_identity():
    """Hiding secret PAGES while still listing every secret HANDOUT is a worse
    lie than having no preview."""
    assert 'user, gm = _chronicle_view_identity()' in _chronicle_visible_source()
    handout = _fn('_handout_visible_to_request')
    assert '_chronicle_view_identity()' in handout


def _chronicle_visible_source():
    start = _SRC.index('def _chronicle_visible_pages(')
    return _SRC[start:_SRC.index('\ndef ', start + 1)]


# --- what the GM is told ----------------------------------------------------

def test_every_chronicle_screen_carries_the_banner_state():
    """The hazard of a preview is forgetting you are in one and concluding the
    players cannot see something they can."""
    body = _fn('_chronicle_render')
    assert 'chronicle_preview=preview' in body
    base = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             'templates', 'chronicle_base.html'), encoding='utf-8').read()
    assert 'chron-preview-bar' in base
    assert 'Back to your view' in base, 'there must always be a way out'


def test_named_players_are_never_listed_to_a_non_gm_or_without_accounts():
    """Two separate guarantees. A non-GM gets no names at all. Outside account
    mode there are no per-player identities to name -- though the GM is still
    offered the generic 'as a player' view, which is the only question that
    exists there."""
    body = _fn('_chronicle_preview_options')
    assert 'if not _is_gm():' in body
    assert 'if not _account_mode():' in body
    assert body.count('return []') >= 2


def test_the_picker_lists_players_not_gms():
    body = _fn('_chronicle_preview_options')
    assert "member.get('role') != 'player'" in body


# --- behaviour --------------------------------------------------------------

@pytest.fixture
def client():
    return app.app.test_client()


def test_a_non_gm_cannot_preview_as_anyone(client, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    with app.app.test_request_context('/chronicle?as=player'):
        assert app._chronicle_preview_target() is None


def test_a_gm_can_preview_as_a_generic_player(monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    with app.app.test_request_context('/chronicle?as=player'):
        target = app._chronicle_preview_target()
        assert target is not None and target['user'] is None
        user, is_gm = app._chronicle_view_identity()
        assert user is None and is_gm is False


def test_without_the_parameter_nothing_changes(monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    with app.app.test_request_context('/chronicle'):
        assert app._chronicle_preview_target() is None
        _, is_gm = app._chronicle_view_identity()
        assert is_gm is True


def test_a_secret_page_is_hidden_from_a_generic_player_preview(monkeypatch):
    """The whole point: a page addressed to one PC must vanish in the preview."""
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    secret = {'slug': 's', 'section': 'story', 'recipients': ['kaladin']}
    public = {'slug': 'p', 'section': 'story', 'recipients': 'all'}
    with app.app.test_request_context('/chronicle?as=player'):
        user, is_gm = app._chronicle_view_identity()
        assert app._chronicle_page_visible(public, user=user, is_gm=is_gm) is True
        assert app._chronicle_page_visible(secret, user=user, is_gm=is_gm) is False
    with app.app.test_request_context('/chronicle'):
        user, is_gm = app._chronicle_view_identity()
        assert app._chronicle_page_visible(secret, user=user, is_gm=is_gm) is True


def test_neither_the_picker_nor_the_banner_reaches_a_non_gm(monkeypatch):
    """/chronicle is public, so this is the end-to-end version of the gate: a
    player must not even be told the control exists, and passing the parameter
    by hand must do nothing."""
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    client = app.app.test_client()
    plain = client.get('/chronicle').get_data(as_text=True)
    forced = client.get('/chronicle?as=player').get_data(as_text=True)
    assert 'chron-preview-pick' not in plain
    assert 'chron-preview-bar' not in forced


def test_a_gm_is_offered_the_preview_even_outside_account_mode(monkeypatch):
    """Legacy-open has no per-player identity, but "as a generic player" still
    answers the only question there is: is this page GM-only?"""
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    html = app.app.test_client().get('/chronicle').get_data(as_text=True)
    assert 'chron-preview-pick' in html
    assert '?as=player' in html
