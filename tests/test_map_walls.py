"""Stage 4a of the map audit: walls you can actually close a room with.

This is a prerequisite, not a convenience. Round 5 chose reveal-by-room over
brush-painted fog, and region reveal is only as good as wall completeness -- a
one-pixel gap leaks the reveal into the next room. Walls were drawn one
disconnected drag per segment, each its own HTTP round-trip, full scene rewrite
and double SSE broadcast, with no snapping and no chaining. Closing a room was
tedious enough to skip, which is exactly the input region fog cannot tolerate.

So: click each corner, the run is chained, and the whole thing is saved in one
request with ends snapped to the grid.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'a1' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()


@pytest.fixture
def gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    return app.app.test_client()


@pytest.fixture
def scene(gm):
    return scenes.create_scene(CID, 'Rooms')


def _walls(sid):
    return scenes.load_scene(CID, sid).get('walls', [])


def _room(x, y, size=200):
    """Four corners, closed -- the shape region fog depends on."""
    return [
        {'x1': x, 'y1': y, 'x2': x + size, 'y2': y},
        {'x1': x + size, 'y1': y, 'x2': x + size, 'y2': y + size},
        {'x1': x + size, 'y1': y + size, 'x2': x, 'y2': y + size},
        {'x1': x, 'y1': y + size, 'x2': x, 'y2': y},
    ]


def test_a_closed_room_is_one_request(gm, scene):
    sid = scene['id']
    response = gm.post(f'/api/scenes/{sid}/elements',
                       json={'action': 'add_walls', 'kind': 'wall', 'segments': _room(100, 100)})
    assert response.status_code == 200
    walls = _walls(sid)
    assert len(walls) == 4
    # ...and it really closes: every corner is shared by exactly two segments.
    corners = {}
    for w in walls:
        for point in ((w['x1'], w['y1']), (w['x2'], w['y2'])):
            corners[point] = corners.get(point, 0) + 1
    assert sorted(corners.values()) == [2, 2, 2, 2], corners


def test_zero_length_segments_are_skipped_not_fatal(gm, scene):
    """A chained run picks these up from a double-click or a click that did not
    move. Failing the whole run would throw away a room the GM just drew."""
    sid = scene['id']
    segments = _room(0, 0) + [{'x1': 50, 'y1': 50, 'x2': 50, 'y2': 50}]
    response = gm.post(f'/api/scenes/{sid}/elements',
                       json={'action': 'add_walls', 'kind': 'wall', 'segments': segments})
    assert response.status_code == 200
    assert len(_walls(sid)) == 4, 'the degenerate one should be dropped, the rest kept'


def test_a_run_of_only_degenerate_segments_is_rejected(gm, scene):
    sid = scene['id']
    response = gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_walls', 'kind': 'wall',
        'segments': [{'x1': 10, 'y1': 10, 'x2': 11, 'y2': 10}]})
    assert response.status_code == 400
    assert _walls(sid) == []


@pytest.mark.parametrize('segments', [None, [], 'nope', [1, 2, 3], [{'x1': 'a'}]])
def test_malformed_runs_are_rejected(gm, scene, segments):
    response = gm.post(f'/api/scenes/{scene["id"]}/elements',
                       json={'action': 'add_walls', 'kind': 'wall', 'segments': segments})
    assert response.status_code == 400
    assert _walls(scene['id']) == []


def test_a_run_is_bounded(gm, scene):
    """An unbounded run would let one request write an arbitrarily large scene."""
    segments = [{'x1': i, 'y1': 0, 'x2': i + 50, 'y2': 0} for i in range(0, 300 * 60, 60)]
    response = gm.post(f'/api/scenes/{scene["id"]}/elements',
                       json={'action': 'add_walls', 'kind': 'wall', 'segments': segments})
    assert response.status_code == 400


def test_doors_can_be_chained_and_stay_secret(gm, scene):
    sid = scene['id']
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_walls', 'kind': 'door', 'secret': True, 'segments': _room(0, 0)})
    walls = _walls(sid)
    assert walls and all(w['kind'] == 'door' for w in walls)
    assert all(w['secret'] is True for w in walls)
    assert all(w['open'] is False for w in walls)


def test_a_chained_wall_is_never_secret(gm, scene):
    """'secret' is a door property; a plain wall carries it as False so the
    masked-secret-door payload stays shape-identical to a real wall."""
    sid = scene['id']
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_walls', 'kind': 'wall', 'secret': True, 'segments': _room(0, 0)})
    assert all(w['secret'] is False for w in _walls(sid))


def test_adding_walls_is_gm_only(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    scene = scenes.create_scene(CID, 'Guarded')
    with app.app.test_client() as client:
        response = client.post(f'/api/scenes/{scene["id"]}/elements',
                               json={'action': 'add_walls', 'kind': 'wall',
                                     'segments': _room(0, 0)})
    assert response.status_code == 403
    assert _walls(scene['id']) == []


# --- the client draws runs, not single drags ------------------------------

def test_wall_ends_snap_to_intersections_not_cell_centres():
    """Opposite of tokens, deliberately: a wall runs along the edge of a square,
    not through the middle of one."""
    body = _JS[_JS.index('function snapToIntersection('):]
    body = body[:body.index('\n    }')]
    assert 'Math.round((point.x - ox) / size)' in body
    assert 'size / 2' not in body, 'no half-cell shift -- that is the token rule'
    assert 'snap_to_grid' in body, 'must honour the scene setting'


def test_the_chain_is_committed_as_one_request():
    body = _JS[_JS.index('async function commitWallChain('):]
    body = body[:body.index('\n    }')]
    assert "action: 'add_walls'" in body
    assert 'segments: segments' in body


def test_a_chain_of_one_point_writes_nothing():
    body = _JS[_JS.index('async function commitWallChain('):]
    assert 'points.length < 2' in body[:400]


def test_escape_finishes_the_run_rather_than_discarding_it():
    handler = _JS[_JS.index("if (event.key === 'Escape')"):]
    assert 'commitWallChain' in handler[:300]


def test_switching_tools_saves_the_run():
    """Discarding a half-drawn room because the GM reached for another tool
    would be the worst possible answer."""
    body = _JS[_JS.index('function setActiveTool('):]
    assert 'commitWallChain' in body[:400]


def test_the_run_in_progress_is_drawn():
    body = _JS[_JS.index('function drawToolOverlay('):]
    assert 'wallChain.length' in body[:600], 'the GM should see the room taking shape'


def test_the_old_one_segment_per_drag_path_is_gone():
    """Left in place it would be dead code that still looks authoritative."""
    assert "action: 'add_wall', kind: finished.tool" not in _JS


def test_clicking_the_first_corner_closes_and_finishes():
    """The gesture every polygon tool uses. Without it the GM clicks the start
    and then has to press Escape -- and a room that merely LOOKS closed leaks
    the reveal into the next one, which is the failure region fog cannot take."""
    body = _JS[_JS.index("if ((activeTool === 'wall' || activeTool === 'door') && cfg.isGm)"):]
    body = body[:body.index('wallChain.push(snapped);')]
    assert 'wallChain.length >= 3' in body, 'a loop needs at least three corners'
    assert 'first.x' in body and 'first.y' in body
    assert 'commitWallChain' in body
