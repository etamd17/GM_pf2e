"""Cosmere builder: the visual tree extended to the Radiant order/surge trees.

radiant_talents.radiant_tree_graphs() exposes each order's spren-bond tree + the
10 surge trees with source node positions + prerequisite edges, keyed by order-
singular slug and surge code. Ideal NODES are excluded (the Ideal track owns
them); a node gated by the Nth Ideal carries idealReq=N, and Third/Fourth-Ideal
talents carry levelReq. Nodes use iid='radiant:<name>' to match the builder's
radiant-talent convention.

Verified live: a Windrunner renders Honorspren Bond + Adhesion + Gravitation
DAGs (21 nodes); before any Ideal all locked; swearing the First Ideal unlocks 5.
"""
from __future__ import annotations

import os
import pathlib

import systems.cosmere.radiant_talents as RT

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_graphs_keyed_by_order_and_surge():
    g = RT.radiant_tree_graphs()
    assert {'windrunner', 'dustbringer', 'skybreaker'} <= set(g)     # order singulars
    assert {'adh', 'grv', 'dvs'} <= set(g)                           # surge codes
    assert any(t['name'] == 'Honorspren Bond' for t in g['windrunner'])
    assert any(t['name'] == 'Gravitation Talents' for t in g['grv'])


def test_ideal_nodes_excluded_and_gates_carried():
    g = RT.radiant_tree_graphs()
    bond = [t for t in g['windrunner'] if t['name'] == 'Honorspren Bond'][0]
    names = {n['name'] for n in bond['nodes']}
    assert not any('Ideal' in n for n in names)                      # Ideal nodes excluded
    # some node is Ideal-gated (idealReq) and nodes use the radiant: id convention
    assert any(n['idealReq'] for n in bond['nodes'])
    assert all(n['iid'].startswith('radiant:') for n in bond['nodes'])
    for n in bond['nodes']:
        assert {'x', 'y', 'deps', 'edges', 'skillReq', 'attrReq', 'levelReq', 'idealReq'} <= set(n)


def test_builder_renders_radiant_tree_with_ideal_level_gates():
    h = pathlib.Path(_REPO, 'templates', 'cosmere_builder.html').read_text()
    assert 'RADIANT_TREES' in h and 'function renderRadiantTree' in h and 'radiant-tree' in h
    # _nodeMet now honours level + Ideal gates (used by the radiant nodes)
    assert 'n.levelReq' in h and 'n.idealReq' in h and '_idealsSworn' in h
