"""Tracker visual-identity payload: Investiture + Radiant order on Cosmere
combatants (spec 2026-07-02-tracker-visual-identity-design.md).

The tracker's Investiture sphere and order-glyph watermark render from
tracker_block(); a player's sheet save must mirror investiture onto the live
combatant the same way health already mirrors."""
from __future__ import annotations

import pytest

import app
from systems.cosmere.actor import CosmereActor


def _combatant(name, spd=3):
    a = CosmereActor({'name': name, 'system': 'cosmere',
                      'system_data': {'system': {'attributes': {'spd': {'value': spd}}},
                                      'type': 'character', 'name': name}})
    a.instance_id = name + '-1'
    a.system = 'cosmere'
    a.is_pc = True
    return a


@pytest.fixture
def pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    pid = 'cd' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Kaladin', 'owner_user_id': 'u1',
           'build': {'name': 'Kaladin', 'level': 3, 'path': 'warrior',
                     'radiant_order': 'windrunners', 'first_ideal_sworn': True,
                     'attributes': {'str': 3, 'spd': 3, 'wil': 1, 'awa': 2}, 'skills': {'hwp': 2}},
           'play_state': {'investiture': 1}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    app.ACTIVE_ENCOUNTER[:] = [_combatant('Kaladin')]
    app.TURN_INDEX = 0
    yield pid
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0


def test_tracker_block_defaults():
    """A bare combatant reports full investiture and no order — never KeyErrors."""
    blk = _combatant('Szeth').tracker_block()
    assert blk['investiture_current'] == blk['investiture_max']
    assert blk['radiant_order'] == ''


def test_tracker_block_reflects_spend_and_order():
    c = _combatant('Kaladin')
    c.radiant_order = 'windrunners'
    c.current_investiture = 1
    blk = c.tracker_block()
    assert blk['investiture_current'] == 1
    assert blk['radiant_order'] == 'windrunners'


def test_cosmere_combatant_seeds_order_and_investiture(pc):
    """Adding a saved PC to the tracker carries order + spent investiture,
    so the sphere is correct even before the next sheet save."""
    c = app._cosmere_combatant(pc)
    assert c is not None
    blk = c.tracker_block()
    assert blk['radiant_order'] == 'windrunners'
    assert blk['investiture_current'] == 1


def test_sheet_save_mirrors_investiture_to_live_combatant(pc):
    r = app.app.test_client().post(
        '/cosmere/pc/' + pc + '/state', json={'investiture': 0})
    assert r.get_json()['ok']
    me = next(x for x in app.ACTIVE_ENCOUNTER if x.name == 'Kaladin')
    assert me.tracker_block()['investiture_current'] == 0


class _PF2ePC:
    """Minimal PF2e PC stand-in carrying only what _get_tracker_state reads
    directly (plus a class_name for the rune-watermark regression below)."""
    def __init__(self, name, class_name):
        self.instance_id = name + '-1'
        self.name = name
        self.is_pc = True
        self.system = 'pf2e'
        self.initiative = 15
        self.level = 3
        self.ac = 18
        self.current_hp = 30
        self.hp = 30
        self.fort = 8
        self.ref = 6
        self.will = 7
        self.perception = 9
        self.conditions = {}
        self.class_name = class_name


@pytest.fixture
def pf2e_pc(monkeypatch):
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *a, **k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *a, **k: None)
    app.ACTIVE_ENCOUNTER[:] = [_PF2ePC('Go’el', 'Cleric')]
    app.TURN_INDEX = 0
    app._invalidate_tracker_cache()
    yield
    app.ACTIVE_ENCOUNTER[:] = []
    app.TURN_INDEX = 0
    app._invalidate_tracker_cache()


def test_pf2e_pc_tracker_entry_carries_class_name(pf2e_pc):
    """The tracker row template gates the class-rune portrait watermark on
    c.class_name (templates/tracker.html classRuneId usage). If the server
    payload never ships class_name, the watermark silently never renders."""
    state = app._get_tracker_state()
    entry = next(e for e in state['combatants'] if e['name'] == 'Go’el')
    assert entry.get('class_name') == 'Cleric'
