"""Stage 2b of the map audit: opening a scene is not showing it to the players.

They used to be one act. Creating a scene, or even glancing at one in the
picker, activated it -- and activation broadcast to every connected client,
which navigated. So building next week's ambush during this week's session put
it on the table mid-fight, and there was no way not to.

`active_scene_id` now means exactly one thing: the scene the table screen is
showing. What the GM has open is their URL and nobody else's business. The
stored key keeps its name so no migration is needed; only the meaning narrowed.

Scenes can also be deleted now, which they never could -- there was no route at
all, so every scene and every uploaded battlemap accumulated forever.
"""
from __future__ import annotations

import io
import os

import pytest

import app
from core import scenes, storage


CID = 'c' * 32
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
    monkeypatch.setattr(app, 'sse_broadcast', lambda *_a, **_k: None)
    return app.app.test_client()


# --- prep is private -------------------------------------------------------

def test_a_new_scene_is_not_on_the_table(gm):
    first = gm.post('/api/scenes', json={'name': 'Ambush prep'})
    assert first.status_code == 201
    assert scenes.table_scene_id(CID) is None, 'creating a scene must not push it'


def test_even_the_very_first_scene_is_not_pushed(gm):
    """The old code special-cased "no active scene yet" and activated the first
    one created, so a campaign's first scene went straight to the players."""
    gm.post('/api/scenes', json={'name': 'Only scene'})
    assert scenes.table_scene_id(CID) is None


def test_nothing_on_the_table_is_a_representable_state(gm):
    """The old lookup fell back to "the first scene that exists", so the table
    could never be empty -- which is the normal state before a session."""
    scenes.create_scene(CID, 'A')
    scenes.create_scene(CID, 'B')
    assert scenes.table_scene_id(CID) is None
    body = gm.get('/api/scenes').get_json()
    assert body['active_scene_id'] is None


def test_showing_a_scene_puts_exactly_that_one_on_the_table(gm):
    a = scenes.create_scene(CID, 'A')
    b = scenes.create_scene(CID, 'B')
    assert gm.post(f'/api/scenes/{b["id"]}/activate').status_code == 200
    assert scenes.table_scene_id(CID) == b['id']
    assert scenes.table_scene_id(CID) != a['id']


def test_the_gm_still_lands_somewhere_sensible(gm):
    """Separating the two must not leave /map with nothing to open."""
    assert scenes.default_open_scene_id(CID) is None
    scene = scenes.create_scene(CID, 'Prep')
    assert scenes.default_open_scene_id(CID) == scene['id']   # nothing on table -> newest
    later = scenes.create_scene(CID, 'Newer')
    assert scenes.default_open_scene_id(CID) == later['id']
    scenes.set_table_scene(CID, scene['id'])
    assert scenes.default_open_scene_id(CID) == scene['id']   # on-table wins


def test_the_table_can_be_cleared(gm):
    scene = scenes.create_scene(CID, 'Done with this')
    scenes.set_table_scene(CID, scene['id'])
    scenes.set_table_scene(CID, None)
    assert scenes.table_scene_id(CID) is None


def test_a_deleted_table_scene_does_not_haunt_the_table(gm):
    """The stored id outlives the file if a scene is removed another way."""
    scene = scenes.create_scene(CID, 'Vanishing')
    scenes.set_table_scene(CID, scene['id'])
    os.remove(storage.scene_file(CID, scene['id']))
    assert scenes.table_scene_id(CID) is None


# --- deleting --------------------------------------------------------------

def test_deleting_removes_the_scene_and_its_background(gm):
    from PIL import Image
    scene = scenes.create_scene(CID, 'Disposable')
    sid = scene['id']
    buf = io.BytesIO()
    Image.new('RGB', (400, 300), (1, 2, 3)).save(buf, format='PNG')
    buf.seek(0)
    gm.post(f'/api/scenes/{sid}/background',
            data={'image': (buf, 'm.png', 'image/png')},
            content_type='multipart/form-data')
    asset = os.path.join(storage.scene_assets_dir(CID), sid + '.png')
    assert os.path.exists(asset)

    assert gm.post(f'/api/scenes/{sid}/delete').status_code == 200
    assert scenes.load_scene(CID, sid) is None
    assert not os.path.exists(asset), 'the battlemap should go with the scene'


def test_the_scene_on_the_table_cannot_be_deleted(gm):
    """Deleting what the players are looking at should not be a misclick."""
    scene = scenes.create_scene(CID, 'Live')
    scenes.set_table_scene(CID, scene['id'])
    response = gm.post(f'/api/scenes/{scene["id"]}/delete')
    assert response.status_code == 409
    assert scenes.load_scene(CID, scene['id']) is not None
    # ...and taking it off the table makes it deletable.
    scenes.set_table_scene(CID, None)
    assert gm.post(f'/api/scenes/{scene["id"]}/delete').status_code == 200


def test_deleting_an_unknown_scene_is_a_404(gm):
    assert gm.post('/api/scenes/' + ('d' * 32) + '/delete').status_code == 404


def test_delete_is_gm_gated_like_every_other_scene_route(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    scene = scenes.create_scene(CID, 'Guarded')
    with app.app.test_client() as client:
        assert client.post(f'/api/scenes/{scene["id"]}/delete').status_code == 403
    assert scenes.load_scene(CID, scene['id']) is not None


def test_delete_lives_on_its_own_path():
    """Not DELETE on /api/scenes/<id>: that path already serves GET and PATCH,
    and keeping destruction on its own verb+path means a later change there
    cannot widen into deletion by accident."""
    app_py = open(os.path.join(_ROOT, 'app.py'), encoding='utf-8').read()
    assert "@app.route('/api/scenes/<scene_id>/delete', methods=['POST'])" in app_py


# --- the client stops dragging everyone around -----------------------------

def test_choosing_a_scene_opens_it_without_pushing_it():
    handler = _JS[_JS.index("sceneSelect.addEventListener('change'"):]
    handler = handler[:handler.index('});')]
    assert 'location.href' in handler
    assert 'activate' not in handler, (
        'picking a scene must not push it to the table -- that is what made '
        'every scene the GM glanced at appear in front of the players')


def test_an_activation_elsewhere_does_not_move_the_gms_view():
    # There are two scene_activated handlers now, so find the working one by
    # what it does rather than by taking whichever appears first.
    handler = _JS[_JS.index("appSSE('scene_activated', function (event) {"):]
    handler = handler[:handler.index('});')]
    code = '\n'.join(line.split('//')[0] for line in handler.splitlines())
    assert 'location.href' not in code, (
        "the GM's window must not be yanked when a scene is pushed to the table")
    assert 'paintTableState()' in code


def test_the_only_activation_reload_is_the_table_screens():
    """A table screen opened before any scene is pushed subscribes from the
    early-return path and reloads when one arrives, because the page is
    server-rendered around a scene id and there is nothing to hand a scene to.

    That reload must stay fenced behind cfg.tableView. On the GM's own window it
    would be exactly the yank the test above exists to prevent -- and it would
    fire mid-prep, every time they pushed a scene."""
    early = _JS[:_JS.index("const ctx = canvas.getContext('2d');")]
    assert 'window.location.reload();' in early
    guard = early[early.index('wireCreateForm();'):early.index('window.location.reload();')]
    assert 'cfg.tableView' in guard
    # ...and it must live in the branch that runs when there is no scene at all.
    assert early.index('if (!sceneId') < early.index('window.location.reload();')


def test_the_sidebar_says_whether_this_scene_is_live():
    assert 'function paintTableState(' in _JS
    for phrase in ('On the table now', 'Another scene is on the table', 'Not on the table'):
        assert phrase in _JS, phrase


def test_deleting_asks_first():
    handler = _JS[_JS.index("map-delete-scene').addEventListener"):]
    assert 'window.confirm(' in handler[:400], 'destructive and irreversible'
