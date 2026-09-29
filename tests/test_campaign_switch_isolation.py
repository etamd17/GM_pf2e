"""Deterministic isolation checks for campaign switches and live SSE state.

These tests deliberately exercise the process-global seams directly.  Real HTTP
streaming would make the important interleavings depend on socket/proxy timing;
queues and threading events let us prove the same contracts without sleeps.
"""
from __future__ import annotations

import queue
import threading
import time

import pytest

import app


CAMPAIGN_A = "a" * 32
CAMPAIGN_B = "b" * 32


@pytest.fixture(autouse=True)
def isolated_sse_state(monkeypatch):
    """Give every test an empty, fully campaign-aware SSE registry."""
    monkeypatch.setattr(app, "_sse_subscribers", [])
    monkeypatch.setattr(app, "_sse_buffer", [])
    monkeypatch.setattr(app, "_sse_event_campaigns", {})
    monkeypatch.setattr(app, "_sse_event_seq", 0)
    monkeypatch.setattr(app, "_sse_last_cleanup", time.time())
    monkeypatch.setattr(app, "_ensure_sse_keepalive", lambda: None)


def _assert_queue_empty(q):
    with pytest.raises(queue.Empty):
        q.get_nowait()


def _open_stream(monkeypatch, campaign_id, *, last_event_id=0, is_gm=True):
    monkeypatch.setattr(app, "_active_campaign_id", lambda: campaign_id)
    monkeypatch.setattr(app, "_is_gm", lambda: is_gm)
    path = f"/api/events?last_event_id={last_event_id}"
    with app.app.test_request_context(path):
        response = app.sse_stream()
    return response


def test_sse_delivery_uses_exact_campaign_and_none_is_not_a_wildcard(monkeypatch):
    q_a = queue.Queue()
    q_b = queue.Queue()
    q_legacy = queue.Queue()
    monkeypatch.setattr(
        app,
        "_sse_subscribers",
        [(q_a, True, CAMPAIGN_A), (q_b, True, CAMPAIGN_B), (q_legacy, True, None)],
    )
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    app.sse_broadcast("active", {"campaign": "A"})
    frame_a = q_a.get_nowait()
    assert "event: active" in frame_a
    assert app._sse_event_campaigns[1] == CAMPAIGN_A
    _assert_queue_empty(q_b)
    _assert_queue_empty(q_legacy)

    app.sse_broadcast("explicit", {"campaign": "B"}, campaign_id=CAMPAIGN_B)
    frame_b = q_b.get_nowait()
    assert "event: explicit" in frame_b
    assert app._sse_event_campaigns[2] == CAMPAIGN_B
    _assert_queue_empty(q_a)
    _assert_queue_empty(q_legacy)

    app.sse_broadcast("legacy", {"campaign": None}, campaign_id=None)
    frame_legacy = q_legacy.get_nowait()
    assert "event: legacy" in frame_legacy
    assert app._sse_event_campaigns[3] is None
    _assert_queue_empty(q_a)
    _assert_queue_empty(q_b)


def test_sse_replay_only_replays_the_subscribers_exact_campaign(monkeypatch):
    app.sse_broadcast("a_seen", {"n": 1}, campaign_id=CAMPAIGN_A)
    app.sse_broadcast("b_private", {"n": 2}, campaign_id=CAMPAIGN_B)
    app.sse_broadcast("a_missed", {"n": 3}, campaign_id=CAMPAIGN_A)

    response = _open_stream(monkeypatch, CAMPAIGN_A, last_event_id=1)
    stream = iter(response.response)
    connected = next(stream)
    replayed = next(stream)

    assert "event: connected" in connected
    assert replayed.startswith("id: 3\n")
    assert "event: a_missed" in replayed
    assert "b_private" not in replayed

    stream.close()
    assert app._sse_subscribers == []


def test_sse_stream_registers_before_the_response_generator_is_consumed(monkeypatch):
    response = _open_stream(monkeypatch, CAMPAIGN_A)

    assert len(app._sse_subscribers) == 1
    assert app._sse_subscribers[0][2] == CAMPAIGN_A

    stream = iter(response.response)
    assert "event: connected" in next(stream)
    stream.close()
    assert app._sse_subscribers == []


def test_load_campaign_closes_old_stream_but_keeps_new_campaign_subscriber(monkeypatch):
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)
    response = _open_stream(monkeypatch, CAMPAIGN_A)
    old_stream = iter(response.response)
    assert "event: connected" in next(old_stream)
    old_queue = app._sse_subscribers[0][0]

    new_queue = queue.Queue()
    new_entry = (new_queue, True, CAMPAIGN_B)
    app._sse_subscribers.append(new_entry)

    monkeypatch.setattr(app, "_flush_pending_persistence", lambda: None)
    monkeypatch.setattr(app, "_reset_live_runtime_state", lambda: None)

    def bind(cid):
        # The stale stream must be removed before any process-global path is rebound.
        assert app._sse_subscribers == [new_entry]
        app.ACTIVE_CAMPAIGN_ID = cid

    monkeypatch.setattr(app, "_bind_campaign_paths", bind)
    monkeypatch.setattr(app, "load_libraries", lambda: None)
    monkeypatch.setattr(app, "_load_session_state", lambda: None)
    monkeypatch.setattr(app, "_load_handouts", lambda: None)

    assert app.load_campaign(CAMPAIGN_B) == CAMPAIGN_B
    with pytest.raises(StopIteration):
        next(old_stream)

    app.sse_broadcast("after_switch", {"campaign": "B"})
    assert "event: after_switch" in new_queue.get_nowait()
    _assert_queue_empty(old_queue)


class _FakeTimer:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


def test_reset_live_runtime_state_drops_stale_campaign_memory(monkeypatch):
    pc_timer = _FakeTimer()
    encounter_timer = _FakeTimer()
    collection_names = (
        "ACTIVE_ENCOUNTER",
        "PENDING_INITIATIVES",
        "_RECENT_DEFEATED",
        "ROUND_EVENTS",
        "COMBAT_LOGS",
        "TURN_REMINDERS",
        "CHAT_MESSAGES",
        "GM_SECRET_LOG",
        "ACTIVE_SKILL_CHALLENGES",
        "HANDOUTS",
        "SESSION_HEALING_LOG",
        "SESSION_JOURNAL",
        "_PARTY_DIR_MTIME_CACHE",
        "_PC_FILE_CACHE",
        "_PC_PERSIST_DIRTY",
        "_PC_BROADCAST_PENDING",
    )
    for name in collection_names:
        current = getattr(app, name)
        value = {"stale"} if isinstance(current, set) else ({"stale": True} if isinstance(current, dict) else ["stale"])
        monkeypatch.setattr(app, name, value)

    monkeypatch.setattr(app, "TURN_INDEX", 9)
    monkeypatch.setattr(app, "ROUND_NUMBER", 12)
    monkeypatch.setattr(app, "ENCOUNTER_NOTES", "campaign A")
    monkeypatch.setattr(app, "SESSION_TIMER_START", 1234)
    monkeypatch.setattr(app, "_TRACKER_STATE_CACHE", {"campaign": "A"})
    monkeypatch.setattr(app, "_TRACKER_STATE_CACHE_TIME", 999.0)
    monkeypatch.setattr(app, "_PARTY_DIR_LISTING_MTIME", 999)
    monkeypatch.setattr(app, "_PERSIST_DIRTY", True)
    monkeypatch.setattr(app, "_PC_BROADCAST_TIMER", pc_timer)
    monkeypatch.setattr(app, "_ENC_BROADCAST_TIMER", encounter_timer)
    monkeypatch.setattr(app, "_ENC_BROADCAST_PENDING", True)

    app._reset_live_runtime_state()

    assert all(not getattr(app, name) for name in collection_names)
    assert app.TURN_INDEX == 0
    assert app.ROUND_NUMBER == 1
    assert app.ENCOUNTER_NOTES == ""
    assert app.SESSION_TIMER_START is None
    assert app._TRACKER_STATE_CACHE is None
    assert app._TRACKER_STATE_CACHE_TIME == 0.0
    assert app._PARTY_DIR_LISTING_MTIME == 0
    assert app._PERSIST_DIRTY is False
    assert app._PC_BROADCAST_TIMER is None
    assert app._ENC_BROADCAST_TIMER is None
    assert app._ENC_BROADCAST_PENDING is False
    assert pc_timer.cancelled is True
    assert encounter_timer.cancelled is True


def test_campaign_switch_waits_for_inflight_persistence_flush(monkeypatch):
    persist_entered = threading.Event()
    allow_persist_to_finish = threading.Event()
    switch_attempted = threading.Event()
    bind_called = threading.Event()
    errors = []
    order = []

    monkeypatch.setattr(app, "_PERSIST_DIRTY", True)
    monkeypatch.setattr(app, "_PC_PERSIST_DIRTY", set())
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    def persist_old_campaign():
        order.append("persist:start")
        persist_entered.set()
        if not allow_persist_to_finish.wait(2):
            raise AssertionError("test did not release the persistence flush")
        order.append("persist:end")
        return True

    def bind_new_campaign(cid):
        order.append(f"bind:{cid}")
        app.ACTIVE_CAMPAIGN_ID = cid
        bind_called.set()

    monkeypatch.setattr(app, "_do_persist_encounter_state", persist_old_campaign)
    monkeypatch.setattr(app, "_close_sse_subscribers_for_other_campaign", lambda _cid: None)
    monkeypatch.setattr(app, "_reset_live_runtime_state", lambda: None)
    monkeypatch.setattr(app, "_bind_campaign_paths", bind_new_campaign)
    monkeypatch.setattr(app, "load_libraries", lambda: None)
    monkeypatch.setattr(app, "_load_session_state", lambda: None)
    monkeypatch.setattr(app, "_load_handouts", lambda: None)

    def capture_errors(fn):
        try:
            fn()
        except BaseException as exc:  # surfaced in the main pytest thread below
            errors.append(exc)

    flush_thread = threading.Thread(
        target=lambda: capture_errors(app._flush_pending_persistence),
        name="test-persistence-flush",
    )
    flush_thread.start()
    assert persist_entered.wait(2)

    def switch_campaign():
        switch_attempted.set()
        app.load_campaign(CAMPAIGN_B)

    switch_thread = threading.Thread(
        target=lambda: capture_errors(switch_campaign),
        name="test-campaign-switch",
    )
    switch_thread.start()
    assert switch_attempted.wait(2)

    # load_campaign cannot bind B while the old campaign snapshot owns the lock.
    assert bind_called.wait(0.1) is False
    allow_persist_to_finish.set()

    flush_thread.join(2)
    switch_thread.join(2)
    assert not flush_thread.is_alive()
    assert not switch_thread.is_alive()
    assert errors == []
    assert order.index("persist:end") < order.index(f"bind:{CAMPAIGN_B}")
    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_B


def test_failed_persistence_restores_all_dirty_markers(monkeypatch):
    monkeypatch.setattr(app, "_PERSIST_DIRTY", True)
    monkeypatch.setattr(app, "_PC_PERSIST_DIRTY", {"Valeros", "Kyra"})
    monkeypatch.setattr(app, "_do_persist_encounter_state", lambda: False)
    monkeypatch.setattr(app, "_do_persist_pc_combat_state", lambda _name: False)

    assert app._flush_pending_persistence_locked() is False
    assert app._PERSIST_DIRTY is True
    assert app._PC_PERSIST_DIRTY == {"Valeros", "Kyra"}


def test_persistence_exceptions_restore_all_dirty_markers(monkeypatch):
    monkeypatch.setattr(app, "_PERSIST_DIRTY", True)
    monkeypatch.setattr(app, "_PC_PERSIST_DIRTY", {"Merisiel"})

    def fail(*_args):
        raise OSError("disk unavailable")

    monkeypatch.setattr(app, "_do_persist_encounter_state", fail)
    monkeypatch.setattr(app, "_do_persist_pc_combat_state", fail)

    assert app._flush_pending_persistence_locked() is False
    assert app._PERSIST_DIRTY is True
    assert app._PC_PERSIST_DIRTY == {"Merisiel"}


def test_load_campaign_aborts_before_runtime_teardown_when_flush_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)
    monkeypatch.setattr(app, "_flush_pending_persistence", lambda: False)
    monkeypatch.setattr(
        app,
        "_close_sse_subscribers_for_other_campaign",
        lambda _cid: calls.append("close"),
    )
    monkeypatch.setattr(app, "_reset_live_runtime_state", lambda: calls.append("reset"))
    monkeypatch.setattr(app, "_bind_campaign_paths", lambda _cid: calls.append("bind"))

    with pytest.raises(RuntimeError, match="could not be persisted"):
        app.load_campaign(CAMPAIGN_B)

    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_A
    assert calls == []


def test_failed_gm_activation_keeps_runtime_live_pointer_and_session(monkeypatch):
    user = {"id": "gm-user", "is_admin": False}
    campaign = {"id": CAMPAIGN_B, "system": "pf2e"}
    durable_writes = []
    monkeypatch.setattr(app._auth, "current_user", lambda: user)
    monkeypatch.setattr(app._campaigns, "get_campaign", lambda cid: campaign if cid == CAMPAIGN_B else None)
    monkeypatch.setattr(app._campaigns, "user_role", lambda _camp, _uid: "gm")
    monkeypatch.setattr(app._campaigns, "is_gm", lambda _camp, _uid: True)
    monkeypatch.setattr(app._storage, "get_live_campaign_id", lambda: CAMPAIGN_A)
    monkeypatch.setattr(
        app._storage,
        "set_live_campaign_id",
        lambda cid: durable_writes.append(cid),
    )
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    def fail_switch(_cid):
        raise RuntimeError("old campaign flush failed")

    monkeypatch.setattr(app, "load_campaign", fail_switch)

    with app.app.test_request_context(
        f"/campaign/{CAMPAIGN_B}/activate",
        method="POST",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ):
        app.session["active_campaign_id"] = CAMPAIGN_A
        response, status = app.activate_campaign(CAMPAIGN_B)
        assert status == 503
        assert response.get_json()["error"] == "campaign_switch_failed"
        assert app.session["active_campaign_id"] == CAMPAIGN_A

    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_A
    assert durable_writes == []


def test_failed_gm_activation_rolls_back_a_partially_rebound_runtime(monkeypatch):
    user = {"id": "gm-user", "is_admin": False}
    campaign = {"id": CAMPAIGN_B, "system": "pf2e"}
    durable = {"campaign_id": CAMPAIGN_A}
    load_calls = []
    monkeypatch.setattr(app._auth, "current_user", lambda: user)
    monkeypatch.setattr(app._campaigns, "get_campaign", lambda cid: campaign if cid == CAMPAIGN_B else None)
    monkeypatch.setattr(app._campaigns, "user_role", lambda _camp, _uid: "gm")
    monkeypatch.setattr(app._campaigns, "is_gm", lambda _camp, _uid: True)
    monkeypatch.setattr(
        app._storage,
        "get_live_campaign_id",
        lambda: durable["campaign_id"],
    )
    monkeypatch.setattr(
        app._storage,
        "set_live_campaign_id",
        lambda cid: durable.__setitem__("campaign_id", cid),
    )
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    def partially_rebind_then_fail(cid):
        load_calls.append(cid)
        app.ACTIVE_CAMPAIGN_ID = cid
        if cid == CAMPAIGN_B:
            raise RuntimeError("new campaign libraries failed to load")
        return cid

    monkeypatch.setattr(app, "load_campaign", partially_rebind_then_fail)

    with app.app.test_request_context(
        f"/campaign/{CAMPAIGN_B}/activate",
        method="POST",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ):
        app.session["active_campaign_id"] = CAMPAIGN_A
        response, status = app.activate_campaign(CAMPAIGN_B)
        assert status == 503
        assert response.get_json()["error"] == "campaign_switch_failed"
        assert app.session["active_campaign_id"] == CAMPAIGN_A

    assert load_calls == [CAMPAIGN_B, CAMPAIGN_A]
    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_A
    assert durable["campaign_id"] == CAMPAIGN_A


def test_failed_gm_stop_keeps_runtime_live_pointer_and_session(monkeypatch):
    user = {"id": "gm-user", "is_admin": False, "last_campaign_id": CAMPAIGN_A}
    campaign = {"id": CAMPAIGN_A, "system": "pf2e"}
    durable_writes = []
    monkeypatch.setattr(app._auth, "current_user", lambda: user)
    monkeypatch.setattr(app._campaigns, "get_campaign", lambda cid: campaign if cid == CAMPAIGN_A else None)
    monkeypatch.setattr(app._campaigns, "is_gm", lambda _camp, _uid: True)
    monkeypatch.setattr(app._storage, "get_live_campaign_id", lambda: CAMPAIGN_A)
    monkeypatch.setattr(
        app._storage,
        "set_live_campaign_id",
        lambda cid: durable_writes.append(cid),
    )
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    def fail_stop(_cid):
        raise RuntimeError("live campaign flush failed")

    monkeypatch.setattr(app, "load_campaign", fail_stop)

    with app.app.test_request_context(
        "/campaign/stop",
        method="POST",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ):
        app.session["active_campaign_id"] = CAMPAIGN_A
        app.session["player_name"] = "Valeros"
        response, status = app.stop_active_campaign()
        assert status == 503
        assert response.get_json()["error"] == "campaign_switch_failed"
        assert app.session["active_campaign_id"] == CAMPAIGN_A
        assert app.session["player_name"] == "Valeros"
        assert "campaign_stopped" not in app.session

    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_A
    assert durable_writes == []


def test_failed_gm_stop_rolls_back_a_partially_cleared_runtime(monkeypatch):
    user = {"id": "gm-user", "is_admin": False, "last_campaign_id": CAMPAIGN_A}
    campaign = {"id": CAMPAIGN_A, "system": "pf2e"}
    durable = {"campaign_id": CAMPAIGN_A}
    load_calls = []
    monkeypatch.setattr(app._auth, "current_user", lambda: user)
    monkeypatch.setattr(app._campaigns, "get_campaign", lambda cid: campaign if cid == CAMPAIGN_A else None)
    monkeypatch.setattr(app._campaigns, "is_gm", lambda _camp, _uid: True)
    monkeypatch.setattr(
        app._storage,
        "get_live_campaign_id",
        lambda: durable["campaign_id"],
    )
    monkeypatch.setattr(
        app._storage,
        "set_live_campaign_id",
        lambda cid: durable.__setitem__("campaign_id", cid),
    )
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)

    def partially_clear_then_fail(cid):
        load_calls.append(cid)
        app.ACTIVE_CAMPAIGN_ID = cid
        if cid is None:
            raise RuntimeError("legacy libraries failed to load")
        return cid

    monkeypatch.setattr(app, "load_campaign", partially_clear_then_fail)

    with app.app.test_request_context(
        "/campaign/stop",
        method="POST",
        headers={"X-Requested-With": "XMLHttpRequest"},
    ):
        app.session["active_campaign_id"] = CAMPAIGN_A
        app.session["player_name"] = "Valeros"
        response, status = app.stop_active_campaign()
        assert status == 503
        assert response.get_json()["error"] == "campaign_switch_failed"
        assert app.session["active_campaign_id"] == CAMPAIGN_A
        assert app.session["player_name"] == "Valeros"
        assert "campaign_stopped" not in app.session

    assert load_calls == [None, CAMPAIGN_A]
    assert app.ACTIVE_CAMPAIGN_ID == CAMPAIGN_A
    assert durable["campaign_id"] == CAMPAIGN_A
