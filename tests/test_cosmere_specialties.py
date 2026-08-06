"""Cosmere builder depth #1: surface heroic-path SPECIALTIES.

The rulebook (Ch.4) divides each heroic path into THREE specialties (e.g. Warrior
-> Duelist / Shardbearer / Soldier). The talents were all loaded under the parent
path but shown as one flat picker. Now each talent is tagged with its specialty
(from the talent-tree it lives in) and the builder groups the picker by specialty.

Verified live: selecting Warrior shows optgroups Core path talents · Duelist ·
Shardbearer · Soldier (8 talents each).
"""
from __future__ import annotations

import os
import pathlib

import app as A
import systems.cosmere.talents as T

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_talent_specialty_map_is_populated():
    sp = T.talent_specialty()
    named = [v for v in sp.values() if v]
    assert len(named) >= 120, 'most path talents should map to a specialty'
    # All 18 canonical specialties (3 per path x 6 paths) should appear.
    assert len(set(named)) >= 18


def test_path_talents_tagged_and_grouped():
    pt = A._cosmere_path_talents()
    warrior = pt.get('warrior') or []
    specs = {t['specialty'] for t in warrior if t['specialty']}
    assert {'Duelist', 'Shardbearer', 'Soldier'} <= specs
    agent = pt.get('agent') or []
    assert {'Investigator', 'Spy', 'Thief'} <= {t['specialty'] for t in agent if t['specialty']}
    # Every talent carries a specialty key ('' for the core/key talent).
    assert all('specialty' in t for t in warrior)


def test_builder_groups_picker_by_specialty():
    h = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()
    assert 'optgroup' in h and 't.specialty' in h and "(specialty)" in h
