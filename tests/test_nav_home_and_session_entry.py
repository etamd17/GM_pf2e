"""Navigation guards.

Back-to-home: inside a campaign there was no easy link to /me (the account home
that lists every campaign + character). The player bottom navs had none at all;
base.html only had it buried in the campaign-switcher dropdown. We add an
explicit Home link, gated on account mode (account_user).
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
