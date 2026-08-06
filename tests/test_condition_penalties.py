"""PF2e condition status/circumstance-penalty math on the Character class.

These numbers decide whether a PC lives or dies: frightened/sickened shave AC,
saves, perception, attacks and DCs; off_guard/prone are -2 circumstance to AC
(and prone is -2 to attacks); enfeebled drops STR-based rolls/damage; clumsy
drops DEX-based (AC/Reflex); stupefied drops spellcasting; drained drops
Fortitude AND lops N*level off max HP (clamping current HP).

The penalty resolver lives at app.py:get_status_penalty (~3586). It is a
*max*, not a sum — frightened 1 + clumsy 3 on Reflex is -3, never -4. The per-
ability "base" is max(frightened, sickened), then the relevant ability folds in
its own condition: str→enfeebled, dex→clumsy(+encumbrance), con→drained,
int/wis/cha→stupefied.

Every assertion below is anchored to a recomputed baseline from the same PC, so
the magnitude of each delta is checked exactly. The save/perception/AC/DC/skill/
attack getters are live @property reads of self.conditions, so mutating
pc.conditions recomputes them on the next access. Drained's max-HP hit is the
one exception — it is applied once in __init__ — so those cases inject the
condition into the build dict before constructing the Character.

CI-safe: each PC is built from a committed fixture via Character(...), never
from PARTY_LIBRARY / live party_data.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from app import Character

_FIX_DIR = pathlib.Path(__file__).parent / 'fixtures'
_GOEL = _FIX_DIR / 'goel_l10.json'          # Cleric L10: STR attacks, WIS divine caster
_AMADEUS = _FIX_DIR / 'amadeus_l11.json'    # Champion L11
_GAVIN = _FIX_DIR / 'gavin_l11.json'        # Kineticist L11


def _build(fixture_path, conditions=None):
    """Construct a Character from a fixture, optionally seeding build conditions
    (needed only for drained, whose max-HP effect is applied in __init__)."""
    raw = copy.deepcopy(json.loads(fixture_path.read_text()))
    if conditions is not None:
        raw['build']['conditions'] = conditions
    return Character(raw, file_path=str(fixture_path))


@pytest.fixture
def goel():
    return _build(_GOEL)


def _athletics(pc):
    """Goel's Athletics total as an int (str-based skill, good enfeebled probe)."""
    return int(next(s['total'] for s in pc.skills if s['name'] == 'Athletics'))


def _skill(pc, name):
    return next(s for s in pc.skills if s['name'] == name)


# ──────────────────────────────────────────────────────────────────────────
# Baseline: a clean fixture has no conditions and zero penalty everywhere.
# ──────────────────────────────────────────────────────────────────────────

def test_clean_pc_has_no_status_penalty(goel):
    assert goel.get_status_penalty() == 0
    for ab in ('str', 'dex', 'con', 'int', 'wis', 'cha'):
        assert goel.get_status_penalty(ab) == 0
    assert goel.status_penalty == 0
    # No encumbrance on the committed fixture, so no baked-in clumsy.
    assert goel.clumsy_penalty == 0


# ──────────────────────────────────────────────────────────────────────────
# Frightened: -value to AC, all saves, perception, attacks, class DC, spell DC.
# ──────────────────────────────────────────────────────────────────────────

def test_frightened_2_hits_every_check(goel):
    base = dict(ac=goel.ac, fort=goel.fort, ref=goel.ref, will=goel.will,
                perc=goel.perception, class_dc=goel.class_dc,
                spell_attack=goel.spell_attack, spell_dc=goel.spell_dc,
                attack=goel.attacks[0]['strikes'][0]['mod'],
                athletics=_athletics(goel))
    goel.conditions['frightened'] = 2
    assert goel.ac == base['ac'] - 2
    assert goel.fort == base['fort'] - 2
    assert goel.ref == base['ref'] - 2
    assert goel.will == base['will'] - 2
    assert goel.perception == base['perc'] - 2
    assert goel.class_dc == base['class_dc'] - 2
    assert goel.spell_attack == base['spell_attack'] - 2
    assert goel.spell_dc == base['spell_dc'] - 2
    assert goel.attacks[0]['strikes'][0]['mod'] == base['attack'] - 2
    assert _athletics(goel) == base['athletics'] - 2
    assert goel.get_status_penalty() == 2
    assert goel.status_penalty == 2


def test_frightened_0_is_no_penalty(goel):
    base_ac = goel.ac
    goel.conditions['frightened'] = 0
    assert goel.ac == base_ac
    assert goel.get_status_penalty() == 0


def test_frightened_scales_with_value(goel):
    base_ac = goel.ac
    for v in (1, 2, 3, 4):
        goel.conditions['frightened'] = v
        assert goel.ac == base_ac - v
        assert goel.get_status_penalty() == v


# ──────────────────────────────────────────────────────────────────────────
# Sickened behaves identically to frightened (shares the max() base).
# ──────────────────────────────────────────────────────────────────────────

def test_sickened_hits_ac_saves_perception(goel):
    base = dict(ac=goel.ac, fort=goel.fort, will=goel.will, perc=goel.perception)
    goel.conditions['sickened'] = 1
    assert goel.ac == base['ac'] - 1
    assert goel.fort == base['fort'] - 1
    assert goel.will == base['will'] - 1
    assert goel.perception == base['perc'] - 1
    assert goel.status_penalty == 1


def test_frightened_and_sickened_take_the_max_not_the_sum(goel):
    base_ac = goel.ac
    goel.conditions['frightened'] = 1
    goel.conditions['sickened'] = 3
    # max(1, 3) = 3, not 1 + 3 = 4.
    assert goel.ac == base_ac - 3
    assert goel.get_status_penalty() == 3


# ──────────────────────────────────────────────────────────────────────────
# Off-guard / prone: -2 CIRCUMSTANCE to AC (distinct from the status track).
# ──────────────────────────────────────────────────────────────────────────

def test_off_guard_is_minus_2_circumstance_ac_only(goel):
    base = dict(ac=goel.ac, fort=goel.fort, ref=goel.ref,
                attack=goel.attacks[0]['strikes'][0]['mod'])
    goel.conditions['off_guard'] = True
    assert goel.ac == base['ac'] - 2
    # off_guard is an AC-only circumstance penalty here: saves + the PC's own
    # attack roll are untouched.
    assert goel.fort == base['fort']
    assert goel.ref == base['ref']
    assert goel.attacks[0]['strikes'][0]['mod'] == base['attack']
    # It is a circumstance penalty, not the status track.
    assert goel.get_status_penalty('dex') == 0


def test_prone_is_minus_2_ac_and_minus_2_to_attacks(goel):
    base = dict(ac=goel.ac, fort=goel.fort,
                attack=goel.attacks[0]['strikes'][0]['mod'])
    goel.conditions['prone'] = True
    assert goel.ac == base['ac'] - 2
    # Prone also applies -2 circumstance to the PC's own attack rolls.
    assert goel.attacks[0]['strikes'][0]['mod'] == base['attack'] - 2
    assert goel.fort == base['fort']


def test_prone_and_off_guard_dont_stack_on_ac(goel):
    base_ac = goel.ac
    goel.conditions['prone'] = True
    goel.conditions['off_guard'] = True
    # AC uses `2 if (prone or off_guard)` — a single -2, never -4.
    assert goel.ac == base_ac - 2


def test_off_guard_and_frightened_stack_status_plus_circumstance(goel):
    base_ac = goel.ac
    goel.conditions['frightened'] = 2     # -2 status
    goel.conditions['off_guard'] = True   # -2 circumstance
    # Different penalty types DO stack: -2 status + -2 circumstance = -4.
    assert goel.ac == base_ac - 4


# ──────────────────────────────────────────────────────────────────────────
# Enfeebled: STR-based only (attack to-hit, melee STR damage, Athletics).
# Does NOT touch DEX-based AC.
# ──────────────────────────────────────────────────────────────────────────

def test_enfeebled_reduces_str_attack_and_damage(goel):
    base_attack = goel.attacks[0]['strikes'][0]['mod']
    base_dmg = goel.attacks[0]['damage']          # e.g. "1d12 + 4S"
    assert '+ 4' in base_dmg                        # sanity: STR +4 baked into the fixture
    goel.conditions['enfeebled'] = 3
    assert goel.attacks[0]['strikes'][0]['mod'] == base_attack - 3
    # STR damage mod drops by 3: 1d12 + 4 -> 1d12 + 1.
    assert '+ 1' in goel.attacks[0]['damage']
    assert '+ 4' not in goel.attacks[0]['damage']
    assert goel.get_status_penalty('str') == 3


def test_enfeebled_reduces_str_skill_athletics(goel):
    base = _athletics(goel)
    goel.conditions['enfeebled'] = 2
    assert _athletics(goel) == base - 2


def test_enfeebled_does_not_touch_dex_ac_or_reflex(goel):
    base = dict(ac=goel.ac, ref=goel.ref)
    goel.conditions['enfeebled'] = 4
    assert goel.ac == base['ac']
    assert goel.ref == base['ref']
    assert goel.get_status_penalty('dex') == 0


def test_enfeebled_can_drive_damage_modifier_negative(goel):
    # STR mod is +4; enfeebled 6 should push the melee damage mod to -2.
    goel.conditions['enfeebled'] = 6
    dmg = goel.attacks[0]['damage']
    assert '- 2' in dmg, dmg


# ──────────────────────────────────────────────────────────────────────────
# Clumsy: DEX-based — AC, Reflex, DEX skills. Does NOT touch Fortitude/Will.
# ──────────────────────────────────────────────────────────────────────────

def test_clumsy_reduces_ac_and_reflex(goel):
    base = dict(ac=goel.ac, ref=goel.ref, fort=goel.fort, will=goel.will)
    goel.conditions['clumsy'] = 2
    assert goel.ac == base['ac'] - 2
    assert goel.ref == base['ref'] - 2
    # Fort (con) and Will (wis) are untouched by clumsy.
    assert goel.fort == base['fort']
    assert goel.will == base['will']
    assert goel.get_status_penalty('dex') == 2


def test_clumsy_reduces_dex_skills(goel):
    base = int(_skill(goel, 'Acrobatics')['total'])
    goel.conditions['clumsy'] = 3
    assert int(_skill(goel, 'Acrobatics')['total']) == base - 3
    assert _skill(goel, 'Acrobatics')['penalty'] == 3


def test_frightened_and_clumsy_take_max_on_reflex(goel):
    base_ref = goel.ref
    goel.conditions['frightened'] = 1
    goel.conditions['clumsy'] = 3
    # Reflex (dex) = max(base=1, clumsy=3) = 3.
    assert goel.ref == base_ref - 3
    assert goel.get_status_penalty('dex') == 3


# ──────────────────────────────────────────────────────────────────────────
# Stupefied: spellcasting (spell attack + spell DC). The fixture's caster
# ability is WIS, so stupefied folds into get_status_penalty('wis').
# ──────────────────────────────────────────────────────────────────────────

def test_stupefied_reduces_spell_dc_and_attack(goel):
    base = dict(spell_attack=goel.spell_attack, spell_dc=goel.spell_dc)
    goel.conditions['stupefied'] = 2
    assert goel.spell_attack == base['spell_attack'] - 2
    assert goel.spell_dc == base['spell_dc'] - 2
    assert goel.get_status_penalty('wis') == 2


def test_stupefied_does_not_reduce_class_dc(goel):
    # class_dc calls get_status_penalty() with NO ability arg, which only sees
    # frightened/sickened — stupefied is not folded in. (RAW: stupefied also
    # affects spell DCs but class DC is a separate proficiency; this matches.)
    base = goel.class_dc
    goel.conditions['stupefied'] = 3
    assert goel.class_dc == base


def test_stupefied_does_not_touch_fortitude_or_reflex(goel):
    base = dict(fort=goel.fort, ref=goel.ref, ac=goel.ac)
    goel.conditions['stupefied'] = 2
    assert goel.fort == base['fort']   # con
    assert goel.ref == base['ref']     # dex
    assert goel.ac == base['ac']       # dex


def test_stupefied_bleeds_into_will_save():
    # POSSIBLE BUG (vs PF2e RAW): stupefied is a WIS-keyed condition in the
    # resolver, and Will = _calc_save('wis', 'will') subtracts
    # get_status_penalty('wis'). So stupefied silently penalizes Will saves,
    # which RAW does NOT do (stupefied only affects spellcasting/concentration,
    # not Will saves). Locking the current behavior.
    pc = _build(_GOEL)
    base_will = pc.will
    pc.conditions['stupefied'] = 2
    assert pc.will == base_will - 2
    assert pc.get_status_penalty('wis') == 2


# ──────────────────────────────────────────────────────────────────────────
# Drained: -value to Fortitude AND -value*level to max HP (clamps current HP).
# The max-HP effect is applied in __init__, so seed it into the build.
# ──────────────────────────────────────────────────────────────────────────

def test_drained_reduces_max_hp_by_value_times_level():
    clean = _build(_GOEL)
    base_hp = clean.hp
    pc = _build(_GOEL, conditions={'drained': 2})
    assert pc.hp == base_hp - 2 * pc.level
    # current_hp defaults to (reduced) max when no explicit current is stored.
    assert pc.current_hp == pc.hp


def test_drained_clamps_current_hp_to_reduced_max():
    # Stored current_hp (130) sits above the drained-reduced max; it must clamp.
    raw = copy.deepcopy(json.loads(_GOEL.read_text()))
    raw['build']['conditions'] = {'drained': 3}
    raw['build']['current_hp'] = 130
    pc = Character(raw, file_path=str(_GOEL))
    expected_max = pc.hp                       # base - 3*level
    assert pc.current_hp == expected_max
    assert pc.current_hp <= pc.hp


def test_drained_reduces_fortitude():
    clean = _build(_GOEL)
    base_fort = clean.fort
    pc = _build(_GOEL, conditions={'drained': 2})
    assert pc.fort == base_fort - 2
    assert pc.get_status_penalty('con') == 2


def test_drained_does_not_touch_reflex_or_will():
    clean = _build(_GOEL)
    base = dict(ref=clean.ref, will=clean.will, ac=clean.ac)
    pc = _build(_GOEL, conditions={'drained': 3})
    assert pc.ref == base['ref']
    assert pc.will == base['will']
    assert pc.ac == base['ac']


def test_drained_zero_is_full_hp():
    clean = _build(_GOEL)
    pc = _build(_GOEL, conditions={'drained': 0})
    assert pc.hp == clean.hp


def test_drained_hp_does_not_recompute_when_mutated_after_init(goel):
    # Mutating pc.conditions['drained'] after construction does NOT re-trim max
    # HP (the HP math runs once in __init__). It DOES affect the live Fort save,
    # though. Locking this asymmetry so a refactor doesn't silently change it.
    base_hp = goel.hp
    base_fort = goel.fort
    goel.conditions['drained'] = 4
    assert goel.hp == base_hp                 # max HP unchanged post-init
    assert goel.fort == base_fort - 4         # save penalty is live


# ──────────────────────────────────────────────────────────────────────────
# Drained + frightened both land on Fortitude (con): take the max, not the sum.
# ──────────────────────────────────────────────────────────────────────────

def test_drained_and_frightened_take_max_on_fortitude():
    pc = _build(_GOEL, conditions={'drained': 3})
    base_fort = pc.fort                        # already -3 from drained
    pc.conditions['frightened'] = 1
    # Fort (con) = max(base=max(fright1)=1, drained=3) = 3, so fright 1 adds
    # nothing on top of drained 3.
    assert pc.fort == base_fort
    assert pc.get_status_penalty('con') == 3


def test_frightened_exceeds_drained_on_fortitude():
    pc = _build(_GOEL, conditions={'drained': 1})
    pc.conditions['frightened'] = 4
    # Fort = max(frightened 4, drained 1) = 4.
    assert pc.get_status_penalty('con') == 4


# ──────────────────────────────────────────────────────────────────────────
# The resolver directly: every ability folds in exactly its own condition,
# always via max() against the frightened/sickened base.
# ──────────────────────────────────────────────────────────────────────────

def test_get_status_penalty_resolver_per_ability(goel):
    goel.conditions.update({
        'frightened': 2, 'sickened': 0, 'enfeebled': 5,
        'clumsy': 4, 'stupefied': 3, 'drained': 1,
    })
    base = max(2, 0)
    assert goel.get_status_penalty() == base            # frightened/sickened only
    assert goel.get_status_penalty('str') == max(base, 5)   # +enfeebled -> 5
    assert goel.get_status_penalty('dex') == max(base, 4)   # +clumsy    -> 4
    assert goel.get_status_penalty('con') == max(base, 1)   # +drained   -> 2 (base wins)
    assert goel.get_status_penalty('wis') == max(base, 3)   # +stupefied -> 3
    assert goel.get_status_penalty('int') == max(base, 3)
    assert goel.get_status_penalty('cha') == max(base, 3)


def test_base_wins_when_larger_than_ability_condition(goel):
    # frightened 5 dominates a small enfeebled/clumsy/etc.
    goel.conditions.update({'frightened': 5, 'enfeebled': 1, 'clumsy': 1,
                            'drained': 1, 'stupefied': 1})
    for ab in ('str', 'dex', 'con', 'wis', 'int', 'cha'):
        assert goel.get_status_penalty(ab) == 5


def test_unknown_stat_arg_falls_back_to_base(goel):
    goel.conditions['frightened'] = 2
    goel.conditions['enfeebled'] = 9
    # An ability the resolver doesn't special-case yields just the base.
    assert goel.get_status_penalty('not_a_stat') == 2


# ──────────────────────────────────────────────────────────────────────────
# Encumbrance baked clumsy: clumsy_penalty (from is_encumbered) folds into dex
# alongside the explicit clumsy condition, taking the max.
# ──────────────────────────────────────────────────────────────────────────

def test_encumbrance_clumsy_penalty_folds_into_dex(goel):
    base_ac = goel.ac
    goel.clumsy_penalty = 2          # simulate encumbered
    assert goel.get_status_penalty('dex') == 2
    assert goel.ac == base_ac - 2


def test_explicit_clumsy_and_encumbrance_take_max(goel):
    base_ref = goel.ref
    goel.clumsy_penalty = 1
    goel.conditions['clumsy'] = 3
    # dex = max(clumsy 3, encumbrance 1) = 3.
    assert goel.get_status_penalty('dex') == 3
    assert goel.ref == base_ref - 3


# ──────────────────────────────────────────────────────────────────────────
# Cross-fixture smoke: the math holds for other classes/levels too.
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('fixture', [_AMADEUS, _GAVIN])
def test_frightened_applies_uniformly_across_fixtures(fixture):
    pc = _build(fixture)
    base = dict(ac=pc.ac, fort=pc.fort, ref=pc.ref, will=pc.will,
                perc=pc.perception, class_dc=pc.class_dc)
    pc.conditions['frightened'] = 2
    assert pc.ac == base['ac'] - 2
    assert pc.fort == base['fort'] - 2
    assert pc.ref == base['ref'] - 2
    assert pc.will == base['will'] - 2
    assert pc.perception == base['perc'] - 2
    assert pc.class_dc == base['class_dc'] - 2


@pytest.mark.parametrize('fixture', [_AMADEUS, _GAVIN])
def test_drained_reduces_max_hp_across_fixtures(fixture):
    clean = _build(fixture)
    base_hp = clean.hp
    pc = _build(fixture, conditions={'drained': 1})
    assert pc.hp == base_hp - pc.level


@pytest.mark.parametrize('fixture', [_AMADEUS, _GAVIN])
def test_off_guard_minus_2_ac_across_fixtures(fixture):
    pc = _build(fixture)
    base_ac = pc.ac
    pc.conditions['off_guard'] = True
    assert pc.ac == base_ac - 2
