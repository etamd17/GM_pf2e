"""Per-turn timer (table-flow): the tracker shows time on the CURRENT turn and
resets it whenever the active combatant changes, so the GM can keep turns moving.
Client-only (no server state) — guard the markup + reset wiring so a refactor of
the render path can't silently drop it.
"""
from __future__ import annotations

import pathlib

_TRACKER = (pathlib.Path(__file__).resolve().parent.parent / 'templates' / 'tracker.html').read_text()


def test_turn_timer_markup_present():
    assert 'id="turn-timer"' in _TRACKER and 'id="turn-timer-val"' in _TRACKER
    assert 'class="session-timer turn-timer"' in _TRACKER          # reuses the timer chip styling


def test_turn_timer_resets_on_active_change():
    # the reset hinges on comparing the active combatant to the timed one
    assert 'function renderTurnTimer' in _TRACKER
    assert "active !== _turnTimerName" in _TRACKER
    assert 'renderTurnTimer();' in _TRACKER                        # wired into the render path


def test_turn_timer_is_distinct_from_encounter_timer():
    assert '.trk .turn-timer' in _TRACKER                          # its own (cooler) tint
    assert 'turn-timer.slow' in _TRACKER                           # long-turn nudge
