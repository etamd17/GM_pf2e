"""Stage 6d: motion the table can follow.

Two things, both table-only like the rest of the atmosphere work: a token
SLIDES to its new square so the room can see who moved and from where, and
damage or healing rises off the creature so the table sees the hit land without
the GM narrating the arithmetic.

The design decision worth recording is where the numbers come from. They are
derived from a token's HP CHANGING, not from the map's own combat buttons --
which is the more useful rule, because damage rolled on the tracker or a player
healing on their own sheet then floats on the table too. The map is not the only
thing that changes HP.
"""
from __future__ import annotations

import os


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- both are table-only ---------------------------------------------------

def test_motion_and_floaters_are_table_only():
    body = _fn('noteMotionAndHealth')
    assert 'if (!isTableView())' in body, (
        "the GM's working view stays static -- that is what 6a bought and what "
        'was asked for')


def test_the_animation_loop_knows_about_them():
    """Without this the ticker stops while a token is still mid-slide."""
    body = _fn('animationsWanted')
    assert 'glides.size > 0' in body
    assert 'floaters.length > 0' in body


# --- gliding ---------------------------------------------------------------

def test_a_moved_token_glides_from_where_it_was():
    body = _fn('noteMotionAndHealth')
    assert 'glides.set(' in body
    assert 'Number(was.x) !== Number(token.x)' in body


def test_a_second_move_continues_from_mid_flight():
    """Restarting from the original square would snap the token backwards
    before setting off again."""
    body = _fn('noteMotionAndHealth')
    assert 'inFlight ? inFlight.currentX' in body


def test_the_glide_eases_out():
    body = _fn('tokenRenderPos')
    assert 'Math.pow(1 - t, 3)' in body, 'linear motion reads as a machine'


def test_reading_a_glide_does_not_mutate_it_away():
    """tokenRenderPos is called more than once per token per frame -- the token
    itself, then any floater above it. Deleting from a read would make the
    second call answer differently from the first."""
    body = _fn('tokenRenderPos')
    assert 'glides.delete' not in body
    assert 'function pruneGlides(' in _JS
    assert 'pruneGlides();' in _JS


def test_the_scene_is_diffed_before_it_is_replaced():
    """Once `scene` is reassigned there is nothing left to compare against."""
    body = _fn('applyScene')
    note_at = body.index('noteMotionAndHealth(')
    assign_at = body.index('scene = normalizeClientScene(next);')
    assert note_at < assign_at


def test_tokens_are_drawn_at_their_glide_position():
    body = _JS[_JS.index('function drawToken('):]
    assert 'const at = tokenRenderPos(token);' in body[:300]


# --- floating numbers ------------------------------------------------------

def test_numbers_come_from_hp_changing_not_from_the_map_buttons():
    """So damage rolled on the tracker, or a player healing on their own sheet,
    floats on the table too."""
    body = _fn('noteMotionAndHealth')
    assert 'token.live && token.live.current_hp' in body
    assert 'floaters.push(' in body
    assert 'hp - had' in body, 'the delta is what is shown'


def test_the_first_sighting_of_a_token_floats_nothing():
    """Otherwise every token would announce its full HP the moment the table
    screen opens."""
    body = _fn('noteMotionAndHealth')
    assert 'had !== undefined' in body


def test_healing_and_damage_are_told_apart():
    body = _fn('drawFloaters')
    assert 'f.delta > 0' in body
    assert "'+'" in body, 'healing should read as a gain'


def test_floaters_expire():
    body = _fn('drawFloaters')
    assert 'floaters.splice(i, 1)' in body
    assert 'FLOAT_MS' in _JS


def test_a_floater_for_a_vanished_token_is_dropped():
    """A creature can be removed from the scene mid-float."""
    body = _fn('drawFloaters')
    assert 'if (!token) { floaters.splice(i, 1); continue; }' in body


def test_floaters_are_drawn_above_the_fog():
    """Drawn before the fog they would be dimmed by it, which defeats the point
    of a number meant to be read across a room."""
    render = _JS[_JS.index('function renderScene()'):]
    render = render[:render.index('\n    function drawGrid')]
    assert render.index('drawFogOverlay();') < render.index('drawFloaters();')
