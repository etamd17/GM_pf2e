"""Cosmere: hard Ideal-gate enforcement at save.

The visual tree locks Ideal/level-gated radiant nodes client-side; this makes the
SAVE-time engine agree. radiant_talents.talent_gates() exposes each radiant
talent's {ideal, level} requirement (from the tree-graph nodes), and
build.unmet_prereqs() now flags a radiant talent taken without its required Ideal
(or level). Because hard_violations() extends unmet_prereqs(), an over-reaching
build is blocked on a player's save (the GM can still force-override).
"""
from __future__ import annotations

import systems.cosmere.radiant_talents as RT
import systems.cosmere.build as B


def test_talent_gates_populated():
    g = RT.talent_gates()
    assert g, 'expected radiant talent gates'
    # Take Squire is a Third-Ideal talent in every order
    sq = [v for k, v in g.items() if k.startswith('take squire')]
    assert sq and all(v['ideal'] >= 3 for v in sq)


def test_ideal_gate_blocks_save_below_ideal():
    b = B.CosmereBuild({'radiant_order': 'windrunners', 'ideals_sworn': 1, 'level': 8,
                        'talents': [{'id': 'radiant:Take Squire (Windrunner)',
                                     'name': 'Take Squire (Windrunner)'}]})
    unmet = b.unmet_prereqs()
    assert any('Third Ideal' in u for u in unmet)
    assert any('Ideal' in h for h in b.hard_violations())   # blocks the save


def test_meeting_the_ideal_clears_it():
    b = B.CosmereBuild({'radiant_order': 'windrunners', 'ideals_sworn': 3, 'level': 8,
                        'talents': [{'id': 'radiant:Take Squire (Windrunner)',
                                     'name': 'Take Squire (Windrunner)'}]})
    assert not any('Ideal' in u for u in b.unmet_prereqs())


def test_non_radiant_talents_unaffected():
    # a plain build with no radiant talents has no Ideal-gate noise
    b = B.CosmereBuild({'path': 'warrior', 'level': 3})
    assert not any('Ideal' in u for u in b.unmet_prereqs())
