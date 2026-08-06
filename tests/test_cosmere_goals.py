"""Cosmere Goal tracker (Ch.8): a PC works a goal across three milestone boxes,
then concludes it for a reward (a possession / relationship / status / ...). The
state lives in play_state and is edited from the player sheet. Rulebook: goals
have three milestone boxes; completing one "unlocks a reward" in categories like
"possessions (fabrials/Shardplate), relationships (companions/patrons), status
(noble titles), and more."
"""
from __future__ import annotations

import pathlib

import pytest

import app

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SHEET = (_REPO / 'templates' / 'cosmere_sheet.html').read_text()


@pytest.fixture
def goal_pc(tmp_path, monkeypatch):
    d = tmp_path / 'pcs'
    d.mkdir()
    monkeypatch.setattr(app, 'COSMERE_PC_DIR', str(d))
    pid = 'ab' * 16
    doc = {'id': pid, 'system': 'cosmere', 'name': 'Shallan', 'owner_user_id': 'u1',
           'build': {'name': 'Shallan', 'level': 1, 'path': 'scholar',
                     'attributes': {'int': 3}, 'skills': {'lore': 1}}}
    app._atomic_write_json(app._cosmere_pc_path(pid), doc, indent=2)
    yield pid


def test_sheet_has_goal_tracker():
    assert 'window.cosGoalAdd' in _SHEET and 'function paintGoals' in _SHEET
    assert 'cosGoalMilestone' in _SHEET and 'cosGoalConclude' in _SHEET
    assert 'id="cos-goals"' in _SHEET and 'Goals &amp; Rewards' in _SHEET


def test_state_persists_goals(goal_pc):
    c = app.app.test_client()
    goals = [{'id': 'g1', 'text': 'Protect the caravan', 'milestones': [True, True, False],
              'concluded': False, 'reward': {'category': '', 'text': ''}}]
    r = c.post('/cosmere/pc/' + goal_pc + '/state', json={'goals': goals}).get_json()
    assert r['ok']
    g = r['play_state']['goals'][0]
    assert g['text'] == 'Protect the caravan' and g['milestones'] == [True, True, False]


def test_goal_validation_clamps_and_normalizes(goal_pc):
    c = app.app.test_client()
    goals = [
        {'text': '   ', 'milestones': [True]},                       # blank text -> dropped
        {'id': 'x' * 99, 'text': 'Win the duel', 'milestones': [True],
         'concluded': 1, 'reward': {'category': 'Status', 'text': 'Highprince', 'extra': 'ignored'}},
    ]
    r = c.post('/cosmere/pc/' + goal_pc + '/state', json={'goals': goals}).get_json()
    gs = r['play_state']['goals']
    assert len(gs) == 1                                              # blank dropped
    g = gs[0]
    assert g['milestones'] == [True, False, False]                  # padded to 3
    assert g['concluded'] is True                                   # coerced to bool
    assert g['reward'] == {'category': 'Status', 'text': 'Highprince'}   # only known keys
    assert len(g['id']) <= 40


def test_goals_round_trip_in_sheet(goal_pc):
    c = app.app.test_client()
    c.post('/cosmere/pc/' + goal_pc + '/state',
           json={'goals': [{'id': 'g1', 'text': 'Reach Urithiru', 'milestones': [True, True, True],
                            'concluded': True, 'reward': {'category': 'Possession', 'text': 'A Soulcaster'}}]})
    html = c.get('/cosmere/pc/' + goal_pc).get_data(as_text=True)
    assert 'Reach Urithiru' in html and 'A Soulcaster' in html
