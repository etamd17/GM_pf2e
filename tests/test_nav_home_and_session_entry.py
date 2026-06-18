"""Navigation + session-entry UX guards.

Two player-facing gaps these cover:

1. Back-to-home: inside a campaign there was no easy link to /me (the account
   home that lists every campaign + character). The player bottom navs had none
   at all; base.html only had it buried in the campaign-switcher dropdown. We add
   an explicit Home link, gated on account mode (account_user).

2. Session curtain: a player landing on the begin-session recap had no button to
   advance — they sat on a wall of text waiting for the GM. They now get an
   "Enter the session" button that takes them to their sheet, and a long recap
   scrolls with a "scroll for more" cue.

These are template renders + structural guards (the curtain logic is client JS;
the repo has no JS runner, but the invariants below prevent the regressions).
"""
from __future__ import annotations

import os
import pathlib

import app as A

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _render(name: str, **ctx) -> str:
    return A.app.jinja_env.get_template(name).render(**ctx)


def _read(name: str) -> str:
    return pathlib.Path(_REPO, 'templates', name).read_text()


# ---- 1. Player nav Home link (gated on account mode) ----------------------

def test_pf2e_player_nav_home_link_shown_in_account_mode():
    html = _render('_player_nav.html', is_gm=False, player_name='Kyle',
                   active_player_tab='sheet', account_user={'id': 'u1'})
    assert 'href="/me"' in html
    assert 'campaigns and characters' in html  # the Home tab's aria-label


def test_pf2e_player_nav_home_link_hidden_without_account():
    html = _render('_player_nav.html', is_gm=False, player_name='Kyle',
                   active_player_tab='sheet', account_user=None)
    assert 'href="/me"' not in html


def test_cosmere_player_nav_home_link_gated():
    on = _render('_cosmere_player_nav.html', active_player_tab='sheet',
                 cosmere_player_char='Shanadin', account_user={'id': 'u1'})
    off = _render('_cosmere_player_nav.html', active_player_tab='sheet',
                  cosmere_player_char='Shanadin', account_user=None)
    assert 'href="/me"' in on and 'href="/me"' not in off


def test_base_nav_has_one_click_home():
    """base.html surfaces a dedicated /me home link in the nav (account mode),
    not only the one hidden inside the switcher dropdown."""
    src = _read('base.html')
    assert src.count('href="/me"') >= 2, 'expected the dropdown link AND a top-level home link'
    assert 'aria-label="Home"' in src


# ---- 2. Session curtain: player can self-advance + recap scrolls -----------

def test_curtain_player_gets_enter_button_to_their_sheet():
    src = _read('_session_curtain.html')
    # A player's Enter advances only them, to their character sheet.
    assert "'/cosmere/player' : '/player'" in src, 'no player→sheet navigation on Enter'
    # Both roles now get a button (player is never stranded waiting for the GM).
    assert "enterBtn.textContent = 'Enter the session" in src


def test_curtain_recap_scrolls_with_cue():
    src = _read('_session_curtain.html')
    assert 'overflow-y: auto' in src, 'recap does not scroll'
    assert 'sc-scrollcue' in src, 'no "scroll for more" cue element'
