"""Regression coverage for SSE secrecy and account-authority revocation.

These tests deliberately exercise the queue/replay seam directly.  That keeps
the security assertions deterministic without holding an HTTP stream open or
depending on heartbeat timing.
"""
from __future__ import annotations

from types import SimpleNamespace
import queue
import time

import pytest
from flask import Response, g, session

import app


CAMPAIGN_A = "a" * 32
CAMPAIGN_B = "b" * 32
GM_USER_ID = "gm-user"
PLAYER_USER_ID = "player-user"
OTHER_USER_ID = "other-user"


@pytest.fixture(autouse=True)
def isolated_realtime_state(monkeypatch):
    """Keep each test independent from process-global SSE and encounter state."""
    monkeypatch.setattr(app, "ACTIVE_CAMPAIGN_ID", CAMPAIGN_A)
    monkeypatch.setattr(app, "_sse_subscribers", [])
    monkeypatch.setattr(app, "_sse_buffer", [])
    monkeypatch.setattr(app, "_sse_event_campaigns", {})
    monkeypatch.setattr(app, "_sse_event_seq", 0)
    monkeypatch.setattr(app, "_sse_last_cleanup", time.time())
    monkeypatch.setattr(app, "GM_SECRET_LOG", [])
    monkeypatch.setattr(app, "COMBAT_LOGS", [])
    monkeypatch.setattr(app, "ACTIVE_ENCOUNTER", [])
    monkeypatch.setattr(app, "PARTY_LIBRARY", {})
    monkeypatch.setattr(app, "_ensure_sse_keepalive", lambda: None)


def _subscriber_queues(monkeypatch):
    gm_queue = queue.Queue()
    player_queue = queue.Queue()
    monkeypatch.setattr(
        app,
        "_sse_subscribers",
        [
            (gm_queue, True, CAMPAIGN_A, GM_USER_ID),
            (player_queue, False, CAMPAIGN_A, PLAYER_USER_ID),
        ],
    )
    return gm_queue, player_queue


def _assert_queue_empty(target):
    with pytest.raises(queue.Empty):
        target.get_nowait()


def test_gm_secret_roll_has_no_player_live_or_replay_frame(monkeypatch):
    gm_queue, player_queue = _subscriber_queues(monkeypatch)
    monkeypatch.setattr(app.random, "randint", lambda _low, _high: 17)

    with app.app.test_request_context(
        "/api/gm_secret_roll",
        method="POST",
        json={"dice_count": 1, "dice_sides": 20, "label": "Hidden Perception"},
    ):
        response = app.gm_secret_roll()

    assert response.status_code == 200
    assert "event: gm_secret_roll" in gm_queue.get_nowait()
    _assert_queue_empty(player_queue)

    assert len(app._sse_buffer) == 1
    _event_id, gm_frame, player_frame = app._sse_buffer[0]
    assert "Hidden Perception" in gm_frame
    assert player_frame is None
    assert app._sse_event_campaigns == {1: CAMPAIGN_A}


def test_recall_knowledge_is_gm_only_in_live_replay_and_polled_log(monkeypatch):
    gm_queue, player_queue = _subscriber_queues(monkeypatch)
    target = SimpleNamespace(
        instance_id="enemy-1",
        is_pc=False,
        visible_to_players=True,
        name="Vault Sentinel",
        level=5,
        traits=["construct"],
        immunities=["poison"],
        weaknesses=["electricity 5"],
        resistances=[],
        fort=15,
        ref=9,
        will=12,
        ac=22,
        actions=[],
        strikes=[],
    )
    player_character = SimpleNamespace(
        name="Scholar",
        is_pc=True,
        skills={"arcana": 13},
    )
    monkeypatch.setattr(app, "ACTIVE_ENCOUNTER", [target])
    monkeypatch.setattr(app, "PARTY_LIBRARY", {"Scholar": player_character})
    monkeypatch.setattr(app, "GM_PASSWORD", "")
    monkeypatch.setattr(app.random, "randint", lambda _low, _high: 12)

    with app.app.test_request_context(
        "/api/recall_knowledge/enemy-1",
        method="POST",
        json={"pc_name": "Scholar"},
    ):
        response = app.recall_knowledge("enemy-1")

    assert response.status_code == 200
    gm_frames = [gm_queue.get_nowait(), gm_queue.get_nowait()]
    assert any("event: combat_log" in frame for frame in gm_frames)
    assert any("event: recall_knowledge" in frame for frame in gm_frames)
    assert all("DC 20" in frame or '"dc": 20' in frame for frame in gm_frames)
    _assert_queue_empty(player_queue)

    assert len(app._sse_buffer) == 2
    assert all(player_frame is None for _sid, _gm, player_frame in app._sse_buffer)
    assert all(
        app._sse_event_campaigns[sid] == CAMPAIGN_A
        for sid, _gm, _player in app._sse_buffer
    )
    assert app.COMBAT_LOGS[-1]["gm_only"] is True
    assert "DC 20" in app.COMBAT_LOGS[-1]["msg"]

    monkeypatch.setattr(app, "_is_gm", lambda: False)
    with app.app.test_request_context("/api/combat_log"):
        player_response = app.get_combat_log()
    player_payload = player_response.get_json()
    assert player_payload == {"log": [], "count": 0}
    assert "Vault Sentinel" not in player_response.get_data(as_text=True)
    assert "DC 20" not in player_response.get_data(as_text=True)

    monkeypatch.setattr(app, "_is_gm", lambda: True)
    with app.app.test_request_context("/api/combat_log"):
        gm_response = app.get_combat_log()
    gm_payload = gm_response.get_json()
    assert gm_payload["count"] == 1
    assert gm_payload["log"][0]["gm_only"] is True
    assert "Vault Sentinel" in gm_payload["log"][0]["msg"]
    assert "DC 20" in gm_payload["log"][0]["msg"]


def test_close_sse_subscribers_for_user_only_terminates_exact_authority():
    revoked_queue = queue.Queue()
    other_user_queue = queue.Queue()
    other_campaign_queue = queue.Queue()
    legacy_queue = queue.Queue()
    revoked = (revoked_queue, False, CAMPAIGN_A, PLAYER_USER_ID)
    other_user = (other_user_queue, False, CAMPAIGN_A, OTHER_USER_ID)
    other_campaign = (other_campaign_queue, False, CAMPAIGN_B, PLAYER_USER_ID)
    legacy = (legacy_queue, False, CAMPAIGN_A)
    app._sse_subscribers[:] = [revoked, other_user, other_campaign, legacy]

    closed = app._close_sse_subscribers_for_user(CAMPAIGN_A, PLAYER_USER_ID)

    assert closed == 1
    assert app._sse_subscribers == [other_user, other_campaign, legacy]
    assert revoked_queue.get_nowait() is app._SSE_CLOSE
    _assert_queue_empty(other_user_queue)
    _assert_queue_empty(other_campaign_queue)
    _assert_queue_empty(legacy_queue)


def test_close_sse_subscribers_for_user_can_revoke_every_campaign():
    first_queue = queue.Queue()
    second_queue = queue.Queue()
    peer_queue = queue.Queue()
    first = (first_queue, False, CAMPAIGN_A, PLAYER_USER_ID)
    second = (second_queue, False, CAMPAIGN_B, PLAYER_USER_ID)
    peer = (peer_queue, False, CAMPAIGN_A, OTHER_USER_ID)
    app._sse_subscribers[:] = [first, second, peer]

    closed = app._close_sse_subscribers_for_user(None, PLAYER_USER_ID)

    assert closed == 2
    assert app._sse_subscribers == [peer]
    assert first_queue.get_nowait() is app._SSE_CLOSE
    assert second_queue.get_nowait() is app._SSE_CLOSE
    _assert_queue_empty(peer_queue)


def test_close_legacy_gm_streams_does_not_disconnect_account_users():
    legacy_gm_queue = queue.Queue()
    older_legacy_gm_queue = queue.Queue()
    legacy_player_queue = queue.Queue()
    account_gm_queue = queue.Queue()
    legacy_gm = (legacy_gm_queue, True, CAMPAIGN_A, None)
    older_legacy_gm = (older_legacy_gm_queue, True, CAMPAIGN_B)
    legacy_player = (legacy_player_queue, False, CAMPAIGN_A, None)
    account_gm = (account_gm_queue, True, CAMPAIGN_A, GM_USER_ID)
    app._sse_subscribers[:] = [legacy_gm, older_legacy_gm, legacy_player, account_gm]

    assert app._close_legacy_gm_sse_subscribers() == 2
    assert app._sse_subscribers == [legacy_player, account_gm]
    assert legacy_gm_queue.get_nowait() is app._SSE_CLOSE
    assert older_legacy_gm_queue.get_nowait() is app._SSE_CLOSE
    _assert_queue_empty(legacy_player_queue)
    _assert_queue_empty(account_gm_queue)


def test_account_logout_revokes_streams_before_clearing_session(monkeypatch):
    order = []
    monkeypatch.setattr(app, '_account_mode', lambda: True)
    monkeypatch.setattr(
        app,
        '_close_sse_subscribers_for_user',
        lambda campaign_id, user_id: order.append(('close', campaign_id, user_id)),
    )
    monkeypatch.setattr(app._auth, 'logout_user', lambda: order.append(('logout',)))

    with app.app.test_request_context('/logout'):
        session['user_id'] = PLAYER_USER_ID
        response = app.logout()

    assert response.status_code == 302
    assert order == [('close', None, PLAYER_USER_ID), ('logout',)]


def test_legacy_gm_logout_revokes_only_for_a_current_epoch(monkeypatch):
    calls = []
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', 'current-legacy-password')
    monkeypatch.setattr(
        app,
        '_close_legacy_gm_sse_subscribers',
        lambda: calls.append('close'),
    )

    with app.app.test_request_context('/gm/logout'):
        session['gm_authenticated'] = True
        session['gm_auth_epoch'] = app._legacy_auth_epoch()
        response = app.gm_logout()
        assert 'gm_authenticated' not in session
        assert 'gm_auth_epoch' not in session

    assert response.status_code == 302
    assert calls == ['close']

    with app.app.test_request_context('/gm/logout'):
        session['gm_authenticated'] = True
        session['gm_auth_epoch'] = 'stale-epoch'
        stale_response = app.gm_logout()

    assert stale_response.status_code == 302
    assert calls == ['close']


def test_successful_identity_switch_login_revokes_previous_streams(monkeypatch):
    order = []
    old_user = {'id': PLAYER_USER_ID, 'username': 'old'}
    new_user = {'id': OTHER_USER_ID, 'username': 'new'}
    monkeypatch.setattr(app._auth, 'account_mode_initialized', lambda: True)
    monkeypatch.setattr(app, '_consume_auth_limit', lambda *_args: None)
    monkeypatch.setattr(app, '_reset_auth_limit', lambda *_args: None)
    monkeypatch.setattr(app._auth, 'verify_credentials', lambda *_args: new_user)
    monkeypatch.setattr(
        app,
        '_close_sse_subscribers_for_user',
        lambda campaign_id, user_id: order.append(('close', campaign_id, user_id)),
    )
    monkeypatch.setattr(
        app._auth,
        'login_user',
        lambda user, remember=True: order.append(('login', user['id'], remember)),
    )

    with app.app.test_request_context(
        '/login',
        method='POST',
        data={'username': 'new', 'password': 'valid-password'},
    ):
        session['user_id'] = old_user['id']
        response = app.login()

    assert response.status_code == 302
    assert order == [
        ('close', None, PLAYER_USER_ID),
        ('login', OTHER_USER_ID, True),
    ]


def test_account_mode_rejects_legacy_gm_login_without_clearing_identity(monkeypatch):
    monkeypatch.setattr(app, '_account_mode', lambda: True)
    with app.app.test_request_context('/gm/login', method='POST'):
        session['user_id'] = PLAYER_USER_ID
        response = app.gm_login()
        assert session['user_id'] == PLAYER_USER_ID

    assert response.status_code == 302
    assert '/login' in response.headers['Location']


def test_account_store_outage_closes_account_streams_and_preserves_legacy(monkeypatch):
    account_queue = queue.Queue()
    legacy_queue = queue.Queue()
    account = (account_queue, False, CAMPAIGN_A, PLAYER_USER_ID)
    legacy = (legacy_queue, False, CAMPAIGN_A, None)
    app._sse_subscribers[:] = [account, legacy]
    monkeypatch.setattr(
        app._auth,
        'account_store_state',
        lambda: app._auth.ACCOUNT_STORE_UNAVAILABLE,
    )

    assert app._send_sse_keepalive_once() == 1
    assert app._sse_subscribers == [legacy]
    assert account_queue.get_nowait() is app._SSE_CLOSE
    assert 'event: keepalive' in legacy_queue.get_nowait()


def test_account_store_probe_error_fails_closed_for_account_streams(monkeypatch):
    account_queue = queue.Queue()
    account = (account_queue, False, CAMPAIGN_A, PLAYER_USER_ID)
    app._sse_subscribers[:] = [account]

    def unavailable_probe():
        raise RuntimeError('simulated storage classification failure')

    monkeypatch.setattr(app._auth, 'account_store_state', unavailable_probe)

    assert app._send_sse_keepalive_once() == 1
    assert app._sse_subscribers == []
    assert account_queue.get_nowait() is app._SSE_CLOSE


def test_authenticated_sse_response_is_private_no_store():
    with app.app.test_request_context("/api/events"):
        session['user_id'] = PLAYER_USER_ID
        g.request_id = 'a' * 32
        response = app._harden_response(Response(status=200))

    cache_control = response.headers['Cache-Control']
    assert 'private' in cache_control
    assert 'no-store' in cache_control
    assert 'no-transform' in cache_control
    assert response.headers['X-Accel-Buffering'] == 'no'


def test_password_change_revokes_every_stream_after_session_refresh(monkeypatch):
    calls = []
    user = {'id': PLAYER_USER_ID, 'username': 'player'}
    monkeypatch.setattr(app._auth, 'current_user', lambda: user)
    monkeypatch.setattr(app, '_consume_auth_limit', lambda *_args: None)
    monkeypatch.setattr(app, '_reset_auth_limit', lambda *_args: None)
    monkeypatch.setattr(app._auth, 'verify_credentials', lambda *_args: user)
    monkeypatch.setattr(
        app._auth,
        'set_password',
        lambda uid, _password: calls.append(('set', uid)),
    )
    monkeypatch.setattr(
        app._auth,
        'refresh_session_version',
        lambda uid: calls.append(('refresh', uid)),
    )
    monkeypatch.setattr(
        app,
        '_close_sse_subscribers_for_user',
        lambda cid, uid: calls.append(('close', cid, uid)),
    )
    monkeypatch.setattr(app, '_me_render', lambda **kwargs: kwargs)

    with app.app.test_request_context(
        '/me/password',
        method='POST',
        data={'current_password': 'old-password', 'new_password': 'new-password'},
    ):
        result = app.change_my_password.__wrapped__()

    assert result == {'pw_msg': 'Password updated.'}
    assert calls == [
        ('set', PLAYER_USER_ID),
        ('refresh', PLAYER_USER_ID),
        ('close', None, PLAYER_USER_ID),
    ]


def test_admin_self_reset_refreshes_notice_session_and_revokes_streams(monkeypatch):
    calls = []
    admin = {'id': GM_USER_ID, 'username': 'gm', 'is_admin': True}
    monkeypatch.setattr(app._auth, 'current_user', lambda: admin)
    monkeypatch.setattr(app._auth, 'get_user', lambda uid: admin if uid == GM_USER_ID else None)
    monkeypatch.setattr(
        app._auth,
        'set_password',
        lambda uid, _password: calls.append(('set', uid)),
    )
    monkeypatch.setattr(
        app._auth,
        'refresh_session_version',
        lambda uid: calls.append(('refresh', uid)),
    )
    monkeypatch.setattr(
        app,
        '_close_sse_subscribers_for_user',
        lambda cid, uid: calls.append(('close', cid, uid)),
    )

    with app.app.test_request_context(
        f'/admin/users/{GM_USER_ID}/reset',
        method='POST',
    ):
        response = app.admin_reset_password.__wrapped__(GM_USER_ID)
        notice = dict(session['_pw_reset_notice'])

    assert response.status_code == 302
    assert notice['username'] == 'gm'
    assert notice['temp']
    assert calls == [
        ('set', GM_USER_ID),
        ('refresh', GM_USER_ID),
        ('close', None, GM_USER_ID),
    ]


def test_keepalive_evicts_and_terminates_a_non_consuming_stream():
    blocked_queue = queue.Queue(maxsize=1)
    healthy_queue = queue.Queue(maxsize=2)
    blocked_queue.put_nowait("stale frame")
    blocked = (blocked_queue, False, CAMPAIGN_A, PLAYER_USER_ID)
    healthy = (healthy_queue, False, CAMPAIGN_A, OTHER_USER_ID)
    app._sse_subscribers[:] = [blocked, healthy]

    assert app._send_sse_keepalive_once() == 1
    assert app._sse_subscribers == [healthy]
    assert blocked_queue.get_nowait() is app._SSE_CLOSE
    assert "event: keepalive" in healthy_queue.get_nowait()


def test_revoking_user_closes_their_live_stream_but_keeps_peer_connected(monkeypatch):
    monkeypatch.setattr(app, "_active_campaign_id", lambda: CAMPAIGN_A)
    monkeypatch.setattr(app, "_is_gm", lambda: False)

    def open_stream(user_id):
        with app.app.test_request_context("/api/events"):
            g.principal = SimpleNamespace(user_id=user_id)
            return app.sse_stream()

    revoked_response = open_stream(PLAYER_USER_ID)
    peer_response = open_stream(OTHER_USER_ID)
    revoked_stream = iter(revoked_response.response)
    peer_stream = iter(peer_response.response)
    assert "event: connected" in next(revoked_stream)
    assert "event: connected" in next(peer_stream)
    assert [entry[3] for entry in app._sse_subscribers] == [
        PLAYER_USER_ID,
        OTHER_USER_ID,
    ]

    assert app._close_sse_subscribers_for_user(
        CAMPAIGN_A, PLAYER_USER_ID
    ) == 1
    with pytest.raises(StopIteration):
        next(revoked_stream)

    app.sse_broadcast("peer_still_connected", {"ok": True})
    peer_frame = next(peer_stream)
    assert "event: peer_still_connected" in peer_frame
    assert '"ok": true' in peer_frame

    peer_stream.close()
    assert app._sse_subscribers == []


@pytest.mark.parametrize(
    ("handler_name", "mutation_name", "path", "form"),
    [
        (
            "campaign_remove_member",
            "remove_member",
            f"/campaign/{CAMPAIGN_A}/members/{PLAYER_USER_ID}/remove",
            {},
        ),
        (
            "campaign_set_role",
            "set_member_role",
            f"/campaign/{CAMPAIGN_A}/members/{PLAYER_USER_ID}/role",
            {"role": "gm"},
        ),
    ],
)
@pytest.mark.parametrize("mutation_succeeds", [False, True])
def test_member_mutation_revokes_stream_only_after_success(
    monkeypatch,
    handler_name,
    mutation_name,
    path,
    form,
    mutation_succeeds,
):
    order = []
    monkeypatch.setattr(
        app,
        "_require_campaign_gm",
        lambda _cid: ({"id": GM_USER_ID}, {"id": CAMPAIGN_A}),
    )

    def mutate(*_args):
        order.append("mutation")
        return {"id": CAMPAIGN_A} if mutation_succeeds else None

    def revoke(campaign_id, user_id):
        order.append(("revoke", campaign_id, user_id))
        return 1

    monkeypatch.setattr(app._campaigns, mutation_name, mutate)
    monkeypatch.setattr(app, "_close_sse_subscribers_for_user", revoke)
    handler = getattr(app, handler_name).__wrapped__

    with app.app.test_request_context(path, method="POST", data=form):
        response = handler(CAMPAIGN_A, PLAYER_USER_ID)

    assert response.status_code == 302
    if mutation_succeeds:
        assert order == [
            "mutation",
            ("revoke", CAMPAIGN_A, PLAYER_USER_ID),
        ]
    else:
        assert order == ["mutation"]
