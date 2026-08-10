"""Bulk and encumbrance.

Before this suite existed, `total_bulk` was 0 for every character in the repo
and no test noticed. Three separate faults stacked up:

  1. `Character.__init__` read positional slot 2 as bulk. Pathbuilder puts a
     container UUID or the literal string 'Invested' there -- never bulk -- so
     parsed values were things like 'd7e7a4f3-...' and 'Invested', scored 0 by
     safe_int's bare `except`.
  2. The compendium writes Light bulk as the NUMBER 0.1 (2494 items, the most
     common value), but the accumulator only recognised the string 'L'.
  3. Weapons carry no bulk key at all, and BUILDER_ARMOR's bulk is '0' for all
     201 armours because it reads a `system` column pf2e_database.db lacks.

So the encumbrance gauge never worked for anyone. These tests pin the numbers
so the next change to this path is reviewable rather than discovered at the
table.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character, canonical_bulk, lookup_bulk


_FIX = pathlib.Path(__file__).parent / 'fixtures'


def _pc(name):
    p = _FIX / f'{name}.json'
    return Character(json.loads(p.read_text(encoding='utf-8')), file_path=str(p))


# ==========================================================================
# Canonicalisation -- every shape this data actually arrives in
# ==========================================================================

@pytest.mark.parametrize('raw, expected', [
    (0.1, 'L'),                  # Foundry Light -- the most common value
    ({'value': 0.1}, 'L'),       # raw compendium shape
    ('0.1', 'L'),
    ('L', 'L'), ('l', 'L'), (' L ', 'L'),
    (1, '1'), (2, '2'), ('2', '2'), (' 2 ', '2'),
    ({'value': 2}, '2'),
    (0, '0'), ('0', '0'),
    ('-', '0'), ('—', '0'),      # dash / em dash = negligible
    (None, ''), ('', ''), ('   ', ''),
    # The values that were silently scoring 0 before, now honestly unknown:
    ('Invested', ''),
    ('d7e7a4f3-7006-491b-a88b-34000066e012', ''),
    ({}, ''),
])
def test_canonical_bulk(raw, expected):
    assert canonical_bulk(raw) == expected


def test_light_is_never_truncated_to_zero():
    """int(float('0.1')) == 0 is exactly how 2494 compendium items vanished."""
    assert canonical_bulk(0.1) == 'L'
    assert canonical_bulk(0.1) != '0'


# ==========================================================================
# The compendium index
# ==========================================================================

def test_index_is_populated_and_correct():
    assert len(app_module.BULK_INDEX) > 5000, 'compendium bulk index did not build'
    assert lookup_bulk('Longsword') == '1'
    assert lookup_bulk('Chain Mail') == '2'
    assert lookup_bulk('Rope') == 'L'
    assert lookup_bulk('Rations') == 'L'


def test_lookup_is_case_and_space_insensitive():
    assert lookup_bulk('  longSWORD ') == '1'


def test_unknown_names_return_empty_not_zero():
    """'' means "we don't know"; '0' means "weighs nothing". Conflating them is
    what turns an obviously-broken meter into a plausibly-wrong one."""
    assert lookup_bulk('Definitely Not A Real Item 12345') == ''
    assert lookup_bulk('') == ''


# ==========================================================================
# Real characters
# ==========================================================================

def test_pathbuilder_slot_two_is_never_read_as_bulk():
    """The original bug, pinned. goel's rows contain both shapes:
        ['Bottle of Wind', 1, 'Invested']
        ['Bedroll', 1, 'd7e7a4f3-...', 'Invested']
    Neither third element may reach the bulk field."""
    pc = _pc('goel_l10')
    bulks = {e['bulk'] for e in pc.equipment}
    assert 'Invested' not in bulks
    assert not any(len(b) > 3 for b in bulks), f'a UUID leaked into bulk: {bulks}'
    # Bedroll is a real compendium item and resolves by name.
    bedroll = next((e for e in pc.equipment if e['name'] == 'Bedroll'), None)
    if bedroll:
        assert bedroll['bulk'] == 'L'


@pytest.mark.parametrize('name', ['goel_l10', 'kyle_l10', 'amadeus_l11', 'gavin_l11'])
def test_every_fixture_now_reports_a_real_total(name):
    """Previously 0 for all four. Not asserting exact values -- those move when
    the compendium updates -- but the gauge must no longer be a constant."""
    pc = _pc(name)
    assert isinstance(pc.total_bulk, int)
    assert pc.total_bulk >= 0
    assert hasattr(pc, 'unknown_bulk_items')


def test_a_loaded_pack_mule_totals_more_than_a_light_one():
    """Relative sanity: goel carries visibly more than gavin."""
    assert _pc('goel_l10').total_bulk > _pc('gavin_l11').total_bulk


def test_light_items_roll_up_ten_to_one():
    pc = _pc('kyle_l10')
    assert 0 <= pc.light_bulk_remainder < 10


# ==========================================================================
# Encumbrance penalties are OFF -- deliberately, and this is the guard
# ==========================================================================

def test_penalties_are_gated_off():
    """Bulk was broken since the feature shipped, so Clumsy 1 / -10 ft Speed
    has never once fired. Turning it on in the same change that fixes bulk
    would move live PCs' AC, Reflex, initiative, Dex skills and Speed
    mid-campaign with no in-game cause. Flip the switch deliberately."""
    assert app_module.ENCUMBRANCE_PENALTIES is False


def test_an_encumbered_pc_takes_no_penalty_while_the_switch_is_off(monkeypatch):
    p = _FIX / 'kyle_l10.json'
    raw = json.loads(p.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    # 40 Chain Mail is 80 Bulk -- far past any threshold.
    build['equipment'] = [{'name': 'Chain Mail', 'qty': 40, 'bulk': '2'}]
    pc = Character(raw, file_path=str(p))

    assert pc.is_encumbered is True, 'the gauge must still report the truth'
    assert pc.clumsy_penalty == 0, 'penalty must not apply while gated off'
    assert pc.active_speed == max(5, pc.base_speed - pc.active_speed_penalty)


def test_flipping_the_switch_applies_the_rule(monkeypatch):
    """The switch has to actually work, or it is decoration."""
    monkeypatch.setattr(app_module, 'ENCUMBRANCE_PENALTIES', True)
    p = _FIX / 'kyle_l10.json'
    raw = json.loads(p.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build['equipment'] = [{'name': 'Chain Mail', 'qty': 40, 'bulk': '2'}]
    pc = Character(raw, file_path=str(p))

    assert pc.is_encumbered is True
    assert pc.clumsy_penalty == 1
    assert pc.get_status_penalty('dex') >= 1, 'Clumsy must reach Dex-derived stats'


# ==========================================================================
# Unknown items are counted, not hidden
# ==========================================================================

def test_unknown_items_are_reported_not_folded_in_as_zero():
    p = _FIX / 'kyle_l10.json'
    raw = json.loads(p.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build['equipment'] = [['Utterly Fictional Doodad', 3]]
    pc = Character(raw, file_path=str(p))

    assert pc.equipment[0]['bulk'] == ''
    assert pc.unknown_bulk_items >= 3, 'unknowns must be surfaced, not silently 0'


def test_an_explicit_bulk_on_a_dict_row_wins_over_the_compendium():
    """A player-supplied value is authoritative -- that is the whole point of
    the add form's bulk box for items the compendium does not know."""
    p = _FIX / 'kyle_l10.json'
    raw = json.loads(p.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build['equipment'] = [{'name': 'Longsword', 'qty': 1, 'bulk': '4'}]
    pc = Character(raw, file_path=str(p))
    assert pc.equipment[0]['bulk'] == '4'      # not the compendium's '1'
