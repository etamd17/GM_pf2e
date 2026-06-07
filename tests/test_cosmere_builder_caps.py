"""Cosmere builder rules enforcement: a build that OVER-applies the rules (over
a per-attribute cap, a skill's tier max rank, or any point budget) can't be
saved by a player. The GM may override with force=true. Soft guidance
(under-spend, missing key talent, unmet prereqs) never blocks.
"""
from __future__ import annotations

import pytest

import app
import systems.cosmere.build as cb


def _legal():
    return {'level': 1, 'name': 'Legal', 'path': 'warrior',
            'attributes': {'str': 3, 'spd': 3, 'int': 2, 'wil': 2, 'awa': 1, 'pre': 1},
            'skills': {'hwp': 1, 'ath': 1}}


def _illegal():
    d = _legal()
    d['attributes']['str'] = 7        # over the creation max (3), the hard cap (5), and the budget
    d['name'] = 'Illegal'
    return d


# --- engine -----------------------------------------------------------------
def test_hard_violations_engine():
    assert cb.CosmereBuild(_legal()).hard_violations() == []
    hv = cb.CosmereBuild(_illegal()).hard_violations()
    assert any('hard cap' in x for x in hv) and any('over budget' in x for x in hv)
    over_sk = _legal()
    over_sk['skills'] = {'hwp': 9}     # rank 9 >> tier-1 max rank of 2
    assert any('max rank' in x for x in cb.CosmereBuild(over_sk).hard_violations())


def test_hard_violations_excludes_soft_guidance():
    # a legal-but-incomplete build (missing the path key talent) has guidance but
    # is NOT hard-blocked -- you can save an in-progress character.
    b = cb.CosmereBuild(_legal())
    assert b.hard_violations() == []
    assert b.validate()                # ...yet validate() still surfaces guidance


@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    return d


# --- route gate -------------------------------------------------------------
def test_builder_saves_legal(store):
    r = app.app.test_client().post('/cosmere/builder', json={'build': _legal()})
    assert r.status_code == 200 and r.get_json()['ok']


def test_builder_blocks_player_illegal(store, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    r = app.app.test_client().post('/cosmere/builder', json={'build': _illegal()})
    assert r.status_code == 400
    j = r.get_json()
    assert j['blocked'] is True and j['hard']


def test_builder_player_force_is_ignored(store, monkeypatch):
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    r = app.app.test_client().post('/cosmere/builder', json={'build': _illegal(), 'force': True})
    assert r.status_code == 400        # force only works for the GM


def test_builder_gm_blocked_without_force(store):
    # default test client is GM -> still blocked until they explicitly override
    r = app.app.test_client().post('/cosmere/builder', json={'build': _illegal()})
    assert r.status_code == 400


def test_builder_gm_override_saves(store):
    r = app.app.test_client().post('/cosmere/builder', json={'build': _illegal(), 'force': True})
    assert r.status_code == 200 and r.get_json()['ok']


# --- preview feeds the client gate ------------------------------------------
def test_preview_exposes_hard_and_isgm(store):
    j = app.app.test_client().post('/cosmere/builder/preview', json={'build': _illegal()}).get_json()
    assert j['hard'] and 'is_gm' in j


def test_builder_page_wires_enforcement(store):
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'updateSaveGate' in body and 'GM_OVERRIDE' in body
    assert 'btn-save-top' in body and 'btn-save-review' in body


def test_builder_page_explains_choices(store):
    # the builder now surfaces what each choice means
    body = app.app.test_client().get('/cosmere/builder').data.decode()
    assert 'Feeds Physical Defense' in body                     # attribute description
    assert 'Climbing, jumping' in body                          # skill description (Athletics)
    assert 'ANCESTRY_INFO' in body and 'CULTURE_INFO' in body   # info-line data
    assert 'is a field you know deeply' in body                 # expertise explanation
