"""Cosmere usability: player Rest button (PR2) + first-timer guidance (PR3).

PR2 — a player rests their OWN character from the sheet (owner/GM), short or long,
via the existing _cosmere_apply_rest (rulebook Ch.9). PR3 — rulebook-accurate
first-timer guidance: a builder glossary, bigger tap targets, and sheet tooltips
(deflect damage types, a skills legend, a non-Radiant Investiture note). All
guidance text was sourced from Stormlight_Rules.txt.

Verified live: Long rest took a hurt PC 4/12 -> 12/12 health, 1->6 focus,
Exhausted [2]->[1], cleared short-lived conditions, kept Affliction; the builder
glossary shows 11 terms; deflect tooltips + skills legend render on the sheet.
"""
from __future__ import annotations

import os
import pathlib

import app as A
import systems.cosmere.build as cb

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    return pathlib.Path(_REPO, rel).read_text()


# ── PR2: player Rest ─────────────────────────────────────────────────────────

def test_player_rest_route_exists():
    rules = [r.rule for r in A.app.url_map.iter_rules()]
    assert '/cosmere/pc/<pid>/rest' in rules


def test_long_rest_recovers_per_rulebook():
    b = cb.CosmereBuild({'attributes': {'wil': 4}})
    doc = {'build': b.to_dict(),
           'play_state': {'health': 1, 'focus': 1,
                          'conditions': {'exhausted': 2, 'disoriented': True, 'afflicted': 3}}}
    ps = A._cosmere_apply_rest(doc, 'long')
    assert ps['health'] == b.health_max()
    assert ps['focus'] == b.focus_max()
    assert ps['conditions'].get('exhausted') == 1      # reduced by one
    assert 'disoriented' not in ps['conditions']        # short-lived: cleared
    assert ps['conditions'].get('afflicted') == 3       # lingering: kept for the GM


def test_sheet_has_rest_ui():
    h = _read('templates/cosmere_sheet.html')
    assert 'cs-rest-btn' in h and 'cosRest(' in h
    assert "/rest'" in h or '/rest"' in h


# ── PR3: first-timer guidance ────────────────────────────────────────────────

def test_builder_glossary_present():
    h = _read('templates/cosmere_builder.html')
    assert 'cb-glossary' in h and 'New to the Cosmere' in h
    for term in ('Investiture', 'Surge', 'Radiant Order', 'Ideals', 'Spren', 'Expertise', 'Heroic Path'):
        assert ('<dt>%s</dt>' % term) in h, 'glossary missing %s' % term


def test_builder_tap_targets_enlarged():
    h = _read('templates/cosmere_builder.html')
    assert '.stepper button' in h and 'width:32px; height:32px' in h


def test_sheet_guidance_tooltips_and_legend():
    h = _read('templates/cosmere_sheet.html')
    assert 'deflect_info' in h and 'bypasses Deflect' in h          # deflect type tooltips
    assert 'governing attribute' in h                               # skills legend
    assert 'Not a Radiant' in h                                     # non-Radiant Investiture note
