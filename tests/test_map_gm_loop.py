"""Stage 1 of the map audit: stop the GM's editing loop fighting the GM.

Four defects, three of them watched happening in a browser rather than inferred:
the token inspector never hid, "Open linked sheet" was permanently visible and
pointed at /tracker, an inbound SSE frame overwrote whatever was being typed,
and advancing the turn silently repointed the inspector at a different token.

The CSS ones are asserted against the stylesheet because there is no JS harness
in this repo, and because the bug was never in the JS: it set `hidden` correctly
every time. `[hidden]` is a UA rule at the lowest possible specificity, so any
author `display:` beats it. That is a rule about the stylesheet, so the
stylesheet is what gets tested -- and `.map-combat-actions` proves the pattern
was already understood, one line away from the elements that lacked it.
"""
from __future__ import annotations

import pathlib

import pytest


_ROOT = pathlib.Path(__file__).parent.parent
_CSS = (_ROOT / 'static' / 'css' / 'map.css').read_text(encoding='utf-8')
_JS = (_ROOT / 'static' / 'js' / 'map.js').read_text(encoding='utf-8')
_HTML = (_ROOT / 'templates' / 'map.html').read_text(encoding='utf-8')


# --- the two CSS lines -----------------------------------------------------

@pytest.mark.parametrize('selector', [
    '.map-token-actions',   # the token inspector
    '.map-btn',             # "Open linked sheet", among others
    '.map-combat-actions',  # already had it; asserted so it cannot be lost
    '.map-token-empty',     # the new empty state
])
def test_every_display_carrying_selector_also_honours_hidden(selector):
    """An author `display:` without a matching `[hidden]` rule is the bug."""
    assert selector + ' {' in _CSS, f'{selector} not found in map.css'
    assert selector + '[hidden] { display:none; }' in _CSS, (
        f'{selector} sets display but never honours [hidden], so JS setting '
        f'el.hidden = true will not hide it')


def test_the_inspector_and_the_empty_state_are_mutually_exclusive():
    """One of the two is always on screen, so the sidebar never gains a hole."""
    assert 'id="map-token-empty"' in _HTML
    assert 'box.hidden = !token;' in _JS
    assert 'if (empty) empty.hidden = !!token;' in _JS


# --- never overwrite an in-progress edit -----------------------------------

def test_a_field_being_edited_is_never_repainted():
    """Typing a scene name while another client moves a token used to lose the
    name. Both repaint paths must consult isBeingEdited."""
    assert 'function isBeingEdited(' in _JS
    # focus is the obvious case; dirty-but-unsaved is the one that bites after
    # tabbing away from a field you have not saved yet.
    assert 'document.activeElement' in _JS
    assert "dataset.mapDirty === '1'" in _JS

    for guarded in (
        "if (el && !isBeingEdited(el)) el[prop || 'value'] = value;",   # fillControls + setField
        'if (x && !isBeingEdited(x))',                                  # grid offset X
        'if (y && !isBeingEdited(y))',                                  # grid offset Y
    ):
        assert guarded in _JS, guarded


def test_dirty_marks_are_set_and_cleared():
    """A dirty flag that is never cleared would freeze the panel permanently --
    strictly worse than the bug it replaces."""
    assert 'function watchDirtyFields(' in _JS
    assert 'watchDirtyFields();' in _JS, 'watcher defined but never started'
    assert 'function clearDirty(' in _JS
    # cleared on both saves...
    assert _JS.count('clearDirty();') >= 2, 'expected clearDirty after both saves'
    # ...and when the selection moves to a different token, or the new token's
    # values could never paint into the stale-dirty fields.
    assert "clearDirty(document.getElementById('map-token-actions'))" in _JS


# --- follow-turn stops stealing the selection ------------------------------

def _code_only(text):
    """Drop // comments. Asserting a symbol is absent from a function is
    meaningless if the comment explaining WHY it is absent mentions it -- which
    is exactly what happened the first time this test was written."""
    return '\n'.join(line.split('//')[0] for line in text.splitlines())


def test_following_the_turn_scrolls_but_does_not_reselect():
    start = _JS.index('function focusActiveTurn(')
    body = _code_only(_JS[start:_JS.index('function draw()', start)])
    assert 'viewport.scrollLeft' in body, 'should still follow the turn'
    assert 'selectedId' not in body, (
        'focusActiveTurn must not reassign selection -- it moved the target out '
        'from under Save/Remove while the GM was mid-edit')


# --- pickers refresh live --------------------------------------------------

def test_pickers_are_rebuilt_from_the_api_not_frozen_at_render():
    assert 'function refreshPickers(' in _JS
    assert 'function refillSelect(' in _JS
    for picker in ('map-scene-select', 'map-token-source', 'map-token-owner'):
        assert picker in _JS, picker
    # encounter_update is the event that means "the combatant list changed".
    encounter = _JS[_JS.index("appSSE('encounter_update'"):]
    assert 'refreshPickers()' in encounter[:400], (
        'encounter_update must refresh the pickers, otherwise adding a combatant '
        'mid-fight leaves the map list stale until a manual reload')


def test_a_rebuilt_picker_keeps_the_current_selection():
    """refreshPickers runs on encounter_update, i.e. while the GM is mid-action.
    Silently resetting a picker they had already set would be its own bug."""
    body = _JS[_JS.index('function refillSelect('):_JS.index('async function refreshPickers(')]
    assert 'const previous = select.value;' in body
    assert 'select.value = previous;' in body
    assert 'CSS.escape(previous)' in body, 'a candidate key must not break the selector'


def test_the_scenes_endpoint_serves_the_candidates():
    """The client cannot rebuild the token pickers without them, and a second
    endpoint for the same refresh would just be another round trip."""
    app_py = (_ROOT / 'app.py').read_text(encoding='utf-8')
    start = app_py.index('def api_scenes():')
    body = app_py[start:start + 1200]
    assert "'token_candidates': _scene_token_candidates(cid)" in body
    assert "'scenes': _scenes.scene_summaries(cid)" in body
