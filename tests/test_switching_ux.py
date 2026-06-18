"""Campaign/character switching UX: deep-link Play -> sheet (validated `then`
redirect), owner-aware /player, the in-nav campaign switcher (/api/my_campaigns),
plus LIVE indicator + Resume-last-session + system accent wiring.
"""
from __future__ import annotations

import pathlib

import app


def test_safe_then_allows_local_paths():
    assert app._safe_then('/player/sheet/Kyle') == '/player/sheet/Kyle'
    assert app._safe_then('/cosmere/pc/abc123') == '/cosmere/pc/abc123'


def test_safe_then_rejects_open_redirects_and_junk():
    for bad in ['//evil.com', 'http://evil.com', 'https://x', 'javascript:alert(1)',
                '/\\evil.com', '', None, '/has space', '/has\nnewline', 5]:
        assert app._safe_then(bad) is None, bad


def test_pc_sheet_url_per_system():
    assert app._pc_sheet_url('pf2e', 'Sir Bob', None) == '/player/sheet/Sir%20Bob'
    assert app._pc_sheet_url('cosmere', 'Kal', 'deadbeef') == '/cosmere/pc/deadbeef'


def test_account_home_deeplinks_play_and_offers_resume_and_live():
    h = pathlib.Path('templates/account_home.html').read_text()
    assert 'name="then"' in h and 'sheet_url' in h          # Play deep-links to the sheet
    assert 'last_campaign' in h                              # Resume-last-session hero
    assert 'is_live' in h                                    # LIVE indicator on cards


def test_base_nav_has_real_campaign_switcher():
    h = pathlib.Path('templates/base.html').read_text()
    assert '/api/my_campaigns' in h                          # dropdown is data-driven
    assert 'campaign-switcher' in h or 'campaignSwitcher' in h
