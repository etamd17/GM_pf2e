"""Stage 7f: reaching a token without a mouse.

The last item on the UI audit. The canvas is the entire interactive surface and
carries no semantics, so there was no keyboard path to a token at all -- the only
two shortcuts in the whole map were Escape and Ctrl+Z.

Three design decisions are load-bearing:

  * NOT bound to Tab. Trapping Tab inside a canvas is a keyboard trap: it would
    leave someone unable to reach the sidebar. Brackets cycle instead, and Tab
    goes on meaning what it means everywhere else.
  * Nudges are debounced into ONE write and ONE undo entry, because this rides
    the single gevent worker that also serves every player's SSE. Holding an
    arrow key must not be thirty round-trips.
  * A token being nudged is protected from incoming scene frames. Every move
    broadcasts a scene_update, so the frame answering keypress one arrives after
    keypress two has already moved the token locally -- and lands on top of it.
    That was found by measurement, not by reading: a fine nudge issued mid-flight
    silently did nothing.
"""
from __future__ import annotations

import os


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- it must not become a keyboard trap -------------------------------------

def test_cycling_is_not_bound_to_tab():
    """Trapping Tab inside a canvas would leave a keyboard user unable to reach
    the sidebar at all -- a worse failure than the one being fixed."""
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert "'Tab'" not in keydown
    assert "event.key === '['" in keydown and "event.key === ']'" in keydown


def test_arrows_are_only_claimed_when_a_token_is_selected():
    """Otherwise they stop scrolling the viewport, which is what they did
    before and what they should go on doing."""
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert 'if (arrow && selectedId && nudgeSelected(' in keydown


def test_escape_clears_the_selection_before_it_resets_the_tool():
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert keydown.index('selectedId = null;') < keydown.index("else setActiveTool('select');")


def test_the_table_screen_ignores_all_of_it():
    """It has no operator."""
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert 'if (isTableView()) return;' in keydown


# --- what it costs the server ------------------------------------------------

def test_holding_a_key_is_one_write_not_thirty():
    """This rides the single gevent worker that also serves every player's
    SSE."""
    body = _fn('nudgeSelected')
    assert 'clearTimeout(nudge.timer)' in body
    assert 'setTimeout(commitNudge, NUDGE_COMMIT_MS)' in body


def test_a_whole_run_is_one_undo_entry_from_where_it_started():
    body = _fn('commitNudge')
    assert 'move: {tokenId: token.id, x: pending.fromX, y: pending.fromY}' in body


def test_an_unmoved_token_is_never_written():
    body = _fn('commitNudge')
    assert 'if (token.x === pending.fromX && token.y === pending.fromY) return;' in body


# --- the race that measurement found ----------------------------------------

def test_an_incoming_frame_cannot_overwrite_a_token_being_nudged():
    """Every token move broadcasts a scene_update, so the frame answering
    keypress one arrives AFTER keypress two has already moved the token locally.
    Without this the second keypress is silently lost -- which is exactly what a
    fine nudge issued mid-flight did.

    Same rule as the one stopping an SSE frame overwriting a field being typed
    into; this is the position equivalent."""
    body = _fn('applyScene')
    assert 'if (nudge) {' in body
    assert 'held.x = nudge.x; held.y = nudge.y;' in body


def test_the_nudge_records_where_the_gm_has_keyed_it_to():
    """The guard above needs the live position, not just the origin."""
    body = _fn('nudgeSelected')
    assert 'nudge.x = token.x;' in body and 'nudge.y = token.y;' in body


def test_a_stale_response_does_not_reconcile_over_a_newer_nudge():
    body = _fn('commitNudge')
    assert 'if (!nudge) applyScene(data.scene);' in body


# --- movement rules ----------------------------------------------------------

def test_a_step_is_a_square_when_the_scene_snaps():
    """One square is the unit the game is played in."""
    body = _fn('nudgeSelected')
    assert "(scene.settings || {}).snap_to_grid ? g.size : 10" in body


def test_shift_is_the_fine_adjustment():
    """For lining art up on an unsnapped scene."""
    body = _fn('nudgeSelected')
    assert 'fine ? 1 :' in body
    keydown = _JS[_JS.index("window.addEventListener('keydown'"):]
    keydown = keydown[:keydown.index('\n    });')]
    assert 'event.shiftKey' in keydown


def test_a_locked_token_says_so_rather_than_doing_nothing():
    """A key that silently does nothing reads as a dead map."""
    body = _fn('nudgeSelected')
    assert "toast('That token is locked.', true)" in body


def test_permission_is_the_same_check_dragging_uses():
    """Not a second copy of the rule."""
    assert 'if (!canControl(token)) return false;' in _fn('nudgeSelected')


def test_the_token_stays_inside_the_scene():
    body = _fn('nudgeSelected')
    assert 'Math.max(0, Math.min(scene.width' in body
    assert 'Math.max(0, Math.min(scene.height' in body


# --- being told what happened ------------------------------------------------

def test_selection_is_announced_for_a_screen_reader():
    """The token ring is the only cue that selection moved, and a screen reader
    cannot see it."""
    assert 'id="map-announce"' in _HTML
    assert 'aria-live="polite"' in _HTML[_HTML.index('id="map-announce"'):][:120]
    assert 'function announce(' in _JS


def test_the_announcement_is_offscreen_not_display_none():
    """display:none would take it out of the accessibility tree and silence
    it."""
    rule = _CSS[_CSS.index('.map-visually-hidden'):]
    rule = rule[:rule.index('}')]
    assert 'position:absolute' in rule
    assert 'display:none' not in rule


def test_it_says_where_the_token_is_not_just_its_name():
    """A position read out as pixels is useless; the grid square is what the
    table talks in."""
    body = _fn('describeSelection')
    assert "', column '" in body and "' row '" in body


def test_cycling_follows_reading_order():
    """"Next" should mean the next one down the map, not whichever happened to
    be added first."""
    body = _fn('keyboardTokens')
    assert '(a.y - b.y) || (a.x - b.x)' in body


def test_cycling_scrolls_the_token_into_view():
    """Selecting something off-screen is the same as selecting nothing."""
    assert 'function focusToken(' in _JS
    assert 'focusToken(next);' in _fn('cycleSelection')


def test_the_map_shows_that_it_has_focus():
    """It takes focus so arrow keys can reach it, so the GM has to be able to
    tell whether the keys will go to the map or to the page."""
    assert '.map-viewport:focus-visible' in _CSS


def test_the_keys_are_documented_where_a_screen_reader_finds_them():
    viewport = _HTML[_HTML.index('id="map-viewport"'):][:260]
    assert 'aria-label' in viewport
    assert 'arrow keys' in viewport.lower()
