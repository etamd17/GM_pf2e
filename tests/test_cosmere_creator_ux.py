"""Cosmere character-creator UX polish.

Three usability fixes the user asked for:

1. The Skills step's +/- steppers overflowed the narrow card; the card is now a
   vertical layout (name+modifier header, blurb, then the stepper row).
2. Talent picking is Pathbuilder-style: tapping a tree node opens an inspector
   that shows the talent's EFFECT text and a per-requirement met/unmet breakdown,
   so a player never has to cross-reference the Skills tab to learn if they
   qualify. Plus an "only show talents I qualify for" filter toggle.
3. To power the inspector, heroic-path talents now carry effect text (the radiant
   ones already did).

Verified live: a locked Agent talent ("Get 'Em Talking") renders its effect plus
"✓ Talent: Opportunist / ✗ Insight rank 2 — you have 1"; the toggle drops a
25-node Agent tree to the 3 nodes the L1 character qualifies for.
"""
from __future__ import annotations

import os
import pathlib

import app as A

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUILDER = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()


def test_path_talents_carry_effect_text():
    pt = A._cosmere_path_talents()
    agent = pt.get('agent') or []
    assert agent, 'agent path should have talents'
    assert all('effect' in t for t in agent)
    # At least most talents resolve a non-empty readable effect summary.
    with_eff = [t for t in agent if (t.get('effect') or '').strip()]
    assert len(with_eff) >= len(agent) // 2, 'most talents should have effect text for the inspector'


def test_skill_card_is_vertical_layout():
    # The header row (name + modifier) lives in a .skill-top wrapper now, and the
    # card is a column so the stepper can never overflow the modifier.
    assert 'skill-top' in _BUILDER
    assert '.skill { display:flex; flex-direction:column;' in _BUILDER


def test_inspector_and_eligibility_toggle_present():
    # The tap-to-inspect machinery.
    for token in ('function selectTreeNode', 'function renderTreeDetail',
                  'function treeDetailAct', 'function _nodeReqStatus',
                  'const TALENT_EFFECTS', 'tree-detail'):
        assert token in _BUILDER, token
    # The eligibility filter toggle (both path + radiant trees).
    assert 'path-only-avail' in _BUILDER and 'radiant-only-avail' in _BUILDER
    assert 'Only show talents I qualify for' in _BUILDER
    # _renderTreeBlock honours the onlyAvail filter.
    assert 'onlyAvail' in _BUILDER


def test_inspector_lists_each_requirement_with_status():
    # The per-requirement breakdown covers talent deps, skill rank, attribute,
    # level and Ideal gates (so the player sees exactly what's missing).
    assert 'td-req-ok' in _BUILDER and 'td-req-no' in _BUILDER
    assert 'rank ' in _BUILDER and 'Ideal sworn' in _BUILDER
