"""Stage 7a: the table screen becomes a table screen.

The UI audit measured `/map/table` on a 1920x1080 TV and found the drawn map
getting 15% of the display: 213px of chrome above it, 321px of dead margin each
side, and a row of 18 GM tools -- Reveal fog, Erase, Poison -- that nobody in the
room can use and that invite questions mid-scene.

The GM's answer to "how much should I strip" was: everything, except that the
players should still be able to see a PING and a RULER, so measuring something
for the table and showing someone how far they could travel happen on the shared
screen instead of being read out.

That second half is the interesting one. The table screen is a SEPARATE BROWSER:
a ruler dragged on the GM's laptop is invisible to a window that has never heard
of their pointer. So "let the players see me measure that" is a broadcast
problem, not a rendering one -- which is why there is a beacon endpoint at all.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'a7' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


@pytest.fixture
def gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    return app.app.test_client()


# --- the TV gets its screen back --------------------------------------------

def test_every_piece_of_chrome_is_stripped_from_the_table():
    """Measured before: 213px of chrome above the map and a row of GM tools the
    room cannot use. After: nothing but the map."""
    for hidden in ('.map-page.is-table-screen .map-header',
                   '.map-page.is-table-screen .map-toolstrip',
                   '.map-page.is-table-screen .map-stage-toolbar'):
        assert hidden in _CSS


def test_the_site_nav_goes_too():
    """It lives in base.html, so it is hidden from here rather than by threading
    a flag through every page. If :has() is unsupported the nav simply stays --
    i.e. it degrades to exactly today's behaviour rather than to something
    broken."""
    assert 'body:has(.map-page.is-table-screen) nav' in _CSS


def test_the_base_template_wrappers_are_neutralised():
    """base.html wraps every page in a 1280px centred container inside a 32px
    padded region -- 640px of width and 64px of height on a 1920 TV before the
    map got a say. Selected by CONTAINMENT rather than by the Tailwind class
    names, so it keeps working when those change."""
    assert 'body:has(.map-page.is-table-screen) div:has(.map-page.is-table-screen)' in _CSS
    assert '.map-page.is-table-screen { max-width:none; margin:0; }' in _CSS


def test_the_table_always_fits_rather_than_restoring_a_view():
    """A TV has no operator to correct a restored view, and the saved one is
    whatever the last window left behind -- so it would come up scrolled into a
    corner and stay there all session."""
    assert 'if (isTableView() || !restoreView()) fitMap();' in _JS


# --- pointing at things, across two screens ---------------------------------

def test_a_beacon_is_never_stored(gm):
    """A ping that survived a reload would be a mystery ring on a battlemap
    nobody remembers making, and a ruler is only true while it is being held."""
    scene = scenes.create_scene(CID, 'Cavern')
    before = scenes.load_scene(CID, scene['id'])
    sent = []
    app.sse_broadcast_original = getattr(app, 'sse_broadcast')
    try:
        app.sse_broadcast = lambda *a, **k: sent.append((a, k))
        response = gm.post('/api/scenes/%s/beacon' % scene['id'],
                           json={'kind': 'ping', 'x': 100, 'y': 200})
    finally:
        app.sse_broadcast = app.sse_broadcast_original
    assert response.status_code == 200
    after = scenes.load_scene(CID, scene['id'])
    assert after == before, 'a beacon must not touch the scene'
    assert after['revision'] == before['revision'], 'and must not bump the revision'
    assert sent, 'but it must go out over SSE'


def test_a_ping_carries_its_point(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    sent = []
    original = app.sse_broadcast
    try:
        app.sse_broadcast = lambda event, data, **k: sent.append((event, data))
        gm.post('/api/scenes/%s/beacon' % scene['id'], json={'kind': 'ping', 'x': 12, 'y': 34})
    finally:
        app.sse_broadcast = original
    assert sent and sent[0][0] == 'scene_beacon'
    assert sent[0][1]['kind'] == 'ping'
    assert sent[0][1]['x'] == 12 and sent[0][1]['y'] == 34


def test_a_measure_can_clear_itself(gm):
    """Putting the ruler down has to take the line off the TV, rather than
    leaving the room staring at a stale number."""
    scene = scenes.create_scene(CID, 'Cavern')
    sent = []
    original = app.sse_broadcast
    try:
        app.sse_broadcast = lambda event, data, **k: sent.append(data)
        gm.post('/api/scenes/%s/beacon' % scene['id'], json={'kind': 'measure', 'clear': True})
    finally:
        app.sse_broadcast = original
    assert sent and sent[0]['clear'] is True


def test_beacons_are_validated(gm):
    scene = scenes.create_scene(CID, 'Cavern')
    url = '/api/scenes/%s/beacon' % scene['id']
    assert gm.post(url, json={'kind': 'shout', 'x': 1, 'y': 1}).status_code == 400
    assert gm.post(url, json={'kind': 'ping', 'x': 'over there', 'y': 1}).status_code == 400
    assert gm.post(url, json={'kind': 'ping', 'x': float('inf'), 'y': 1}).status_code == 400


def test_beacons_are_gm_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    with app.app.test_client() as client:
        response = client.post('/api/scenes/whatever/beacon',
                               json={'kind': 'ping', 'x': 1, 'y': 1})
        assert response.status_code in (302, 403)


def test_the_ruler_is_broadcast_as_the_gm_drags_it():
    assert 'shareMeasure(ruler.start, ruler.end, label, false);' in _JS


def test_the_movement_measure_is_broadcast_too():
    """The one the GM asked for by name: how far a creature could still travel,
    shown on the shared screen rather than read out."""
    assert 'shareMeasure(from, to,' in _fn('drawMoveMeasure')


def test_the_broadcast_is_throttled():
    """It rides the single gevent worker that also serves every player's SSE. A
    drag fires at frame rate; the table does not need 60 updates a second to
    follow a line."""
    body = _fn('shareMeasure')
    assert 'MEASURE_MIN_GAP_MS' in body
    assert 'key === lastMeasureKey' in body, 'a held-still pointer must not stream duplicates'


def test_only_the_gms_own_window_broadcasts():
    """The table echoing the line back would double-draw it."""
    assert 'if (!cfg.isGm || isTableView()) return;' in _fn('shareMeasure')


def test_the_shared_ruler_draws_only_on_the_table():
    """The GM already sees their own line locally and at full fidelity."""
    assert 'if (!sharedMeasure || !isTableView()) return;' in _fn('drawSharedMeasure')


def test_the_ping_and_ruler_draw_above_the_fog():
    """A ruler held up for the room and a ring being pointed with are the two
    things that must not be dimmed by it."""
    render = _JS[_JS.index('function renderScene()'):]
    render = render[:render.index('\n    function drawGrid')]
    assert render.index('drawFogOverlay();') < render.index('drawSharedMeasure();')
    assert render.index('drawFogOverlay();') < render.index('drawPings();')


def test_pointing_works_whatever_tool_is_armed():
    """Pointing is what a GM does mid-sentence. Having to disarm the wall tool
    first means not doing it."""
    block = _JS[_JS.index("if (activeTool === 'ping'"):]
    assert 'event.altKey' in block[:400]


def test_the_ping_shortcut_does_not_steal_terrains_alt_click():
    """Stage 6e already claims Alt-click for 'paint one square'."""
    block = _JS[_JS.index("if (activeTool === 'ping'"):][:400]
    assert "!activeTool.startsWith('terrain-')" in block


def test_a_ping_animates_on_the_gms_view_as_well():
    """It is the confirmation that the thing they pointed at actually went out.
    It stops the moment the rings finish, so 6a's event-driven idle holds
    everywhere else."""
    body = _fn('animationsWanted')
    assert 'if (pings.length > 0) return true;' in body
    assert body.index('pings.length') < body.index('if (!isTableView()) return false;')


# --- the third typeface -----------------------------------------------------

def test_no_canvas_text_is_drawn_in_a_hardcoded_face():
    """All eight ctx.font assignments used to hardcode system-ui -- the FALLBACK
    inside --font-ui, the thing Inter exists to avoid. The canvas is the entire
    table screen, so that made the one typeface the players read all session the
    one the project's two-face rule forbids. It hid in JS rather than CSS, which
    is why every previous font sweep missed it."""
    fonts = [line for line in _JS.splitlines() if 'ctx.font =' in line]
    assert fonts, 'expected canvas text to exist at all'
    for line in fonts:
        assert 'uiFont()' in line, line.strip()
        assert 'system-ui' not in line, line.strip()


def test_the_face_comes_from_the_token():
    assert "getPropertyValue('--font-ui')" in _fn('uiFont')


def test_the_map_repaints_once_the_webfont_loads():
    """Stage 6a made rendering event-driven, so a first paint before Inter
    loaded would measure in the fallback face and never repaint -- and
    measureText sizes the ruler and turn-banner backing boxes."""
    assert 'document.fonts.ready.then' in _JS
    ready = _JS[_JS.index('document.fonts.ready.then'):][:120]
    assert "cachedUiFont = ''" in ready, 'the cached stack must be dropped, not just redrawn'
    assert 'draw()' in ready


# --- the toolstrip ----------------------------------------------------------

def test_the_build_tools_fold_away():
    """Measured: 19 buttons needed 1256px and a 1280 laptop gives the strip 968.
    Building a map and running a fight are different jobs, and only the second
    happens with four people waiting."""
    assert 'id="map-build-toggle"' in _HTML
    assert 'id="map-build-group"' in _HTML
    # Anchor on the group's real close, not the first </span> -- there is a
    # divider span inside it, and slicing at that hid the terrain tools.
    group = _HTML[_HTML.index('id="map-build-group"'):]
    group = group[:group.index('</span>{% endif %}')]
    for build_tool in ('fog-reveal', 'wall', 'door', 'light', 'erase', 'terrain-lava'):
        assert 'data-map-tool="%s"' % build_tool in group


def test_the_combat_tools_stay_out_of_the_drawer():
    strip = _HTML[_HTML.index('class="map-toolstrip"'):_HTML.index('id="map-build-group"')]
    for combat_tool in ('select', 'target', 'measure', 'burst', 'emanation', 'cone', 'line', 'ping'):
        assert 'data-map-tool="%s"' % combat_tool in strip


def test_the_drawer_hides_when_hidden():
    """[hidden] is a UA rule with the lowest possible specificity, so an author
    display: above it wins and the element stays on screen. This file already
    carries that scar three times over."""
    assert '.map-tool-group[hidden] { display:none; }' in _CSS


def test_the_open_drawer_is_allowed_a_second_row():
    """So nothing hides behind a scroll at any width. It costs height only while
    the GM is building."""
    assert '.map-toolstrip:has(#map-build-group:not([hidden])) { flex-wrap:wrap' in _CSS


def test_folding_the_drawer_disarms_a_tool_inside_it():
    """Otherwise the GM is left in a mode with nothing on screen naming it."""
    block = _JS[_JS.index('function wireBuildTools'):]
    block = block[:block.index('})();')]
    assert "setActiveTool('select');" in block


def test_erase_no_longer_looks_like_ruler():
    """It removes a wall, door, light or template in one click with no undo."""
    assert 'map-tool--danger' in _HTML
    assert '.map-tool--danger' in _CSS
