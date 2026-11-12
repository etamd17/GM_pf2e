"""Sheet saves that returned 200 and changed nothing (site request 2026-11-12).

Bug 1 -- daily preparation never persisted. Two causes:

  * CLIENT: templates/player_sheet.html created window._spellStateReady inside
    a DOMContentLoaded handler, but the synchronous initDailyPrep() at the
    script tail renders the prep UI first, and that render saves. Its
    `await window._spellStateReady` awaited `undefined`, so EVERY page load
    posted empty slot arrays over the saved prep. The Long Rest flow did
    re-save the prep correctly, then reloaded the page, which wiped it.
  * The Long Rest flow itself took three requests (save, daily_prep which
    clears, save again). /api/daily_prep now takes the chosen prep and writes
    reset + prep in one go.

Bug 2 -- removing an imported companion did nothing. /api/remove_pet filtered
only build['pets_custom']; a Pathbuilder companion lives in build['pets'].
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module


_FIX = pathlib.Path(__file__).parent / 'fixtures'
_PREP = {'0': {'0': ['Divine Lance', 'Shield'], '1': ['Heal', 'Bless', None]}}


def _install(tmp_path, monkeypatch, fixture):
    raw = json.loads((_FIX / fixture).read_text(encoding='utf-8'))
    pc_file = tmp_path / fixture
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    character = app_module.Character(raw, file_path=str(pc_file))
    monkeypatch.setitem(app_module.PARTY_LIBRARY, character.name, character)
    monkeypatch.setattr(app_module, 'get_pc_file_path',
                        lambda n: str(pc_file) if n == character.name else None)
    monkeypatch.setattr(app_module, '_broadcast_pc_state', lambda *_a, **_k: None)
    monkeypatch.setattr(app_module, '_is_gm', lambda: True)
    return character.name, pc_file


def _build(pc_file):
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    return raw.get('build', raw)


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def goel(tmp_path, monkeypatch):
    return _install(tmp_path, monkeypatch, 'goel_l10.json')


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    return _install(tmp_path, monkeypatch, 'kyle_l10.json')


# --- Bug 1: daily prep -------------------------------------------------------

def test_daily_prep_writes_the_prep_it_is_given(goel, client):
    name, pc_file = goel
    _build_before = _build(pc_file)
    _build_before['expended_slots'] = {'c0_l1': [True]}
    raw = json.loads(pc_file.read_text(encoding='utf-8'))
    raw.get('build', raw).update(_build_before)
    pc_file.write_text(json.dumps(raw), encoding='utf-8')

    r = client.post(f'/api/daily_prep/{name}', json={'heal_full': True, 'prepared_spells': _PREP})
    assert r.status_code == 200, r.get_json()
    build = _build(pc_file)
    assert build['prepared_spells'] == _PREP
    assert build['expended_slots'] == {}, 'the new day must still refresh slots'


def test_daily_prep_without_a_prep_still_clears_it(goel, client):
    name, pc_file = goel
    client.post(f'/api/spell_slots/{name}', json={'prepared_spells': _PREP})
    r = client.post(f'/api/daily_prep/{name}', json={'heal_full': False})
    assert r.status_code == 200
    assert _build(pc_file)['prepared_spells'] == {}


def test_daily_prep_rejects_a_malformed_prep(goel, client):
    name, pc_file = goel
    client.post(f'/api/spell_slots/{name}', json={'prepared_spells': _PREP})
    r = client.post(f'/api/daily_prep/{name}', json={'prepared_spells': ['Heal']})
    assert r.status_code == 400
    assert _build(pc_file)['prepared_spells'] == _PREP, 'a rejected call must not write'


def _sheet():
    return (pathlib.Path(app_module.BASE_DIR) / 'templates' / 'player_sheet.html').read_text(
        encoding='utf-8')


def test_spell_state_promise_exists_before_anything_can_save():
    """The load promise must be created at parse time.

    initDailyPrep() runs synchronously at the script tail and saves; any
    DOMContentLoaded handler runs after it. A promise created inside DCL is
    `undefined` when that first save awaits it.
    """
    sheet = _sheet()
    assign = sheet.index('window._spellStateReady = (async () => {')
    tail_init = sheet.index('\n        initDailyPrep();\n')
    assert assign < tail_init
    # Not nested in a DOMContentLoaded handler: no DCL listener opens between
    # the start of the slot-tracker block and the assignment.
    block = sheet.rindex('// INIT SLOT TRACKERS', 0, assign)
    assert 'addEventListener("DOMContentLoaded"' not in sheet[block:assign], (
        'window._spellStateReady is created inside a DOMContentLoaded handler '
        'again; the sync initDailyPrep() save will wipe prepared spells')


def test_writer_refuses_when_the_initial_load_failed():
    sheet = _sheet()
    body = sheet[sheet.index('async function _saveSpellState()'):]
    body = body[:body.index('\n        }\n')]
    assert 'if (!window._spellStateLoaded)' in body
    assert 'res.ok' in body, 'a non-200 save must be reported, not toasted as saved'


def test_quick_prep_sends_cantrips_to_the_cantrip_selection():
    sheet = _sheet()
    body = sheet[sheet.index('function prepareSpell(cIdx, lvlNum, spellName)'):]
    body = body[:body.index('function _legacyPrepareSpell')]
    assert 'baseRank === 0' in body and 'toggleCantrip(' in body


def test_long_rest_saves_prep_in_the_daily_prep_request():
    dp = (pathlib.Path(app_module.BASE_DIR) / 'templates' / '_pc_sheet' /
          '_js_daily_prep.html').read_text(encoding='utf-8')
    handler = dp[dp.index("querySelector('#dp-long-rest').addEventListener"):]
    assert 'prepared_spells: window._preparedSpells' in handler
    assert handler.index('res.ok') < handler.index('window.location.reload()')


# --- Bug 2: removing an imported companion ----------------------------------

def _pet_names(pc_name):
    return [p['name'] for p in app_module.PARTY_LIBRARY[pc_name].pets]


def test_remove_pet_removes_an_imported_companion(kyle, client):
    name, pc_file = kyle
    imported = _build(pc_file)['pets'][0]['name']
    assert imported in _pet_names(name)

    r = client.post(f'/api/remove_pet/{name}', json={'name': imported})
    assert r.status_code == 200, r.get_json()
    build = _build(pc_file)
    assert all(p.get('name') != imported for p in build.get('pets') or [])
    assert imported in build['pets_removed']
    assert imported not in _pet_names(name)


def test_remove_pet_keeps_custom_companions_working(kyle, client):
    name, pc_file = kyle
    client.post(f'/api/add_pet/{name}', json={'name': 'Jonathon', 'type': 'Animal Companion'})
    assert 'Jonathon' in _pet_names(name)
    r = client.post(f'/api/remove_pet/{name}', json={'name': 'Jonathon'})
    assert r.status_code == 200
    assert 'Jonathon' not in _pet_names(name)
    assert 'Jonathon' not in (_build(pc_file).get('pets_removed') or [])


def test_remove_pet_reports_a_miss(kyle, client):
    name, pc_file = kyle
    before = pc_file.read_text(encoding='utf-8')
    r = client.post(f'/api/remove_pet/{name}', json={'name': 'Nobody'})
    assert r.status_code == 404
    assert r.get_json()['success'] is False
    assert pc_file.read_text(encoding='utf-8') == before


def test_a_removed_import_stays_removed_when_pathbuilder_still_lists_it():
    """Update-from-Pathbuilder re-imports 'pets'; the tombstone must hide it."""
    raw = json.loads((_FIX / 'kyle_l10.json').read_text(encoding='utf-8'))
    build = raw.get('build', raw)
    imported = build['pets'][0]['name']
    build['pets_removed'] = [imported]
    character = app_module.Character(raw, file_path='kyle.json')
    assert imported not in [p['name'] for p in character.pets]
