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
    assert vtt.calls == [("camp_a", {"c1": {"name": "from camp_a"}}, {"p1": {"name": "Valeros"}})]


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
