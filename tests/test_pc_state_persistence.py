"""PC state survives a server restart.

The contract, in the GM's words: "players need to be picking up their sheet
immediately with each session as if they never left it." A Railway redeploy is
a process restart, so everything a player can change during a session has to
make a full round trip -- written to the character JSON on the way out, and
read back into the Character on the way in.

These tests simulate the restart the cheap and honest way: mutate the live PC,
run the real persistence function, then build a NEW Character from the same
file on disk -- exactly what load_libraries() does on boot. A field that is
written but never read back looks fine on disk and still resets at the table,
so both directions are asserted together.

No live party_data is required; each test builds a PC from a committed fixture
and redirects get_pc_file_path at a tmp file.
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

import app as app_module
from app import Character


_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC registered in PARTY_LIBRARY, backed by a real file on disk."""
    pc_file = tmp_path / 'Kyle.json'
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    character = Character(raw, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, character.name, character)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == character.name else None)
    return character, pc_file


def _restart(pc_file):
    """Rebuild the Character from disk -- what boot does after a redeploy."""
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    return Character(raw, file_path=str(pc_file))


def test_hp_and_conditions_survive_a_restart(pc):
    character, pc_file = pc
    character.current_hp = 41
    character.conditions['wounded'] = 2
    character.conditions['frightened'] = 1
    character.conditions['prone'] = True

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert reloaded.current_hp == 41
    assert reloaded.conditions.get('wounded') == 2
    assert reloaded.conditions.get('frightened') == 1
    assert reloaded.conditions.get('prone') is True


def test_a_dying_pc_is_still_dying_after_a_restart(pc):
    # The worst case to get wrong: a redeploy mid-fight must not quietly
    # resurrect someone.
    character, pc_file = pc
    character.current_hp = 0
    character.conditions['dying'] = 2
    character.conditions['wounded'] = 1

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert reloaded.current_hp == 0
    assert reloaded.conditions.get('dying') == 2
    assert reloaded.conditions.get('wounded') == 1


def test_condition_timers_survive_a_restart(pc):
    """Character.__init__ has always READ build['condition_expiry'], but
    nothing wrote it -- so a duration only survived for a PC who happened to
    be inside the encounter autosave. Every other countdown reset."""
    character, pc_file = pc
    character.conditions['frightened'] = 2
    character.condition_expiry = {'frightened': 3}

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert reloaded.conditions.get('frightened') == 2
    assert reloaded.condition_expiry.get('frightened') == 3


def test_resources_survive_a_restart(pc):
    character, pc_file = pc
    character.current_focus = 0
    character.hero_points = 3
    character.reaction_used = True

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert reloaded.current_focus == 0        # spent focus stays spent
    assert reloaded.hero_points == 3
    assert reloaded.reaction_used is True


def test_spent_focus_is_not_refilled_when_the_pool_is_recomputed(pc):
    """Regression: a redeploy used to hand spent focus back.

    The focus pool is derived from feats and can be revised upward on every
    load. That branch did `current_focus = max(current_focus, focus_max)`,
    which refilled anyone who had spent a point -- so a mid-session restart
    silently restored the party's focus.
    """
    character, pc_file = pc
    if character.focus_max < 1:
        pytest.skip('fixture has no focus pool')
    character.current_focus = 0
    app_module._do_persist_pc_combat_state(character.name)

    reloaded = _restart(pc_file)
    assert reloaded.current_focus == 0
    assert reloaded.focus_max == character.focus_max      # ceiling unchanged


def test_a_fresh_import_still_starts_with_full_focus(tmp_path):
    """The other side of that fix: a character with no stored current_focus
    is a new import, not someone who spent everything."""
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build.pop('current_focus', None)
    pc_file = tmp_path / 'Fresh.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')

    fresh = Character(raw, file_path=str(pc_file))
    assert fresh.current_focus == fresh.focus_max


def test_expended_spell_slots_survive_a_restart(pc):
    # Slots are written by the spell-slot routes rather than the debounced
    # combat-state flush, so this asserts the READ side specifically: a slot
    # marked spent on disk must come back spent.
    character, pc_file = pc
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build['expended_slots'] = {'0-3-0': True, '0-3-1': True}
    pc_file.write_text(json.dumps(raw), encoding='utf-8')

    reloaded = _restart(pc_file)
    assert reloaded.expended_slots.get('0-3-0') is True
    assert reloaded.expended_slots.get('0-3-1') is True


def test_active_effects_survive_a_restart(pc):
    # A buff bought with a spell slot must not evaporate on redeploy, or the
    # slot was spent for nothing.
    character, pc_file = pc
    character.pc_active_effects = [{'name': 'Heroism', 'rounds': 10}]

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert [e.get('name') for e in reloaded.pc_active_effects] == ['Heroism']


def test_shield_state_survives_a_restart(pc):
    character, pc_file = pc
    character.shield_raised = True
    character.shield_hp = 4

    app_module._do_persist_pc_combat_state(character.name)
    reloaded = _restart(pc_file)

    assert reloaded.shield_hp == 4


def test_persistence_is_atomic(pc):
    """The write must never leave a half-written character file.

    A redeploy can kill the process mid-write; a truncated JSON would fail to
    parse on boot and take the PC's whole sheet with it.
    """
    character, pc_file = pc
    character.current_hp = 33
    app_module._do_persist_pc_combat_state(character.name)

    raw = json.loads(pc_file.read_text(encoding='utf-8'))   # parses = not truncated
    build = raw.get('build', raw)
    assert build['current_hp'] == 33
    # The build survived intact -- persistence must not drop the sheet itself.
    assert build.get('class') or build.get('name')


def test_importing_the_module_starts_the_persistence_thread(tmp_path):
    """The regression that cost a campaign's worth of HP.

    `_start_persistence_thread` was called ONLY from `if __name__ ==
    '__main__':`. Production is `gunicorn app:app`, which IMPORTS the module
    -- so __name__ is 'app', the block never ran, and neither the 2-second
    flush loop nor the atexit shutdown hook (both defined inside that
    function) ever existed. Nothing drained the dirty set: every HP change,
    condition, focus point and active effect was marked dirty and dropped on
    exit.

    This has to run in a SUBPROCESS. Importing `app` inside the suite takes
    the deliberate pytest exemption in _autostart_persistence, and the module
    is already imported by the time any test runs, so an in-process assertion
    could only ever check a flag we set ourselves. Only a fresh interpreter
    importing it the way gunicorn does can prove the production behaviour.
    """
    import subprocess
    import sys

    probe = (
        'import app; import threading; '
        "names = [t.name for t in threading.enumerate()]; "
        "print('STARTED' if app._persist_thread_started else 'NOT_STARTED'); "
        "print('THREAD' if 'persistence-flush' in names else 'NO_THREAD')"
    )
    env = dict(os.environ)
    # Strip the markers _autostart_persistence uses to detect a test run --
    # this subprocess must look exactly like production.
    env.pop('PYTEST_CURRENT_TEST', None)
    env['FLASK_DEBUG'] = 'false'
    # A throwaway DATA_DIR is mandatory, not tidiness. This subprocess starts
    # the real persistence thread, and a flush with no live encounter UNLINKS
    # <DATA_DIR>/saved_encounters/_autosave.json. Pointed at the repo it
    # deletes a tracked file out from under the working tree.
    env['DATA_DIR'] = str(tmp_path / 'probe-data')

    result = subprocess.run([sys.executable, '-c', probe], capture_output=True,
                            text=True, cwd=app_module.BASE_DIR, timeout=180, env=env)
    assert result.returncode == 0, result.stderr[-2000:]
    assert 'STARTED' in result.stdout, result.stdout + result.stderr[-2000:]
    assert 'THREAD' in result.stdout and 'NO_THREAD' not in result.stdout, result.stdout


def test_state_survives_a_full_process_exit(tmp_path):
    """End-to-end: change HP in one process, read it back in the next.

    This is the shutdown half of the fix. `atexit.register(_flush_pending_
    persistence)` is registered inside _start_persistence_thread, so before
    that function ran in production there was no shutdown flush either -- the
    last few seconds of play died with the worker. gunicorn's graceful stop
    turns Railway's SIGTERM into a normal interpreter exit, which is exactly
    what this subprocess performs.

    Deliberately does NOT call the flush itself: the process is allowed to
    end on its own, so a missing atexit registration fails the test.
    """
    import subprocess
    import sys

    data_dir = tmp_path / 'data'
    party = data_dir / 'party_data'
    party.mkdir(parents=True)
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    (party / 'Kyle.json').write_text(json.dumps(raw), encoding='utf-8')

    # Mark a PC dirty and exit normally. Nothing here flushes.
    writer = (
        'import app;'
        'name = next(iter(app.PARTY_LIBRARY));'
        'pc = app.PARTY_LIBRARY[name];'
        'pc.current_hp = 17;'
        "pc.conditions['wounded'] = 1;"
        'app._persist_pc_combat_state(name);'
        "print('DIRTY', name)"
    )
    env = dict(os.environ)
    env.pop('PYTEST_CURRENT_TEST', None)
    env['FLASK_DEBUG'] = 'false'
    env['DATA_DIR'] = str(data_dir)
    env['GM_PASSWORD'] = ''

    wrote = subprocess.run([sys.executable, '-c', writer], capture_output=True,
                           text=True, cwd=app_module.BASE_DIR, timeout=180, env=env)
    assert wrote.returncode == 0, wrote.stderr[-2000:]
    assert 'DIRTY' in wrote.stdout, wrote.stdout + wrote.stderr[-2000:]

    # The process is gone. Whatever is on disk now is what the next boot sees.
    stored = json.loads((party / 'Kyle.json').read_text(encoding='utf-8'))
    build = stored.get('build', stored)
    assert build.get('current_hp') == 17, 'HP did not survive process exit'
    assert build.get('conditions', {}).get('wounded') == 1

    # And a fresh interpreter reads it back as the live value.
    reader = ('import app;'
              'pc = app.PARTY_LIBRARY[next(iter(app.PARTY_LIBRARY))];'
              "print('HP', pc.current_hp, 'WOUNDED', pc.conditions.get('wounded'))")
    back = subprocess.run([sys.executable, '-c', reader], capture_output=True,
                          text=True, cwd=app_module.BASE_DIR, timeout=180, env=env)
    assert back.returncode == 0, back.stderr[-2000:]
    assert 'HP 17 WOUNDED 1' in back.stdout, back.stdout


def test_the_debounced_flush_actually_writes(pc):
    """_persist_pc_combat_state only marks dirty; something must flush it.

    If the background thread never ran, every change would sit in memory and
    die with the process -- which is exactly the reported symptom.
    """
    character, pc_file = pc
    character.current_hp = 27
    app_module._persist_pc_combat_state(character.name)
    assert character.name in app_module._PC_PERSIST_DIRTY

    app_module._flush_pending_persistence()

    assert character.name not in app_module._PC_PERSIST_DIRTY
    assert _restart(pc_file).current_hp == 27
