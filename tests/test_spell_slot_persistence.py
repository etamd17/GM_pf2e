"""Spent spell slots must not be destroyed by an empty write.

Two ways they could be, both found together:

  * SERVER: /api/sync_spell_slots did `data.get('expended_slots', {})`, so a
    POST that omitted the key wrote an empty dict over every expended slot --
    and it took no spell lock, so it could also clobber an in-flight
    /api/cast_spell write. Covered here.

  * CLIENT: templates/player_sheet.html initialises window._expendedSlots to
    {} and fills it from an async GET, while every writer POSTs the WHOLE map.
    A slot click landing before that GET resolved persisted {} and cleared the
    sheet. Fixed by gating every writer on window._spellStateReady; asserted
    at the end of this file, since there is no JS harness in this repo.
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module


_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'
_SPENT = {'c0_l3': [True, True], 'c0_l4': [True]}


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC on disk with two ranks of slots already spent."""
    pc_file = tmp_path / 'Kyle.json'
    raw = json.loads(_FIX.read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    build['expended_slots'] = json.loads(json.dumps(_SPENT))
    pc_file.write_text(json.dumps(raw), encoding='utf-8')

    character = app_module.Character(raw, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, character.name, character)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == character.name else None)
    monkeypatch.setattr(app_module, '_broadcast_pc_state', lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, '_is_gm', lambda: True)
    return character.name, pc_file


def _stored_slots(pc_file):
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    return (raw.get('build', raw)).get('expended_slots')


@pytest.fixture
def client():
    return app_module.app.test_client()


def test_sync_without_the_key_does_not_wipe_slots(pc, client):
    """An absent key means "nothing to sync", not "clear everything"."""
    name, pc_file = pc
    response = client.post(f'/api/sync_spell_slots/{name}', json={})
    assert response.status_code == 400
    assert _stored_slots(pc_file) == _SPENT      # untouched


def test_sync_rejects_a_non_object(pc, client):
    name, pc_file = pc
    response = client.post(f'/api/sync_spell_slots/{name}',
                           json={'expended_slots': 'all of them'})
    assert response.status_code == 400
    assert _stored_slots(pc_file) == _SPENT


def test_sync_still_writes_a_real_payload(pc, client):
    name, pc_file = pc
    fresh = {'c0_l1': [True]}
    response = client.post(f'/api/sync_spell_slots/{name}',
                           json={'expended_slots': fresh})
    assert response.status_code == 200
    assert _stored_slots(pc_file) == fresh


def test_an_explicit_empty_map_is_still_honoured(pc, client):
    """Daily prep legitimately clears every slot -- that must still work.

    The guard is on the key being ABSENT, not on the value being empty.
    """
    name, pc_file = pc
    response = client.post(f'/api/sync_spell_slots/{name}',
                           json={'expended_slots': {}})
    assert response.status_code == 200
    assert _stored_slots(pc_file) == {}


def test_spell_slots_route_ignores_keys_it_was_not_given(pc, client):
    """The live writer already got this right; pin it so it stays right."""
    name, pc_file = pc
    response = client.post(f'/api/spell_slots/{name}',
                           json={'prepared_spells': {'c0_l1': ['Bless']}})
    assert response.status_code == 200
    assert _stored_slots(pc_file) == _SPENT      # slots untouched


# ==========================================================================
# Client gate. There is no JS harness in this repo, so this asserts on the
# source -- the same approach tests/test_inline_handler_escaping.py takes.
# ==========================================================================

def _sheet():
    return (pathlib.Path(app_module.BASE_DIR) / 'templates' / 'player_sheet.html').read_text(
        encoding='utf-8')


def test_every_slot_writer_waits_for_the_initial_load():
    """window._expendedSlots starts {} and every writer POSTs the whole map.

    Without a gate, a click during the initial GET persists {} and clears the
    player's whole day. Each writer must await window._spellStateReady first.
    """
    sheet = _sheet()
    assert 'window._spellStateReady = (async () => {' in sheet, 'load promise missing'

    # One await per writer, plus the one inside the loader itself.
    gates = sheet.count('await window._spellStateReady')
    posts = sheet.count("await fetch(`/api/spell_slots/${pcNameEncoded}`, {")
    assert gates >= posts, (
        f'{posts} slot writers but only {gates} readiness gates -- '
        'a writer can still fire before the initial load lands')


def test_the_loader_awaits_its_own_promise():
    """The init block still has to block on the load before it renders.

    It assigns the promise (so other writers can reach it) and then awaits it,
    so every handler attached later in that block sees populated state. Search
    forward FROM the assignment -- writers appear earlier in the file than the
    loader does, which is fine at runtime but breaks a naive first-index check.
    """
    sheet = _sheet()
    assign = sheet.index('window._spellStateReady = (async () => {')
    assert 'await window._spellStateReady;' in sheet[assign:assign + 2000], (
        'the loader assigns the promise but never awaits it, so the rest of '
        'the init block renders against empty slot state')
