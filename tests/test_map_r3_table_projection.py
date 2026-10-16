"""MAP-R3: the shared table screen gets a table projection, never the GM payload.

/map/table runs in a GM session (that is how the TV is authenticated), so it
used to receive exactly what the GM's own window receives: hidden tokens,
GM-only lights and templates, exact NPC HP, prep scenes. It now has a server-side
audience of its own:

- the SSE stream from /map/table asks for ``audience=table``; a GM stream with
  it is sent table frames, and keeps its GM authority for revocation;
- scene_update reaches it only for the scene on the table, as the player
  projection; beacons only for that scene; scene_activated so it can follow;
- every other event gives it the player frame;
- it reads its scene from /api/scenes/table, the same projection.
"""

from __future__ import annotations

import json
import queue
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app
from core import scenes, storage

CID = 'a' * 32
ROOT = Path(app.__file__).resolve().parent


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_scene_character_records', lambda _cid: {})
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [
        SimpleNamespace(instance_id='ogre-1', name='Ogre', is_pc=False, system='pf2e', current_hp=12, hp=40,
                        conditions={}, visible_to_players=True),
    ])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setattr(app, '_ensure_sse_keepalive', lambda: None)
    gm_q, table_q, player_q = queue.Queue(), queue.Queue(), queue.Queue()
    monkeypatch.setattr(app, '_sse_subscribers', [
        (gm_q, True, CID, None),
        (table_q, True, CID, None, 'table'),
        (player_q, False, CID, 'player-1'),
    ])
    monkeypatch.setattr(app, '_sse_buffer', [])
    monkeypatch.setattr(app, '_sse_event_seq', 0)
    monkeypatch.setattr(app, '_sse_event_campaigns', {})
    monkeypatch.setattr(app, '_sse_table_frames', {}, raising=False)
    monkeypatch.setattr(app, '_sse_last_cleanup', time.time())
    with app.app.test_client() as client:
        yield SimpleNamespace(client=client, gm=gm_q, table=table_q, player=player_q)


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return ''.join(f for f in out if isinstance(f, str))


def drain_all(w):
    return drain(w.gm), drain(w.table), drain(w.player)


def frames(text, event):
    out = []
    for block in text.split('\n\n'):
        if f'event: {event}\n' in block + '\n':
            data = [line[6:] for line in block.split('\n') if line.startswith('data: ')]
            out.append(json.loads(data[0]))
    return out


def table_scene(w, name='On the Table'):
    scene = scenes.create_scene(CID, name)
    scenes.add_token(scene, name='Hero')
    scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    scenes.add_token(scene, name='Lurking Assassin', visible_to_players=False)
    scenes.save_scene(CID, scene)
    response = w.client.post(f'/api/scenes/{scene["id"]}/activate', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    drain_all(w)
    return scene['id']


# --- live frames -----------------------------------------------------------------------------

def test_table_scene_save_sends_the_table_projection(wired):
    w = wired
    sid = table_scene(w)
    response = w.client.post(f'/api/scenes/{sid}/tokens', json={'name': 'Late Arrival'})
    assert response.status_code in (200, 201)
    gm, table, player = drain_all(w)
    [gm_scene] = frames(gm, 'scene_update')
    [tv_scene] = frames(table, 'scene_update')
    assert 'Lurking Assassin' in [t['name'] for t in gm_scene['tokens']]
    names = [t['name'] for t in tv_scene['tokens']]
    assert 'Late Arrival' in names and 'Hero' in names
    assert 'Lurking Assassin' not in names and 'Lurking Assassin' not in table
    ogre = next(t for t in tv_scene['tokens'] if t['name'] == 'Ogre')
    assert ogre['live']['hp_status'] and 'current_hp' not in ogre['live'] and 'max_hp' not in ogre['live']
    assert '"current_hp"' not in table
    assert player == ''


def test_a_prep_scene_save_reaches_the_gm_but_not_the_table(wired):
    w = wired
    table_scene(w)
    prep = scenes.create_scene(CID, 'Secret Prep')
    response = w.client.post(f'/api/scenes/{prep["id"]}/tokens', json={'name': 'Surprise Dragon'})
    assert response.status_code in (200, 201)
    gm, table, player = drain_all(w)
    assert 'Surprise Dragon' in gm
    assert table == '' and player == ''


def test_activating_reaches_the_table_so_it_can_follow(wired):
    w = wired
    sid = table_scene(w)
    other = scenes.create_scene(CID, 'Next Room')
    w.client.post(f'/api/scenes/{other["id"]}/activate', json={})
    gm, table, player = drain_all(w)
    assert frames(table, 'scene_activated') == [{'scene_id': other['id']}]
    [tv_scene] = frames(table, 'scene_update')
    assert tv_scene['id'] == other['id']
    assert player == ''
    assert sid != other['id']


def test_beacons_reach_the_table_only_on_the_table_scene(wired):
    w = wired
    sid = table_scene(w)
    prep = scenes.create_scene(CID, 'Prep')
    w.client.post(f'/api/scenes/{prep["id"]}/beacon', json={'kind': 'ping', 'x': 1, 'y': 1})
    gm, table, player = drain_all(w)
    assert 'scene_beacon' in gm and table == '' and player == ''
    w.client.post(f'/api/scenes/{sid}/beacon', json={'kind': 'measure', 'x1': 0, 'y1': 0, 'x2': 70, 'y2': 0,
                                                    'label': '5 ft'})
    gm, table, player = drain_all(w)
    assert frames(table, 'scene_beacon')[0]['label'] == '5 ft' and player == ''


def test_every_other_event_gives_the_table_the_player_frame_not_the_gm_one(wired):
    w = wired
    app.sse_broadcast('encounter_update', {'secret': 'gm-only', 'shared': 1},
                      player_filter=lambda d: {'shared': d['shared']}, campaign_id=CID)
    app.sse_broadcast('gm_secret_roll', {'total': 17}, player_filter=lambda _d: None, campaign_id=CID)
    gm, table, player = drain_all(w)
    assert 'gm-only' in gm and 'gm_secret_roll' in gm
    assert frames(table, 'encounter_update') == [{'shared': 1}] and 'gm-only' not in table
    assert 'gm_secret_roll' not in table
    assert table == player


# --- the stream itself -----------------------------------------------------------------------

def _open(monkeypatch, *, audience=None, is_gm=True, last_event_id=0):
    monkeypatch.setattr(app, '_is_gm', lambda: is_gm)
    params = [f'last_event_id={last_event_id}']
    if audience:
        params.append(f'audience={audience}')
    with app.app.test_request_context('/api/events?' + '&'.join(params)):
        return app.sse_stream()


def _first_frames(response, n):
    it = iter(response.response)
    out = [next(it) for _ in range(n)]
    response.response.close()
    return out


def test_only_a_gm_stream_can_take_the_table_audience(wired, monkeypatch):
    monkeypatch.setattr(app, '_sse_subscribers', [])
    _open(monkeypatch, audience='table', is_gm=True)
    _open(monkeypatch, audience='table', is_gm=False)
    _open(monkeypatch, audience=None, is_gm=True)
    entries = app._sse_subscribers
    assert [app._sse_entry_is_table(e) for e in entries] == [True, False, False]
    assert entries[0][1] is True        # keeps GM authority, so GM revocation reaches it


def test_a_reconnecting_table_replays_table_frames(wired, monkeypatch):
    w = wired
    sid = table_scene(w)
    w.client.post(f'/api/scenes/{sid}/tokens', json={'name': 'Replayed'})
    app.sse_broadcast('encounter_update', {'secret': 'gm-only'}, player_filter=lambda d: {'ok': 1},
                      campaign_id=CID)
    drain_all(w)
    monkeypatch.setattr(app, '_sse_subscribers', [])
    replay = ''.join(_first_frames(_open(monkeypatch, audience='table', last_event_id=1),
                                   1 + len(app._sse_buffer) - 1))
    assert 'Replayed' in replay and 'Lurking Assassin' not in replay and 'gm-only' not in replay
    gm_replay = ''.join(_first_frames(_open(monkeypatch, last_event_id=1), 1 + len(app._sse_buffer) - 1))
    assert 'Lurking Assassin' in gm_replay and 'gm-only' in gm_replay


def test_legacy_gm_logout_closes_the_table_stream_too(wired, monkeypatch):
    closed = app._close_legacy_gm_sse_subscribers()
    assert closed == 2      # the GM window and the TV; the player stream is account-backed
    assert all(not app._sse_entry_is_table(e) for e in app._sse_subscribers)


# --- the TV's own read -----------------------------------------------------------------------

def test_the_table_endpoint_serves_the_table_projection(wired):
    w = wired
    assert w.client.get('/api/scenes/table').status_code == 404
    sid = table_scene(w)
    body = w.client.get('/api/scenes/table').get_json()
    assert body['scene']['id'] == sid
    text = json.dumps(body)
    assert 'Lurking Assassin' not in text and '"current_hp"' not in text and '"max_hp"' not in text
    # the GM's own read still has everything
    assert 'Lurking Assassin' in w.client.get(f'/api/scenes/{sid}').get_data(as_text=True)


def test_a_player_cannot_read_the_table_endpoint(tmp_path, monkeypatch):
    # The real _is_gm, as in test_scenes.real_auth_player_client: legacy mode with
    # a GM password set and a session holding only a player name.
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_account_mode', lambda: False)
    monkeypatch.setattr(app, 'GM_PASSWORD', 'not-the-player')
    scene = scenes.create_scene(CID, 'On the TV')
    scenes.set_table_scene(CID, scene['id'])
    with app.app.test_client() as client:
        with client.session_transaction() as sess:
            sess['player_name'] = 'Kyle'
            sess.pop('gm_authenticated', None)
        assert client.get('/api/scenes/table').status_code == 403


def test_the_table_page_asks_for_the_table_audience_and_reads_the_table_endpoint():
    hub = (ROOT / 'templates' / '_sse_hub.html').read_text(encoding='utf-8')
    assert "location.pathname === '/map/table'" in hub and "audience=table" in hub
    script = (ROOT / 'static' / 'js' / 'map.js').read_text(encoding='utf-8')
    assert "'/api/scenes/table'" in script
