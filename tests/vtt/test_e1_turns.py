"""E-1: turn changes from the VTT go through the tracker's own cycle_turn,
bound to what the GM page showed (handoff 087, Codex's 085 answer 3).

cycle_turn itself is replaced by a recorder here; the browser harness runs the
real one end to end.
"""

from __future__ import annotations

import pytest

import app as app_module


class Combatant:
    """A tracker combatant, as far as the turn step looks (weak-referenceable,
    like Character and Monster)."""

    def __init__(self, instance_id):
        self.instance_id = instance_id


@pytest.fixture
def tracker(monkeypatch):
    calls = []
    encounter = [Combatant("a1"), Combatant("b2")]
    monkeypatch.setattr(app_module, "ACTIVE_ENCOUNTER", encounter)
    monkeypatch.setattr(app_module, "TURN_INDEX", 0)
    monkeypatch.setattr(app_module, "ROUND_NUMBER", 3)
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module._campaigns, "get_campaign", lambda cid: {"id": cid, "system": "pf2e"})

    def cycle(direction):
        # Records the lock state the tracker would run under, then advances.
        calls.append((direction, app_module._LIVE_CAMPAIGN_DISPATCH_LOCK._is_owned()))
        app_module.TURN_INDEX = (app_module.TURN_INDEX + (1 if direction == "next" else -1)) % 2
        app_module._persist_encounter_state()             # as every tracker mutation does
        return None

    monkeypatch.setattr(app_module, "cycle_turn", cycle)
    return calls


def token():
    return app_module._vtt_turn_token()


def step(direction="next", actor="cmb_a1", round_=3, campaign="camp_a", turn=None):
    return app_module._vtt_turn_step(campaign, direction, actor, round_, token() if turn is None else turn)


def strip(result):
    code, detail = result
    return code, {k: v for k, v in detail.items() if k != "turnToken"}


def test_a_matching_request_changes_the_turn_through_the_tracker_under_the_lock(tracker):
    before = token()
    code, detail = step(turn=before)
    assert (code, detail["actorId"], detail["round"]) == ("ok", "cmb_b2", 3)
    assert detail["turnToken"] == token() != before        # the new turn's token, for the next request
    assert tracker == [("next", True)]


def test_a_duplicate_click_is_stale_and_changes_nothing(tracker):
    shown = token()
    assert step(turn=shown)[0] == "ok"
    code, detail = step(turn=shown)                         # the same request again
    assert (code, detail["actorId"], detail["turnToken"]) == ("stale", "cmb_b2", token())
    assert len(tracker) == 1


def test_a_replay_after_next_and_previous_is_stale(tracker):
    # Review 095, case 1: Next, then Previous back to the same actor and round;
    # the original Next delivered again must not run a second time.
    original = token()
    assert step("next", "cmb_a1", turn=original)[0] == "ok"
    assert step("prev", "cmb_b2")[0] == "ok"
    assert (app_module.TURN_INDEX, app_module.ROUND_NUMBER) == (0, 3)       # actor and round as before
    assert strip(step("next", "cmb_a1", turn=original)) == ("stale", {"actorId": "cmb_a1", "round": 3})
    assert tracker == [("next", True), ("prev", True)]


def test_a_request_from_before_an_encounter_reload_is_stale(tracker, monkeypatch):
    # Review 095, case 2: load_encounter rebuilds the combatants with the same
    # instance ids and round; an old request must not act on the new encounter.
    shown = token()
    app_module.ACTIVE_ENCOUNTER[:] = [Combatant("a1"), Combatant("b2")]
    app_module._persist_encounter_state()
    assert strip(step(turn=shown)) == ("stale", {"actorId": "cmb_a1", "round": 3})
    assert tracker == []


def test_an_unobserved_reload_is_still_caught_at_the_request(tracker):
    shown = token()
    app_module.ACTIVE_ENCOUNTER[:] = [Combatant("a1"), Combatant("b2")]     # no persist or broadcast
    assert step(turn=shown)[0] == "stale"
    assert tracker == []


def test_a_tracker_move_and_back_invalidates_the_token(tracker):
    shown = token()
    app_module.cycle_turn("next")                          # from /tracker, not the VTT
    app_module.cycle_turn("prev")
    assert step(turn=shown)[0] == "stale"
    assert step()[0] == "ok"                                # with the current token it works


def test_the_token_is_stable_while_nothing_changes(tracker):
    assert token() == token()
    app_module._persist_encounter_state()
    assert token() == token()


def test_a_different_round_is_stale(tracker):
    assert strip(step(round_=2)) == ("stale", {"actorId": "cmb_a1", "round": 3})
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
    assert step(turn="x") == ("no-encounter", {})


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
