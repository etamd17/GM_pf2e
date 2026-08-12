"""Stage 2a of the map audit: the image is the map.

A background used to be stretched to whatever size the scene happened to be
created with, so a 4096x2304 battlemap was squashed into 1400x900 and zoom could
never reach the source pixels. The scene now takes the image's shape.

Three smaller things are fixed in the same upload path, because they live in the
same twenty lines and are all about the asset rather than the picture:
the bytes are now verified to be a real image (the only prior check was the
client-supplied mimetype, which the client chooses), replacing a PNG with a JPEG
no longer strands the PNG on disk forever, and the derived grid size is saved
alongside the offsets so one alignment drag calibrates both.
"""
from __future__ import annotations

import io
import os

import pytest

import app
from core import scenes, storage


CID = 'b' * 32
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = open(os.path.join(_ROOT, 'static', 'js', 'map.js'), encoding='utf-8').read()
_HTML = open(os.path.join(_ROOT, 'templates', 'map.html'), encoding='utf-8').read()


def _png(width, height):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (20, 30, 40)).save(buf, format='PNG')
    buf.seek(0)
    return buf


@pytest.fixture
def scene_client(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: True)
    monkeypatch.setattr(app, '_broadcast_scene', lambda *_a, **_k: None)
    return app.app.test_client()


# --- the sizing rule, as a pure function -----------------------------------

@pytest.mark.parametrize('given, expected', [
    ((4096, 2304), (4096, 2304)),     # a real battlemap, untouched
    ((1400, 900), (1400, 900)),
    ((12000, 8000), (8000, 5333)),    # capped, aspect 1.5 preserved
    ((9000, 3000), (8000, 2667)),
    ((640, 640), (640, 640)),         # square stays square
])
def test_scene_takes_the_image_shape(given, expected):
    assert scenes.fit_scene_dimensions(*given) == expected


def test_a_tiny_image_still_gets_a_usable_scene():
    """The one case where aspect is deliberately not preserved -- a sliver would
    otherwise produce a scene too thin to interact with."""
    assert scenes.fit_scene_dimensions(100, 50) == (scenes.MIN_WIDTH, scenes.MIN_HEIGHT)


@pytest.mark.parametrize('bad', [(0, 10), (10, 0), (-5, 5), ('x', None), (None, None)])
def test_unusable_sizes_are_rejected_rather_than_guessed(bad):
    assert scenes.fit_scene_dimensions(*bad) is None


# --- through the upload route ----------------------------------------------

def test_uploading_a_background_resizes_the_scene_to_it(scene_client):
    scene = scenes.create_scene(CID, 'Squashed', width=1400, height=900)
    assert (scene['width'], scene['height']) == (1400, 900)

    response = scene_client.post(
        f'/api/scenes/{scene["id"]}/background',
        data={'image': (_png(2048, 1152), 'map.png', 'image/png')},
        content_type='multipart/form-data')
    assert response.status_code == 200

    stored = scenes.load_scene(CID, scene['id'])
    assert (stored['width'], stored['height']) == (2048, 1152)


def test_replacing_the_background_does_not_strand_the_old_asset(scene_client):
    """The asset is named <scene_id><ext>, so a format change used to leave the
    previous file on disk with nothing referencing it and nothing removing it."""
    scene = scenes.create_scene(CID, 'Reupload')
    sid = scene['id']
    assets = storage.scene_assets_dir(CID)

    scene_client.post(f'/api/scenes/{sid}/background',
                      data={'image': (_png(800, 600), 'a.png', 'image/png')},
                      content_type='multipart/form-data')
    assert os.path.exists(os.path.join(assets, sid + '.png'))

    jpeg = io.BytesIO()
    from PIL import Image
    Image.new('RGB', (900, 900), (10, 10, 10)).save(jpeg, format='JPEG')
    jpeg.seek(0)
    scene_client.post(f'/api/scenes/{sid}/background',
                      data={'image': (jpeg, 'a.jpg', 'image/jpeg')},
                      content_type='multipart/form-data')

    assert os.path.exists(os.path.join(assets, sid + '.jpg'))
    assert not os.path.exists(os.path.join(assets, sid + '.png')), 'old asset stranded'
    assert scenes.load_scene(CID, sid)['background']['mime'] == 'image/jpeg'


def test_bytes_that_are_not_an_image_are_rejected(scene_client):
    """The mimetype is chosen by the client, so it proves nothing on its own."""
    scene = scenes.create_scene(CID, 'Not An Image')
    response = scene_client.post(
        f'/api/scenes/{scene["id"]}/background',
        data={'image': (io.BytesIO(b'MZ\x90\x00 this is not a png'), 'x.png', 'image/png')},
        content_type='multipart/form-data')
    assert response.status_code == 400
    stored = scenes.load_scene(CID, scene['id'])
    assert not stored.get('background'), 'a rejected upload must not be recorded'


def test_a_rejected_upload_leaves_no_temp_file_behind(scene_client):
    scene = scenes.create_scene(CID, 'Cleanup')
    assets = storage.scene_assets_dir(CID)
    scene_client.post(
        f'/api/scenes/{scene["id"]}/background',
        data={'image': (io.BytesIO(b'nope'), 'x.png', 'image/png')},
        content_type='multipart/form-data')
    leftovers = [n for n in os.listdir(assets)] if os.path.isdir(assets) else []
    assert not [n for n in leftovers if n.endswith('.upload')], leftovers


# --- grid calibration derives size, not just offset ------------------------

def test_alignment_drag_derives_size_and_offset():
    assert 'function calibrationFromDrag(' in _JS
    body = _JS[_JS.index('function calibrationFromDrag('):_JS.index('function setCalibrationMode(')]
    assert 'map-calibrate-squares' in body, 'size must come from a stated span'
    assert 'Math.max(dx, dy)' in body, (
        'measure the longer axis only -- a drag is never perfectly horizontal '
        'and averaging both axes lets vertical wobble shrink the derived size')
    assert 'normalizedOffset(start.x' in body and 'normalizedOffset(start.y' in body


def test_a_short_drag_does_not_rewrite_the_grid_size():
    """A stray click in alignment mode must not redefine the squares."""
    body = _JS[_JS.index('function calibrationFromDrag('):_JS.index('function setCalibrationMode(')]
    assert 'span < 8 ? current' in body


def test_the_alignment_drag_saves_the_size_too():
    saved = _JS[_JS.index("if (finished.type === 'grid')"):]
    assert 'size: scene.grid.size' in saved[:400], (
        'deriving the size but only persisting the offsets would silently drop it')


def test_the_squares_control_exists():
    assert 'id="map-calibrate-squares"' in _HTML


# --- per-scene viewport ----------------------------------------------------

def test_the_viewport_is_remembered_per_scene_and_per_role():
    assert 'function saveView(' in _JS and 'function restoreView(' in _JS
    key = _JS[_JS.index("const VIEW_KEY ="):]
    assert "cfg.isGm ? 'gm' : 'table'" in key[:200], (
        'the table screen is a different view of the same scene and must never '
        'inherit the GM viewport')
    assert 'if (isTableView() || !restoreView()) fitMap();' in _JS, (
        'a first visit should fit the scene rather than land in the top-left corner')
