"""Stage 5b: the three combat things Round 6 asked for.

Two of them are wiring rather than new engines, which is what the audit
predicted. Condition timers and persistent damage are already modelled,
persisted and ticked down server-side -- the map just never reached them.

The third, measuring a move against Speed while dragging, is genuinely new. A
ruler existed, but using it meant putting the token down, switching tools,
measuring, and switching back -- so nobody did.

The persistent-damage split is the part that needs care. A monster stores a
string ("1d6 fire"); a PC stores a list of dicts. Writing the string form onto a
PC once corrupted it into list("1d6 fire") -> ['1','d','6',...], which is why
/api/set_persistent_damage refuses PCs outright. Each shape keeps its own
engine here rather than being reimplemented.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import app
from core import scenes, storage


CID = 'e5' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()


@pytest.fixture
def combat(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    monkeypatch.setattr(app, '_broadcast_encounter_state', lambda *_a, **_k: None)
    monkeypatch.setattr(app, '_persist_encounter_state', lambda *_a, **_k: None)

    ogre = SimpleNamespace(instance_id='ogre-1', name='Ogre', is_pc=False, system='pf2e',
                           current_hp=40, hp=40, conditions={}, visible_to_players=True,
                           persistent_damage='', attributes={'spd': 25})
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [ogre])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)

    scene = scenes.create_scene(CID, 'Fight')
    scenes.add_token(scene, name='Ogre', combatant_id='ogre-1')
    scenes.save_scene(CID, scene)
    return app.app.test_client(), scene['id'], ogre


def _act(client, sid, body):
    return client.post(f'/api/scenes/{sid}/combatants/ogre-1', json=body)


# --- condition durations ---------------------------------------------------

def test_a_condition_can_carry_a_duration(combat):
    """_apply_condition_change has always taken `rounds`; the map never passed
    it, so a timed condition could only be set from the tracker."""
    client, sid, _ogre = combat
    response = _act(client, sid, {'action': 'condition', 'condition': 'frightened',
                                  'operation': 'add', 'rounds': 3})
    assert response.status_code == 200
    assert response.get_json()['result']['rounds'] == 3


def test_no_duration_still_works(combat):
    client, sid, _ogre = combat
    response = _act(client, sid, {'action': 'condition', 'condition': 'prone',
                                  'operation': 'add'})
    assert response.status_code == 200
    assert response.get_json()['result']['rounds'] == 0


@pytest.mark.parametrize('rounds', [-1, 101, 'soon', 1.5e9])
def test_nonsense_durations_are_rejected(combat, rounds):
    client, sid, _ogre = combat
    assert _act(client, sid, {'action': 'condition', 'condition': 'frightened',
                              'operation': 'add', 'rounds': rounds}).status_code == 400


# --- persistent damage -----------------------------------------------------

def test_persistent_damage_on_a_monster_uses_the_string_form(combat):
    client, sid, ogre = combat
    response = _act(client, sid, {'action': 'persistent_damage',
                                  'damage': '1d6', 'damage_type': 'fire'})
    assert response.status_code == 200
    assert ogre.persistent_damage == '1d6 fire'


def test_persistent_damage_needs_an_expression(combat):
    client, sid, _ogre = combat
    assert _act(client, sid, {'action': 'persistent_damage', 'damage': '  '}).status_code == 400


def test_a_pc_uses_the_list_form_not_the_string(tmp_path, monkeypatch):
    """The whole reason /api/set_persistent_damage refuses PCs: the string form
    corrupts the list into ['1','d','6',...]."""
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, 'ACTIVE_CAMPAIGN_ID', CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)

    hero = SimpleNamespace(instance_id='hero-1', name='Kyle', is_pc=True, system='pf2e',
                           current_hp=30, hp=30, conditions={}, visible_to_players=True,
                           persistent_damage=[], base_speed=25)
    monkeypatch.setattr(app, 'ACTIVE_ENCOUNTER', [hero])
    monkeypatch.setattr(app, 'TURN_INDEX', 0)
    monkeypatch.setitem(app.PARTY_LIBRARY, 'Kyle', hero)
    captured = {}

    def _fake_delta(name, mutator, **_kw):
        mutator(hero)
        captured['name'] = name
        return None, hero
    monkeypatch.setattr(app, 'apply_pc_delta', _fake_delta)

    scene = scenes.create_scene(CID, 'PC Fight')
    scenes.add_token(scene, name='Kyle', combatant_id='hero-1', is_pc=True)
    scenes.save_scene(CID, scene)

    with app.app.test_client() as client:
        response = client.post(f'/api/scenes/{scene["id"]}/combatants/hero-1',
                               json={'action': 'persistent_damage',
                                     'damage': '1d6', 'damage_type': 'fire'})
    assert response.status_code == 200
    assert captured['name'] == 'Kyle'
    assert hero.persistent_damage == [{'damage': '1d6', 'type': 'fire', 'source': 'map'}]
    assert isinstance(hero.persistent_damage, list), 'never the string form for a PC'


def test_an_unknown_combat_action_is_still_rejected(combat):
    client, sid, _ogre = combat
    assert _act(client, sid, {'action': 'teleport'}).status_code == 400


# --- speed reaches the client ----------------------------------------------

def test_speed_is_projected_onto_the_token(combat):
    """Without it the drag measurement has nothing to compare against."""
    client, sid, _ogre = combat
    scene = client.get(f'/api/scenes/{sid}').get_json()['scene']
    assert scene['tokens'][0]['live']['speed'] == 25


# --- the drag measurement --------------------------------------------------

def test_movement_is_measured_while_dragging():
    assert 'function drawMoveMeasure(' in _JS
    body = _JS[_JS.index('function drawMoveMeasure('):]
    body = body[:body.index('\n    }')]
    assert "interaction.type !== 'token'" in body, 'only while moving a token'
    assert 'interaction.fromX' in body, 'measured from where the drag started'
    assert 'pf2eDistanceLabel(from, to)' in body, 'reuse the 5-10-5 diagonal rule'


def test_going_over_speed_is_flagged_not_forbidden():
    """Over Speed is legal -- a second action, or a Stride and a Step."""
    body = _JS[_JS.index('function drawMoveMeasure('):]
    body = body[:body.index('\n    }')]
    assert 'const over = speed > 0 && feet > speed' in body
    assert 'return' not in body.split('const over')[1][:120], 'must not block the move'


def test_the_measurement_is_a_gm_affordance():
    """It is drawn from drawToolOverlay, which the table view does not call."""
    assert 'drawMoveMeasure();' in _JS
    draw = _JS[_JS.index('    function draw() {'):]
    draw = draw[:draw.index('\n    function drawGrid')]
    assert 'if (!isTableView()) drawToolOverlay();' in draw


def test_the_duration_and_persistent_damage_controls_exist():
    for control in ('map-condition-rounds', 'map-pd-damage', 'map-pd-type', 'map-apply-pd'):
        assert f'id="{control}"' in _HTML, control


def test_no_control_is_nested_inside_a_select():
    """A regression guard with a real cause.

    The duration and persistent-damage controls were first inserted directly
    after the line opening <select id="map-condition">, which put them INSIDE
    it. A browser auto-closes the select at the first non-<option> child, so the
    condition dropdown lost its options and the new controls landed in the wrong
    place. Jinja parsing did not care and neither did any assertion -- it took a
    browser console warning to surface it.

    Checked for every select on the page, not just that one.
    """
    import re
    for match in re.finditer(r'<select\b[^>]*>(.*?)</select>', _HTML, re.S):
        inner = match.group(1)
        stray = [t for t in re.findall(r'<(\w+)', inner) if t.lower() != 'option']
        assert not stray, f'non-option tags inside a <select>: {stray}'
