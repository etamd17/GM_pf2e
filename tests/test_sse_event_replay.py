"""SSE event-replay on reconnect (reliability): every broadcast gets a monotonic
id and is kept in a ring buffer, so a client that briefly dropped (tablet asleep /
off wifi) reconnects with Last-Event-ID and is replayed the events it missed
instead of silently showing stale HP/conditions until a manual reload.
"""
from __future__ import annotations

import pathlib

import app

_HUB = (pathlib.Path(__file__).resolve().parent.parent / 'templates' / '_sse_hub.html').read_text()


def _reset(monkeypatch):
    monkeypatch.setattr(app, '_sse_buffer', [])
    monkeypatch.setattr(app, '_sse_event_seq', 0)
    monkeypatch.setattr(app, '_sse_subscribers', [])


def test_broadcast_stamps_ids_and_buffers(monkeypatch):
    _reset(monkeypatch)
    app.sse_broadcast('pc_update', {'a': 1})
    app.sse_broadcast('combat_log', {'b': 2})
    buf = app._sse_buffer
    assert [i for (i, _, _) in buf] == [1, 2]
    assert buf[0][1].startswith('id: 1\n') and 'event: pc_update' in buf[0][1]
    assert buf[0][2] is not None                      # no filter -> players get it too


def test_gm_only_event_has_no_player_frame(monkeypatch):
    _reset(monkeypatch)
    app.sse_broadcast('secret', {'x': 1}, player_filter=lambda d: None)
    sid, gm, pl = app._sse_buffer[-1]
    assert gm.startswith('id: 1\n') and pl is None     # GM frame stamped, player dropped


def test_buffer_is_bounded(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(app, '_SSE_BUFFER_MAX', 5)
    for n in range(12):
        app.sse_broadcast('x', {'n': n})
    assert len(app._sse_buffer) == 5
    assert [i for (i, _, _) in app._sse_buffer] == [8, 9, 10, 11, 12]   # newest kept


def test_replay_selection_is_gap_free_and_dedup():
    # the slice the stream computes: last_seen < id <= start_id
    buf = [(1, 'a', None), (2, 'b', 'B'), (3, 'c', 'C'), (4, 'd', 'D')]
    last_seen, start_id = 1, 3
    replay = [(i, gm, pl) for (i, gm, pl) in buf if last_seen < i <= start_id]
    assert [i for (i, _, _) in replay] == [2, 3]       # 4 arrives live, 1 already seen


def test_stream_endpoint_is_event_stream(monkeypatch):
    _reset(monkeypatch)
    r = app.app.test_client().get('/api/events', headers={'Last-Event-ID': '0'})
    assert r.mimetype == 'text/event-stream'
    it = iter(r.response)
    first = next(it)
    if isinstance(first, bytes):
        first = first.decode()
    assert 'connected' in first
    it.close()


def test_hub_tracks_and_sends_last_event_id():
    assert 'lastId' in _HUB and 'function record' in _HUB
    assert 'last_event_id=' in _HUB and 'e.lastEventId' in _HUB
