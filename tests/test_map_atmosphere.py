"""Stage 6c: atmosphere, without undoing stage 6a.

Three things: PF2e's bright/dim light zones, torches that flicker, and fog
whose edge does not announce the grid.

The constraint is the interesting part. Stage 6a made rendering event-driven --
one frame, only when something changes -- and animation is the opposite. So
animation is confined to the TABLE screen, which is what was asked for and what
keeps the GM's working view at 6a's cost. The loop also stops dead when there is
nothing to animate, so a lightless scene on the TV costs nothing either.

The subtlest decision here: flicker moves the GLOW but never the carved vision.
Visually a strobing revealed area is unreadable. Mechanically the vision mask is
cached on a signature containing light.radius, so a flickering carve radius
would either serve a stale mask or miss every frame and drag the whole raycast
back into the frame budget -- undoing 6a to make a torch wobble.
"""
from __future__ import annotations

import os


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- animation stays where it was asked for --------------------------------

def test_animation_runs_only_on_the_table_screen():
    body = _fn('animationsWanted')
    assert 'isTableView()' in body, (
        "the GM's working view must stay static -- that is 6a's win and what "
        'was asked for')


def test_the_loop_stops_when_there_is_nothing_to_animate():
    """A permanent loop on a TV left running all session is real power and heat."""
    body = _fn('animationsWanted')
    assert '(scene.lights || []).length > 0' in body
    step = _fn('stepAnimation')
    assert 'if (!animationsWanted()) { animationHandle = null; return; }' in step


def test_the_loop_is_never_started_twice():
    body = _fn('syncAnimation')
    assert 'animationHandle === null' in body
    assert 'cancelAnimationFrame' in body, 'and it must be cancellable'


# --- lighting is two-zone ---------------------------------------------------

def test_light_has_a_bright_and_a_dim_radius():
    """PF2e: bright to the stated radius, dim to twice it. One soft circle made
    the edge of a lit area mean nothing."""
    body = _fn('lightRadii')
    assert 'dim: bright * 2' in body


def test_the_glow_shows_the_boundary():
    body = _fn('drawAmbientLights')
    assert 'addColorStop(0.48' in body and 'addColorStop(0.52' in body, (
        'the bright/dim boundary should be visible, not a smooth ramp')


def test_vision_is_carved_to_the_dim_radius():
    """A creature can see by dim light, it just sees worse. Carving only the
    bright zone made a torch reveal half the area it should."""
    body = _fn('drawVisionOverlay')
    assert 'lightRadii(light).dim' in body


# --- flicker, and the line it must not cross -------------------------------

def test_flicker_moves_the_glow():
    body = _fn('drawAmbientLights')
    assert 'flickerFactor(light)' in body


def test_flicker_never_moves_the_carved_vision():
    """The load-bearing one. lightRadii is what the carve uses, and it must
    stay steady: a flickering carve radius would either serve a stale cached
    mask or miss the cache every frame, dragging the raycast back into the
    frame budget -- undoing 6a to make a torch wobble."""
    body = _fn('lightRadii')
    assert 'flickerFactor' not in body
    carve = _fn('drawVisionOverlay')
    assert 'flickerFactor' not in carve


def test_flicker_is_table_only_like_the_rest():
    body = _fn('flickerFactor')
    assert "if (!isTableView()) return 1;" in body


def test_two_torches_do_not_flicker_in_lockstep():
    """Synchronised flicker reads as a strobe rather than as fire."""
    assert 'function lightPhase(' in _JS
    body = _fn('flickerFactor')
    assert 'lightPhase(light)' in body


def test_flicker_is_not_a_clean_sine():
    """One wave reads as a heartbeat. Two detuned ones read as flame."""
    body = _fn('flickerFactor')
    assert body.count('Math.sin(') >= 2


# --- fog edges --------------------------------------------------------------

def test_the_fog_boundary_is_feathered():
    body = _fn('drawFogOverlay')
    assert "mctx.filter = 'blur(" in body, (
        'region reveal works in whole squares, so the edge announces the grid')
    assert "mctx.filter = 'none';" in body, (
        'the blur must be turned off again or it bleeds into the legacy strokes')


def test_the_blur_is_applied_to_the_mask_not_the_map():
    """Blurring the canvas itself would soften the battlemap and every token."""
    body = _fn('drawFogOverlay')
    blur_at = body.index("mctx.filter = 'blur(")
    assert 'ctx.filter' not in body[:blur_at + 200].replace('mctx.filter', '')


def test_the_feather_scales_with_the_grid():
    """A fixed blur looks right at one grid size and wrong at every other."""
    body = _fn('drawFogOverlay')
    assert 'g.size * 0.35' in body
