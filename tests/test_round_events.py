"""Round-events lane backend (tracker feature 7, task 1).

Spec: docs/superpowers/specs/2026-07-04-round-events-lane-design.md
Plan: docs/superpowers/plans/2026-07-04-round-events-lane.md (Task 1)

A GM-authored timeline of round-triggered events: each fires once combat
reaches its round (with optional every-N-rounds repeat), executing an
optional payload (conditions and/or damage/heal) through the EXISTING
mutation internals (`_apply_condition_change` / `_apply_hp_delta`) so
sheet-sync/SSE/combat-log side effects are inherited, not reimplemented.

These tests exercise the engine directly (`app._fire_round_events`,
`app.ROUND_EVENTS`) rather than going through `cycle_turn`'s full turn-order
machinery, so they isolate the firing/idempotence/payload contract from
turn-advance mechanics (which are already covered elsewhere). A couple of
end-to-end tests drive `cycle_turn` itself to prove the hook fires at the
right site with the right (settled, once-only) round number.
"""
from __future__ import annotations

import os

import json

import pytest

import app as app_module


class _Combatant:
    """Minimal monster-shaped stand-in carrying only what
    `_apply_condition_change` / `_apply_hp_delta` / `_fire_round_events`
    touch. Mirrors the `_PF2ePC`-style stubs in test_tracker_visual_payload.py
    and test_dying_state.py's fixture-backed PCs, but lighter since round
    events only need HP + conditions, not full sheet derivation."""

    def __init__(self, name, hp=20, is_pc=False, instance_id=None):
        self.instance_id = instance_id or (name + '-1')
        self.name = name
        self.is_pc = is_pc
        self.system = 'pf2e'
        self.hp = hp
        self.current_hp = hp
        self.conditions = {}
        self.condition_expiry = {}
        self.file_path = name + '.json'
        self.delaying = False
        self.initiative = 10   # save_encounter serializes this directly
        # Monster-only fields some tracker code paths getattr() defensively.
        self.immunities = []
        self.resistances = []
        self.weaknesses = []


def _real_monster(name, hp=30, instance_id=None):
    """A real `app.Monster` (not the lighter `_Combatant` stub above), needed
    for tests that exercise `_do_persist_encounter_state` / `_get_tracker_state`
    -- both read monster-only computed properties (ac/fort/ref/will/
    perception/initiative) that `_Combatant` doesn't model."""
    m = app_module.Monster({'name': name, 'system': {'attributes': {
        'hp': {'max': hp, 'value': hp}, 'ac': {'value': 15},
    }}})
    m.instance_id = instance_id or (name + '-1')
    return m


@pytest.fixture
def encounter(monkeypatch):
    """A two-combatant live encounter with persistence/broadcast stubbed out
    (same pattern as test_tracker_visual_payload.py) so tests assert on
    in-memory state without touching disk or SSE subscribers."""
    persisted = []
    broadcasts = []
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: persisted.append(1))
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: broadcasts.append(1))
    goel = _Combatant('Goel', hp=30)
    ally = _Combatant('Ally', hp=20)
    app_module.ACTIVE_ENCOUNTER[:] = [goel, ally]
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module.ROUND_EVENTS[:] = []
    yield {'goel': goel, 'ally': ally, 'persisted': persisted, 'broadcasts': broadcasts}
    app_module.ACTIVE_ENCOUNTER[:] = []
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module.ROUND_EVENTS[:] = []


def _event(**kwargs):
    ev = {
        'id': 'ev1',
        'round': 3,
        'repeat_every': None,
        'title': 'The ceiling groans',
        'text': 'Dust rains down.',
        'show_on_table': False,
        'payload': None,
        'last_fired_round': None,
    }
    ev.update(kwargs)
    return ev


# ---------------------------------------------------------------------------
# Fire-at-round / repeat-every / idempotence
# ---------------------------------------------------------------------------

def test_fires_at_its_round(encounter):
    ev = _event(round=3)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert ev['last_fired_round'] is None
    app_module._fire_round_events(3)
    assert ev['last_fired_round'] == 3


def test_does_not_fire_before_its_round(encounter):
    ev = _event(round=5)
    app_module.ROUND_EVENTS.append(ev)
    for r in (1, 2, 3, 4):
        app_module._fire_round_events(r)
    assert ev['last_fired_round'] is None


def test_repeat_every_fires_at_n_and_n_plus_k(encounter):
    ev = _event(round=2, repeat_every=2)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert ev['last_fired_round'] == 2
    app_module._fire_round_events(3)
    assert ev['last_fired_round'] == 2  # not due yet
    app_module._fire_round_events(4)
    assert ev['last_fired_round'] == 4
    app_module._fire_round_events(6)
    assert ev['last_fired_round'] == 6


def test_repeat_every_does_not_fire_off_cadence(encounter):
    ev = _event(round=2, repeat_every=3)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(4)  # 4-2=2, not a multiple of 3
    assert ev['last_fired_round'] is None
    app_module._fire_round_events(5)  # 5-2=3, due
    assert ev['last_fired_round'] == 5


def test_last_fired_round_idempotence_same_round_never_refires(encounter):
    """Re-cycling through the same round (e.g. re-entering the engine call
    twice for round 3 without ever leaving it) must not fire twice."""
    ev = _event(round=3)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(3)
    assert ev['last_fired_round'] == 3
    fire_count = {'n': 0}
    orig = app_module._fire_round_event_payload

    def _count(*a, **k):
        fire_count['n'] += 1
        return orig(*a, **k)

    app_module._fire_round_event_payload = _count
    try:
        app_module._fire_round_events(3)
    finally:
        app_module._fire_round_event_payload = orig
    assert fire_count['n'] == 0


def test_backward_then_forward_cycle_does_not_refire(encounter):
    """Cycling backward past a fired round then forward again through the
    same round must not re-fire (last_fired_round only ever advances)."""
    ev = _event(round=3)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(3)
    assert ev['last_fired_round'] == 3
    # Simulate cycling backward (ROUND_NUMBER drops via /prev, no fire call
    # happens on the way down -- _fire_round_events is only ever invoked on
    # forward advance) then forward again to round 3.
    app_module._fire_round_events(3)
    assert ev['last_fired_round'] == 3


def test_backward_cycle_never_unfires(encounter):
    """last_fired_round only advances -- there is no code path that resets
    or decrements it, so a GM cycling backward can't accidentally "un-fire"
    an event and have it go off again on replay."""
    ev = _event(round=3, last_fired_round=3)
    app_module.ROUND_EVENTS.append(ev)
    # Even asking the engine to fire for an earlier round than last_fired
    # must not touch last_fired_round backward.
    app_module._fire_round_events(2)
    assert ev['last_fired_round'] == 3


# ---------------------------------------------------------------------------
# Payload execution: conditions
# ---------------------------------------------------------------------------

def test_condition_payload_applies_via_real_helper(encounter):
    goel = encounter['goel']
    ev = _event(round=1, payload={
        'conditions': [{'target_ids': [goel.instance_id], 'condition': 'frightened', 'value': 2}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert goel.conditions.get('frightened') == 2


def test_condition_payload_all_targeting(encounter):
    goel, ally = encounter['goel'], encounter['ally']
    ev = _event(round=1, payload={
        'conditions': [{'target_ids': 'all', 'condition': 'off_guard', 'value': 1}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert goel.conditions.get('off_guard') is True
    assert ally.conditions.get('off_guard') is True


def test_condition_payload_unknown_target_skipped_silently(encounter):
    ev = _event(round=1, payload={
        'conditions': [{'target_ids': ['does-not-exist'], 'condition': 'frightened', 'value': 1}],
    })
    app_module.ROUND_EVENTS.append(ev)
    # Must not raise.
    app_module._fire_round_events(1)
    assert ev['last_fired_round'] == 1


# ---------------------------------------------------------------------------
# Payload execution: damage / heal
# ---------------------------------------------------------------------------

def test_damage_payload_rolls_dice_and_applies_hp_within_bounds(encounter):
    goel = encounter['goel']
    ev = _event(round=1, payload={
        'damage': [{'target_ids': [goel.instance_id], 'dice': '2d6', 'kind': 'damage'}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    lost = 30 - goel.current_hp
    assert 2 <= lost <= 12


def test_heal_payload_applies_via_hp_helper(encounter):
    goel = encounter['goel']
    goel.current_hp = 10
    ev = _event(round=1, payload={
        'damage': [{'target_ids': [goel.instance_id], 'dice': '1d4+1', 'kind': 'heal'}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    gained = goel.current_hp - 10
    assert 2 <= gained <= 5


def test_damage_payload_all_targeting(encounter):
    goel, ally = encounter['goel'], encounter['ally']
    ev = _event(round=1, payload={
        'damage': [{'target_ids': 'all', 'dice': '1d1', 'kind': 'damage'}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert goel.current_hp == 29
    assert ally.current_hp == 19


def test_combined_condition_and_damage_payload(encounter):
    goel = encounter['goel']
    ev = _event(round=1, payload={
        'conditions': [{'target_ids': [goel.instance_id], 'condition': 'frightened', 'value': 1}],
        'damage': [{'target_ids': [goel.instance_id], 'dice': '1d1', 'kind': 'damage'}],
    })
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert goel.conditions.get('frightened') == 1
    assert goel.current_hp == 29


# ---------------------------------------------------------------------------
# Combat log + SSE
# ---------------------------------------------------------------------------

def test_fire_appends_combat_log_entry(encounter, monkeypatch):
    logged = []
    monkeypatch.setattr(app_module, '_combat_log', lambda msg, kind='info': logged.append((msg, kind)))
    ev = _event(round=1, title='The ceiling groans')
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert any('ceiling groans' in msg for msg, _kind in logged)


def test_sse_frame_gm_always_gets_title_and_text(encounter, monkeypatch):
    sent = {}

    def _fake_broadcast(event_type, data, *, player_filter=None):
        sent['event_type'] = event_type
        sent['gm'] = data
        sent['player_filter'] = player_filter

    monkeypatch.setattr(app_module, 'sse_broadcast', _fake_broadcast)
    ev = _event(round=1, title='Hidden GM note', text='Only the GM sees this detail.',
                show_on_table=False)
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    assert sent['event_type'] == 'round_event'
    assert sent['gm']['title'] == 'Hidden GM note'
    assert sent['gm']['text'] == 'Only the GM sees this detail.'
    assert sent['gm']['show_on_table'] is False


def test_sse_player_frame_omitted_when_show_on_table_false(encounter, monkeypatch):
    sent = {}

    def _fake_broadcast(event_type, data, *, player_filter=None):
        sent['player_filter'] = player_filter
        sent['gm'] = data

    monkeypatch.setattr(app_module, 'sse_broadcast', _fake_broadcast)
    ev = _event(round=1, show_on_table=False, title='t', text='x')
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    filtered = sent['player_filter'](dict(sent['gm']))
    assert filtered is None


def test_sse_player_frame_present_when_show_on_table_true(encounter, monkeypatch):
    sent = {}

    def _fake_broadcast(event_type, data, *, player_filter=None):
        sent['player_filter'] = player_filter
        sent['gm'] = data

    monkeypatch.setattr(app_module, 'sse_broadcast', _fake_broadcast)
    ev = _event(round=1, show_on_table=True, title='The ceiling groans', text='Dust falls.')
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(1)
    filtered = sent['player_filter'](dict(sent['gm']))
    assert filtered is not None
    assert filtered['title'] == 'The ceiling groans'
    assert filtered['text'] == 'Dust falls.'
    assert filtered['round'] == 1
    # Payload mechanics must never leak to players -- only title/text/round.
    assert 'payload' not in filtered
    assert 'show_on_table' not in filtered


# ---------------------------------------------------------------------------
# Persistence round-trip
# ---------------------------------------------------------------------------

def test_persistence_round_trip_via_autosave(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ENCOUNTER_DIR', str(tmp_path))
    goel = _real_monster('Goel', hp=30)
    app_module.ACTIVE_ENCOUNTER[:] = [goel]
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 5
    ev = _event(round=3, last_fired_round=3)
    app_module.ROUND_EVENTS[:] = [ev]
    try:
        app_module._do_persist_encounter_state()

        # Clear the in-memory globals to prove rehydrate is what restores them,
        # not leftover state.
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []
        app_module.ROUND_NUMBER = 1
        app_module.TURN_INDEX = 0

        app_module._restore_encounter_autosave()

        assert app_module.ROUND_NUMBER == 5
        assert len(app_module.ROUND_EVENTS) == 1
        restored = app_module.ROUND_EVENTS[0]
        assert restored['id'] == ev['id']
        assert restored['round'] == 3
        assert restored['last_fired_round'] == 3
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []
        app_module.ROUND_NUMBER = 1
        app_module.TURN_INDEX = 0


def test_persistence_writes_round_events_to_autosave_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, 'ENCOUNTER_DIR', str(tmp_path))
    goel = _real_monster('Goel', hp=30)
    app_module.ACTIVE_ENCOUNTER[:] = [goel]
    ev = _event(round=4)
    app_module.ROUND_EVENTS[:] = [ev]
    try:
        app_module._do_persist_encounter_state()
        raw = json.loads((tmp_path / '_autosave.json').read_text())
        assert 'round_events' in raw
        assert raw['round_events'][0]['id'] == ev['id']
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []


# ---------------------------------------------------------------------------
# Encounter clear wipes events
# ---------------------------------------------------------------------------

def test_clear_encounter_wipes_round_events(encounter):
    app_module.ROUND_EVENTS.append(_event(round=1))
    assert len(app_module.ROUND_EVENTS) == 1
    client = app_module.app.test_client()
    resp = client.post('/api/clear_encounter')
    assert resp.status_code in (200, 302)
    assert app_module.ROUND_EVENTS == []


# ---------------------------------------------------------------------------
# End-to-end: cycle_turn hook fires exactly once, with the settled round
# ---------------------------------------------------------------------------

def test_cycle_turn_fires_event_once_on_forward_round_advance(monkeypatch):
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    goel = _Combatant('Goel', hp=30)
    ally = _Combatant('Ally', hp=20)
    app_module.ACTIVE_ENCOUNTER[:] = [goel, ally]
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    app_module.ROUND_EVENTS[:] = []
    fire_calls = []
    orig_fire = app_module._fire_round_events

    def _tracking_fire(new_round):
        fire_calls.append(new_round)
        return orig_fire(new_round)

    monkeypatch.setattr(app_module, '_fire_round_events', _tracking_fire)
    ev = _event(round=2)
    app_module.ROUND_EVENTS.append(ev)
    try:
        client = app_module.app.test_client()
        # Goel -> Ally: still round 1, no fire.
        client.post('/api/cycle_turn/next')
        assert fire_calls == []
        assert ev['last_fired_round'] is None
        # Ally -> Goel: wraps back to index 0, round advances to 2 -> fires once.
        client.post('/api/cycle_turn/next')
        assert fire_calls == [2]
        assert ev['last_fired_round'] == 2
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []
        app_module.TURN_INDEX = 0
        app_module.ROUND_NUMBER = 1


def test_cycle_turn_backward_then_forward_does_not_refire(monkeypatch):
    """Full end-to-end proof of the spec's idempotence guarantee: cycling
    forward into round 2 (fires), backward to round 1 (no fire call at all
    -- /prev never invokes the engine), then forward again into round 2
    must NOT refire."""
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    goel = _Combatant('Goel', hp=30)
    ally = _Combatant('Ally', hp=20)
    app_module.ACTIVE_ENCOUNTER[:] = [goel, ally]
    app_module.TURN_INDEX = 0
    app_module.ROUND_NUMBER = 1
    ev = _event(round=2)
    app_module.ROUND_EVENTS[:] = [ev]
    try:
        client = app_module.app.test_client()
        client.post('/api/cycle_turn/next')  # Goel -> Ally (round 1)
        client.post('/api/cycle_turn/next')  # Ally -> Goel (round 2, fires)
        assert ev['last_fired_round'] == 2
        client.post('/api/cycle_turn/prev')  # Goel -> Ally (round drops to 1)
        assert app_module.ROUND_NUMBER == 1
        client.post('/api/cycle_turn/next')  # Ally -> Goel (round 2 again)
        assert app_module.ROUND_NUMBER == 2
        assert ev['last_fired_round'] == 2  # unchanged -- did not refire
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []
        app_module.TURN_INDEX = 0
        app_module.ROUND_NUMBER = 1


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------

def test_create_round_event_endpoint(encounter):
    client = app_module.app.test_client()
    resp = client.post('/api/round_events', json={
        'round': 4, 'repeat_every': 2, 'title': 'Storm surges', 'text': 'Wind howls.',
        'show_on_table': True,
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['success'] is True
    assert body['event']['round'] == 4
    assert body['event']['repeat_every'] == 2
    assert body['event']['id']
    assert body['event']['last_fired_round'] is None
    assert len(app_module.ROUND_EVENTS) == 1


def test_create_round_event_requires_round(encounter):
    client = app_module.app.test_client()
    resp = client.post('/api/round_events', json={'title': 'no round'})
    assert resp.status_code == 400
    assert app_module.ROUND_EVENTS == []


def test_update_round_event_endpoint(encounter):
    ev = _event(round=1)
    app_module.ROUND_EVENTS.append(ev)
    client = app_module.app.test_client()
    resp = client.post(f'/api/round_events/{ev["id"]}/update', json={'title': 'New title', 'round': 7})
    assert resp.status_code == 200
    assert ev['title'] == 'New title'
    assert ev['round'] == 7


def test_update_round_event_cannot_set_last_fired_round(encounter):
    """last_fired_round is engine-owned; the update endpoint must not let a
    client forge it (that would be a way to fake idempotence state)."""
    ev = _event(round=1, last_fired_round=None)
    app_module.ROUND_EVENTS.append(ev)
    client = app_module.app.test_client()
    client.post(f'/api/round_events/{ev["id"]}/update', json={'last_fired_round': 99})
    assert ev['last_fired_round'] is None


def test_update_round_event_not_found(encounter):
    client = app_module.app.test_client()
    resp = client.post('/api/round_events/does-not-exist/update', json={'title': 'x'})
    assert resp.status_code == 404


def test_delete_round_event_endpoint(encounter):
    ev = _event(round=1)
    app_module.ROUND_EVENTS.append(ev)
    client = app_module.app.test_client()
    resp = client.post(f'/api/round_events/{ev["id"]}/delete')
    assert resp.status_code == 200
    assert app_module.ROUND_EVENTS == []


def test_delete_round_event_not_found(encounter):
    client = app_module.app.test_client()
    resp = client.post('/api/round_events/does-not-exist/delete')
    assert resp.status_code == 404


def test_tracker_state_carries_round_events(monkeypatch):
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app_module, '_broadcast_encounter_state', lambda *a, **k: None)
    goel = _real_monster('Goel', hp=30)
    app_module.ACTIVE_ENCOUNTER[:] = [goel]
    app_module.TURN_INDEX = 0
    app_module.ROUND_EVENTS[:] = [_event(round=2)]
    try:
        app_module._invalidate_tracker_cache()
        state = app_module._get_tracker_state()
        assert 'round_events' in state
        assert state['round_events'][0]['round'] == 2
    finally:
        app_module.ACTIVE_ENCOUNTER[:] = []
        app_module.ROUND_EVENTS[:] = []
        app_module.TURN_INDEX = 0
        app_module._invalidate_tracker_cache()


def test_named_saved_encounter_round_trips_round_events(encounter, tmp_path, monkeypatch):
    """The spec requires events to travel with SAVED encounters, not just the
    autosave (review T1 finding: this path was implemented but untested)."""
    monkeypatch.setattr(app_module, 'ENCOUNTER_DIR', str(tmp_path))
    app_module.ROUND_EVENTS[:] = [_event(round=3, title='Cave-in', repeat_every=2,
                                         last_fired_round=3, show_on_table=True)]
    client = app_module.app.test_client()
    r = client.post('/api/save_encounter', data={'encounter_name': 'trip test'})
    assert r.status_code in (200, 302)

    # Wipe live state, then load the saved encounter back.
    app_module.ROUND_EVENTS[:] = []
    saved = [f for f in os.listdir(str(tmp_path)) if f.endswith('.json')]
    assert saved, 'save_encounter wrote no file'
    r2 = client.post('/api/load_encounter', data={'encounter_name': saved[0][:-5]})
    assert r2.status_code in (200, 302)
    assert len(app_module.ROUND_EVENTS) == 1
    ev = app_module.ROUND_EVENTS[0]
    assert ev['title'] == 'Cave-in'
    assert ev['round'] == 3 and ev['repeat_every'] == 2
    assert ev['last_fired_round'] == 3
    assert ev['show_on_table'] is True


def test_malformed_amount_no_ops_adjust_hp(encounter):
    """Pre-extraction, a non-numeric amount aborted the whole handler; the
    thin wrapper must preserve that (review T1 finding) -- no HP change, no
    persist, no broadcast, no spurious 'took 0 damage' log."""
    goel = encounter['goel']
    before_hp = goel.current_hp
    before_persists = len(encounter['persisted'])
    r = app_module.app.test_client().post(
        '/api/adjust_hp/' + goel.instance_id,
        data={'amount': 'not-a-number', 'action': 'damage'})
    assert r.status_code in (200, 302)
    assert goel.current_hp == before_hp
    assert len(encounter['persisted']) == before_persists
