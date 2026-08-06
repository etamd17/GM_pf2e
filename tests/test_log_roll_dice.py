"""dice-batch-b Task 1: /api/log_roll's optional `dice` array.

The route forwards a client-supplied `dice` array (individual physical die
faces, e.g. [{sides:20,value:17}]) through to the `player_roll` SSE broadcast
so the GM's 3D overlay can animate the SAME dice the roller saw. Since the
route is unauthenticated for non-GM callers and forwards data straight into a
broadcast, `dice` must be validated defensively: absent/malformed input is
dropped entirely (never partially trusted), and every existing caller that
never sends `dice` must behave exactly as before (additive only).
"""
from __future__ import annotations

import pytest

import app


def _capture(monkeypatch):
    calls = []

    def fake(event_type, data, *, player_filter=None):
        calls.append({'event': event_type, 'data': data, 'filter': player_filter})

    monkeypatch.setattr(app, 'sse_broadcast', fake)
    return calls


def _post(client, **over):
    body = {'name': 'Go\'el', 'action': 'Athletics', 'result': '17', 'detail': 'd20(13) +4'}
    body.update(over)
    return client.post('/api/log_roll', json=body)


@pytest.fixture
def client():
    return app.app.test_client()


def test_dice_absent_is_unaffected(client, monkeypatch):
    """The overwhelming majority of existing callers never send `dice` at
    all -- the broadcast payload (and combat log entry) must not grow a
    `dice` key just because the route now knows how to handle one."""
    calls = _capture(monkeypatch)
    before = len(app.COMBAT_LOGS)
    r = _post(client)
    assert r.get_json()['success'] is True
    assert len(app.COMBAT_LOGS) == before + 1
    assert 'dice' not in app.COMBAT_LOGS[-1]
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert len(rolls) == 1
    assert 'dice' not in rolls[0]['data']


def test_valid_dice_is_passed_through(client, monkeypatch):
    calls = _capture(monkeypatch)
    dice = [{'sides': 20, 'value': 17}]
    r = _post(client, dice=dice)
    assert r.get_json()['success'] is True
    assert app.COMBAT_LOGS[-1]['dice'] == dice
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert rolls[0]['data']['dice'] == dice


def test_multi_die_dice_is_passed_through(client, monkeypatch):
    calls = _capture(monkeypatch)
    dice = [{'sides': 6, 'value': 4}, {'sides': 6, 'value': 2}]
    r = _post(client, action='Damage: Dagger', result='6', detail='2d6: [4, 2] = 6', dice=dice)
    assert r.get_json()['success'] is True
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert rolls[0]['data']['dice'] == dice


@pytest.mark.parametrize('bad_dice', [
    'not a list',
    123,
    {},
    [],
    [{'sides': 20}],                       # missing value
    [{'value': 5}],                        # missing sides
    [{'sides': '20', 'value': 5}],         # sides not an int
    [{'sides': 20, 'value': '5'}],         # value not an int
    [{'sides': True, 'value': 1}],         # bool is an int subclass -- must be excluded
    [{'sides': 20, 'value': 21}],          # value out of range for its own die
    [{'sides': 20, 'value': 0}],           # value below 1
    [{'sides': 1, 'value': 1}],            # sides too small
    [{'sides': 101, 'value': 1}],          # sides too large
    [{'sides': 20, 'value': 5}] * 21,      # too many dice (cap is 20)
    [{'sides': 20, 'value': 5}, 'garbage'],  # mixed valid + garbage entries
])
def test_malformed_dice_is_dropped_entirely(client, monkeypatch, bad_dice):
    """Malformed `dice` must not partially survive -- the roll itself still
    logs/broadcasts normally, just without any dice attached."""
    calls = _capture(monkeypatch)
    r = _post(client, dice=bad_dice)
    assert r.get_json()['success'] is True
    assert 'dice' not in app.COMBAT_LOGS[-1]
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert len(rolls) == 1
    assert 'dice' not in rolls[0]['data']


def test_exactly_twenty_dice_is_the_allowed_cap(client, monkeypatch):
    calls = _capture(monkeypatch)
    dice = [{'sides': 6, 'value': 3}] * 20
    r = _post(client, dice=dice)
    assert r.get_json()['success'] is True
    rolls = [c for c in calls if c['event'] == 'player_roll']
    assert rolls[0]['data']['dice'] == dice
