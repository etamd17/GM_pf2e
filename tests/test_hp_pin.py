"""The pinned vitals bar on the PF2e player sheet.

Why it exists, measured in a real browser rather than assumed:

  * `.char-header` is 489px tall at 1280x800 (61% of the viewport) and 824px
    at 375x812 -- 101% of a phone screen.
  * It is NOT sticky and never has been: `_header.html` carries an inline
    `style="position:relative"`, which beats the stylesheet's
    `position: sticky` because that rule has no `!important`. Every comment in
    the repo calling it a sticky header is stale.

So HP, temp HP and conditions scroll away on EVERY device, and pinning the
header itself is not an option at that height. The bar is a 43px stand-in
(5% of the screen) that shows only while the real HP block is off screen --
which is what keeps it from being the duplicate that got the original header
chip removed (_header.html:46).

These are source assertions. The repo has no JS harness, so this mirrors
tests/test_inline_handler_escaping.py's approach and guards the wiring that
is easy to break silently: which paint funnel the bar hooks, and where its
markup sits.
"""
from __future__ import annotations

import pathlib
import re

import pytest

import app as app_module


def _read(rel):
    return (pathlib.Path(app_module.BASE_DIR) / rel).read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def sheet():
    return _read('templates/player_sheet.html')


@pytest.fixture(scope='module')
def header():
    return _read('templates/_pc_sheet/_header.html')


def test_the_bar_exists_and_carries_the_id_the_hp_writers_already_target(sheet):
    """#header-hp-chip-cur is written by BOTH existing HP writers, which is why
    the bar needs no third writer. If the id is renamed, HP silently freezes."""
    assert 'id="hp-pin"' in sheet
    assert 'id="header-hp-chip-cur"' in sheet
    # Both writers still target it.
    assert sheet.count("getElementById('header-hp-chip-cur')") >= 2


def test_the_bar_is_not_a_child_of_the_header(sheet):
    """Two system.css rules key on `.char-header > div:first-child` and match
    NOTHING today, because the header's first child is the conn-pip span.
    Putting the bar inside the header would silently activate them and
    restyle row 1."""
    pin = sheet.index('id="hp-pin"')
    include = sheet.index('{% include "_pc_sheet/_header.html" %}')
    assert pin < include, 'the bar must be a sibling BEFORE the header include'


def test_conditions_hook_the_only_painter_the_sse_frame_calls(sheet):
    """_paintMetaQuickConds is the one condition painter applyPcUpdate calls.

    _refreshConditionStrip re-fetches /api/export_character and reloads the
    page on failure, and applyConditionUpdate only toggles visibility. Hooking
    either of those is how the header's static "Debuffed -N" chip went
    permanently stale.
    """
    body = sheet[sheet.index('function _paintMetaQuickConds'):]
    body = body[:body.index('\n        function ', 10)]
    assert '_paintHpPinConds(' in body


def test_temp_hp_mirrors_in_the_single_funnel_not_at_call_sites():
    """_paintTempHpChip has five callers. Mirroring there rather than at each
    call site is what stops the HP-policy drift CLAUDE.md warns about."""
    defense = _read('templates/_pc_sheet/_js_defense_state.html')
    body = defense[defense.index('function _paintTempHpChip'):]
    body = body[:body.index('function _paintShieldGauge')]
    assert "getElementById('hp-pin-temp')" in body
    assert "getElementById('hp-pin-temp-val')" in body


def test_visibility_is_not_wired_to_intersection_observer(sheet):
    """IntersectionObserver is the tidier tool but cannot be exercised in this
    project's headless preview pane -- it delivers no callbacks at all, not
    even the initial one for document.body -- so an IO version could only ever
    ship unverified. Keep the scroll+rAF version that can actually be tested."""
    init = sheet[sheet.index('function _initHpPin'):]
    init = init[:init.index('\n        function ', 10)]
    # Match the CONSTRUCTOR, not the word -- the comment above the code
    # explains why IO was avoided and would otherwise trip this.
    assert 'new IntersectionObserver' not in init
    assert "addEventListener('scroll'" in init
    assert 'requestAnimationFrame' in init


def test_init_does_not_rely_on_a_late_domcontentloaded_listener(sheet):
    """This code runs INSIDE an existing DOMContentLoaded handler, so a bare
    addEventListener('DOMContentLoaded', ...) never fires -- a listener added
    while that event is dispatching is not called. Cost an hour to find."""
    assert "document.readyState === 'loading'" in sheet


def test_the_header_hp_chip_tombstone_is_still_accurate(header):
    """The original chip was removed as a DUPLICATE of the vital strip below
    it. That reasoning still holds for the header; the pinned bar sidesteps it
    by only appearing when that strip is off screen. If someone re-adds a chip
    inside the <h1>, this test should make them re-read the argument."""
    assert 'HP chip removed' in header
    assert 'header-hp-chip-cur' not in header


@pytest.mark.parametrize('cls', [
    '.hp-pin', '.hp-pin.is-visible', '.hp-pin__hp', '.hp-pin__conds',
    '.hp-pin__conds.is-warn', '.hp-pin__conds.is-crit',
    '.hp-pin.is-bloodied', '.hp-pin.is-critical',
])
def test_styles_shipped(cls):
    css = _read('static/css/system.css')
    assert cls in css, f'{cls} is referenced by the bar but not styled'


def test_bar_is_hidden_in_print_and_respects_reduced_motion():
    css = _read('static/css/system.css')
    tail = css[css.index('Pinned vitals bar'):]
    assert '@media print' in tail
    assert 'prefers-reduced-motion' in tail


def test_conditions_summary_leads_with_what_is_actionable(sheet):
    """A bare count cannot tell {concealed, prone, off_guard} -- harmless --
    from {dying 2, wounded 1, doomed 1}, which is one failed recovery check
    from death. Both are "3 active". The bar leads with dying, then the status
    penalty, and keeps the count as context.
    """
    body = sheet[sheet.index('function _paintHpPinConds'):]
    body = body[:body.index('\n        function ', 10)]
    assert 'Dying ' in body
    assert 'all checks' in body
    # The penalty is a MAX, not a sum (app.py::status_penalty).
    assert re.search(r"Math\.max\(\s*val\('frightened'\),\s*val\('sickened'\)\s*\)", body)


def test_status_penalty_matches_the_server_definition():
    """Guard the client mirror against the server rule drifting."""
    src = _read('app.py')
    block = src[src.index('def status_penalty'):]
    block = block[:block.index('\n    def ', 10)]
    assert 'max(' in block and 'frightened' in block and 'sickened' in block
