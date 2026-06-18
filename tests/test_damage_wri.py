"""Regression tests for `_calculate_damage_with_wri` (app.py ~9453).

This is a life-or-death combat function: it applies a monster/NPC's immunities,
resistances, and weaknesses (WRI) to an incoming damage amount and decides how
much actually lands. It also drives `_parse_damage_type_value` (app.py ~9438),
which parses entries like ``"fire 5"`` or ``"slashing 10 (except adamantine)"``.

These tests lock CURRENT behavior. The function reads exactly three attributes
off the combatant -- ``immunities``, ``resistances``, ``weaknesses`` -- via
``getattr(combatant, attr, [])``, so the cleanest CI-safe vehicle is a tiny stub
object exposing just those lists (no live PARTY_LIBRARY / party_data needed). A
couple of tests additionally drive the real ``app.Monster`` constructor to prove
the Foundry-format string assembly feeds the function correctly end-to-end.

NB on the data model the function expects:
  - immunities: list of plain type strings, e.g. ["fire", "physical"]
  - resistances / weaknesses: list of "<type> <value>" strings, optionally with
    a "(except <materials>)" clause on resistances.
"""

from __future__ import annotations

import app


# --------------------------------------------------------------------------
# Minimal combatant stub -- exactly the attributes the function getattr's.
# --------------------------------------------------------------------------
class WRIStub:
    def __init__(self, immunities=None, resistances=None, weaknesses=None):
        self.immunities = list(immunities or [])
        self.resistances = list(resistances or [])
        self.weaknesses = list(weaknesses or [])


def calc(amount, dtype, *, immunities=None, resistances=None, weaknesses=None):
    """Shorthand: run the function against a freshly built stub."""
    stub = WRIStub(immunities=immunities, resistances=resistances, weaknesses=weaknesses)
    return app._calculate_damage_with_wri(amount, dtype, stub)


# ==========================================================================
# Untyped / empty damage_type -> unchanged, empty log
# ==========================================================================

def test_untyped_string_returns_unchanged():
    eff, notes = calc(20, 'untyped', resistances=['fire 5'], weaknesses=['fire 5'])
    assert eff == 20
    assert notes == []


def test_empty_damage_type_returns_unchanged():
    eff, notes = calc(13, '', immunities=['fire'])
    assert eff == 13
    assert notes == []


def test_none_damage_type_returns_unchanged():
    eff, notes = calc(7, None, weaknesses=['slashing 10'])
    assert eff == 7
    assert notes == []


def test_no_wri_at_all_passes_damage_through():
    eff, notes = calc(15, 'fire')
    assert eff == 15
    assert notes == []


def test_zero_amount_with_no_wri():
    eff, notes = calc(0, 'fire')
    assert eff == 0
    assert notes == []


# ==========================================================================
# Immunity -- exact type match -> 0 damage
# ==========================================================================

def test_immunity_exact_type_zeroes_damage():
    eff, notes = calc(40, 'fire', immunities=['fire'])
    assert eff == 0
    assert notes == ['IMMUNE to fire']


def test_immunity_is_case_insensitive_on_both_sides():
    # damage_type uppercased, immunity entry uppercased -> still matches.
    eff, notes = calc(40, 'FIRE', immunities=['Fire'])
    assert eff == 0
    # Note preserves the (uppercased) original damage_type spelling.
    assert notes == ['IMMUNE to FIRE']


def test_immunity_strips_whitespace_on_damage_type():
    eff, notes = calc(10, '  poison  ', immunities=['poison'])
    assert eff == 0
    assert notes == ['IMMUNE to   poison  ']


def test_immunity_unmatched_type_does_not_block():
    # Immune to fire, but taking cold -> not blocked.
    eff, notes = calc(12, 'cold', immunities=['fire'])
    assert eff == 12
    assert notes == []


def test_immunity_takes_priority_over_resistance_and_weakness():
    eff, notes = calc(30, 'fire',
                      immunities=['fire'],
                      resistances=['fire 5'],
                      weaknesses=['fire 10'])
    assert eff == 0
    # Short-circuits: only the immunity note, nothing about resist/weak.
    assert notes == ['IMMUNE to fire']


# ==========================================================================
# 'physical' immunity category -> blocks bludgeoning/piercing/slashing only
# ==========================================================================

def test_physical_immunity_blocks_bludgeoning():
    eff, notes = calc(25, 'bludgeoning', immunities=['physical'])
    assert eff == 0
    assert notes == ['IMMUNE to physical (bludgeoning)']


def test_physical_immunity_blocks_piercing():
    eff, notes = calc(25, 'piercing', immunities=['physical'])
    assert eff == 0
    assert notes == ['IMMUNE to physical (piercing)']


def test_physical_immunity_blocks_slashing():
    eff, notes = calc(25, 'slashing', immunities=['physical'])
    assert eff == 0
    assert notes == ['IMMUNE to physical (slashing)']


def test_physical_immunity_does_not_block_energy():
    # 'physical' immunity must NOT zero fire damage.
    eff, notes = calc(25, 'fire', immunities=['physical'])
    assert eff == 25
    assert notes == []


def test_physical_immunity_does_not_block_mental():
    eff, notes = calc(25, 'mental', immunities=['physical'])
    assert eff == 25
    assert notes == []


def test_exact_immunity_checked_before_physical_category():
    # 'piercing' listed explicitly -> matched by the exact-type branch,
    # producing the plain note (not the "physical (...)" note).
    eff, notes = calc(18, 'piercing', immunities=['piercing', 'physical'])
    assert eff == 0
    assert notes == ['IMMUNE to piercing']


# ==========================================================================
# Resistance -- subtracts value, floored at 0, only first match
# ==========================================================================

def test_resistance_subtracts_value():
    eff, notes = calc(20, 'fire', resistances=['fire 5'])
    assert eff == 15
    assert notes == ['Resist fire 5']


def test_resistance_floors_at_zero_never_negative():
    eff, notes = calc(3, 'fire', resistances=['fire 5'])
    assert eff == 0  # max(0, 3 - 5)
    assert notes == ['Resist fire 5']


def test_resistance_exactly_reduces_to_zero():
    eff, notes = calc(5, 'fire', resistances=['fire 5'])
    assert eff == 0
    assert notes == ['Resist fire 5']


def test_resistance_unmatched_type_no_effect():
    eff, notes = calc(20, 'cold', resistances=['fire 5'])
    assert eff == 20
    assert notes == []


def test_resistance_only_first_matching_applies():
    # Two fire resistances listed; only the FIRST (5) applies due to `break`.
    eff, notes = calc(20, 'fire', resistances=['fire 5', 'fire 10'])
    assert eff == 15
    assert notes == ['Resist fire 5']


def test_resistance_value_zero_is_noted_but_no_reduction():
    # An entry with no parseable value parses to value 0.
    eff, notes = calc(20, 'fire', resistances=['fire'])
    assert eff == 20
    assert notes == ['Resist fire 0']


def test_resistance_is_case_insensitive():
    eff, notes = calc(20, 'FIRE', resistances=['Fire 5'])
    assert eff == 15
    # Note echoes the original (uppercased) damage_type label.
    assert notes == ['Resist FIRE 5']


# ==========================================================================
# Resistance category matching: 'physical' and 'all'
# ==========================================================================

def test_resistance_physical_matches_slashing():
    eff, notes = calc(20, 'slashing', resistances=['physical 5'])
    assert eff == 15
    assert notes == ['Resist slashing 5']


def test_resistance_physical_matches_bludgeoning_and_piercing():
    for dt in ('bludgeoning', 'piercing'):
        eff, notes = calc(20, dt, resistances=['physical 7'])
        assert eff == 13, dt
        assert notes == [f'Resist {dt} 7'], dt


def test_resistance_physical_does_not_match_energy():
    eff, notes = calc(20, 'fire', resistances=['physical 5'])
    assert eff == 20
    assert notes == []


def test_resistance_all_matches_any_type():
    for dt in ('fire', 'slashing', 'mental', 'poison'):
        eff, notes = calc(20, dt, resistances=['all 5'])
        assert eff == 15, dt
        assert notes == [f'Resist {dt} 5'], dt


def test_resistance_all_is_first_match_and_breaks():
    # 'all' matches first; a later specific entry never runs (break).
    eff, notes = calc(20, 'fire', resistances=['all 5', 'fire 10'])
    assert eff == 15
    assert notes == ['Resist fire 5']


# ==========================================================================
# Resistance exception clause -- parsed, but IGNORED for incoming damage
# ==========================================================================

def test_resistance_with_except_clause_still_applies():
    # "resistance 5 (except cold iron)": the function comments that it does not
    # track the attacking weapon's material on incoming damage, so the exception
    # never fires -- the resistance applies regardless.
    eff, notes = calc(20, 'slashing', resistances=['slashing 5 (except cold iron)'])
    assert eff == 15
    assert notes == ['Resist slashing 5']


def test_resistance_physical_with_except_adamantine_still_applies():
    eff, notes = calc(20, 'bludgeoning',
                      resistances=['physical 10 (except adamantine)'])
    assert eff == 10
    assert notes == ['Resist bludgeoning 10']


def test_parse_strips_except_clause_from_type_and_value():
    # Direct check of the parse helper: exception clause is stripped out and the
    # type/value parse correctly around it.
    rtype, rval, exceptions = app._parse_damage_type_value('slashing 10 (except adamantine)')
    assert rtype == 'slashing'
    assert rval == 10
    assert exceptions == ['adamantine']


def test_parse_multiple_exceptions_split_on_comma():
    rtype, rval, exceptions = app._parse_damage_type_value(
        'physical 5 (except cold iron, silver)')
    assert rtype == 'physical'
    assert rval == 5
    assert exceptions == ['cold iron', 'silver']


def test_parse_no_value_returns_zero():
    rtype, rval, exceptions = app._parse_damage_type_value('fire')
    assert rtype == 'fire'
    assert rval == 0
    assert exceptions == []


def test_parse_non_numeric_trailing_token_is_not_a_value():
    # "fire aura" -> isdigit() false -> whole string is the type, value 0.
    rtype, rval, exceptions = app._parse_damage_type_value('fire aura')
    assert rtype == 'fire aura'
    assert rval == 0
    assert exceptions == []


# ==========================================================================
# Weakness -- adds value, only first match
# ==========================================================================

def test_weakness_adds_value():
    eff, notes = calc(20, 'fire', weaknesses=['fire 5'])
    assert eff == 25
    assert notes == ['Weak fire +5']


def test_weakness_unmatched_type_no_effect():
    eff, notes = calc(20, 'cold', weaknesses=['fire 5'])
    assert eff == 20
    assert notes == []


def test_weakness_only_first_matching_applies():
    eff, notes = calc(20, 'fire', weaknesses=['fire 5', 'fire 10'])
    assert eff == 25
    assert notes == ['Weak fire +5']


def test_weakness_zero_amount_can_still_take_weakness():
    # Edge: 0 damage but a weakness -- PF2e applies weakness even to 0? Here the
    # raw 0 flows through and weakness adds.
    eff, notes = calc(0, 'fire', weaknesses=['fire 5'])
    assert eff == 5
    assert notes == ['Weak fire +5']


def test_weakness_physical_matches_slashing():
    eff, notes = calc(10, 'slashing', weaknesses=['physical 5'])
    assert eff == 15
    assert notes == ['Weak slashing +5']


def test_weakness_physical_does_not_match_energy():
    eff, notes = calc(10, 'fire', weaknesses=['physical 5'])
    assert eff == 10
    assert notes == []


def test_weakness_all_category_does_NOT_match():
    # POSSIBLE BUG: resistances honor an 'all' category but weaknesses do not --
    # the weakness loop only checks exact type or 'physical'. A creature with
    # "weakness all 5" (PF2e: weakness to all damage, e.g. some constructs/swarms
    # vs area) would take NO extra damage here. Locking current behavior.
    eff, notes = calc(20, 'fire', weaknesses=['all 5'])
    assert eff == 20
    assert notes == []


def test_weakness_is_case_insensitive():
    eff, notes = calc(20, 'FIRE', weaknesses=['Fire 5'])
    assert eff == 25
    assert notes == ['Weak FIRE +5']


def test_weakness_with_value_zero_adds_nothing_but_notes():
    eff, notes = calc(20, 'fire', weaknesses=['fire'])
    assert eff == 20
    assert notes == ['Weak fire +0']


# ==========================================================================
# Ordering -- resistance applied BEFORE weakness
# ==========================================================================

def test_resistance_then_weakness_order():
    # 20 - resist 5 = 15, then + weak 10 = 25. (If weakness ran first it'd be
    # 20 + 10 = 30 then - 5 = 25 -- same here, so use an asymmetric case below.)
    eff, notes = calc(20, 'fire', resistances=['fire 5'], weaknesses=['fire 10'])
    assert eff == 25
    assert notes == ['Resist fire 5', 'Weak fire +10']


def test_order_matters_when_resistance_would_floor():
    # Resist applied first: max(0, 3 - 5) = 0, then + weak 4 = 4.
    # If weakness ran first: (3 + 4) = 7, then max(0, 7 - 5) = 2. The function
    # gives 4, proving resistance is applied first (and floored before the add).
    eff, notes = calc(3, 'fire', resistances=['fire 5'], weaknesses=['fire 4'])
    assert eff == 4
    assert notes == ['Resist fire 5', 'Weak fire +4']


def test_resist_and_weak_both_physical_category():
    eff, notes = calc(20, 'slashing',
                      resistances=['physical 5'],
                      weaknesses=['physical 3'])
    assert eff == 18  # 20 - 5 + 3
    assert notes == ['Resist slashing 5', 'Weak slashing +3']


def test_resist_one_type_weak_another_only_relevant_one_fires():
    # Resist cold, weak fire; incoming fire -> only the weakness fires.
    eff, notes = calc(20, 'fire', resistances=['cold 5'], weaknesses=['fire 5'])
    assert eff == 25
    assert notes == ['Weak fire +5']


# ==========================================================================
# Return-shape contract
# ==========================================================================

def test_returns_tuple_of_int_and_list():
    result = calc(20, 'fire', resistances=['fire 5'])
    assert isinstance(result, tuple) and len(result) == 2
    eff, notes = result
    assert isinstance(eff, int)
    assert isinstance(notes, list)
    assert all(isinstance(n, str) for n in notes)


def test_notes_empty_list_when_nothing_applies():
    eff, notes = calc(20, 'fire', resistances=['cold 5'], weaknesses=['cold 5'])
    assert eff == 20
    assert notes == []


# ==========================================================================
# End-to-end through the real Monster constructor (Foundry format -> strings)
# ==========================================================================

def _monster_with(resistances=None, weaknesses=None, immunities=None):
    """Build a real app.Monster whose WRI lists are assembled by its
    constructor from Foundry-style dict entries. This proves the exact string
    shapes the constructor emits ("fire 5", "slashing 10 (except adamantine)")
    are the shapes `_calculate_damage_with_wri` consumes."""
    data = {
        'name': 'Test Beast',
        'system': {
            'attributes': {
                'immunities': immunities or [],
                'resistances': resistances or [],
                'weaknesses': weaknesses or [],
            }
        }
    }
    return app.Monster(data)


def test_monster_constructor_resistance_feeds_function():
    m = _monster_with(resistances=[{'type': 'fire', 'value': 5}])
    assert m.resistances == ['fire 5']
    eff, notes = app._calculate_damage_with_wri(20, 'fire', m)
    assert eff == 15
    assert notes == ['Resist fire 5']


def test_monster_constructor_weakness_feeds_function():
    m = _monster_with(weaknesses=[{'type': 'fire', 'value': 10}])
    assert m.weaknesses == ['fire 10']
    eff, notes = app._calculate_damage_with_wri(20, 'fire', m)
    assert eff == 30
    assert notes == ['Weak fire +10']


def test_monster_constructor_immunity_feeds_function():
    m = _monster_with(immunities={'value': ['fire', 'poison']})
    assert 'fire' in m.immunities
    eff, notes = app._calculate_damage_with_wri(40, 'poison', m)
    assert eff == 0
    assert notes == ['IMMUNE to poison']


def test_monster_constructor_resistance_with_exceptions_feeds_function():
    m = _monster_with(resistances=[
        {'type': 'physical', 'value': 10, 'exceptions': ['adamantine']}])
    assert m.resistances == ['physical 10 (except adamantine)']
    # Exception is parsed but ignored for incoming damage -> resistance applies.
    eff, notes = app._calculate_damage_with_wri(25, 'bludgeoning', m)
    assert eff == 15
    assert notes == ['Resist bludgeoning 10']


def test_monster_full_wri_stack_end_to_end():
    m = _monster_with(
        immunities={'value': ['void']},
        resistances=[{'type': 'physical', 'value': 5}],
        weaknesses=[{'type': 'fire', 'value': 5}],
    )
    # Physical resistance applies to slashing.
    eff, notes = app._calculate_damage_with_wri(20, 'slashing', m)
    assert eff == 15 and notes == ['Resist slashing 5']
    # Fire weakness applies; physical resist does not touch fire.
    eff, notes = app._calculate_damage_with_wri(20, 'fire', m)
    assert eff == 25 and notes == ['Weak fire +5']
    # Void immunity zeroes void damage.
    eff, notes = app._calculate_damage_with_wri(99, 'void', m)
    assert eff == 0 and notes == ['IMMUNE to void']
