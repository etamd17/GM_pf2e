"""Stage 7c: the small findings, which were mostly things that only looked fine.

Four of the five here are cases where the UI was actively reassuring rather than
merely thin: a connection indicator that could not report a disconnection, two
CSS rules that had never matched anything, an error that looked like a success,
and a table screen whose comment claimed it scaled things it did not.

The fifth is faction: PC versus NPC was carried by green against red, which is
the one axis a red-green colourblind viewer cannot use at all.
"""
from __future__ import annotations

import os


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()
_HUB = open(os.path.join(_ROOT, 'templates', '_sse_hub.html'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- the live indicator can now report bad news -----------------------------

def test_the_live_dot_is_driven_by_the_socket():
    """It was a green dot with no JS behind it at all -- permanently
    reassuring, including while the stream was dead, which is worse than having
    no indicator."""
    assert 'watchLiveSync' in _JS
    body = _JS[_JS.index('function watchLiveSync()'):]
    body = body[:body.index('})();')]
    assert '__appSSE.isConnected()' in body
    assert "'Reconnecting...'" in body


def test_the_indicator_is_polled_because_there_is_no_disconnect_event():
    """The hub nulls its socket and retries on a backoff; nothing announces the
    drop, so the only way to notice it is to ask."""
    body = _JS[_JS.index('function watchLiveSync()'):]
    body = body[:body.index('})();')]
    assert 'setInterval(paint, 2000)' in body


def test_the_hub_reports_open_rather_than_merely_constructed():
    """Between `new EventSource` and onopen the socket exists but carries
    nothing, and the map reads this to decide whether to claim it is in sync."""
    assert 'return !!es && es.readyState === 1;' in _HUB


def test_the_dropped_state_has_its_own_styling():
    assert '.map-live.is-dropped .map-live-dot' in _CSS


# --- two rules that had never matched anything ------------------------------

def test_the_canvas_rules_target_the_id_it_actually_has():
    """The element is <canvas id="map-canvas"> with no class, so
    `.map-canvas.is-drop-target` and `.map-page.is-previewing .map-canvas`
    matched nothing. The preview frame is the only cue that the GM's canvas is
    deliberately showing an incomplete view, and it had never once rendered."""
    assert '.map-canvas.is-drop-target' not in _CSS
    assert '.map-page.is-previewing .map-canvas ' not in _CSS
    assert '#map-canvas.is-drop-target' in _CSS
    assert '.map-page.is-previewing #map-canvas' in _CSS
    assert 'class="map-canvas"' not in _HTML, (
        'if the canvas ever gains the class, revisit -- do not let both exist')


# --- an error stops looking like a success ----------------------------------

def test_a_failure_stays_on_screen_longer_than_a_success():
    """Both used to vanish on the same 2.6 seconds, so an error that landed
    while the GM was looking at the map was simply gone."""
    body = _fn('toast')
    assert 'error ? 7000 : 2600' in body


def test_an_error_toast_is_more_than_a_border_colour():
    rule = _CSS[_CSS.index('.map-toast.is-error'):]
    rule = rule[:rule.index('}')]
    assert 'background' in rule and 'border-left' in rule


# --- the table screen scales more than the nameplate ------------------------

def test_there_is_one_place_that_scales_canvas_type_for_the_room():
    assert 'function tableType(px) { return isTableView() ? Math.round(px * 1.7) : px; }' in _JS


def test_the_health_bar_and_conditions_scale_with_the_name():
    """The nameplate was the only isTableView()-aware font in the file, despite
    its comment claiming the health bar came with it. So the room could read WHO
    a token was but not that it was Frightened or Prone -- which is the entire
    reason conditions are painted under the token rather than left in the
    sidebar."""
    body = _JS[_JS.index('const barWidth = radius * 1.7;'):]
    body = body[:body.index('if (!isTableView() && token.visible_to_players === false)')]
    assert 'tableType(5)' in body, 'the HP bar'
    assert 'tableType(10)' in body, 'the condition text'


def test_conditions_stack_on_the_table_rather_than_running_wide():
    """Three condition names joined by ' / ' at 17px runs wider than the token
    and collides with whatever is beside it."""
    body = _JS[_JS.index('const conditions = Object.keys(live.conditions || {});'):]
    body = body[:body.index('if (!isTableView() && token.visible_to_players === false)')]
    assert "const lines = isTableView() ? names : [names.join(' / ')];" in body


# --- faction is no longer a single hue --------------------------------------

def test_party_tokens_carry_a_shape_cue_as_well_as_a_colour():
    """#4f8a62 against #a84b45 is green against red -- the one axis a red-green
    colourblind viewer cannot use."""
    body = _JS[_JS.index('if (token.is_pc) {'):]
    body = body[:body.index('ctx.textAlign')]
    assert 'ctx.arc(x, y, Math.max(2, radius - 6)' in body
    assert 'ctx.stroke();' in body


def test_the_cue_marks_the_party_not_the_monsters():
    """There are four PCs and a screen full of monsters; marking the smaller set
    keeps the map quiet."""
    assert 'if (token.is_pc) {' in _JS
    assert 'if (!token.is_pc) {' not in _JS
