"""MAP-R2: scene frames never reach a player SSE connection.

The map is GM-only (/map and /map/table are both GM-authenticated), but the
scene broadcasts used to go to every subscriber of the campaign: every saved
scene's projection (prep included), which scene was put on the table, and the
GM's pings and ruler. No player page consumes any of them, so they are dropped
for players outright rather than filtered.
"""

from __future__ import annotations

import ast
import queue
from pathlib import Path

import pytest

import app
from core import scenes, storage

CID = 'a' * 32
SCENE_EVENTS = ('scene_update', 'scene_activated', 'scene_beacon')


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_scene_character_records', lambda _cid: {})
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [])
    gm_q, player_q = queue.Queue(), queue.Queue()
    monkeypatch.setattr(app, '_sse_subscribers', [(gm_q, True, CID), (player_q, False, CID)])
    monkeypatch.setattr(app, '_sse_buffer', [])
    monkeypatch.setattr(app, '_sse_event_seq', 0)
    monkeypatch.setattr(app, '_sse_event_campaigns', {})
    with app.app.test_client() as client:
        yield client, gm_q, player_q


def _drain(q):
    frames = []
    while True:
        try:
            frames.append(q.get_nowait())
        except queue.Empty:
            return ''.join(f for f in frames if isinstance(f, str))


def test_saving_a_prep_scene_reaches_the_gm_but_not_a_player(wired):
    client, gm_q, player_q = wired
    created = client.post('/api/scenes', json={'name': 'Secret Lair Prep'})
    assert created.status_code in (200, 201)
    sid = created.get_json()['scene']['id']
    response = client.post(f'/api/scenes/{sid}/tokens', json={'name': 'Hidden Dragon'})
    assert response.status_code in (200, 201)
    gm, player = _drain(gm_q), _drain(player_q)
    assert 'event: scene_update' in gm and 'Hidden Dragon' in gm
    assert 'scene_update' not in player
    assert 'Secret Lair Prep' not in player and 'Hidden Dragon' not in player


def test_activating_a_scene_reaches_the_gm_but_not_a_player(wired):
    client, gm_q, player_q = wired
    sid = scenes.create_scene(CID, 'Throne Room')['id']
    response = client.post(f'/api/scenes/{sid}/activate', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    gm, player = _drain(gm_q), _drain(player_q)
    assert 'event: scene_activated' in gm and sid in gm
    assert 'event: scene_update' in gm
    assert 'scene_activated' not in player and 'scene_update' not in player
    assert sid not in player


@pytest.mark.parametrize('body', [
    {'kind': 'ping', 'x': 12, 'y': 34},
    {'kind': 'measure', 'x1': 0, 'y1': 0, 'x2': 60, 'y2': 0, 'label': '5 ft'},
    {'kind': 'measure', 'clear': True},
])
def test_a_beacon_reaches_the_gm_and_table_but_not_a_player(wired, body):
    client, gm_q, player_q = wired
    sid = scenes.create_scene(CID, 'Cavern')['id']
    _drain(gm_q), _drain(player_q)
    response = client.post(f'/api/scenes/{sid}/beacon', json=body)
    assert response.status_code == 200
    assert 'event: scene_beacon' in _drain(gm_q)
    assert _drain(player_q) == ''


def test_the_replay_buffer_holds_no_player_copy_of_a_scene_frame(wired):
    # A player reconnecting with Last-Event-ID is served from the buffer, so a
    # leak there would bypass the live filter entirely.
    client, _gm_q, _player_q = wired
    sid = scenes.create_scene(CID, 'Replay Check')['id']
    client.post(f'/api/scenes/{sid}/activate', json={})
    client.post(f'/api/scenes/{sid}/beacon', json={'kind': 'ping', 'x': 1, 'y': 1})
    scene_frames = [(gm_msg, player_msg) for _sid, gm_msg, player_msg in app._sse_buffer
                    if any(f'event: {name}' in gm_msg for name in SCENE_EVENTS)]
    assert {name for name in SCENE_EVENTS if any(f'event: {name}' in g for g, _ in scene_frames)} \
        == set(SCENE_EVENTS)
    assert all(player_msg is None for _gm, player_msg in scene_frames)


def test_every_scene_frame_broadcast_is_gm_only():
    """Structural guard: a new sse_broadcast of a scene_* event must pass the
    GM-only filter, or it goes to players by default. scene_mood is the one
    deliberate exception -- it is the player-facing ambience, not the map."""
    tree = ast.parse(Path(app.__file__).read_text(encoding='utf-8'))
    seen = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'sse_broadcast'):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)):
            continue
        name = node.args[0].value
        if not (isinstance(name, str) and name.startswith('scene_')) or name == 'scene_mood':
            continue
        flt = next((k.value for k in node.keywords if k.arg == 'player_filter'), None)
        seen.append(name)
        assert isinstance(flt, ast.Name) and flt.id == '_scene_gm_only', \
            f'{name} at app.py:{node.lineno} is not GM-only'
    assert set(seen) == set(SCENE_EVENTS)
    assert app._scene_gm_only({'anything': 1}) is None
