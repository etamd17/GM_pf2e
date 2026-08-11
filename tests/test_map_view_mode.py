"""Stage 4b: rendering is decided by view mode, not by who you are.

Vision and fog were gated on `cfg.isGm`, which conflated two different
questions: "may this person edit the scene" and "should this canvas show only
what the party can see". That is precisely why the GM could never preview the
table view -- the check deciding whether to draw vision was the same check
deciding whether to show the sidebar.

Splitting them is the highest-leverage change in the plan: it is simultaneously
the preview toggle and the mechanism the shared table screen will render
through, which is why the table screen ends up a view mode of this page rather
than a second app.

The subtle requirement is that the preview must be TRUTHFUL. The GM's payload
still contains hidden tokens and secret doors -- the server only strips those
for player-facing payloads -- so a preview that merely dimmed them would quietly
lie about what the table can see. It has to drop them.
"""
from __future__ import annotations

import os

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()
_CSS = open(os.path.join(_ROOT, 'static', 'css', 'map.css'), encoding='utf-8').read()


def _draw_body():
    start = _JS.index('    function draw() {')
    return _JS[start:_JS.index('\n    function drawGrid()', start)]


# --- the split ------------------------------------------------------------

def test_a_view_mode_exists_and_is_separate_from_the_role():
    assert 'let viewMode' in _JS
    assert 'function isTableView(' in _JS


def test_rendering_no_longer_asks_who_you_are():
    """draw() decides what to render from the view, not the role. Permission
    checks stay on cfg.isGm; only rendering moved."""
    body = _draw_body()
    code = '\n'.join(line.split('//')[0] for line in body.splitlines())
    assert 'cfg.isGm' not in code, (
        'draw() should gate on isTableView(); cfg.isGm is about permission')
    assert 'isTableView()' in code


def test_vision_is_drawn_for_the_table_view():
    body = _draw_body()
    assert 'if (isTableView()) drawVisionOverlay();' in body


def test_gm_furniture_is_hidden_in_the_table_view():
    """Walls and light handles are GM tools; the table sees their effect through
    fog and vision, never the objects themselves."""
    body = _draw_body()
    assert 'if (!isTableView()) {' in body
    assert 'drawWallsAndDoors();' in body
    assert 'if (!isTableView()) drawToolOverlay();' in body


def test_fog_opacity_follows_the_view():
    assert 'const darkness = isTableView() ? .97 : .30;' in _JS


# --- the preview must not flatter ------------------------------------------

def test_hidden_tokens_are_dropped_in_the_table_view_not_dimmed():
    """The GM payload still contains them -- the server strips them only for
    player-facing payloads -- so dimming would be a lie about what is visible."""
    body = _draw_body()
    assert 'isTableView() && token.visible_to_players === false' in body
    assert 'continue;' in body


def test_ghosting_is_a_gm_view_affordance_only():
    body = _JS[_JS.index('function drawToken('):]
    body = body[:body.index('function ', 20)]
    assert '!isTableView() && token.visible_to_players === false' in body, (
        'ghosting tells the GM a token is hidden; in the table view it is simply '
        'not drawn at all')


# --- union of party vision -------------------------------------------------

def test_the_table_sees_the_union_of_the_party_s_vision():
    """One screen, one answer, no per-player state -- Round 5's decision."""
    body = _JS[_JS.index('function ownsTokenForVision('):]
    body = body[:body.index('\n    }')]
    assert 'if (isTableView()) return !!token.is_pc;' in body


# --- the toggle ------------------------------------------------------------

def test_the_preview_toggle_exists_and_is_gm_only():
    assert 'id="map-preview-table"' in _HTML
    button_line = next(l for l in _HTML.splitlines() if 'map-preview-table' in l)
    # Stage 6b narrowed this: previewing is a GM affordance AND meaningless on
    # the table screen itself, which is already showing the table view.
    assert '{% if map_gm and not table_view %}' in button_line
    assert "getElementById('map-preview-table').addEventListener" in _JS


def test_previewing_steps_back_to_select():
    """Drawing while previewing would edit what you cannot fully see."""
    body = _JS[_JS.index('function setViewMode('):]
    body = body[:body.index('\n    function setCalibrationMode')]
    assert "setActiveTool('select')" in body


def test_previewing_says_so():
    body = _JS[_JS.index('function setViewMode('):]
    assert 'Table preview' in body[:900]
    assert 'is-previewing' in _CSS, 'the frame is the reminder it is incomplete'


def test_the_view_mode_defaults_correctly():
    """Stage 6b added the table route, which is GM-authenticated but must still
    start in table view -- so the default cannot be role alone."""
    assert "let viewMode = (cfg.tableView || !cfg.isGm) ? 'table' : 'gm';" in _JS
