"""Stage 5a: templates that mean what they say.

Three problems, all from Round 6.

Burst and emanation were rendered and hit-tested identically, so two toolbar
buttons did one thing. They are different in PF2e: a burst radiates from a
point, an emanation from a creature's whole space. Linking the source token is
what makes them differ -- the emanation grows by that creature's footprint, and
never catches the creature it radiates from.

Cone and line took their extent from how far you happened to drag, while burst
took it from a number field. So "20 feet" meant two different things depending
on which tool was selected, and PF2e states every area in feet. The number is
now the size for every shape; the drag only aims.

Coverage was tested as a circle of tokenRadius() around the token's centre.
That circle (size*grid*0.42) is smaller than the square's half-diagonal
(~0.707), so a template grazing a Large creature's corner missed it -- which is
exactly the case a GM has to adjudicate out loud, and exactly what stage 3 made
footprints real for. PF2e affects a creature when the area overlaps ANY square
of its space.
"""
from __future__ import annotations

import os

import pytest

import app
from core import scenes, storage


CID = 'd4' * 16
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()


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
def scene_with_token(gm):
    scene = scenes.create_scene(CID, 'Areas')
    token = scenes.add_token(scene, name='Ogre', x=300, y=300, size=2)
    scenes.save_scene(CID, scene)
    return scene['id'], token['id']


def _templates(sid):
    return scenes.load_scene(CID, sid).get('templates', [])


# --- emanation remembers its source ----------------------------------------

def test_an_emanation_records_the_creature_it_radiates_from(gm, scene_with_token):
    sid, tid = scene_with_token
    response = gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_template', 'kind': 'emanation',
        'x1': 300, 'y1': 300, 'radius': 200, 'source_token_id': tid})
    assert response.status_code == 200
    assert _templates(sid)[0]['source_token_id'] == tid


def test_a_burst_never_records_a_source(gm, scene_with_token):
    """A burst radiates from a point. Carrying a source would make it behave as
    an emanation in the hit test."""
    sid, tid = scene_with_token
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_template', 'kind': 'burst',
        'x1': 300, 'y1': 300, 'radius': 200, 'source_token_id': tid})
    assert 'source_token_id' not in _templates(sid)[0]


def test_a_source_that_names_nothing_is_dropped(gm, scene_with_token):
    """An unvalidated id would make the emanation quietly behave as a burst,
    which is the bug this whole change exists to remove."""
    sid, _tid = scene_with_token
    gm.post(f'/api/scenes/{sid}/elements', json={
        'action': 'add_template', 'kind': 'emanation',
        'x1': 10, 'y1': 10, 'radius': 100, 'source_token_id': 'not-a-token'})
    assert 'source_token_id' not in _templates(sid)[0]


# --- the client geometry ---------------------------------------------------

def test_every_shape_takes_its_size_from_the_number():
    """Cone and line used the drag length while burst used the field, so the
    same stated area produced two different shapes."""
    body = _JS[_JS.index('function draftTemplate('):]
    body = body[:body.index('\n    }')]
    assert 'Math.cos(aimed) * radius' in body and 'Math.sin(aimed) * radius' in body, (
        'cone/line must be projected to the stated radius, not the drag length')


def test_a_drag_only_aims_and_has_a_default():
    body = _JS[_JS.index('function draftTemplate('):]
    body = body[:body.index('\n    }')]
    assert '>= 5 ? aim : 0' in body, 'a click without a drag still needs a direction'


def test_the_old_short_drag_fallback_is_gone():
    """It patched the symptom of drag-sizing. With sizing fixed it is dead code
    that would silently override the aim."""
    assert 'template.x2 = template.x1 + template.radius;' not in _JS


def test_an_emanation_grows_by_its_source_footprint():
    body = _JS[_JS.index('function draftTemplate('):]
    body = body[:body.index('\n    }')]
    assert 'tokenFootprint(source) * gridSize / 2' in body, (
        'an emanation measures from the edge of the creature, not its centre')


# --- coverage uses the creature's space ------------------------------------

def test_coverage_samples_the_footprint_not_a_circle():
    assert 'function tokenSpacePoints(' in _JS
    body = _JS[_JS.index('function templateContainsToken('):]
    body = body[:body.index('\n    }')]
    assert 'tokenSpacePoints(token).some(' in body
    assert 'tokenRadius(token)' not in body, (
        'the circular padding was smaller than the square half-diagonal, so a '
        'template grazing a corner missed')


def test_the_space_points_include_the_corners():
    body = _JS[_JS.index('function tokenSpacePoints('):]
    body = body[:body.index('\n    }')]
    assert body.count('x - half') >= 2 and body.count('x + half') >= 2, (
        'corners are the whole point -- a centre-only test is what it replaced')
    assert 'tokenFootprint(token)' in body


def test_an_emanation_does_not_catch_its_own_source():
    body = _JS[_JS.index('function templateContainsToken('):]
    body = body[:body.index('\n    }')]
    assert "template.source_token_id === token.id" in body


def test_a_cone_still_spans_ninety_degrees():
    """PF2e cones are a quarter circle. The render and the hit test have to
    agree or the highlighted squares will not match who is targeted."""
    body = _JS[_JS.index('function templatePointInside('):]
    body = body[:body.index('\n    }')]
    assert 'Math.PI / 4' in body
    draw = _JS[_JS.index('function drawTemplate('):]
    draw = draw[:draw.index('\n    }')]
    assert 'Math.PI / 4' in draw


def test_the_cone_origin_square_counts():
    """distance 0 has no meaningful angle, so the creature at the apex would
    fall out of the cone on a floating-point technicality."""
    body = _JS[_JS.index('function templatePointInside('):]
    body = body[:body.index('\n    }')]
    assert 'distance < 1e-6' in body
