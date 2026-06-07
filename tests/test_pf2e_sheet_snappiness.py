"""PF2e sheet snappiness: combat-hot persistent-damage actions update the strip
in place from the server's returned list instead of doing a full-page reload
(which blanked + rebuilt the ~10k-line sheet on every add/remove/flat-check).
"""
from __future__ import annotations

import json
import pathlib

import pytest

import app as app_module
from app import Character

_FIX = pathlib.Path(__file__).parent / 'fixtures' / 'kyle_l10.json'


@pytest.fixture
def kyle(tmp_path, monkeypatch):
    raw = json.loads(_FIX.read_text())
    pc_file = tmp_path / 'Kyle.json'
    pc_file.write_text(json.dumps(raw), encoding='utf-8')
    monkeypatch.setitem(app_module.PARTY_LIBRARY, 'Kyle', Character(raw, file_path=str(pc_file)))
    monkeypatch.setattr(app_module, 'get_pc_file_path', lambda n: str(pc_file) if n == 'Kyle' else None)
    return 'Kyle'


def test_persistent_damage_renders_in_place(kyle):
    body = app_module.app.test_client().get('/player/sheet/Kyle').data.decode()
    assert 'function renderPersistentDamage' in body                       # the reusable in-place renderer
    # pdAddPrompt / pdRemove / pdFlatCheck (and the SSE handler) re-render from
    # the server's list rather than reloading the page.
    assert body.count('renderPersistentDamage(data.persistent_damage)') >= 3


def test_persistent_damage_handlers_dropped_the_reload(kyle):
    body = app_module.app.test_client().get('/player/sheet/Kyle').data.decode()
    # isolate just the three persistent-damage handlers (pdAddPrompt .. end of
    # pdFlatCheck) and confirm none of them reload the page anymore.
    start = body.index('async function pdAddPrompt')
    end = body.index("'pdFlatCheck:'")                     # inside the last pd handler's catch
    pd_block = body[start:end]
    assert pd_block.count('renderPersistentDamage(data.persistent_damage)') == 3
    assert 'location.reload' not in pd_block
