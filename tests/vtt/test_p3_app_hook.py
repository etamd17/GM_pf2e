"""Parity P3: app.py mirrors the tracker into the VTT (handoff 069).

The VTT itself is replaced by a recorder; what is tested here is when app.py
calls it and with what.
"""

from __future__ import annotations

import pytest

import app as app_module


class Recorder:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def sync_live(self, campaign_id, combatants, characters):
        self.calls.append((campaign_id, combatants, characters))
        if self.fail:
            raise RuntimeError("database down")
        return []


@pytest.fixture
def vtt(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(app_module, "VTT_SERVICE", recorder, raising=False)
    monkeypatch.setattr(app_module, "_VTT_LIVE_LAST_CID", None)
    monkeypatch.setattr(app_module, "ROUND_NUMBER", 1)       # E-1: every combatant carries it
    monkeypatch.setattr(app_module, "_scene_live_indexes",
                        lambda cid: ({"c1": {"name": f"from {cid}"}}, {"p1": {"name": "Valeros"}}))
    yield recorder
    timer = app_module._VTT_LIVE_TIMER
    if timer is not None:
        timer.cancel()
    monkeypatch.setattr(app_module, "_VTT_LIVE_TIMER", None)


def test_a_flush_mirrors_the_loaded_campaign(vtt, monkeypatch):
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    app_module._vtt_live_flush()
    assert vtt.calls == [("camp_a", {"c1": {"name": "from camp_a", "vtt_round": 1, "vtt_turn": app_module._vtt_turn_token(),
                                 "vtt_encounter": app_module._vtt_encounter_token()}},
                          {"p1": {"name": "Valeros", "vtt_encounter": app_module._vtt_encounter_token()}})]


def test_switching_campaigns_empties_the_previous_mirror_first(vtt, monkeypatch):
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    app_module._vtt_live_flush()
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_b")
    app_module._vtt_live_flush()
    assert [c[0] for c in vtt.calls] == ["camp_a", "camp_a", "camp_b"]
    assert vtt.calls[1] == ("camp_a", {}, {})


def test_a_failed_flush_is_logged_not_raised(vtt, monkeypatch):
    vtt.fail = True
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    app_module._vtt_live_flush()                    # no exception reaches the tracker
    assert len(vtt.calls) == 1


def test_live_state_frames_schedule_one_coalesced_flush(vtt, monkeypatch):
    monkeypatch.setattr(app_module, "_VTT_LIVE_DELAY", 60)      # never fires during the test
    app_module.sse_broadcast("chat_message", {"text": "hi"})
    assert app_module._VTT_LIVE_TIMER is None
    app_module.sse_broadcast("encounter_update", {})
    first = app_module._VTT_LIVE_TIMER
    assert first is not None
    app_module.sse_broadcast("pc_update", {})
    assert app_module._VTT_LIVE_TIMER is first     # coalesced behind the one timer


def test_nothing_is_scheduled_without_the_vtt(monkeypatch):
    monkeypatch.setattr(app_module, "VTT_SERVICE", None, raising=False)
    monkeypatch.setattr(app_module, "_VTT_LIVE_TIMER", None)
    app_module.sse_broadcast("encounter_update", {})
    assert app_module._VTT_LIVE_TIMER is None


def test_a_flush_runs_where_the_tracker_helpers_can_build_sheet_links(vtt, monkeypatch):
    """The timer thread has no request; production's account mode builds PC
    sheet links with url_for, which needs one (found in self-review)."""
    import threading
    from flask import has_request_context
    seen = {}

    def indexes(cid):
        seen["context"] = has_request_context()
        seen["url"] = app_module._pc_sheet_url("pf2e", "Valeros", "pc_1")
        return {}, {}

    monkeypatch.setattr(app_module, "_scene_live_indexes", indexes)
    monkeypatch.setattr(app_module, "_account_mode", lambda: True)
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    worker = threading.Thread(target=app_module._vtt_live_flush)     # off the test's own context, like the timer
    worker.start()
    worker.join(10)
    assert seen["context"] is True and "pc_1" in seen["url"]
    assert vtt.calls == [("camp_a", {}, {})]


# --- the snapshot is taken under the campaign dispatch lock (handoff 072, finding 2) ------------

def test_a_flush_waits_for_the_campaign_dispatch_lock(vtt, monkeypatch):
    import threading
    entered = threading.Event()
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes", lambda cid: (entered.set() or {}, {}))
    with app_module._LIVE_CAMPAIGN_DISPATCH_LOCK:
        worker = threading.Thread(target=app_module._vtt_live_flush)
        worker.start()
        raced = entered.wait(1)
    worker.join(10)
    assert not raced, "read tracker state while the campaign dispatch lock was held"
    assert entered.is_set() and vtt.calls == [("camp_a", {}, {})]


def test_a_flush_never_sees_a_half_rebound_campaign(vtt, monkeypatch):
    """load_campaign binds the new campaign before it reloads that campaign's
    state, under the dispatch lock. A flush that starts in between must see
    only the finished switch."""
    import threading
    state = {"cid": "camp_a", "encounter": "camp_a fight"}
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: state["cid"])
    monkeypatch.setattr(app_module, "_scene_live_indexes",
                        lambda cid: ({"c1": {"name": state["encounter"]}}, {}))
    with app_module._LIVE_CAMPAIGN_DISPATCH_LOCK:
        state["cid"] = "camp_b"                         # bound ...
        worker = threading.Thread(target=app_module._vtt_live_flush)
        worker.start()
        worker.join(0.5)
        state["encounter"] = "camp_b fight"             # ... and then reloaded
    worker.join(10)
    assert vtt.calls == [("camp_b", {"c1": {"name": "camp_b fight", "vtt_round": 1,
                                           "vtt_turn": app_module._vtt_turn_token(),
                                           "vtt_encounter": app_module._vtt_encounter_token()}}, {})]


def test_an_older_snapshot_never_commits_after_a_newer_one(vtt, monkeypatch):
    import threading
    state = {"hp": 20}
    first_writing, release_first = threading.Event(), threading.Event()
    order = []

    def sync(campaign_id, combatants, characters):
        hp = combatants["c1"]["hp"]
        if hp == 20:
            first_writing.set()
            release_first.wait(10)                      # a slow database write
        order.append(hp)
        return []

    vtt.sync_live = sync
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes", lambda cid: ({"c1": {"hp": state["hp"]}}, {}))
    first = threading.Thread(target=app_module._vtt_live_flush)
    first.start()
    assert first_writing.wait(10)
    state["hp"] = 5                                     # the tracker moves on, unblocked
    with app_module._LIVE_CAMPAIGN_DISPATCH_LOCK:
        pass                                            # the dispatch lock is free during the write
    second = threading.Thread(target=app_module._vtt_live_flush)
    second.start()
    second.join(0.5)
    release_first.set()
    first.join(10)
    second.join(10)
    assert order == [20, 5]


def test_the_snapshot_is_detached_from_the_tracker(vtt, monkeypatch):
    shared = {"c1": {"name": "Skeleton", "conditions": {"prone": True}}}
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes", lambda cid: (shared, {}))
    app_module._vtt_live_flush()
    sent = vtt.calls[0][1]
    assert sent == {"c1": {**shared["c1"], "vtt_round": 1, "vtt_turn": app_module._vtt_turn_token(),
                           "vtt_encounter": app_module._vtt_encounter_token()}}
    assert "vtt_round" not in shared["c1"] and "vtt_turn" not in shared["c1"]
    assert sent is not shared and sent["c1"]["conditions"] is not shared["c1"]["conditions"]


def test_the_flush_adds_placement_hints_from_the_map_candidates(vtt, monkeypatch):
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes",
                        lambda cid: ({"c1": {"name": "Skeleton"}, "c2": {"name": "Kyra"}},
                                     {"p1": {"name": "Kyra"}, "p2": {"name": "Valeros"}}))
    monkeypatch.setattr(app_module, "_scene_token_candidates", lambda cid: [
        {"combatant_id": "c1", "character_id": None, "size": 2, "sheet_url": "/tracker"},
        {"combatant_id": "c2", "character_id": "p1", "size": 1, "sheet_url": "/player/sheet/Kyra"},
        {"combatant_id": None, "character_id": "p2", "size": 1, "sheet_url": "/player/sheet/Valeros"},
    ])
    app_module._vtt_live_flush()
    _cid, combatants, characters = vtt.calls[0]
    assert combatants["c1"]["vtt_hints"] == {"size": 2, "sheet_url": "/tracker"}
    assert characters["p1"]["vtt_hints"]["sheet_url"] == "/player/sheet/Kyra"     # from its combatant
    assert characters["p2"]["vtt_hints"]["sheet_url"] == "/player/sheet/Valeros"


def test_the_shared_live_indexes_are_not_given_vtt_keys(vtt, monkeypatch):
    shared = ({"c1": {"name": "Skeleton"}}, {})
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes", lambda cid: shared)
    monkeypatch.setattr(app_module, "_scene_token_candidates", lambda cid: [])
    app_module._vtt_live_flush()
    assert "vtt_hints" not in shared[0]["c1"]                                  # /map's data untouched


def test_a_hint_failure_never_stops_the_live_mirror(vtt, monkeypatch):
    monkeypatch.setattr(app_module, "_loaded_campaign_id", lambda: "camp_a")
    monkeypatch.setattr(app_module, "_scene_live_indexes", lambda cid: ({"c1": {"name": "Skeleton"}}, {}))

    def broken(cid):
        raise OSError("party folder unreadable")

    monkeypatch.setattr(app_module, "_scene_token_candidates", broken)
    app_module._vtt_live_flush()
    assert vtt.calls and "vtt_hints" not in vtt.calls[0][1]["c1"]                # mirrored, without hints
