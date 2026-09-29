"""A read-modify-write of the character file must not revert live combat state.

Two write paths share one file. The debounced one holds HP, conditions, focus,
hero points, temp HP, shield, reaction and persistent damage in memory and
flushes them every two seconds. The immediate one -- ~30 routes covering spell
slots, inventory, notes, XP, level-ups and portraits -- reads the file, mutates
the build, writes it back, and calls reload_single_character(), which rebuilds
the Character from what was just written.

So a route that reads the file *before* the debounced flush lands writes back a
stale HP and then makes it authoritative. At the table: take damage, cast a
spell within two seconds, and the damage is gone.

The fix has to be ordered flush-then-read. It cannot live inside
save_and_reload_character -- by the time that runs the caller is already holding
a stale pc_json, and stamping live state over it there would break the routes
that reset combat state on purpose (daily prep pops current_hp so the Character
defaults back to max). So each route flushes before its own read, and the guard
at the bottom of this file checks that ordering across all of them.
"""
from __future__ import annotations

import ast
import io
import json
import pathlib

import pytest

import app as app_module


_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'
_APP_PY = pathlib.Path(__file__).parent.parent / 'app.py'


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC on disk, registered live, with nothing flushed yet."""
    pc_file = tmp_path / 'Kyle.json'
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    pc_file.write_text(json.dumps(raw), encoding='utf-8')

    character = app_module.Character(raw, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, character.name, character)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == character.name else None)
    monkeypatch.setattr(app_module, '_broadcast_pc_state', lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, '_is_gm', lambda: True)
    # Leave the dirty set as the test found it.
    monkeypatch.setattr(app_module, '_PC_PERSIST_DIRTY', set())
    return character, pc_file


@pytest.fixture
def client():
    return app_module.app.test_client()


def _stored(pc_file):
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    return raw.get('build', raw)


def test_an_unrelated_write_does_not_revert_unflushed_hp(pc, client):
    """The bug, at the table: damage taken, then a note saved two seconds later."""
    character, pc_file = pc
    starting_hp = character.current_hp

    character.current_hp = starting_hp - 30
    app_module._persist_pc_combat_state(character.name)   # dirty, not yet written
    assert _stored(pc_file).get('current_hp') != starting_hp - 30

    response = client.post(f'/api/save_notes/{character.name}',
                           json={'notes': 'the gate is barred'})
    assert response.status_code == 200

    assert _stored(pc_file)['current_hp'] == starting_hp - 30
    assert app_module.PARTY_LIBRARY[character.name].current_hp == starting_hp - 30
    assert _stored(pc_file)['notes'] == 'the gate is barred'


def test_conditions_survive_an_unrelated_write(pc, client):
    character, pc_file = pc
    character.conditions['frightened'] = 2
    character.conditions['prone'] = True
    app_module._persist_pc_combat_state(character.name)

    response = client.post(f'/api/save_notes/{character.name}', json={'notes': 'x'})
    assert response.status_code == 200

    stored = _stored(pc_file)['conditions']
    assert stored.get('frightened') == 2
    assert stored.get('prone') is True
    reloaded = app_module.PARTY_LIBRARY[character.name]
    assert reloaded.conditions.get('frightened') == 2


def test_the_flush_clears_the_dirty_flag(pc, client):
    """Otherwise the next background tick rewrites what the route just saved."""
    character, _ = pc
    character.current_hp = 7
    app_module._persist_pc_combat_state(character.name)
    assert character.name in app_module._PC_PERSIST_DIRTY

    client.post(f'/api/save_notes/{character.name}', json={'notes': 'x'})
    assert character.name not in app_module._PC_PERSIST_DIRTY


def test_failed_flush_aborts_rmw_without_overwriting_live_or_disk_state(
        pc, client, monkeypatch):
    """A persistence failure is a 503 barrier, never permission to read stale JSON."""
    character, pc_file = pc
    original_disk = pc_file.read_bytes()
    starting_hp = character.current_hp
    character.current_hp = starting_hp - 30
    app_module._persist_pc_combat_state(character.name)
    monkeypatch.setattr(
        app_module,
        '_do_persist_pc_combat_state',
        lambda _name: False,
    )

    response = client.post(
        f'/api/save_notes/{character.name}',
        json={'notes': 'must not land'},
    )

    assert response.status_code == 503
    assert response.get_json()['success'] is False
    assert 'No changes were applied' in response.get_json()['error']
    assert pc_file.read_bytes() == original_disk
    assert app_module.PARTY_LIBRARY[character.name] is character
    assert character.current_hp == starting_hp - 30
    assert character.name in app_module._PC_PERSIST_DIRTY


def test_atomic_save_failure_returns_503_without_reload_or_success_broadcast(
        pc, client, monkeypatch):
    character, pc_file = pc
    original_disk = pc_file.read_bytes()
    broadcasts = []
    monkeypatch.setattr(
        app_module,
        '_atomic_write_json',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('disk full')),
    )
    monkeypatch.setattr(
        app_module,
        '_broadcast_pc_state',
        lambda name: broadcasts.append(name),
    )

    response = client.post(
        f'/api/save_notes/{character.name}',
        json={'notes': 'must not land'},
    )

    assert response.status_code == 503
    assert response.get_json()['success'] is False
    assert 'This character was not changed' in response.get_json()['error']
    assert pc_file.read_bytes() == original_disk
    assert app_module.PARTY_LIBRARY[character.name] is character
    assert broadcasts == []


def test_failed_long_rest_preflight_does_not_mutate_or_broadcast(
        pc, client, monkeypatch):
    character, pc_file = pc
    character.current_hp = max(1, character.current_hp - 30)
    character.current_focus = 0
    character.temp_hp_manual = 7
    character.conditions = {'frightened': 2, 'drained': 1, 'doomed': 1}
    before = (
        character.current_hp,
        character.current_focus,
        character.temp_hp_manual,
        dict(character.conditions),
    )
    original_disk = pc_file.read_bytes()
    broadcasts = []
    app_module._persist_pc_combat_state(character.name)
    monkeypatch.setattr(
        app_module,
        '_do_persist_pc_combat_state',
        lambda _name: False,
    )
    monkeypatch.setattr(
        app_module,
        '_broadcast_pc_state',
        lambda name: broadcasts.append(name),
    )

    response = client.post(f'/api/long_rest/{character.name}', json={})

    assert response.status_code == 503
    assert app_module.PARTY_LIBRARY[character.name] is character
    assert (
        character.current_hp,
        character.current_focus,
        character.temp_hp_manual,
        dict(character.conditions),
    ) == before
    assert pc_file.read_bytes() == original_disk
    assert character.name in app_module._PC_PERSIST_DIRTY
    assert broadcasts == []


def test_long_rest_publishes_only_the_durable_reloaded_state(pc, client):
    character, pc_file = pc
    character.current_focus = 0
    character.temp_hp_manual = 7
    character.conditions = {'frightened': 2, 'drained': 2, 'doomed': 1}
    character.hp -= 2 * character.level
    character.current_hp = max(1, character.hp - 30)
    app_module._persist_pc_combat_state(character.name)
    con_mod = int(character.mods.get('con', 0))
    expected_max_hp = character.hp + character.level
    expected_hp = min(
        expected_max_hp,
        character.current_hp + max(1, con_mod) * character.level,
    )

    response = client.post(f'/api/long_rest/{character.name}', json={})

    assert response.status_code == 200
    stored = _stored(pc_file)
    reloaded = app_module.PARTY_LIBRARY[character.name]
    assert stored['current_hp'] == expected_hp == reloaded.current_hp
    assert reloaded.hp == expected_max_hp
    assert stored['current_focus'] == reloaded.focus_max == reloaded.current_focus
    assert stored['temp_hp'] == 0 == reloaded.temp_hp_manual
    assert stored['conditions']['frightened'] == 0
    assert stored['conditions']['drained'] == 1
    assert stored['conditions']['doomed'] == 0
    assert reloaded.conditions['drained'] == 1
    assert reloaded.conditions.get('doomed', 0) == 0


def test_batch_rest_preflights_every_pc_before_mutating_first(
        pc, client, tmp_path, monkeypatch):
    first, first_file = pc
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    raw.get('build', raw)['name'] = 'Second Hero'
    second_file = tmp_path / 'Second_Hero.json'
    second_file.write_text(json.dumps(raw), encoding='utf-8')
    second = app_module.Character(raw, file_path=str(second_file))
    party = {first.name: first, second.name: second}
    paths = {first.name: str(first_file), second.name: str(second_file)}
    monkeypatch.setattr(app_module, 'PARTY_LIBRARY', party)
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda name: paths[name])
    monkeypatch.setattr(
        app_module,
        '_PC_PERSIST_DIRTY',
        {first.name, second.name},
    )
    flush_order = []

    def persist(name):
        flush_order.append(name)
        return name != second.name

    monkeypatch.setattr(app_module, '_do_persist_pc_combat_state', persist)
    saves = []
    broadcasts = []
    monkeypatch.setattr(
        app_module,
        '_save_and_reload_character_batch',
        lambda updates: saves.extend(name for name, _doc, _path in updates),
    )
    monkeypatch.setattr(
        app_module,
        '_broadcast_pc_state',
        lambda name: broadcasts.append(name),
    )
    before = {
        name: (actor.current_hp, actor.current_focus, dict(actor.conditions))
        for name, actor in party.items()
    }

    response = client.post('/api/rest/apply', json={'type': 'long'})

    assert response.status_code == 503
    assert flush_order == [first.name, second.name]
    assert saves == []
    assert broadcasts == []
    assert {
        name: (actor.current_hp, actor.current_focus, dict(actor.conditions))
        for name, actor in party.items()
    } == before
    assert second.name in app_module._PC_PERSIST_DIRTY


def test_batch_rest_second_stage_failure_is_atomic(
        pc, client, tmp_path, monkeypatch):
    first, first_file = pc
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    raw.get('build', raw)['name'] = 'Second Hero'
    second_file = tmp_path / 'Second_Hero.json'
    second_file.write_text(json.dumps(raw), encoding='utf-8')
    second = app_module.Character(raw, file_path=str(second_file))
    first.current_hp = max(1, first.hp - 30)
    second.current_hp = max(1, second.hp - 30)
    party = {first.name: first, second.name: second}
    paths = {first.name: str(first_file), second.name: str(second_file)}
    monkeypatch.setattr(app_module, 'PARTY_LIBRARY', party)
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda name: paths[name])
    monkeypatch.setattr(app_module, '_PC_PERSIST_DIRTY', set())
    broadcasts = []
    monkeypatch.setattr(
        app_module,
        '_broadcast_pc_state',
        lambda name: broadcasts.append(name),
    )
    original_atomic_write = app_module._atomic_write_json
    write_count = 0

    def fail_second_write(*args, **kwargs):
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise OSError('second character disk failure')
        return original_atomic_write(*args, **kwargs)

    monkeypatch.setattr(app_module, '_atomic_write_json', fail_second_write)
    first_disk_before = first_file.read_bytes()
    second_disk_before = second_file.read_bytes()

    response = client.post('/api/rest/apply', json={'type': 'long'})

    payload = response.get_json()
    assert response.status_code == 503
    assert payload['success'] is False
    assert 'No changes were applied' in payload['error']
    assert 'partial' not in payload
    assert broadcasts == []
    assert first_file.read_bytes() == first_disk_before
    assert second_file.read_bytes() == second_disk_before
    assert app_module.PARTY_LIBRARY[first.name] is first
    assert app_module.PARTY_LIBRARY[second.name] is second


def test_daily_prep_still_resets_hp_despite_the_flush(pc, client):
    """The reason this is flush-then-read and not stamp-after-reload.

    Daily prep pops current_hp so Character.__init__ falls back to max. If the
    fix re-applied live state after the reload instead, a full-HP rest would
    hand the player back the damage they just slept off."""
    character, pc_file = pc
    character.current_hp = 12
    app_module._persist_pc_combat_state(character.name)

    response = client.post(f'/api/daily_prep/{character.name}',
                           json={'heal_full': True})
    assert response.status_code == 200

    assert 'current_hp' not in _stored(pc_file)
    reloaded = app_module.PARTY_LIBRARY[character.name]
    assert reloaded.current_hp == reloaded.hp


# --------------------------------------------------------------------------
# Static guard. The behavioural tests above exercise two routes; ~30 share the
# shape. This walks app.py and holds every one of them to the same ordering,
# so a route added later cannot quietly reintroduce the race.
# --------------------------------------------------------------------------

# Builds its JSON from the request rather than reading the character file, so
# there is no stale read to guard.
_NO_READ_BEFORE_WRITE = {'save_new_character'}

_FLUSHERS = {'_flush_pc_dirty', 'require_pc_json'}


def _called_names(node):
    """(name, lineno) for every call in this function's body."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            name = getattr(func, 'id', None) or getattr(func, 'attr', None)
            if name:
                out.append((name, sub.lineno))
    return out


def _write_back_functions():
    tree = ast.parse(io.open(_APP_PY, encoding='utf-8').read())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = _called_names(node)
        if any(n in {
            'save_and_reload_character',
            '_save_and_reload_character_batch',
        } for n, _ in calls):
            yield node.name, calls


def test_every_write_back_route_flushes_before_it_reads():
    offenders = []
    checked = 0
    for name, calls in _write_back_functions():
        if name in _NO_READ_BEFORE_WRITE:
            continue
        reads = [ln for n, ln in calls if n == 'load']          # json.load(f)
        flushes = [ln for n, ln in calls if n in _FLUSHERS]
        if not reads:
            continue
        checked += 1
        if not flushes:
            offenders.append(f'{name}: reads at line {min(reads)}, never flushes')
        elif min(flushes) > min(reads):
            offenders.append(
                f'{name}: flushes at line {min(flushes)}, but already read at '
                f'{min(reads)} -- the flush has to come first')
    assert not offenders, (
        'read-modify-write of a character file without flushing debounced '
        'combat state first:\n  ' + '\n  '.join(offenders))
    assert checked >= 25, f'expected ~30 write-back routes, walked {checked}'


def test_the_shared_loader_flushes_before_its_read():
    """require_pc_json is the front door for this pattern; if it stops
    flushing, every caller silently regresses."""
    tree = ast.parse(io.open(_APP_PY, encoding='utf-8').read())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == 'require_pc_json')
    calls = _called_names(fn)
    flush = [ln for n, ln in calls if n == '_flush_pc_dirty']
    read = [ln for n, ln in calls if n == 'load']
    assert flush, 'require_pc_json no longer flushes'
    assert read, 'require_pc_json no longer reads -- update this test'
    assert min(flush) < min(read)
