"""Stage 4c: reveal a room with one click.

Round 5 chose reveal-by-room over brush painting, and this is why wall chaining
(4a) had to land first: the reveal floods until it hits a wall, so a room with a
one-pixel gap leaks into the next one. Walls used to be disconnected drags with
no snapping, which produced exactly that.

The store changed too. Brushing appended arcs to an operation log that was
replayed in full every frame and only ever grew -- capped at 2000, with no
compaction, so a long session paid more per frame than a short one. Revealed
CELLS are bounded by the grid instead: re-revealing a room is idempotent rather
than another thirty entries, and the render cost is flat.

The old operations are still replayed so scenes fogged before this change do not
suddenly go dark. Nothing writes them any more.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'b2' * 16
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
    return scenes.create_scene(CID, 'Dungeon')


def _fog(sid):
    return scenes.load_scene(CID, sid).get('fog', {})


def _reveal(gm, sid, cells, mode='reveal'):
    return gm.post(f'/api/scenes/{sid}/elements',
                   json={'action': 'fog_region', 'mode': mode, 'cells': cells})


def test_revealing_a_region_stores_cells(gm, scene):
    sid = scene['id']
    assert _reveal(gm, sid, ['0,0', '1,0', '0,1']).status_code == 200
    assert sorted(_fog(sid)['revealed_cells']) == ['0,0', '0,1', '1,0']


def test_revealing_the_same_room_twice_is_idempotent(gm, scene):
    """The brush log grew by another thirty entries every time. A cell set does
    not, which is the point of changing the store."""
    sid = scene['id']
    cells = ['%d,%d' % (c, r) for c in range(6) for r in range(6)]
    _reveal(gm, sid, cells)
    first = _fog(sid)['revealed_cells']
    _reveal(gm, sid, cells)
    assert _fog(sid)['revealed_cells'] == first


def test_hiding_removes_only_those_cells(gm, scene):
    sid = scene['id']
    _reveal(gm, sid, ['0,0', '1,0', '2,0'])
    _reveal(gm, sid, ['1,0'], mode='hide')
    assert sorted(_fog(sid)['revealed_cells']) == ['0,0', '2,0']


def test_reset_clears_both_stores(gm, scene):
    """A scene carrying legacy brush arcs must come fully clean, or resetting
    would appear to do nothing on exactly the scenes that need it."""
    sid = scene['id']
    _reveal(gm, sid, ['0,0'])
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'fog_ops',
        'operations': [{'mode': 'reveal', 'x': 10, 'y': 10, 'radius': 50}]})
    assert _fog(sid)['operations'] and _fog(sid)['revealed_cells']
    gm.post(f'/api/scenes/{sid}/elements', json={'action': 'fog_reset'})
    assert _fog(sid)['operations'] == []
    assert _fog(sid)['revealed_cells'] == []


@pytest.mark.parametrize('cells', [None, [], 'nope', [123], ['1'], ['a,b'], ['1,2,3']])
def test_malformed_cells_are_rejected(gm, scene, cells):
    assert _reveal(gm, scene['id'], cells).status_code == 400
    assert _fog(scene['id']).get('revealed_cells', []) == []


def test_a_region_is_bounded(gm, scene):
    assert _reveal(gm, scene['id'], ['%d,0' % i for i in range(20001)]).status_code == 400


def test_revealing_is_gm_only(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    scene = scenes.create_scene(CID, 'Guarded')
    with app.app.test_client() as client:
        response = client.post(f'/api/scenes/{scene["id"]}/elements',
                               json={'action': 'fog_region', 'cells': ['0,0']})
    assert response.status_code == 403


def test_old_scenes_backfill_the_new_field(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    scene = scenes.create_scene(CID, 'Legacy')
    raw = storage.load_json(storage.scene_file(CID, scene['id']))
    # A scene written before region fog existed: fog with arcs and no cell set.
    raw['fog'] = {'enabled': True,
                  'operations': [{'mode': 'reveal', 'x': 10, 'y': 10, 'radius': 40}]}
    storage.atomic_write_json(storage.scene_file(CID, scene['id']), raw, indent=2)
    loaded = scenes.load_scene(CID, scene['id'])
    assert loaded['fog']['revealed_cells'] == []
    assert loaded['fog']['operations'], 'the old strokes must survive the backfill'


# --- the client-side flood --------------------------------------------------

def test_the_flood_stops_at_walls():
    body = _JS[_JS.index('function floodRegion('):]
    body = body[:body.index('\n    }')]
    assert 'edgeBlocked(' in body, 'the reveal must stop at walls or rooms leak'
    assert 'queue' in body and 'seen' in body


def test_an_open_door_does_not_stop_the_reveal():
    """Opening a door is exactly the act of connecting two rooms."""
    body = _JS[_JS.index('function blockingWallList('):]
    body = body[:body.index('\n    }')]
    assert "w.kind !== 'door' || !w.open" in body


def test_the_flood_is_bounded_by_the_scene():
    body = _JS[_JS.index('function floodRegion('):]
    body = body[:body.index('\n    }')]
    assert 'maxCol' in body and 'maxRow' in body, 'an unwalled map must not run away'
    assert '20000' in body, 'and a pathological grid must stop rather than hang'


def test_the_fog_tools_are_a_click_not_a_drag():
    handler = _JS[_JS.index("if ((activeTool === 'fog-reveal' || activeTool === 'fog-hide') && cfg.isGm)"):]
    handler = handler[:handler.index('\n        }')]
    assert 'floodRegion(point)' in handler
    assert "action: 'fog_region'" in handler
    assert 'setPointerCapture' not in handler, 'no longer a drag'


def test_legacy_brush_strokes_are_still_rendered():
    """Nothing writes them, but a scene fogged before this change must not go
    dark the moment it is opened."""
    body = _JS[_JS.index('function drawFogOverlay('):]
    body = body[:body.index('\n    }')]
    assert 'revealed_cells' in body
    assert 'scene.fog.operations' in body


def test_the_flood_is_tested_centre_to_centre_not_along_the_edge():
    """The bug this exists to prevent, found by driving a real two-room map.

    segmentsCross uses strict sign changes, so two COLLINEAR segments never
    register as crossing. A wall snapped to the grid -- which is exactly what
    stage 4a's snapping guarantees -- lies along the cell edge it is meant to
    block, so testing the edge missed every grid-aligned wall and the reveal
    flooded the entire map (124 cells instead of 20).

    A centre-to-centre segment is perpendicular to such a wall, so the crossing
    is unambiguous. Asserted structurally because the failure is silent: the
    code runs, returns a plausible answer, and quietly reveals the dungeon.
    """
    body = _JS[_JS.index('function edgeBlocked('):]
    body = body[:body.index('\n    }')]
    assert '+ 0.5) * g.size' in body, 'must start from the cell CENTRE'
    assert 'dcol * g.size' in body and 'drow * g.size' in body, (
        'and step to the neighbouring centre')
    assert 'segmentsCross(from, to' in body
