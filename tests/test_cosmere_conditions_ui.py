"""Cosmere condition automation + visibility (PR1).

The user's ask: keep condition automation intact AND make it obvious which
stats/abilities each condition affects. Before this, only Enhanced was wired;
Exhausted/Disoriented/Restrained/Empowered/Focused were defined but inert.

Now (cosmere_sheet.html), rulebook-accurate per Stormlight Ch.3 ("Advantages &
Disadvantages") + Ch.9 ("Conditions"):
  - Exhausted [X]  -> -X to every test result
  - Empowered      -> advantage on all tests
  - Disoriented / Restrained -> disadvantage
  - advantages and disadvantages cancel 1:1 (a manual toggle overrides a
    condition, e.g. Restrained "except to escape")
  - Afflicted      -> reminder only (the GM applies the damage)
  - an "Active effects" panel lists each live condition's plain-language effect

Structural guards (the logic is client JS, no in-repo JS runner). The behavior
was verified live in a browser against a seeded Cosmere PC: exhausted -2 + a
single d20 when Empowered+Disoriented cancel; advantage when Empowered alone;
disadvantage when Restrained alone; override via manual advantage.
"""
from __future__ import annotations

import os
import pathlib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sheet() -> str:
    return pathlib.Path(_REPO, 'templates', 'cosmere_sheet.html').read_text()


def test_conditions_threaded_into_client_state():
    assert 'conditions:{{ cur.conditions|tojson }}' in _sheet(), \
        'the roll logic cannot read conditions if they are not in the JS `cur`'


def test_exhausted_penalty_is_applied_to_tests():
    assert "flat: -COND_VAL('exhausted')" in _sheet()


def test_empowered_advantage_and_disoriented_restrained_disadvantage():
    s = _sheet()
    assert 'c.empowered ? 1 : 0' in s
    assert '(c.disoriented?1:0) + (c.restrained?1:0)' in s


def test_advantage_and_disadvantage_cancel_one_to_one():
    s = _sheet()
    assert 'netAdvDis' in s and 'net > 0' in s and 'net < 0' in s, \
        'advantage/disadvantage must net (rulebook: each cancels one of the other)'


def test_active_effects_panel_is_rendered():
    s = _sheet()
    assert 'cos-cond-fx' in s and 'function paintCondFx' in s and 'Active effects' in s


def test_afflicted_is_reminder_only_not_auto_damage():
    s = _sheet()
    assert 'afflicted:' in s and 'the GM applies it' in s
    # Afflicted must NOT feed the roll/damage math — it only appears in the panel.
    assert "COND_VAL('afflicted')" not in s
