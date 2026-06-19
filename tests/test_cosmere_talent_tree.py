"""Cosmere builder: the visual talent tree (the rulebook DAG).

talents.tree_graphs() exposes each heroic path's talent trees with the source
node positions + prerequisite edges, so the builder can draw the rulebook tree
(core + 3 specialties per path) with taken / available / prerequisite-locked
node states and tap-to-pick. Verified live: Warrior renders Core + Duelist /
Shardbearer / Soldier DAGs; picking an available node takes it, a locked node
(unmet skill/talent prereq) refuses.
"""
from __future__ import annotations

import os
import pathlib

import systems.cosmere.talents as T

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_tree_graphs_per_path():
    g = T.tree_graphs()
    assert set(g) >= {'agent', 'envoy', 'hunter', 'leader', 'scholar', 'warrior'}
    warrior = g['warrior']
    specs = {t['specialty'] for t in warrior}
    assert {'Duelist', 'Shardbearer', 'Soldier'} <= specs
    assert '' in specs                                   # the core ("<Path> Talents") tree
    # No duplicate (path, name) trees after the base+handbook dedupe.
    names = [t['name'] for t in warrior]
    assert len(names) == len(set(names))


def test_nodes_carry_positions_edges_and_structured_prereqs():
    warrior = T.tree_graphs()['warrior']
    duel = [t for t in warrior if t['specialty'] == 'Duelist'][0]
    assert duel['vb'] and {'x', 'y', 'w', 'h'} <= set(duel['vb'])
    assert len(duel['nodes']) == 8
    for n in duel['nodes']:
        assert {'id', 'slug', 'name', 'iid', 'x', 'y', 'deps', 'edges', 'skillReq', 'attrReq'} <= set(n)
    # at least one node has a within-tree edge and one a skill/attr prereq
    assert any(n['edges'] for n in duel['nodes'])
    assert any(n['skillReq'] or n['attrReq'] for n in duel['nodes'])


def test_builder_renders_and_gates_the_tree():
    h = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()
    assert 'PATH_TREES' in h and 'function renderPathTree' in h and 'tree-svg' in h
    assert 'function toggleTreeTalent' in h
    # locked nodes can't be picked from the tree
    assert "g.classList.contains('locked')" in h
