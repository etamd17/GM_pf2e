"""Stage 6a: the render loop stops fighting the machine.

Three costs, all paid on every frame, and the audit's Round 7 answer was to fix
enough for a laptop driving a TV rather than the full tablet treatment.

  * draw() was called from 18 places, several inside pointermove, so dragging a
    token re-rendered the whole scene once per mouse event -- often several
    times per displayed frame. There was no requestAnimationFrame anywhere.

  * Two full-scene offscreen canvases were allocated EVERY frame, roughly 5 MB
    each at 1400x900. Stage 2a made scenes take their background image's size,
    so a 2560x1440 map turned that into ~15 MB of garbage per frame while
    dragging.

  * The vision mask raycasts every source against every wall, and was
    recomputed on every frame including pure pan and zoom, which cannot change
    it at all.

Only rendering changed. Every call site still calls draw().
"""
from __future__ import annotations

import os
import re


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()


def _fn(name):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index('\n    }', start)]


# --- frames are scheduled ---------------------------------------------------

def test_draw_only_requests_a_frame():
    body = _fn('draw')
    assert 'requestAnimationFrame' in body
    assert 'frameQueued' in body, 'repeat calls in one frame must coalesce'


def test_the_real_work_moved_behind_the_scheduler():
    assert 'function renderScene()' in _JS
    body = _JS[_JS.index('function renderScene()'):]
    body = body[:body.index('\n    function drawGrid')]
    for stage in ('drawGrid();', 'drawFogOverlay();', 'drawToken(token)'):
        assert stage in body, stage


def test_call_sites_were_not_touched():
    """The point of a scheduler is that callers keep calling draw(). Rewriting
    18 call sites would have been a bigger change with more to get wrong."""
    assert len(re.findall(r'^\s*draw\(\);', _JS, re.M)) >= 15
    assert 'renderScene();' in _JS
    # Exactly two callers, and both are deliberate: the rAF callback, and the
    # __mapRenderNow diagnostic seam. A third would mean something started
    # rendering synchronously again, which is the thing this stage removed.
    assert len(re.findall(r'renderScene\(\);', _JS)) == 2


def test_pointermove_no_longer_renders_directly():
    """Dragging fired draw() per mouse event; now it queues a frame instead."""
    assert 'frameQueued = true;' in _JS


# --- offscreen canvases are reused -----------------------------------------

def test_no_canvas_is_allocated_during_a_render():
    """Two per frame at ~5 MB each was the most GC-hostile thing in the file,
    and it got worse when scenes started matching their background image."""
    for name in ('drawVisionOverlay', 'drawFogOverlay'):
        body = _fn(name)
        assert "document.createElement('canvas')" not in body, name
        assert 'scratchCanvas(' in body, name


def test_the_scratch_canvas_is_resized_not_recreated():
    body = _fn('scratchCanvas')
    assert 'entry.canvas.width !== canvas.width' in body
    assert 'clearRect' in body, 'a reused canvas must be cleared'
    assert "globalCompositeOperation = 'source-over'" in body, (
        'composite mode persists on a reused context and would corrupt the '
        'next mask')


def test_the_two_masks_do_not_share_a_buffer():
    """Vision and fog are drawn in the same frame; one scratch canvas would
    make the second overwrite the first."""
    assert "scratchCanvas('vision')" in _JS
    assert "scratchCanvas('fog')" in _JS


# --- vision is cached -------------------------------------------------------

def test_vision_is_cached_between_frames():
    body = _fn('drawVisionOverlay')
    assert 'visionSignature()' in body
    assert 'visionKey === signature' in body
    assert 'ctx.drawImage(entry.canvas, 0, 0);' in body, 'a hit must reuse the pixels'


def test_the_signature_covers_everything_that_changes_vision():
    body = _fn('visionSignature')
    for source in ('scene.walls', 'scene.lights', 'scene.tokens'):
        assert source in body, source
    assert 'w.open' in body, 'opening a door changes what is visible'
    assert 'ownsTokenForVision(t)' in body, 'only sources count, not every token'
    assert 'dynamic_lighting' in body
    assert 'isTableView()' in body, (
        'the GM and table views carve different sources, so one cache entry '
        'cannot serve both')


def test_the_signature_includes_canvas_size():
    """A cached mask at the old size would be stretched or clipped after the
    scene is resized by a background upload."""
    body = _fn('visionSignature')
    assert 'canvas.width' in body and 'canvas.height' in body


def test_panning_and_zooming_are_not_in_the_signature():
    """They cannot change what is visible, and they are exactly the gestures
    that used to recompute the mask dozens of times a second."""
    body = _fn('visionSignature')
    assert 'zoom' not in body
    assert 'scrollLeft' not in body and 'scrollTop' not in body


def test_a_hidden_document_can_still_be_forced_to_render():
    """requestAnimationFrame does not fire while document.hidden is true. That
    is correct -- a backgrounded tab should not burn CPU -- but it also means
    the canvas never renders in a headless preview pane, where hidden is
    permanently true, so the map cannot be visually verified there at all.

    The seam is deliberately NOT a timeout fallback inside draw(): rendering a
    hidden document on a timer is precisely the waste rAF exists to avoid.
    """
    assert 'window.__mapRenderNow' in _JS
    body = _JS[_JS.index('window.__mapRenderNow'):]
    assert 'frameQueued = false; renderScene();' in body[:160]
    # ...and draw() must not have grown a timer behind our backs.
    assert 'setTimeout' not in _fn('draw')
