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


def _rank_at(cls, field, level):
    """Rank of `field` at `level` from base_proficiencies + CLASS_PROGRESSION."""
    base = _base()[cls]['base_proficiencies']
    r = base.get(field, 0)
    for lv, bumps in sorted(cm.CLASS_PROGRESSION.get(cls, {}).items()):
        if lv <= level and field in bumps:
            r = max(r, bumps[field])
    return r


def test_magus_progression_matches_secrets_of_magic():
    """Magus per-level proficiency milestones, verified vs Secrets of Magic
    Table 2-1 + the Foundry pf2e data. The old table had every save/spell bump
    at the wrong level and phantom legendary ranks the magus never gets."""
    R = lambda f, l: _rank_at('magus', f, l)
    assert R('reflex', 4) == 2 and R('reflex', 5) == 4            # Lightning Reflexes L5 (not L3)
    assert R('will', 8) == 4 and R('will', 9) == 6                # Resolve L9 (not L11)
    assert R('fortitude', 14) == 4 and R('fortitude', 15) == 6    # Juggernaut L15 (not L9)
    assert R('spell_attack', 8) == 2 and R('spell_attack', 9) == 4    # Expert Spellcaster L9 (not L7)
    assert R('spell_attack', 16) == 4 and R('spell_attack', 17) == 6  # Master Spellcaster L17 (not 15)
    assert R('perception', 8) == 2 and R('perception', 9) == 4    # Alertness L9, expert only
    # magus has NO legendary anything and no master perception
    for f in ('fortitude', 'reflex', 'will', 'spell_attack', 'spell_dc', 'simple', 'martial'):
        assert R(f, 20) <= 6, f"magus {f} should never reach legendary"
    assert R('perception', 20) == 4


def test_summoner_progression_matches_secrets_of_magic():
    """Summoner per-level milestones, verified vs Secrets of Magic Table 2-3 +
    feature text. Eidolon-only features are excluded; summoner gets no martial,
    no legendary, and simple/unarmed expertise at L11 (not L5)."""
    R = lambda f, l: _rank_at('summoner', f, l)
    assert R('perception', 2) == 2 and R('perception', 3) == 4    # Shared Vigilance L3
    assert R('reflex', 8) == 2 and R('reflex', 9) == 4            # Shared Reflexes L9 (not L3)
    assert R('fortitude', 10) == 4 and R('fortitude', 11) == 6    # Twin Juggernauts L11 (not L9)
    assert R('will', 14) == 4 and R('will', 15) == 6              # Shared Resolve L15 (not L11)
    assert R('simple', 10) == 2 and R('simple', 11) == 4          # Simple Weapon Expertise L11 (not L5)
    assert R('spell_attack', 16) == 4 and R('spell_attack', 17) == 6  # Master Spellcaster L17 (not 15)
    assert R('martial', 20) == 0                                   # summoner never trains martial
    for f in ('fortitude', 'reflex', 'will', 'spell_attack', 'spell_dc'):
        assert R(f, 20) <= 6, f"summoner {f} should never reach legendary"
