"""Cosmere sheet redesign extras: ambient highstorm + collapsible reference.

- A faint, slow-breathing "highstorm" light leaks in from the right edge of the
  Cosmere sheet (body.system-cosmere::after) — restrained, hidden on narrow
  screens, and motionless under prefers-reduced-motion.
- The non-combat reference sections (Expertises, Talents, Infected Arts,
  Spheres & Goods, Inventory, Session Notes) are collapsible (combat-first): each
  header folds its body, and the choice persists per character in localStorage.

Verified live on a Windrunner: clicking the Inventory header toggled its body
block→none; all reference headers carry a chevron; the storm pseudo is
position:fixed with the highstorm animation; no console errors.
"""
from __future__ import annotations

import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


def test_highstorm_ambient_is_restrained():
    assert 'body.system-cosmere::after' in _SHEET
    assert '@keyframes highstorm' in _SHEET
    # hidden on narrow screens + motionless under reduced-motion
    assert 'max-width:900px' in _SHEET
    assert 'body.system-cosmere::after { animation:none' in _SHEET


def test_reference_sections_are_collapsible():
    # the six reference headers are marked foldable; combat sections are not
    for label in ('Expertises', 'Talents', 'Infected Arts', 'Spheres', 'Inventory', 'Session Notes'):
        assert ('cs-section-h cs-foldable">' + (label if label != 'Spheres' else 'Spheres')) in _SHEET or \
               ('cs-foldable">' + label) in _SHEET, label
    # the fold machinery + persistence
    assert 'cs-fold-body' in _SHEET and 'collapsed' in _SHEET
    assert 'cs-fold-chev' in _SHEET
    assert "localStorage.setItem(key" in _SHEET


def test_combat_sections_stay_open():
    # Strikes / Skills / Attributes must NOT be foldable (combat-first keeps them up)
    assert 'cs-section-h cs-foldable">Strikes' not in _SHEET
    assert 'cs-section-h">Strikes' in _SHEET
