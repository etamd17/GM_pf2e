"""Applying a supported condition must never crash the tracker.

Three separate KeyError paths in ``_apply_condition_change`` produced a live
HTTP 500 mid-combat, and the GM only saw a generic "Internal server error"
toast because the global /api/ handler swallows the traceback into the Railway
log (app.py:6182-6203):

  1. The boolean toggle read ``combatant.conditions[condition]`` bare.
  2. The PC mirror read ``combatant.conditions[condition]`` bare.
  3. ``Character.__init__`` never seeded ``undetected``, though both condition
     pickers offer it and ``Monster.__init__`` does seed it.

(1) and (2) fire on *supported* conditions -- prone, sickened -- not just
missing ones, because a PC's tracker row is rebuilt from ``build['conditions']``
and that is persisted through a truthy-only filter. After the player clears a
condition on their own sheet, the row's dict holds only the ACTIVE conditions,
and every other key is gone.

Why a new file rather than extending tests/test_tracker_sheet_sync.py: those
tests skip without party_data/ (local-only, gitignored, absent in CI) AND they
pre-seed the key they toggle (test_tracker_sheet_sync.py:168-172), which is
precisely the state that hides this bug. These drive the core function with a
deliberately SPARSE dict and run everywhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_FIXTURES = Path(__file__).parent / 'fixtures'


@pytest.fixture
def app_module():
    import app
    return app


@pytest.fixture
def sparse_encounter(app_module, monkeypatch):
    """A PC row whose conditions dict holds ONLY an active condition.

    This is not a contrived state: it is what the tracker row looks like after
    any player-sheet condition change round-trips through the truthy-only
    persist filter.
    """
    a = app_module
    monkeypatch.setattr(a, '_broadcast_pc_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, '_persist_pc_combat_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, '_persist_encounter_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, '_broadcast_encounter_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(a, '_combat_log', lambda *args, **kwargs: None)

    row = SimpleNamespace(
        instance_id='pc-sparse-1',
        name='SparsePC',
        is_pc=True,
        conditions={'frightened': 2},   # everything else absent, as in production
        condition_expiry={},
    )
    library_pc = SimpleNamespace(name='SparsePC', conditions={'frightened': 2})

    a.ACTIVE_ENCOUNTER.append(row)
    a.PARTY_LIBRARY['SparsePC'] = library_pc
    yield row, library_pc
    a.ACTIVE_ENCOUNTER.remove(row)
    a.PARTY_LIBRARY.pop('SparsePC', None)


@pytest.mark.parametrize('condition', ['prone', 'off_guard', 'concealed', 'hidden', 'undetected'])
def test_toggling_a_flag_condition_on_a_sparse_row_does_not_crash(app_module, sparse_encounter, condition):
    """Was: KeyError -> 500 on `not combatant.conditions[condition]`."""
    row, library_pc = sparse_encounter
    assert condition not in row.conditions, 'fixture must start sparse or it proves nothing'

    applied = app_module._apply_condition_change(row.instance_id, condition, 'toggle')

    assert applied is True
    assert row.conditions[condition] is True
    assert library_pc.conditions[condition] is True, 'the PC mirror must see it too'


@pytest.mark.parametrize('condition,action', [
    ('sickened', 'decrease'),
    ('prone', 'decrease'),
    ('frightened', 'remove'),
    ('stunned', 'remove'),
])
def test_no_op_and_remove_actions_on_a_sparse_row_do_not_crash(app_module, sparse_encounter, condition, action):
    """Was: KeyError -> 500 at the PC mirror, because an action that matched no
    arm left the key absent and the mirror read it with []."""
    row, _library_pc = sparse_encounter
    applied = app_module._apply_condition_change(row.instance_id, condition, action)
    assert applied is True


def test_remove_actually_clears_a_valued_condition(app_module, sparse_encounter):
    """The valued branch had no 'remove' arm at all, so the stack survived while
    the caller was told the condition had been removed. The map routes accept
    operation='remove' for every condition (app.py:7480, 7724)."""
    row, library_pc = sparse_encounter
    assert row.conditions['frightened'] == 2

    applied = app_module._apply_condition_change(row.instance_id, 'frightened', 'remove')

    assert applied is True
    assert row.conditions['frightened'] == 0
    assert library_pc.conditions['frightened'] == 0


def test_an_unknown_condition_is_refused_rather_than_faked(app_module, sparse_encounter):
    """Was: fell through both arms but still persisted, broadcast, bumped the
    stat, logged 'Grabbed -> 0' and returned True -- so the GM got a success
    toast for a condition that was never applied."""
    row, _library_pc = sparse_encounter
    applied = app_module._apply_condition_change(row.instance_id, 'grabbed', 'add')

    assert applied is False, 'an unsupported condition must not report success'
    assert 'grabbed' not in row.conditions


def test_character_and_monster_seed_the_same_condition_keys(app_module):
    """`undetected` was offered by both pickers but seeded only on Monster, so
    applying it to a PC raised KeyError. Any future condition added to one
    constructor and not the other reintroduces exactly that bug."""
    fixture = _FIXTURES / 'kyle_l10.json'
    if not fixture.is_file():
        pytest.skip('kyle_l10.json fixture not present')
    build = json.loads(fixture.read_text(encoding='utf-8'))
    pc = app_module.Character(build, file_path=str(fixture))

    missing = sorted(set(app_module.APPLICABLE_CONDITIONS) - set(pc.conditions))
    assert not missing, f'Character.__init__ does not seed: {missing}'


def test_the_valued_list_is_exactly_the_pf2e_valued_conditions(app_module):
    """PF2e Remaster defines exactly these eleven conditions as carrying a
    numeric value (persistent damage is the twelfth and has its own subsystem).
    This list being COMPLETE is why the condition gap is entirely in the flag
    half -- guard it so nobody 'extends' it with a valueless condition."""
    assert set(app_module.VALUED_CONDITIONS) == {
        'clumsy', 'doomed', 'drained', 'dying', 'enfeebled', 'frightened',
        'sickened', 'slowed', 'stunned', 'stupefied', 'wounded',
    }
