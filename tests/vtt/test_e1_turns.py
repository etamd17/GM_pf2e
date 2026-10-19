"""E-1: turn changes from the VTT go through the tracker's own cycle_turn,
bound to what the GM page showed (handoff 087, Codex's 085 answer 3).

cycle_turn itself is replaced by a recorder here; the browser harness runs the
real one end to end.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app as app_module


@pytest.fixture
def tracker(monkeypatch):
    calls = []
    encounter = [SimpleNamespace(instance_id="a1"), SimpleNamespace(instance_id="b2")]
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", encounter)
    monkeypatch.setattr(app_module, "TURN_INDEX", 0)
    monkeypatch.setattr(app_module, "ROUND_NUMBER", 3)
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: {"id": cid, "system": "pf2e"})

    def cycle(direction):
        # Records the lock state the tracker would run under, then advances.
        calls.append((direction, app_module._LIVE_CAMPAIGN_DISPATCH_LOCK._is_owned()))
        app_module.TURN_INDEX = (app_module.TURN_INDEX + (1 if direction == "next" else -1)) % 2
        return None

    monkeypatch.setattr(app_module, "cycle_turn", cycle)
    return calls


def step(direction="next", actor="cmb_a1", round_=3, campaign="camp_a"):
    return app_module._vtt_turn_step(campaign, direction, actor, round_)


def test_a_matching_request_changes_the_turn_through_the_tracker_under_the_lock(tracker):
    assert step() == ("ok", {"actorId": "cmb_b2", "round": 3})
    assert tracker == [("next", True)]


def test_a_duplicate_click_is_stale_and_changes_nothing(tracker):
    assert step()[0] == "ok"
    code, detail = step()                                   # the same expectation again
    assert (code, detail) == ("stale", {"actorId": "cmb_b2", "round": 3})
    assert len(tracker) == 1


def test_a_different_round_is_stale(tracker):
    assert step(round_=2) == ("stale", {"actorId": "cmb_a1", "round": 3})
    assert tracker == []


def test_another_campaign_is_never_activated(tracker):
    assert step(campaign="camp_b") == ("not-live", {})
    assert tracker == []


def test_a_system_without_an_integrated_tracker_is_refused(tracker, monkeypatch):
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: {"id": cid, "system": "dnd5e"})
    assert step()[0] == "unsupported"
    assert tracker == []


def test_cosmere_is_supported(tracker, monkeypatch):
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: {"id": cid, "system": "cosmere"})
    assert step()[0] == "ok"


def test_no_encounter_is_refused(tracker, monkeypatch):
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", [])
    assert step() == ("no-encounter", {})


def test_a_refusal_inside_the_tracker_is_reported_and_nothing_else_happens(tracker, monkeypatch):
    def refuse(direction):
        raise PermissionError("actor is not mutable")

    monkeypatch.setattr(app_module, "cycle_turn", refuse)
    assert step() == ("blocked", {"reason": "PermissionError"})
    assert app_module.TURN_INDEX == 0


def test_a_bad_direction_is_refused(tracker):
    assert step(direction="sideways")[0] == "blocked"
    assert tracker == []


def test_previous_turn_is_dispatched_as_prev(tracker):
    assert step(direction="prev")[0] == "ok"
    assert tracker == [("prev", True)]
