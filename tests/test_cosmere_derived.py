"""Cosmere builder depth #3: attribute-derived sheet stats + Connections.

The four Ch.3 lookup tables the builder was missing — Movement Rate (Speed),
Senses Range (Awareness), Lifting/Carrying (Strength) — plus the Recovery Die
(Willpower, already in combat.py). All auto-calculate from effective attributes
and show on the sheet + the builder preview. Also adds the rulebook narrative
fields (Connections, Occupation, Relationships, Loyalties, Personality).

Verified live: str3/spd5/awa1/wil4 -> Move 40 ft, Senses 10 ft, Lift 500 lb /
carry 250, Recovery d8; Connections renders on the sheet.
"""
from __future__ import annotations

import os
import pathlib

import systems.cosmere.build as cb

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_lookup_tables_match_rulebook():
    assert [cb.movement_rate(s) for s in (0, 1, 3, 5, 7, 9)] == [20, 25, 30, 40, 60, 80]
    assert [cb.senses_range(a) for a in (0, 1, 3, 5, 7)] == [5, 10, 20, 50, 100]
    assert cb.senses_range(9) == 'Unaffected'
    assert [cb.lifting_capacity(s) for s in (0, 1, 3, 5, 7, 9)] == [100, 200, 500, 1000, 5000, 10000]
    assert [cb.carrying_capacity(s) for s in (0, 1, 3, 5, 7, 9)] == [50, 100, 250, 500, 2500, 5000]


def test_derived_stats_auto_calculate():
    b = cb.CosmereBuild({'attributes': {'str': 3, 'spd': 5, 'awa': 1, 'wil': 4}})
    d = b.derived_stats()
    assert d == {'movement': 40, 'senses': 10, 'lifting': 500, 'carrying': 250, 'recovery_die': 'd8'}


def test_narrative_fields_roundtrip():
    src = {'connections': 'Bridge Four', 'occupation': 'soldier',
           'relationships': 'Teft', 'loyalties': 'Dalinar', 'personality': 'wry'}
    b = cb.CosmereBuild(src)
    out = b.to_dict()
    for k, v in src.items():
        assert out[k] == v, k


def test_builder_and_sheet_wiring():
    bld = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()
    assert 'f-connections' in bld and 'cs-derived-pv' in bld
    sheet = pathlib.Path(_REPO, 'templates', 'cosmere_sheet.html').read_text()
    assert 'cs-derived' in sheet and 'derived.movement' in sheet and 'build.connections' in sheet
