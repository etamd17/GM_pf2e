"""The GM party-status board (/status) and session export must show live
Cosmere party state, not an empty PF2e roster. These cover the pure assembly
helper (no account-mode setup needed) that the route/export call.
"""
from __future__ import annotations

import ast
import copy
import pathlib

import app
import systems.cosmere.build as cosmere_build
import systems.cosmere.party as cosmere_party

_REPO = pathlib.Path(__file__).resolve().parent.parent
_APP_SOURCE = (_REPO / 'app.py').read_text(encoding='utf-8')
_ROSTER_TPL = (_REPO / 'templates' / 'cosmere_pcs.html').read_text(encoding='utf-8')

_DOC = {
    'id': 'p1', 'name': 'Kal',
    'build': {
        'name': 'Kal', 'level': 2, 'ancestry': 'Human', 'path': 'Warrior',
        'attributes': {'str': 3, 'spd': 2, 'int': 1, 'wil': 1, 'awa': 1, 'pre': 1},
    },
    'play_state': {'health': 5, 'conditions': {'slowed': True}, 'focus': 1, 'injuries': 1},
}


def test_cosmere_status_party_assembles_live_state():
    rows = app._cosmere_status_party([_DOC])
    assert len(rows) == 1
    r = rows[0]
    assert r['id'] == 'p1'
    assert r['name'] == 'Kal'
    assert r['level'] == 2
    assert r['current_hp'] == 5                 # from play_state, not max
    assert r['max_hp'] > 0                       # derived from the build
    assert 0 <= r['hp_pct'] <= 100
    assert r['conditions'].get('slowed') is True
    assert r['hero_points'] is None              # Cosmere has no hero points
    assert r['class_name']                       # path/order shown where PF2e shows class
    assert 'investiture_max' in r and 'focus_max' in r


def test_cosmere_status_party_handles_missing_play_state():
    doc = {'id': 'p2', 'name': 'NoState', 'build': dict(_DOC['build'])}
    rows = app._cosmere_status_party([doc])
    assert len(rows) == 1
    r = rows[0]
    assert r['current_hp'] == r['max_hp']        # full health when no play_state
    assert r['conditions'] == {}


def test_player_state_broadcast_is_centralized():
    tree = ast.parse(_APP_SOURCE)
    owners = []
    for function in (node for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not (isinstance(call.func, ast.Name)
                    and call.func.id == 'sse_broadcast' and call.args):
                continue
            event = call.args[0]
            if isinstance(event, ast.Constant) and event.value == 'cosmere_player_state':
                owners.append(function.name)

    assert owners == ['_broadcast_cosmere_player_state']


def test_complete_player_state_payload_preserves_call_site_extras(monkeypatch):
    document = copy.deepcopy(_DOC)
    document['id'] = 'stable-pid'
    document['name'] = 'Renamed Hero'
    projection = {
        'id': 'stable-pid',
        'name': 'Renamed Hero',
        'resources': {
            'health': {'current': 5, 'max': 17},
            'focus': {'current': 1, 'max': 6},
            'investiture': {'current': 2, 'max': 9},
        },
    }
    monkeypatch.setattr(cosmere_party, 'project_member',
                        lambda *_args, **_kwargs: copy.deepcopy(projection))
    monkeypatch.setattr(app, '_cosmere_homebrew_store', lambda *_args, **_kwargs: {})
    published = []
    monkeypatch.setattr(app, 'sse_broadcast',
                        lambda event, payload, **_kwargs:
                        published.append((event, copy.deepcopy(payload))))
    receipt = {'id': 'receipt-1', 'coordination_status': 'complete'}

    app._broadcast_cosmere_player_state(
        document, ready_to_level=True, revision=7, action_receipt=receipt)

    assert len(published) == 1
    event, payload = published[0]
    assert event == 'cosmere_player_state'
    assert payload['pid'] == 'stable-pid'
    assert payload['name'] == 'Renamed Hero'
    assert payload['play_state'] == document['play_state']
    assert payload['health_max'] == 17
    assert payload['focus_max'] == 6
    assert payload['investiture_max'] == 9
    assert payload['ready_to_level'] is True
    assert payload['revision'] == 7
    assert payload['action_receipt'] == receipt


def test_roster_projects_current_and_maximum_resources(monkeypatch):
    pid = 'ab' * 16
    document = {
        'id': pid,
        'name': 'Vin',
        'build': {},
        'play_state': {
            'health': 4,
            'focus': 2,
            # The compatibility value is stale for this Mistborn character.
            'investiture': 1,
            'mistborn': {'allomancy': {
                'current_investiture': 3,
                'max_investiture': 8,
            }},
        },
    }
    projection = {
        'id': pid,
        'name': 'Vin',
        'resources': {
            'health': {'current': 4, 'max': 12},
            'focus': {'current': 2, 'max': 6},
            'investiture': {'current': 3, 'max': 8},
        },
    }

    class FakeBuild:
        level = 1
        tier = 1
        path = 'agent'
        is_radiant = False
        spren_name = ''
        radiant_order = ''
        first_ideal_sworn = False

        def __init__(self, *_args, **_kwargs):
            pass

        def order(self):
            return None

        def defenses(self):
            return {'phy': 10, 'cog': 11, 'spi': 12}

        def health_max(self):
            return 99

        def focus_max(self):
            return 99

        def investiture_max(self):
            return 99

        def deflect_value(self):
            return 0

    captured = {}
    monkeypatch.setattr(app, '_list_cosmere_pcs', lambda: [copy.deepcopy(document)])
    monkeypatch.setattr(app, '_cosmere_homebrew_store', lambda: {})
    monkeypatch.setattr(cosmere_build, 'CosmereBuild', FakeBuild)
    monkeypatch.setattr(cosmere_party, 'project_member',
                        lambda *_args, **_kwargs: copy.deepcopy(projection))
    monkeypatch.setattr(app, '_workflow_landing_context',
                        lambda: {'workflow_access': {}, 'account_user': None})
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_loaded_campaign_id', lambda: 'campaign-1')
    monkeypatch.setattr(app, 'render_template',
                        lambda template, **context:
                        captured.update({'template': template, **context}) or '')

    with app.app.test_request_context('/cosmere/pcs'):
        app.cosmere_pcs()

    assert captured['template'] == 'cosmere_pcs.html'
    card = captured['pcs'][0]
    assert (card['current_health'], card['health']) == (4, 12)
    assert (card['current_focus'], card['focus']) == (2, 6)
    assert (card['current_investiture'], card['investiture']) == (3, 8)


def test_roster_repaints_all_resources_through_shared_sse():
    assert "appSSE('cosmere_player_state'" in _ROSTER_TPL
    assert 'EventSource' not in _ROSTER_TPL
    assert 'data-pid="{{ pc.id }}"' in _ROSTER_TPL
    for key in ('health', 'focus', 'investiture'):
        assert f'data-f="{key}"' in _ROSTER_TPL
        assert f'data-m="{key}"' in _ROSTER_TPL
