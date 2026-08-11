"""Stage 4d: lights you can adjust, and that actually differ from each other.

Lights had a data model, a server API and a player filter, but only add and
delete. Changing a torch's radius meant erasing it and placing another, and
intensity had no UI at all -- every light in every scene was 0.75, because that
was hardcoded at the single call site.

Two rendering bugs went with that, both in the gradient stop:

  * the stop is built by concatenating an alpha byte onto the colour, so
    anything not exactly #rrggbb -- a 3-digit shorthand, a named colour, an
    empty string -- produced an invalid stop and threw inside the render loop,
    taking the whole canvas down rather than one light. The server stored any
    20-character string.
  * the alpha was `intensity * 90`, capped at 0x5A, about 35%. A candle and a
    bonfire were nearly indistinguishable, which is presumably why nobody
    noticed intensity was never set.

Lights already contributed to what vision carves; that part was right.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'c3' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()


@pytest.fixture
def gm(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    return app.app.test_client()


@pytest.fixture
def lit(gm):
    scene = scenes.create_scene(CID, 'Torchlit')
    gm.post(f'/api/scenes/{scene["id"]}/elements', json={
        'action': 'add_light', 'x': 100, 'y': 100, 'radius': 300,
        'color': '#ffd98a', 'intensity': .75})
    light = scenes.load_scene(CID, scene['id'])['lights'][0]
    return scene['id'], light['id']


def _light(sid):
    return scenes.load_scene(CID, sid)['lights'][0]


def test_a_light_can_be_edited_after_placement(gm, lit):
    sid, lid = lit
    response = gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'update_light', 'id': lid,
        'radius': 720, 'color': '#3366ff', 'intensity': .2})
    assert response.status_code == 200
    light = _light(sid)
    assert light['radius'] == 720
    assert light['color'] == '#3366ff'
    assert abs(light['intensity'] - .2) < 1e-6


def test_updating_one_field_leaves_the_others_alone(gm, lit):
    sid, lid = lit
    gm.post(f'/api/scenes/{sid}/elements',
            json={'action': 'update_light', 'id': lid, 'radius': 500})
    light = _light(sid)
    assert light['radius'] == 500
    assert light['color'] == '#ffd98a'
    assert abs(light['intensity'] - .75) < 1e-6


def test_updating_an_unknown_light_is_a_404(gm, lit):
    sid, _lid = lit
    assert gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'update_light', 'id': 'nope', 'radius': 100}).status_code == 404


def test_intensity_is_clamped(gm, lit):
    sid, lid = lit
    for sent, expected in ((5, 1.0), (-3, .05), (0, .05)):
        gm.post(f'/api/scenes/{sid}/elements',
                json={'action': 'update_light', 'id': lid, 'intensity': sent})
        assert abs(_light(sid)['intensity'] - expected) < 1e-6, sent


# --- the colour has to be exactly #rrggbb ----------------------------------

@pytest.mark.parametrize('bad', ['red', '#f00', '', 'rgb(1,2,3)', 'javascript:x', None, 12345])
def test_a_colour_that_would_break_the_gradient_is_replaced(bad):
    """The client concatenates an alpha byte onto this. Anything else yields an
    invalid gradient stop, which throws inside the render loop and takes the
    whole canvas down -- not just the one light."""
    assert app._scene_light_color(bad) == '#ffd98a'


@pytest.mark.parametrize('good', ['#ff0000', '#FFD98A', '#123abc'])
def test_a_valid_colour_is_kept(good):
    assert app._scene_light_color(good) == good


def test_the_colour_is_validated_on_both_add_and_update(gm, lit):
    sid, lid = lit
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'update_light', 'id': lid, 'color': 'chartreuse'})
    assert _light(sid)['color'] == '#ffd98a'
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_light', 'x': 5, 'y': 5, 'color': 'chartreuse'})
    assert all(l['color'] == '#ffd98a' for l in scenes.load_scene(CID, sid)['lights'])


def test_editing_a_light_is_gm_only(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    scene = scenes.create_scene(CID, 'Guarded')
    with app.app.test_client() as client:
        assert client.post(f'/api/scenes/{scene["id"]}/elements', json={
            'action': 'update_light', 'id': 'x', 'radius': 10}).status_code == 403


# --- rendering -------------------------------------------------------------

def test_the_renderer_guards_the_colour_too():
    """Defence in depth: existing scenes already hold unvalidated colours."""
    body = _JS[_JS.index('function drawAmbientLights('):]
    body = body[:body.index('\n    }')]
    assert '/^#[0-9a-f]{6}$/i.test(light.color' in body


def test_brightness_actually_varies():
    body = _JS[_JS.index('function drawAmbientLights('):]
    body = body[:body.index('\n    }')]
    assert 'alpha * 200' in body, (
        'was alpha * 90 -- capped near 35%, so a candle and a bonfire looked '
        'the same and intensity was effectively decorative')


def test_lights_still_carve_vision():
    """This part was already right and must not regress: a light reveals what
    it illuminates on the table."""
    body = _JS[_JS.index('function drawVisionOverlay('):]
    body = body[:body.index('\n    }')]
    assert 'for (const light of scene.lights' in body
    assert 'carveVisibility' in body


# --- selecting one ---------------------------------------------------------

def test_a_light_is_selected_by_its_handle_not_its_glow():
    """A large light would otherwise swallow every click in the room."""
    body = _JS[_JS.index('function nearestLight('):]
    body = body[:body.index('\n    }')]
    assert 'distance < 22' in body


def test_the_light_inspector_exists_and_hides_like_the_token_one():
    assert 'id="map-light-actions"' in _HTML
    assert 'class="map-token-actions"' in _HTML, (
        'reuses the class that carries the [hidden] rule fixed in stage 1')
    assert 'function paintLightPanel(' in _JS


def test_editing_a_light_respects_the_in_progress_edit_rule():
    """Same rule as stage 1: an inbound frame must not overwrite what the GM is
    typing."""
    body = _JS[_JS.index('function paintLightPanel('):]
    body = body[:body.index('\n    }')]
    assert 'isBeingEdited(el)' in body
