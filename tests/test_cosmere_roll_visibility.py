"""Cosmere roll visibility + table-facing roll notifications.

Players asked for two things on the sheet: a way to choose who sees a roll, and
an actual notification when others roll (the Cosmere player nav never subscribed
to player_roll, so players saw nothing).

Visibility modes:
  * group  — the whole table sees it (player_roll broadcast to everyone) [default]
  * gm     — whispered to the GM only (broadcast carries a player_filter that
             drops it for player connections; the GM-facing detail is tagged
             "[GM only]"); still logged to the GM combat feed
  * private — never leaves the player's device (the client skips posting; a
             'private' that still reaches the server is a no-op safeguard)
"""
from __future__ import annotations

import pathlib

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'cd' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Shallan', 'owner_user_id': 'u1',
           'build': {'name': 'Shallan', 'level': 2, 'path': 'scholar',
                     'attributes': {'int': 2}, 'skills': {}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def _capture(monkeypatch):
    calls = []

    def fake(event_type, data, *, player_filter=None):
        calls.append({'event': event_type, 'data': data, 'filter': player_filter})

    monkeypatch.setattr(app, 'sse_broadcast', fake)
    return calls


def _post(pid, vis, **over):
    body = {'pid': pid, 'action': 'Lore test', 'result': '17', 'detail': 'd20(13) +4', 'visibility': vis}
    body.update(over)
    return app.app.test_client().post('/api/cosmere/roll', json=body)


def test_group_roll_broadcasts_to_everyone(pc, monkeypatch):
    calls = _capture(monkeypatch)
    before = len(app.COMBAT_LOGS)
    assert _post(pc, 'group').get_json()['ok']
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert len(rolls) == 1 and rolls[0]['filter'] is None      # no filter -> all subscribers
    assert len(app.COMBAT_LOGS) == before + 1


def test_gm_roll_is_filtered_from_players(pc, monkeypatch):
    calls = _capture(monkeypatch)
    before = len(app.COMBAT_LOGS)
    assert _post(pc, 'gm').get_json()['ok']
    roll = [c for c in calls if c['event'] == 'player_roll'][0]
    # GM receives `data`; the player filter drops the message for every player.
    assert roll['filter'] is not None
    assert roll['filter']({'name': 'anyone', 'detail': 'x'}) is None
    # The GM's copy is tagged so the feed shows it was a whisper, and still logged.
    assert roll['data']['detail'].startswith('[GM only]')
    assert len(app.COMBAT_LOGS) == before + 1


def test_private_roll_is_not_sent_or_logged(pc, monkeypatch):
    calls = _capture(monkeypatch)
    before = len(app.COMBAT_LOGS)
    assert _post(pc, 'private').get_json()['ok']
    assert not [c for c in calls if c['event'] == 'player_roll']   # nothing broadcast
    assert len(app.COMBAT_LOGS) == before                          # nothing logged


def test_unknown_visibility_defaults_to_group(pc, monkeypatch):
    calls = _capture(monkeypatch)
    assert _post(pc, 'bogus').get_json()['ok']
    roll = [c for c in calls if c['event'] == 'player_roll'][0]
    assert roll['filter'] is None


def test_sheet_has_visibility_control():
    h = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()
    assert 'cs-vis' in h
    for mode in ('group', 'gm', 'private'):
        assert "cosVis('%s')" % mode in h
    assert "vis:'group'" in h                       # default = table-visible
    assert "if(mods.vis==='private') return" in h    # client skips posting private


def test_cosmere_nav_shows_incoming_rolls():
    h = (_REPO / 'templates' / '_cosmere_player_nav.html').read_text()
    assert 'cos-roll-toast-container' in h
    assert "appSSE('player_roll'" in h
    assert 'roll.name === CHAR' in h                 # don't toast my own roll
