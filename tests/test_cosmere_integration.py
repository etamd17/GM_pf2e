"""Cosmere sheet + tracker integration (Phase 3).

Proves a Cosmere actor is viewable (sheet + bestiary routes) and can ride in the
combat tracker -- the serializers emit a `system` tag plus a `cosmere` stat block
additively, and PF2e combatants are left byte-identical. Imports `app` (conftest
puts the repo root on sys.path).
"""
from __future__ import annotations

import app
from systems.cosmere import load_pack


def _adv_id(name='Archer'):
    return next(
        d['_id'] for d in load_pack('companions-and-adversaries')
        if d.get('name') == name and d.get('type') == 'adversary'
    )


# -- sheet / bestiary routes ------------------------------------------------

def test_cosmere_bestiary_route():
    r = app.app.test_client().get('/cosmere/bestiary')
    assert r.status_code == 200
    assert b'Cosmere Bestiary' in r.data
    assert b'Archer' in r.data


def test_cosmere_sheet_route_renders_actor():
    r = app.app.test_client().get('/cosmere/sheet/' + _adv_id('Archer'))
    assert r.status_code == 200
    body = r.data.decode()
    assert 'Archer' in body
    assert 'Physical' in body and 'Deflect' in body
    assert '13' in body              # the physical defense value


def test_cosmere_sheet_unknown_is_404():
    r = app.app.test_client().get('/cosmere/sheet/notarealid')
    assert r.status_code == 404


# -- tracker add factory ----------------------------------------------------

def test_cosmere_combatant_factory():
    a = app._cosmere_combatant(_adv_id('Archer'))
    assert a is not None and a.name == 'Archer' and a.system == 'cosmere'
    assert app._cosmere_combatant('bogus-id') is None


# -- tracker-state contract -------------------------------------------------

def test_cosmere_combatant_in_tracker_state():
    actor = app._cosmere_combatant(_adv_id('Archer'))
    actor.instance_id = 'cos-test-1'
    saved, saved_idx = list(app.ACTIVE_ENCOUNTER), app.TURN_INDEX
    try:
        app.ACTIVE_ENCOUNTER.append(actor)
        app._invalidate_tracker_cache()
        state = app._get_tracker_state()
        ent = next(c for c in state['combatants'] if c['instance_id'] == 'cos-test-1')
        assert ent['system'] == 'cosmere'
        assert ent['cosmere']['defenses'] == {'phy': 13, 'cog': 13, 'spi': 13}
        assert ent['cosmere']['deflect']['value'] == 1
        assert 'energy' in ent['cosmere']['deflect']['types']
        assert ent['current_hp'] == 12 and ent['max_hp'] == 12
        # PF2e combatants are untouched: tagged pf2e, no cosmere block.
        for c in state['combatants']:
            if c['instance_id'] != 'cos-test-1':
                assert c.get('system') == 'pf2e'
                assert 'cosmere' not in c
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.TURN_INDEX = saved_idx
        app._invalidate_tracker_cache()


def test_cosmere_damage_deflect_and_injury_spiral():
    """The Cosmere damage path: Deflect on impact/keen/energy, bypass on
    spirit/vital, and the injury death-spiral at 0 health (Ch.9)."""
    a = app._cosmere_combatant(_adv_id('Archer'))      # deflect 1, health 12
    a.instance_id = 'cos-dmg-1'
    # impact damage: Deflect 1 applies -> 5 - 1 = 4 taken
    app._cosmere_adjust_hp(a, 5, 'damage', 'impact')
    assert a.current_hp == 8
    # spirit bypasses Deflect -> full 3
    app._cosmere_adjust_hp(a, 3, 'damage', 'spirit')
    assert a.current_hp == 5
    # reduced to 0 -> first injury + Unconscious
    app._cosmere_adjust_hp(a, 50, 'damage', 'impact')
    assert a.current_hp == 0 and a.injuries == 1 and a.conditions.get('unconscious') is True
    # damage while at 0 -> another injury (death-spiral)
    app._cosmere_adjust_hp(a, 5, 'damage', 'impact')
    assert a.injuries == 2
    # healing above 0 clears Unconscious
    app._cosmere_adjust_hp(a, 4, 'heal', '')
    assert a.current_hp == 4 and 'unconscious' not in a.conditions


def test_cosmere_4phase_turn_queue_sort():
    """A pure-Cosmere encounter sorts into the 4-phase queue (fast_pc ->
    fast_npc -> slow_pc -> slow_npc; Speed then d20 tiebreak)."""
    from systems.cosmere.actor import CosmereActor

    def mk(name, is_pc, choice, spd, tb):
        a = CosmereActor({'name': name, 'type': 'character' if is_pc else 'adversary',
                          'system': {'attributes': {'spd': {'value': spd}}}})
        a.instance_id = name
        a.speed_choice = choice
        a.initiative = tb
        return a

    saved, idx = list(app.ACTIVE_ENCOUNTER), app.TURN_INDEX
    try:
        app.ACTIVE_ENCOUNTER[:] = [
            mk('SlowNPC', False, 'slow', 3, 10), mk('FastPCLo', True, 'fast', 1, 5),
            mk('FastPCHi', True, 'fast', 4, 5), mk('FastNPC', False, 'fast', 5, 1),
            mk('SlowPC', True, 'slow', 2, 1),
        ]
        app.TURN_INDEX = 0
        app._sort_encounter()
        assert [c.name for c in app.ACTIVE_ENCOUNTER] == \
            ['FastPCHi', 'FastPCLo', 'FastNPC', 'SlowPC', 'SlowNPC']
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.TURN_INDEX = idx
        app._invalidate_tracker_cache()


def test_plot_die_route():
    r = app.app.test_client().post('/api/plot_die')
    assert r.status_code == 200
    d = r.get_json()
    assert d['type'] in ('blank', 'opportunity', 'complication')
    assert 'label' in d and isinstance(d.get('spend'), list)


def test_cosmere_combatant_broadcast_does_not_crash():
    actor = app._cosmere_combatant(_adv_id('Archer'))
    actor.instance_id = 'cos-test-2'
    saved = list(app.ACTIVE_ENCOUNTER)
    try:
        app.ACTIVE_ENCOUNTER.append(actor)
        app._do_broadcast_encounter_state()   # must not raise with a Cosmere combatant present
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app._invalidate_tracker_cache()
