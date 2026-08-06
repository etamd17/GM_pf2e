"""Shared PF2e routes must not render PF2e content for a Cosmere table reached
by a bookmarked/typed URL — they redirect to the Cosmere equivalent. And the
PF2e player nav's Combat tab points at the phone-optimized /mobile view, not the
heavy GM /tracker page.
"""
from __future__ import annotations

import pathlib

import app


def test_shared_pf2e_routes_redirect_in_cosmere_mode(monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'cosmere')
    client = app.app.test_client()
    for path, dest in [('/gmscreen', '/cosmere/gmscreen'),
                       ('/generator', '/cosmere/generator'),
                       ('/player', '/cosmere/player')]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (301, 302), f"{path} did not redirect"
        assert dest in r.headers.get('Location', ''), f"{path} -> {r.headers.get('Location')}"


def test_shared_routes_unchanged_in_pf2e_mode(monkeypatch):
    monkeypatch.setattr(app, '_active_system', lambda: 'pf2e')
    client = app.app.test_client()
    # PF2e mode still serves the PF2e screens (no redirect to /cosmere/*)
    r = client.get('/gmscreen', follow_redirects=False)
    assert '/cosmere/' not in r.headers.get('Location', '')


def test_player_nav_combat_links_to_mobile():
    html = pathlib.Path('templates/_player_nav.html').read_text()
    assert 'href="/mobile"' in html
    assert 'href="/tracker"' not in html
