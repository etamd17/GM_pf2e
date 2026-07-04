"""Levelup-parity save validation for character creation (spec 2026-07-03, T4).

The builder previously had ZERO server-side validation (the audit's biggest
levelup/builder asymmetry): any feat payload was accepted silently. The
backstop mirrors levelup's soft-block philosophy — violations return a
warning payload unless `force` is set (the GM-override path), and a clean
build saves exactly as before.

Scope (documented): level gates, feat-chain prereqs, and skill-rank prereqs
at level 1 (trained only). Ability-score prereqs are deliberately NOT
checked server-side (the payload's ability values are boost-derived and
scale-ambiguous here; the client picker already greys those)."""
from __future__ import annotations

import json
import os

import pytest

import app


@pytest.fixture
def builder_env(tmp_path, monkeypatch):
    pdir = tmp_path / 'party'
    pdir.mkdir()
    monkeypatch.setattr(app, 'PARTY_DIR', str(pdir))
    # Synthetic gated feats injected into the lookup so the tests don't
    # depend on any particular compendium content.
    gated = [
        {'name': 'Test High Gate', 'level': 8, 'type': 'general',
         'prereqs_struct': {'level': 8, 'abilities': {}, 'skills': {}, 'feats': [], 'features': [], 'raw': ''}},
        {'name': 'Test Chain Gate', 'level': 1, 'type': 'general',
         'prereqs_struct': {'level': None, 'abilities': {}, 'skills': {}, 'feats': ['Test Root Feat'], 'features': [], 'raw': ''}},
        {'name': 'Test Skill Gate', 'level': 1, 'type': 'skill',
         'prereqs_struct': {'level': None, 'abilities': {}, 'skills': {'athletics': 'expert'}, 'feats': [], 'features': [], 'raw': ''}},
        {'name': 'Test Trained Gate', 'level': 1, 'type': 'skill',
         'prereqs_struct': {'level': None, 'abilities': {}, 'skills': {'medicine': 'trained'}, 'feats': [], 'features': [], 'raw': ''}},
    ]
    general = app.BUILDER_FEATS.get('general')
    skill = app.BUILDER_FEATS.get('skill')
    if not isinstance(general, list) or not isinstance(skill, list):
        pytest.skip('BUILDER_FEATS not populated in this environment')
    general.extend([gated[0], gated[1]])
    skill.extend([gated[2], gated[3]])
    yield str(pdir)
    for f in (gated[0], gated[1]):
        general.remove(f)
    for f in (gated[2], gated[3]):
        skill.remove(f)


def _payload(feats, skills=None, name='Backstop Test PC'):
    return {
        'name': name, 'class_name': 'Fighter', 'ancestry': 'Human',
        'abilities': {'str': 4, 'dex': 2, 'con': 2, 'int': 0, 'wis': 1, 'cha': 0},
        'skills': skills or [],
        'feats': [{'name': n, 'type': 'general', 'desc': ''} for n in feats],
    }


def test_clean_build_saves_untouched(builder_env):
    r = app.app.test_client().post('/api/save_new_character', json=_payload([]))
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert os.path.exists(os.path.join(builder_env, 'Backstop_Test_PC.json'))


def test_level_gate_rejected_without_force(builder_env):
    r = app.app.test_client().post('/api/save_new_character', json=_payload(['Test High Gate']))
    assert r.status_code == 409
    body = r.get_json()
    assert body['success'] is False and body['needs_force'] is True
    assert any('level 8' in v.lower() for v in body['violations'])
    assert not os.path.exists(os.path.join(builder_env, 'Backstop_Test_PC.json'))


def test_feat_chain_rejected_without_force(builder_env):
    r = app.app.test_client().post('/api/save_new_character', json=_payload(['Test Chain Gate']))
    assert r.status_code == 409
    assert any('Test Root Feat' in v for v in r.get_json()['violations'])


def test_feat_chain_satisfied_by_sibling_pick(builder_env):
    # The chain requirement can be met by another feat in the SAME submission.
    payload = _payload(['Test Chain Gate'])
    payload['feats'].append({'name': 'Test Root Feat', 'type': 'general', 'desc': ''})
    r = app.app.test_client().post('/api/save_new_character', json=payload)
    assert r.status_code == 200


def test_skill_rank_beyond_trained_rejected(builder_env):
    # Expert+ is unreachable at level 1 regardless of picks.
    r = app.app.test_client().post(
        '/api/save_new_character', json=_payload(['Test Skill Gate'], skills=['Athletics']))
    assert r.status_code == 409
    assert any('expert' in v.lower() for v in r.get_json()['violations'])


def test_trained_requirement_met_by_submitted_skill(builder_env):
    r = app.app.test_client().post(
        '/api/save_new_character', json=_payload(['Test Trained Gate'], skills=['Medicine']))
    assert r.status_code == 200


def test_trained_requirement_missing_rejected(builder_env):
    r = app.app.test_client().post(
        '/api/save_new_character', json=_payload(['Test Trained Gate'], skills=[]))
    assert r.status_code == 409


def test_force_bypasses_all(builder_env):
    payload = _payload(['Test High Gate', 'Test Chain Gate'])
    payload['force'] = True
    r = app.app.test_client().post('/api/save_new_character', json=payload)
    assert r.status_code == 200
    assert r.get_json()['success'] is True
    assert os.path.exists(os.path.join(builder_env, 'Backstop_Test_PC.json'))


def test_unknown_feat_names_ignored(builder_env):
    # Homebrew / renamed feats absent from the lookup must not block saves.
    r = app.app.test_client().post(
        '/api/save_new_character', json=_payload(['Totally Homebrew Feat']))
    assert r.status_code == 200
