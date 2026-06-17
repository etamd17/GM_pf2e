"""L1 initial save/perception proficiency ranks for 3 of the previously-unaudited
classes, corrected against the authoritative Foundry pf2e system data AND
independent web sources (see tools/verify_pf2e_progression.py + PF2E_RULES_AUDIT).

These were author-encoded errors: gunslinger had Fortitude trained (should be
expert); exemplar had reflex/will swapped; commander had fortitude/reflex
swapped. Ranks: 2=trained, 4=expert, 6=master, 8=legendary.
"""
from __future__ import annotations

import class_matrix as cm


def _base():
    for v in vars(cm).values():
        if (isinstance(v, dict) and isinstance(v.get('gunslinger'), dict)
                and 'base_proficiencies' in v['gunslinger']):
            return v
    raise AssertionError('class base_proficiencies table not found')


def test_unaudited_class_l1_saves_match_authoritative_source():
    base = _base()
    bp = lambda c: base[c]['base_proficiencies']

    # Gunslinger: Perception/Fortitude/Reflex expert, Will trained (G&G).
    g = bp('gunslinger')
    assert (g['perception'], g['fortitude'], g['reflex'], g['will']) == (4, 4, 4, 2)

    # Exemplar: Fortitude + Will expert; Perception + Reflex trained (War of Immortals).
    e = bp('exemplar')
    assert (e['perception'], e['fortitude'], e['reflex'], e['will']) == (2, 4, 2, 4)

    # Commander: Perception/Reflex/Will expert; Fortitude trained (War of Immortals).
    c = bp('commander')
    assert (c['perception'], c['fortitude'], c['reflex'], c['will']) == (4, 2, 4, 4)
