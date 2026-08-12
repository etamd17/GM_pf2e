"""Stage 6b: the shared table screen.

Round 7 settled what this is: a browser on a TV, driven by the GM's own
machine, showing fog applied and hidden tokens absent, with no chrome, larger
nameplates, and a prominent turn indicator.

It is a VIEW MODE of the existing page rather than a second application, which
is what the stage 4b split (render by view, permit by role) bought. That in turn
is why a GM-authenticated route is enough: the GM opens it themselves on a
screen they control, so no player-facing auth surface is reintroduced -- and
that absence is what keeps the two leaks found during the port unreachable.

It follows whatever scene is ON THE TABLE rather than naming one in the URL, so
pushing a different scene changes the TV without touching this window.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'f6' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()


@pytest.fixture
def gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    return app.app.test_client()


# --- the route -------------------------------------------------------------

def test_the_table_route_follows_the_scene_on_the_table(gm):
    prep = scenes.create_scene(CID, 'Prep')
    live = scenes.create_scene(CID, 'Live')
    scenes.set_table_scene(CID, live['id'])
    response = gm.get('/map/table')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert live['id'] in body
    assert prep['id'] not in body, 'the table must not see what is only being prepped'


def test_pushing_a_different_scene_changes_what_the_table_shows(gm):
    a = scenes.create_scene(CID, 'A')
    b = scenes.create_scene(CID, 'B')
    scenes.set_table_scene(CID, a['id'])
    assert a['id'] in gm.get('/map/table').get_data(as_text=True)
    scenes.set_table_scene(CID, b['id'])
    assert b['id'] in gm.get('/map/table').get_data(as_text=True)


def test_an_empty_table_renders_rather_than_erroring(gm):
    """Nothing on the table is a normal state -- before a session, between
    fights -- and stage 2b made it representable on purpose."""
    scenes.create_scene(CID, 'Not pushed')
    assert scenes.table_scene_id(CID) is None
    assert gm.get('/map/table').status_code == 200


def test_the_table_route_is_gm_only(tmp_path, monkeypatch):
    """The whole reason a GM-authenticated route is sufficient: the GM opens it
    on a screen they control, so no player-facing surface comes back."""
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    with app.app.test_client() as client:
        assert client.get('/map/table').status_code in (302, 403)


def test_table_is_not_swallowed_by_the_scene_id_route():
    """/map/<scene_id> would happily match 'table'. Registration order is what
    keeps them apart, so assert both exist rather than trusting it."""
    rules = {r.rule for r in app.app.url_map.iter_rules()}
    assert '/map/table' in rules and '/map/<scene_id>' in rules


# --- it is a view mode, not a second app -----------------------------------

def test_the_client_starts_in_table_mode():
    assert "let viewMode = (cfg.tableView || !cfg.isGm) ? 'table' : 'gm';" in _JS
    assert "'tableView': table_view|default(false)," in _HTML


def test_the_table_has_no_gm_chrome():
    for guard in ('{% if map_gm and not table_view %}',):
        assert guard in _HTML
    assert _HTML.count('{% if map_gm and not table_view %}') >= 3, (
        'sidebar, create bar and the preview button all have to go')
    assert '.map-page.is-table-screen .map-header-actions { display:none; }' in _CSS


def test_the_table_screen_marks_itself_on_the_page():
    assert 'is-table-screen' in _HTML and 'is-table-screen' in _CSS


# --- what it shows differently ---------------------------------------------

def test_nameplates_are_scaled_for_the_room():
    body = _JS[_JS.index('if (token.show_nameplate !== false) {'):]
    # Stage 7c routed this through tableType(), which is the same 13 -> 22 the
    # nameplate always used -- but now the health bar and condition text scale
    # with it instead of staying pinned at laptop size.
    assert "tableType(13)" in body[:700], (
        'the table is read from several feet away; the GM screen is not')
    assert 'function tableType(px) { return isTableView() ? Math.round(px * 1.7) : px; }' in _JS


def test_the_turn_indicator_is_table_only():
    assert 'function drawTurnBanner(' in _JS
    body = _JS[_JS.index('function drawTurnBanner('):]
    body = body[:body.index('\n    }')]
    assert 'if (!isTableView()) return;' in body


def test_the_turn_indicator_never_names_a_hidden_creature():
    """An ambusher taking its turn must not announce itself on the shared
    screen -- that is the one place it would give the game away."""
    body = _JS[_JS.index('function drawTurnBanner('):]
    body = body[:body.index('\n    }')]
    assert "visible_to_players !== false" in body


def test_the_turn_indicator_stays_put_while_the_map_pans():
    body = _JS[_JS.index('function drawTurnBanner('):]
    body = body[:body.index('\n    }')]
    assert 'viewport.scrollLeft' in body and 'setTransform' in body


def test_the_banner_is_drawn_outside_the_gm_only_overlay():
    """drawToolOverlay is skipped in table view, so a banner drawn from inside
    it would never appear on the only screen it is for."""
    render = _JS[_JS.index('function renderScene()'):]
    render = render[:render.index('\n    function drawGrid')]
    assert 'if (!isTableView()) drawToolOverlay();' in render
    assert 'drawTurnBanner();' in render
