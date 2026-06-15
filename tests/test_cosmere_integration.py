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


def test_cosmere_adjust_hp_route_applies_deflect_on_untyped():
    """The GM tracker's quick-damage / Enter-to-damage path sends
    damage_type='untyped' (and the route default is 'untyped'); a Cosmere
    combatant must STILL get armor Deflect, because the rulebook default for a
    hit is a deflectable physical blow, not a bypassing one. Regression guard for
    the bug where 'untyped' slipped past the DEFLECTABLE gate and Deflect was
    silently skipped on every GM-applied Cosmere hit."""
    a = app._cosmere_combatant(_adv_id('Archer'))      # deflect 1, health 12
    a.instance_id = 'cos-route-1'
    saved = list(app.ACTIVE_ENCOUNTER)
    try:
        app.ACTIVE_ENCOUNTER.append(a)
        app._invalidate_tracker_cache()
        client = app.app.test_client()
        hdr = {'X-Requested-With': 'XMLHttpRequest'}
        # untyped quick-damage: Deflect 1 still applies -> 5 - 1 = 4 taken
        r = client.post('/api/adjust_hp/cos-route-1', headers=hdr,
                        data={'amount': '5', 'action': 'damage', 'damage_type': 'untyped'})
        assert r.status_code == 200
        assert a.current_hp == 8
        # no damage_type at all (route form default 'untyped') -> deflectable too
        client.post('/api/adjust_hp/cos-route-1', headers=hdr,
                    data={'amount': '5', 'action': 'damage'})
        assert a.current_hp == 4                # 8 - (5 - 1)
        # an explicit bypassing type (spirit) still bypasses Deflect
        client.post('/api/adjust_hp/cos-route-1', headers=hdr,
                    data={'amount': '3', 'action': 'damage', 'damage_type': 'spirit'})
        assert a.current_hp == 1                # 4 - 3 (no deflect)
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app._invalidate_tracker_cache()


def test_tracker_offers_cosmere_damage_types():
    """The GM tracker damage UI must expose the Cosmere damage types so the GM
    can force a deflectable hit (impact/keen/energy) or a bypassing one
    (spirit/vital), instead of only PF2e types. Guards the UI half of the
    Deflect-reachability fix (the server half is covered by the route test)."""
    import pathlib
    html = pathlib.Path('templates/tracker.html').read_text()
    # the Cosmere damage-type vocabulary is defined for the JS
    assert 'COSMERE_DAMAGE_TYPES' in html
    for t in ('impact', 'keen', 'energy', 'spirit', 'vital'):
        assert f"'{t}'" in html
    # the per-target damage selector picks its option list by combatant system
    assert 'damageTypeListFor' in html
    # the damage preview is Deflect-aware for Cosmere combatants
    assert 'Deflect' in html
    # the multi-target modal selector is rebuilt per active system
    assert 'syncMultiDamageTypes' in html


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


def test_cosmere_encounter_survives_autosave_roundtrip(tmp_path, monkeypatch):
    """A live Cosmere encounter must survive a process restart. The autosave
    serializer/restorer was PF2e-only (looked combatants up in MONSTER_LIBRARY/
    PARTY_LIBRARY, which never hold Cosmere actors), so a Railway redeploy
    silently wiped the whole fight. Round-trip a Cosmere adversary with live
    combat state through persist -> restart -> restore."""
    monkeypatch.setattr(app, 'ENCOUNTER_DIR', str(tmp_path))
    a = app._cosmere_combatant(_adv_id('Archer'))
    a.instance_id = 'cos-persist-1'
    a.current_hp = 7
    a.injuries = 2
    a.conditions = {'slowed': True}
    a.speed_choice = 'fast'
    a.initiative = 14
    saved, idx, rnd = list(app.ACTIVE_ENCOUNTER), app.TURN_INDEX, app.ROUND_NUMBER
    try:
        app.ACTIVE_ENCOUNTER[:] = [a]
        app.TURN_INDEX = 0
        app.ROUND_NUMBER = 3
        app._do_persist_encounter_state()            # write the autosave
        app.ACTIVE_ENCOUNTER.clear()                 # simulate a worker restart
        app._restore_encounter_autosave()            # rehydrate from disk
        assert len(app.ACTIVE_ENCOUNTER) == 1
        r = app.ACTIVE_ENCOUNTER[0]
        assert getattr(r, 'system', None) == 'cosmere'
        assert r.name == 'Archer'
        assert r.current_hp == 7
        assert int(getattr(r, 'injuries', 0)) == 2
        assert r.conditions.get('slowed') is True
        assert getattr(r, 'speed_choice', None) == 'fast'
        assert r.instance_id == 'cos-persist-1'
        assert app.ROUND_NUMBER == 3
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.TURN_INDEX = idx
        app.ROUND_NUMBER = rnd
        app._invalidate_tracker_cache()


def test_cosmere_turn_advance_keeps_fast_actions_and_bool_conditions(tmp_path, monkeypatch):
    """Advancing turns must NOT run PF2e integer condition-ticking on Cosmere
    combatants (whose conditions are booleans) and must NOT reset a Fast actor's
    action ceiling to 3 -- fast=2 / slow=3 has to survive turn advance, derived
    from the elected speed each turn."""
    monkeypatch.setattr(app, 'ENCOUNTER_DIR', str(tmp_path))
    a = app._cosmere_combatant(_adv_id('Archer'))
    a.instance_id = 'cos-t-a'; a.speed_choice = 'fast'; a.max_actions = 2
    a.conditions = {'slowed': True}
    b = app._cosmere_combatant(_adv_id('Archer'))
    b.instance_id = 'cos-t-b'; b.speed_choice = 'slow'; b.max_actions = 3
    saved, idx, rnd = list(app.ACTIVE_ENCOUNTER), app.TURN_INDEX, app.ROUND_NUMBER
    try:
        app.ACTIVE_ENCOUNTER[:] = [a, b]
        app.TURN_INDEX = 0
        client = app.app.test_client()
        hdr = {'X-Requested-With': 'XMLHttpRequest'}
        client.post('/api/cycle_turn/next', headers=hdr)     # end a's turn, start b's
        assert app.ACTIVE_ENCOUNTER[app.TURN_INDEX].instance_id == 'cos-t-b'
        assert b.max_actions == 3                             # slow keeps 3
        client.post('/api/cycle_turn/next', headers=hdr)      # wrap to a's turn again
        assert app.ACTIVE_ENCOUNTER[app.TURN_INDEX].instance_id == 'cos-t-a'
        assert a.max_actions == 2                             # fast keeps 2 (not reset to 3)
        assert a.conditions.get('slowed') is True             # boolean NOT mangled to int 0
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.TURN_INDEX = idx
        app.ROUND_NUMBER = rnd
        app._invalidate_tracker_cache()


def test_corrupt_cosmere_combatant_does_not_wipe_encounter(tmp_path, monkeypatch):
    """One Cosmere combatant whose build throws on rebuild (e.g. a malformed PC
    doc off the volume after a partial write / schema drift) must NOT abort the
    whole restore loop and silently truncate the live fight. The bad one is
    skipped; everything else survives."""
    monkeypatch.setattr(app, 'ENCOUNTER_DIR', str(tmp_path))
    archer_id = _adv_id('Archer')
    real = app._cosmere_combatant

    def boom(aid):
        if aid == 'BOOM':
            raise ValueError('corrupt cosmere build')
        return real(aid)
    monkeypatch.setattr(app, '_cosmere_combatant', boom)

    import json as _json
    autosave = {'round': 2, 'turn_index': 0, 'notes': '', 'session_timer_start': None,
                'combatants': [
                    {'system': 'cosmere', 'type': 'monster', 'cosmere_id': archer_id,
                     'instance_id': 'g1', 'current_hp': 10, 'conditions': {}},
                    {'system': 'cosmere', 'type': 'monster', 'cosmere_id': 'BOOM',
                     'instance_id': 'bad', 'current_hp': 5, 'conditions': {}},
                    {'system': 'cosmere', 'type': 'monster', 'cosmere_id': archer_id,
                     'instance_id': 'g2', 'current_hp': 8, 'conditions': {}},
                ]}
    with open(tmp_path / '_autosave.json', 'w', encoding='utf-8') as f:
        _json.dump(autosave, f)
    saved, idx, rnd = list(app.ACTIVE_ENCOUNTER), app.TURN_INDEX, app.ROUND_NUMBER
    try:
        app.ACTIVE_ENCOUNTER.clear()
        app._restore_encounter_autosave()
        ids = [c.instance_id for c in app.ACTIVE_ENCOUNTER]
        assert ids == ['g1', 'g2']        # corrupt one skipped, the rest survive
    finally:
        app.ACTIVE_ENCOUNTER[:] = saved
        app.TURN_INDEX = idx
        app.ROUND_NUMBER = rnd
        app._invalidate_tracker_cache()


def test_cosmere_injury_records_details_and_applies_effect(monkeypatch):
    """Dropping to 0 must record a STRUCTURED injury (severity/duration/effect)
    on the combatant AND auto-apply the d8 effect as a tracked condition, instead
    of only scrolling past in the combat log with an integer count."""
    import systems.cosmere.combat as cc
    monkeypatch.setattr(cc, 'roll_injury', lambda **k: {
        'd20': 10, 'total': 10, 'severity': 'shallow',
        'duration': '1d6 days', 'is_death': False, 'effect': 'Slowed'})
    a = app._cosmere_combatant(_adv_id('Archer'))
    a.instance_id = 'inj-1'
    a.current_hp = 3
    app._cosmere_adjust_hp(a, 50, 'damage', 'impact')
    assert a.current_hp == 0 and a.injuries == 1
    assert a.conditions.get('slowed') is True          # d8 effect auto-applied
    assert isinstance(a.injury_log, list) and len(a.injury_log) == 1
    rec = a.injury_log[-1]
    assert rec['severity'] == 'shallow' and rec['effect'] == 'Slowed'
    assert rec['duration'] == '1d6 days' and rec['n'] == 1


def test_cosmere_injury_exhausted_effect_stacks(monkeypatch):
    import systems.cosmere.combat as cc
    monkeypatch.setattr(cc, 'roll_injury', lambda **k: {
        'd20': 9, 'total': 9, 'severity': 'shallow', 'duration': '',
        'is_death': False, 'effect': 'Exhausted [-2]'})
    a = app._cosmere_combatant(_adv_id('Archer'))
    a.instance_id = 'inj-2'
    a.current_hp = 2
    app._cosmere_adjust_hp(a, 50, 'damage', 'impact')
    assert a.conditions.get('exhausted') == 2          # magnitude parsed + stacked


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
