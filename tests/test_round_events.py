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
    """Every fire logs a combat-log entry; the title appears only for
    show_on_table events (the log is player-visible -- hidden events log a
    generic line, covered by test_hidden_event_title_kept_out_of_combat_log)."""
    logged = []
    monkeypatch.setattr(app_module, '_combat_log', lambda msg, kind='info': logged.append((msg, kind)))
    ev = _event(round=1, title='The ceiling groans', show_on_table=True)
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


def test_round_events_global_declared_before_boot_rehydrate():
    """Import-order guard (found live in the T4 walk): the boot-time
    autosave restore is the module-tail _restore_encounter_autosave() call
    (the mid-import load_libraries() boot call passes restore_autosave=False
    -- restoring mid-import NameError'd on Cosmere helpers defined later in
    the file and wiped live fights). Three source-order invariants keep both
    boot bugs dead: (1) the ROUND_EVENTS declaration executes before the
    tail restore (else restored events are wiped as the import continues);
    (2) a module-level _restore_encounter_autosave() call EXISTS (else
    nothing restores at boot at all); (3) that call sits after the
    _restore_cosmere_combatant def (the helper whose absence caused the
    original NameError). Post-import unit tests cannot catch import-order
    regressions, so we assert the source order directly."""
    import ast

    src_path = os.path.join(os.path.dirname(app_module.__file__), 'app.py')
    with open(src_path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())

    decl_line = None
    tail_restore_line = None
    cosmere_def_line = None
    for node in tree.body:  # module scope only
        if isinstance(node, ast.Assign) and decl_line is None:
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == 'ROUND_EVENTS':
                    decl_line = node.lineno
        if (isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == '_restore_encounter_autosave'):
            tail_restore_line = node.lineno
        if isinstance(node, ast.FunctionDef) and node.name == '_restore_cosmere_combatant':
            cosmere_def_line = node.lineno

    assert decl_line is not None, 'module-level ROUND_EVENTS declaration missing'
    assert tail_restore_line is not None, (
        'module-level _restore_encounter_autosave() boot call missing -- '
        'nothing rehydrates the encounter at boot')
    assert cosmere_def_line is not None, '_restore_cosmere_combatant def missing'
    assert decl_line < tail_restore_line, (
        f'ROUND_EVENTS declared at line {decl_line}, after the boot restore '
        f'at line {tail_restore_line} -- the boot rehydrate would be wiped')
    assert cosmere_def_line < tail_restore_line, (
        f'boot restore at line {tail_restore_line} runs before '
        f'_restore_cosmere_combatant (def at {cosmere_def_line}) exists -- '
        f'a live Cosmere fight would fail to restore (and the next persist '
        f'deletes the autosave)')


# ---------------------------------------------------------------------------
# Cosmere condition payloads (found live in the T4 walk): the engine must
# route Cosmere combatants through the Cosmere condition path -- the PF2e
# helper's hardcoded condition lists silently no-op on e.g. 'exhausted'.
# ---------------------------------------------------------------------------

def _cosmere_combatant(name, hp=23, instance_id=None):
    c = _Combatant(name, hp=hp, instance_id=instance_id)
    c.system = 'cosmere'
    return c


def test_cosmere_condition_payload_applies(encounter):
    kal = _cosmere_combatant('Kaladin')
    app_module.ACTIVE_ENCOUNTER.append(kal)
    ev = _event(round=2, payload={'conditions': [
        {'target_ids': [kal.instance_id], 'condition': 'exhausted', 'value': 1}]})
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert kal.conditions.get('exhausted'), (
        'Cosmere condition payload must land on the combatant (the PF2e '
        'helper silently no-ops on Cosmere condition names)')


def test_cosmere_condition_payload_negative_value_removes(encounter):
    kal = _cosmere_combatant('Kaladin')
    kal.conditions['exhausted'] = True
    app_module.ACTIVE_ENCOUNTER.append(kal)
    ev = _event(round=2, payload={'conditions': [
        {'target_ids': [kal.instance_id], 'condition': 'exhausted', 'value': -1}]})
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert 'exhausted' not in kal.conditions


def test_cosmere_condition_payload_unknown_condition_skipped(encounter):
    kal = _cosmere_combatant('Kaladin')
    app_module.ACTIVE_ENCOUNTER.append(kal)
    ev = _event(round=2, payload={'conditions': [
        {'target_ids': [kal.instance_id], 'condition': 'frightened', 'value': 1}]})
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)  # must not raise
    assert not kal.conditions.get('frightened')


def test_cosmere_condition_route_still_applies(encounter, monkeypatch):
    """The tracker route must keep working after extract-and-share. The
    route's AJAX response path renders full tracker state (monster-only
    computed properties the _Combatant stub doesn't model), so stub the
    response builder -- the assertions target the mutation + status codes."""
    monkeypatch.setattr(app_module, '_tracker_json_response',
                        lambda *a, **k: app_module.jsonify({'ok': True}))
    kal = _cosmere_combatant('Kaladin')
    app_module.ACTIVE_ENCOUNTER.append(kal)
    client = app_module.app.test_client()
    r = client.post('/api/cosmere/combatant/%s/condition' % kal.instance_id,
                    json={'condition': 'exhausted', 'action': 'add'})
    assert r.status_code == 200
    assert kal.conditions.get('exhausted') is True
    r = client.post('/api/cosmere/combatant/%s/condition' % kal.instance_id,
                    json={'condition': 'exhausted', 'action': 'remove'})
    assert r.status_code == 200
    assert 'exhausted' not in kal.conditions
    r = client.post('/api/cosmere/combatant/%s/condition' % kal.instance_id,
                    json={'condition': 'no-such-cond', 'action': 'add'})
    assert r.status_code == 400


def test_mixed_encounter_routes_conditions_per_system(encounter):
    """One event targeting 'all' with a PF2e condition: applies to PF2e
    combatants via the PF2e helper, silently skips Cosmere combatants."""
    goel = encounter['goel']
    kal = _cosmere_combatant('Kaladin')
    app_module.ACTIVE_ENCOUNTER.append(kal)
    ev = _event(round=2, payload={'conditions': [
        {'target_ids': 'all', 'condition': 'frightened', 'value': 1}]})
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert goel.conditions.get('frightened') == 1
    assert not kal.conditions.get('frightened')


def test_boot_restore_survives_cosmere_autosave():
    """Full-boot regression guard (both T4-walk boot-order bugs): import app
    in a subprocess with a live-campaign Cosmere autosave seeded on disk.
    The old code (a) wiped restored ROUND_EVENTS when the late module-level
    declaration re-ran after the mid-import restore, and (b) NameError'd on
    _restore_cosmere_combatant (defined later in the file), aborting the
    restore -- after which the next persist deleted the autosave (the
    'restart wipes a live Cosmere fight' data-loss bug). Post-import unit
    tests can't catch either: only a real import replays the boot order."""
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    body = '''
import json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
TMP = tempfile.mkdtemp(); os.environ['DATA_DIR'] = TMP; os.environ['GM_PASSWORD'] = ''
cid = 'cafecafecafecafecafecafecafecafe'
camp = os.path.join(TMP, 'campaigns', cid)
enc = os.path.join(camp, 'saved_encounters')
os.makedirs(enc)
json.dump({'live_campaign_id': cid}, open(os.path.join(TMP, 'server_state.json'), 'w'))
json.dump({'schema_version': 1, 'id': cid, 'slug': 'storm', 'name': 'Storm',
           'system': 'cosmere', 'members': [], 'system_config': {}},
          open(os.path.join(camp, 'campaign.json'), 'w'))
json.dump({'round': 4, 'turn_index': 0, 'notes': '', 'session_timer_start': None,
           'round_events': [{'id': 'ev1', 'round': 2, 'repeat_every': None,
                             'title': 'Highstorm', 'text': 'It hits.',
                             'show_on_table': True, 'payload': None,
                             'last_fired_round': 2}],
           'combatants': [{'system': 'cosmere', 'type': 'pc', 'cosmere_id': 'missing-pc',
                           'path': 'Kaladin', 'instance_id': 'k1', 'initiative': 12,
                           'current_hp': 19, 'conditions': {}}]},
          open(os.path.join(enc, '_autosave.json'), 'w'))

import app as A
assert A.ROUND_NUMBER == 4, 'round not restored: %r' % A.ROUND_NUMBER
assert len(A.ROUND_EVENTS) == 1, 'ROUND_EVENTS wiped at boot: %r' % A.ROUND_EVENTS
assert A.ROUND_EVENTS[0]['title'] == 'Highstorm'
assert A.ROUND_EVENTS[0]['last_fired_round'] == 2
assert os.path.exists(os.path.join(enc, '_autosave.json')), 'autosave deleted'
print('BOOT-RESTORE-OK')
'''
    r = subprocess.run([sys.executable, '-c', body],
                       capture_output=True, text=True, cwd=repo, timeout=300)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert 'BOOT-RESTORE-OK' in out
    assert 'Failed to restore autosave' not in out, (
        'boot restore raised (the pre-fix NameError class): %s' % out[-2000:])


# ---------------------------------------------------------------------------
# Final-review fixes (whole-branch adversarial review): high-water idempotence
# for repeat events, delay_turn round boundary, boolean-condition removal,
# payload validation, fire containment, hidden-title log privacy, and the
# non-GM page-embed strip.
# ---------------------------------------------------------------------------

def test_repeat_event_backward_forward_does_not_refire(encounter):
    """last_fired_round is a high-water mark: after a repeat event fires
    through R4, back-cycling and re-advancing through R3/R4 must not
    double-apply -- only R5+ fires again. (The old equality-only check
    re-fired repeat events on every re-traversed round.)"""
    goel = encounter['goel']
    ev = _event(round=2, repeat_every=1, payload={'damage': [
        {'target_ids': [goel.instance_id], 'dice': '1d1', 'kind': 'damage'}]})
    app_module.ROUND_EVENTS.append(ev)
    for r in (2, 3, 4):
        app_module._fire_round_events(r)
    assert ev['last_fired_round'] == 4
    hp_after_first_pass = goel.current_hp
    assert hp_after_first_pass == 30 - 3  # 1d1 x3 fires
    # GM cycles backward to round 2, then forward again through 3 and 4.
    app_module._fire_round_events(3)
    app_module._fire_round_events(4)
    assert goel.current_hp == hp_after_first_pass, 're-traversed rounds re-applied the payload'
    assert ev['last_fired_round'] == 4, 'last_fired_round regressed'
    app_module._fire_round_events(5)
    assert goel.current_hp == hp_after_first_pass - 1
    assert ev['last_fired_round'] == 5


def test_delay_turn_round_boundary_fires_events(encounter):
    """Delaying the last combatant in initiative order crosses the round
    boundary through delay_turn's own advance loop -- events due that round
    must fire there too, or one-shots are silently lost forever."""
    ally = encounter['ally']
    app_module.TURN_INDEX = 1  # ally is last in order and active
    app_module.ROUND_NUMBER = 2
    ev = _event(round=3)
    app_module.ROUND_EVENTS.append(ev)
    r = app_module.app.test_client().post('/api/delay_turn/' + ally.instance_id)
    assert r.status_code in (200, 302)
    assert app_module.ROUND_NUMBER == 3
    assert ev['last_fired_round'] == 3, 'round boundary via delay_turn skipped the fire'


def test_negative_value_removes_boolean_condition(encounter):
    """A negative-value condition row means "remove"; for PF2e boolean
    conditions (concealed etc.) the shared helper's boolean branch needs a
    decrease/remove arm -- without it the row no-ops and the log claims
    'gained'."""
    goel = encounter['goel']
    goel.conditions['concealed'] = True
    ev = _event(round=2, payload={'conditions': [
        {'target_ids': [goel.instance_id], 'condition': 'concealed', 'value': -1}]})
    app_module.ROUND_EVENTS.append(ev)
    app_module._fire_round_events(2)
    assert goel.conditions.get('concealed') is False


def test_bad_dice_rejected_at_create(encounter):
    client = app_module.app.test_client()
    for dice, why in (('2d0', 'zero sides raises at fire time'),
                      ('0d6', 'zero qty'),
                      ('99999999d6', 'stalls the single gevent worker'),
                      ('garbage', 'no NdM term -- would silently no-op')):
        r = client.post('/api/round_events', json={
            'round': 3, 'title': 'x',
            'payload': {'damage': [{'target_ids': 'all', 'dice': dice, 'kind': 'damage'}]}})
        assert r.status_code == 400, f'{dice!r} accepted ({why})'
    r = client.post('/api/round_events', json={
        'round': 3, 'title': 'x',
        'payload': {'damage': [{'target_ids': 'all', 'dice': '2d6+3', 'kind': 'damage'}]}})
    assert r.status_code == 200


def test_malformed_payload_rows_rejected_at_create(encounter):
    client = app_module.app.test_client()
    for payload in ({'conditions': 'oops'},
                    {'conditions': ['not-a-dict']},
                    {'damage': 'oops'},
                    {'damage': [None]}):
        r = client.post('/api/round_events', json={'round': 3, 'payload': payload})
        assert r.status_code == 400, f'accepted malformed payload {payload!r}'
    # update path shares the sanitizer
    r = client.post('/api/round_events', json={'round': 3, 'title': 'ok'})
    ev_id = r.get_json()['event']['id']
    r = client.post(f'/api/round_events/{ev_id}/update',
                    json={'payload': {'conditions': ['bad']}})
    assert r.status_code == 400


def test_one_bad_event_does_not_block_siblings(encounter):
    """A corrupt payload already in the store (old save; the sanitizer only
    guards the CRUD path) must not abort the other events due that round or
    the tail persist/broadcast."""
    goel = encounter['goel']
    bad = _event(round=2, payload={'damage': [
        {'target_ids': 'all', 'dice': '2d0', 'kind': 'damage'}]})
    bad['id'] = 'bad1'
    good = _event(round=2, payload={'conditions': [
        {'target_ids': [goel.instance_id], 'condition': 'frightened', 'value': 1}]})
    good['id'] = 'good1'
    app_module.ROUND_EVENTS.extend([bad, good])
    app_module._fire_round_events(2)  # must not raise
    assert bad['last_fired_round'] == 2
    assert good['last_fired_round'] == 2
    assert goel.conditions.get('frightened') == 1
    assert encounter['persisted'], 'tail persist skipped'


def test_hidden_event_title_kept_out_of_combat_log(encounter, monkeypatch):
    """The combat log is player-visible (open /api/combat_log + player SSE
    frame) -- a show_on_table=false event must not leak its title there."""
    logged = []
    monkeypatch.setattr(app_module, '_combat_log', lambda msg, *a, **k: logged.append(msg))
    hidden = _event(round=2, title='Assassin strikes from the rafters', show_on_table=False)
    hidden['id'] = 'h1'
    shown = _event(round=2, title='The horn sounds', show_on_table=True)
    shown['id'] = 's1'
    app_module.ROUND_EVENTS.extend([hidden, shown])
    app_module._fire_round_events(2)
    assert 'Round 2: GM event fired' in logged
    assert 'Round 2: The horn sounds' in logged
    assert not any('Assassin' in m for m in logged)


def test_tracker_page_strips_round_events_for_non_gm(monkeypatch):
    """/tracker is served to player viewers (CSS-only GM hiding); the initial
    STATE embed must not carry GM-secret events (hidden titles/payloads) for
    non-GM sessions, while the GM page keeps them."""
    monkeypatch.setattr(app_module, '_persist_encounter_state', lambda *a, **k: None)
    app_module.ACTIVE_ENCOUNTER[:] = []
    app_module.ROUND_EVENTS[:] = [
        _event(round=5, title='SECRET-AMBUSH-XYZZY', show_on_table=False)]
    try:
        client = app_module.app.test_client()
        monkeypatch.setattr(app_module, '_is_gm', lambda *a, **k: False)
        page = client.get('/tracker').get_data(as_text=True)
        assert 'SECRET-AMBUSH-XYZZY' not in page
        monkeypatch.setattr(app_module, '_is_gm', lambda *a, **k: True)
        page = client.get('/tracker').get_data(as_text=True)
        assert 'SECRET-AMBUSH-XYZZY' in page
    finally:
        app_module.ROUND_EVENTS[:] = []
