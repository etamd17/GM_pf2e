"""Cosmere sheet thematic polish pass.

Sheds the generic "rounded dark boxes" look without restructuring the layout:
section dividers become engraved serif labels with a leading order-accent diamond
and a fading hairline rule; cards get a quiet top sheen (the defenses' sheen
carries the order accent); the name header gets a fading accent rule. All of it
is skin-aware via --order-accent / --accent, so it reads correctly under both the
Stormlight and Mistborn world skins.

(This is the incremental polish the user asked to ship before reviewing fuller
redesign mockups.)
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_section_dividers_are_serif_with_diamond_and_rule():
    # serif label
    assert '.cs-section-h { font-family:var(--font-display, serif);' in _SHEET
    # leading order-accent diamond + fading hairline rule
    assert '.cs-section-h::before' in _SHEET and 'rotate(45deg)' in _SHEET
    assert '.cs-section-h::after' in _SHEET
    assert 'var(--order-accent, var(--accent))' in _SHEET


def test_cards_have_quiet_sheen_and_defenses_carry_accent():
    assert '.cs-card::before' in _SHEET
    assert '.cs-def::before' in _SHEET


def test_header_has_fading_accent_rule():
    assert '.cs-head::after' in _SHEET
    # the rule fades from the order accent (banner edge, not a flat border)
    assert 'linear-gradient(90deg, var(--order-accent, var(--accent)), transparent)' in _SHEET
