"""Cosmere printable character sheet.

A Print button (window.print) in the header, plus an @media print stylesheet that
turns the dark play sheet into a clean ink-on-white paper sheet: hides all chrome
+ interactivity (nav, sticky bar, roll tools, action buttons, the floating dice/
chat/music widgets, toasts), expands any folded reference sections so the
printout is complete, flattens the light-gradient spheres into plain ringed
circles with their value, and avoids splitting sections across pages.

Verified live by lifting the @media print rules into screen styles: content
renders ink-on-white (cards, skills, attributes), spheres become plain circles,
and the dice widget is hidden.
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_print_button():
    assert 'window.print()' in _SHEET
    assert '>Print<' in _SHEET


def test_print_media_block():
    assert '@media print' in _SHEET


def test_print_hides_chrome_and_widgets():
    # the print hide list covers nav/controls + the global floating widgets
    for sel in ('.cs-rolltools', '.cs-amt', '.cs-rad-btn', '#cs-toast', '.cs-vbar',
                '#dice-widget', '#chat-widget', '#audio-widget', '.no-print'):
        assert sel in _SHEET, sel


def test_print_lightens_and_flattens():
    # ink on white + spheres flattened (fill/arc hidden) + folds expanded for print
    assert 'background: #fff !important' in _SHEET
    assert '.orb-fill, .orb-spec, .orb-arc { display: none !important; }' in _SHEET
    assert '.cs-fold-body.collapsed { display: block !important; }' in _SHEET
