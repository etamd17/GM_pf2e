"""Stage 3 of the map audit: tokens land where you put them, on squares.

Two defects, both watched in a browser during the audit. Every token added from
the sidebar landed on the same hardcoded square -- add four and you got a stack
to drag apart. And snapping rounded a token's centre to the nearest gridline
INTERSECTION, so a Medium creature sat on a corner straddling four squares,
which is the one thing snapping exists to prevent.

The snap rule is the interesting part and it is not "round to the middle of a
cell". Where a centre belongs depends on the footprint: an odd number of cells
centres inside a cell, an even number centres ON a line, because a 2x2 creature
genuinely straddles it. Getting that wrong in either direction puts half the
creatures in the game off-square.

There is no JS harness in this repo, so the invariants are asserted against the
source and the arithmetic was verified in a real browser (see the PR).
"""
from __future__ import annotations

import os
import re

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()


def _fn(name, end):
    start = _JS.index('function ' + name + '(')
    return _JS[start:_JS.index(end, start)]


# --- snapping is footprint-aware -------------------------------------------

def test_snapping_accounts_for_the_footprint():
    body = _fn('snapPoint', '\n    }')
    assert 'tokenFootprint(token) % 2 === 1' in body, (
        'an odd footprint centres in a cell, an even one centres on a line -- '
        'without that distinction Large creatures land wrong')
    assert 'size / 2' in body, 'the half-cell shift is the whole mechanism'


def test_snapping_no_longer_rounds_bare_to_intersections():
    """The old body was Math.round((p.x - ox) / size) * size + ox, with no
    half-cell term at all. Its return must not come back."""
    body = _fn('snapPoint', '\n    }')
    assert re.search(r'Math\.round\(\(point\.x - ox\) / size\)', body) is None


def test_a_token_snaps_by_its_own_size():
    """snapPoint takes the token, not just a point -- passing only a point would
    silently treat every creature as Medium."""
    assert 'snapPoint(token, token)' in _JS


def test_the_footprint_helper_is_shared():
    """Stage 5 needs the same footprint for template coverage: a burst clipping
    the corner of a Large creature has to catch it. One definition, not two."""
    assert 'function tokenFootprint(' in _JS
    body = _fn('tokenFootprint', '\n    }')
    assert 'Math.max(1' in body, 'Tiny still occupies a square for placement'


# --- placement --------------------------------------------------------------

def test_the_hardcoded_drop_square_is_gone():
    """Every token used to land at grid*2 from the origin, so adding several
    produced a stack."""
    assert '* 2 + (Number(scene.grid.offset_x)' not in _JS
    assert '* 2 + (Number(scene.grid.offset_y)' not in _JS


def test_tokens_are_placed_by_dragging_onto_the_map():
    assert 'draggable="true"' in _HTML
    assert "addButton.addEventListener('dragstart'" in _JS
    assert "canvas.addEventListener('drop'" in _JS
    drop = _JS[_JS.index("canvas.addEventListener('drop'"):]
    assert 'placeToken(pointFromEvent(event))' in drop[:400], (
        'the drop point is where the token goes')


def test_dragover_is_prevented_or_the_drop_never_fires():
    """A browser will not fire drop unless dragover is cancelled. Easy to omit
    and the failure looks like "drag does nothing"."""
    over = _JS[_JS.index("canvas.addEventListener('dragover'"):]
    assert 'event.preventDefault()' in over[:300]


def test_the_drag_carries_a_payload_for_firefox():
    start = _JS[_JS.index("addButton.addEventListener('dragstart'"):]
    assert "setData('text/plain'" in start[:500], (
        'Firefox will not start a drag with no data set')


def test_clicking_still_places_but_somewhere_visible():
    """The click path is a fallback, and must not reintroduce a fixed corner."""
    click = _JS[_JS.index("addButton.addEventListener('click'"):]
    assert 'viewportCenter()' in click[:400]
    assert 'placeToken(' in click[:400]


def test_a_placed_token_respects_the_snap_setting():
    body = _fn('placeToken', '\n        }')
    assert "(scene.settings || {}).snap_to_grid" in body
    assert 'snapPoint(at' in body


def test_a_placed_token_cannot_land_outside_the_scene():
    body = _fn('placeToken', '\n        }')
    assert 'Math.min(scene.width' in body and 'Math.min(scene.height' in body


# --- hidden tokens are visibly hidden --------------------------------------

def test_a_token_hidden_from_players_is_ghosted_for_the_gm():
    """visible_to_players is enforced server-side but was invisible on the only
    screen that renders it, so it was easy to narrate a monster the party had
    never been shown."""
    body = _JS[_JS.index('function drawToken('):]
    body = body[:body.index('function ', 20)]
    assert 'token.visible_to_players === false' in body
    assert 'globalAlpha' in body
