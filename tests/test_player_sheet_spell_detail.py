"""Guard: a player can click a spell in the combat Play view to see its details.

The player sheet renders spell lists twice: the Jinja partial (_tab_magic.html)
gives every row an `openSpellCard(...)` click, but the JS `renderPlayView()` in
player_sheet.html OVERWRITES the prepared casters' `play-view-body` with its own
Cast-row markup -- which had dropped the detail handler. So during play (the
default "Play" view) clicking a cantrip or prepared spell did nothing, while the
searchable spellbook, focus, and spontaneous rows (server-rendered) still worked.

The fix wires the play-view cantrip + prepared/spontaneous name spans back to the
detail card, so a player can check a spell's damage dice + save before casting.
These are static template guards (the render is client JS, no in-repo JS runner);
the fix was verified live in a browser.
"""
from __future__ import annotations

import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sheet() -> str:
    with open(os.path.join(_REPO, 'templates', 'player_sheet.html')) as f:
        return f.read()


def _fn(src: str, marker: str) -> str:
    """Return the source of a `function <marker>(...) { ... }` via brace match."""
    i = src.index(marker)
    depth = 0
    started = False
    out = []
    for ch in src[i:]:
        out.append(ch)
        if ch == '{':
            depth += 1
            started = True
        elif ch == '}':
            depth -= 1
            if started and depth == 0:
                break
    return ''.join(out)


def test_play_view_rows_open_the_spell_card():
    """renderPlayView's cantrip AND prepared/spontaneous Cast rows must open the
    detail card on the spell name -- otherwise clicking a spell during play is a
    no-op (the reported bug)."""
    fn = _fn(_sheet(), 'function renderPlayView(')
    # Rows open the detail card via the shared _spellCardOnclick builder (which
    # emits the escaped openSpellCard call).
    assert '_spellCardOnclick' in fn, 'renderPlayView never opens the spell detail card'
    # Both row types (cantrip + ranked prepared) must be wired, not just one.
    assert fn.count('_spellCardOnclick') >= 2, \
        'only one play-view row type opens the detail card (cantrip and prepared both need it)'
    # A visible affordance so players know the name is clickable.
    assert 'spell-clickable' in fn, 'no clickable affordance on play-view spell names'


def test_spell_card_onclick_helper_escapes_apostrophes():
    """The shared inline-handler builder must JS-escape the spell name -- PC and
    spell names contain apostrophes (Go'el, Thieves' Tools), which otherwise
    close the JS string and kill the handler (the recurring apostrophe bug)."""
    src = _sheet()
    assert 'function _spellCardOnclick' in src, 'missing the shared spell-card onclick builder'
    helper = _fn(src, 'function _spellCardOnclick')
    assert "replace(/'/g" in helper, 'the spell-card onclick builder does not escape apostrophes'


def test_affordance_style_present():
    """.spell-clickable must have a pointer cursor so the click target is discoverable."""
    src = _sheet()
    assert re.search(r'\.spell-clickable\b[^}]*cursor\s*:\s*pointer', src), \
        'no cursor:pointer affordance for .spell-clickable'
