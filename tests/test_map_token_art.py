"""Per-token art upload.

Round 3 chose "compendium art by default, with per-token upload as an override".
The automatic half turned out not to exist: every monster JSON carries an `img`,
but across 2497 bestiary files 2473 of 2475 entries are the same generic Foundry
default icon and the two exceptions are Foundry-internal paths this app cannot
serve. Wiring it would give every creature an identical grey silhouette, which
is worse than the coloured disc with initials it would replace.

So uploading is the only route to real token art, and the only one built. The
tests below are mostly about the things that go wrong with stored files rather
than about the picture: art must not outlive its token, must not survive a
format change, and must not be reachable or writable by the wrong people.
"""
from __future__ import annotations

import io
import os

import pytest

import app
from core import scenes, storage


CID = 'e' * 32


def _png(colour=(90, 20, 20)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new('RGB', (128, 128), colour).save(buf, format='PNG')
    buf.seek(0)
    return buf


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
def token(gm):
    scene = scenes.create_scene(CID, 'Art')
    tok = scenes.add_token(scene, name='Ogre')
    scenes.save_scene(CID, scene)
    return scene['id'], tok['id']


def _art_files(sid):
    assets = storage.scene_assets_dir(CID)
    if not os.path.isdir(assets):
        return []
    return sorted(n for n in os.listdir(assets) if '_token_' in n)


def test_uploading_art_points_the_token_at_it(gm, token):
    sid, tid = token
    response = gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
                       data={'image': (_png(), 'ogre.png', 'image/png')},
                       content_type='multipart/form-data')
    assert response.status_code == 200
    stored = scenes.load_scene(CID, sid)['tokens'][0]
    assert stored['image'], 'the token should now reference its art'
    assert f'/tokens/{tid}/image' in stored['image']
    assert 'v=' in stored['image'], 'needs a cache buster or a replacement never shows'
    assert len(_art_files(sid)) == 1


def test_the_art_is_served_back(gm, token):
    sid, tid = token
    gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
            data={'image': (_png(), 'a.png', 'image/png')},
            content_type='multipart/form-data')
    served = gm.get(f'/api/scenes/{sid}/tokens/{tid}/image')
    assert served.status_code == 200
    assert served.mimetype == 'image/png'
    assert served.data[:8] == b'\x89PNG\r\n\x1a\n'


def test_replacing_art_with_another_format_leaves_one_file(gm, token):
    """The filename carries the extension, so without cleanup the old file
    survives -- and the GET, which probes formats in order, could keep finding
    the stale one."""
    sid, tid = token
    gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
            data={'image': (_png(), 'a.png', 'image/png')},
            content_type='multipart/form-data')
    from PIL import Image
    jpeg = io.BytesIO()
    Image.new('RGB', (64, 64), (5, 5, 5)).save(jpeg, format='JPEG')
    jpeg.seek(0)
    gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
            data={'image': (jpeg, 'a.jpg', 'image/jpeg')},
            content_type='multipart/form-data')
    files = _art_files(sid)
    assert len(files) == 1, files
    assert files[0].endswith('.jpg')
    assert gm.get(f'/api/scenes/{sid}/tokens/{tid}/image').mimetype == 'image/jpeg'


def test_clearing_art_removes_the_file_and_the_reference(gm, token):
    sid, tid = token
    gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
            data={'image': (_png(), 'a.png', 'image/png')},
            content_type='multipart/form-data')
    assert gm.delete(f'/api/scenes/{sid}/tokens/{tid}/image').status_code == 200
    assert _art_files(sid) == []
    assert not scenes.load_scene(CID, sid)['tokens'][0]['image']


def test_deleting_the_token_takes_its_art(gm, token):
    """Otherwise the file outlives the token with nothing referencing it -- the
    same way background assets used to be stranded."""
    sid, tid = token
    gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
            data={'image': (_png(), 'a.png', 'image/png')},
            content_type='multipart/form-data')
    assert _art_files(sid)
    assert gm.delete(f'/api/scenes/{sid}/tokens/{tid}').status_code == 200
    assert _art_files(sid) == []


def test_deleting_the_scene_takes_every_token_s_art(gm):
    scene = scenes.create_scene(CID, 'Doomed')
    sid = scene['id']
    ids = []
    for name in ('A', 'B', 'C'):
        tok = scenes.add_token(scene, name=name)
        ids.append(tok['id'])
    scenes.save_scene(CID, scene)
    for tid in ids:
        gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
                data={'image': (_png(), 'x.png', 'image/png')},
                content_type='multipart/form-data')
    assert len(_art_files(sid)) == 3
    assert gm.post(f'/api/scenes/{sid}/delete').status_code == 200
    assert _art_files(sid) == []


def test_bytes_that_are_not_an_image_are_rejected(gm, token):
    sid, tid = token
    response = gm.post(f'/api/scenes/{sid}/tokens/{tid}/image',
                       data={'image': (io.BytesIO(b'not a png at all'), 'x.png', 'image/png')},
                       content_type='multipart/form-data')
    assert response.status_code == 400
    assert _art_files(sid) == []
    assert not scenes.load_scene(CID, sid)['tokens'][0]['image']


def test_an_unknown_token_is_a_404_not_a_stray_file(gm, token):
    sid, _tid = token
    response = gm.post(f'/api/scenes/{sid}/tokens/{"f" * 32}/image',
                       data={'image': (_png(), 'x.png', 'image/png')},
                       content_type='multipart/form-data')
    assert response.status_code == 404
    assert _art_files(sid) == []


def test_uploading_is_gm_only(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, 'CAMPAIGNS_DIR', str(tmp_path / 'campaigns'))
    storage.ensure_campaign_dirs(CID)
    monkeypatch.setattr(app, '_active_campaign_id', lambda: CID)
    monkeypatch.setattr(app, '_scene_member_allowed', lambda: True)
    monkeypatch.setattr(app, '_is_gm', lambda: False)
    scene = scenes.create_scene(CID, 'Guarded')
    tok = scenes.add_token(scene, name='Ogre')
    scenes.save_scene(CID, scene)
    with app.app.test_client() as client:
        for method in (client.post, client.delete):
            assert method(f'/api/scenes/{scene["id"]}/tokens/{tok["id"]}/image').status_code == 403


def test_the_whole_route_is_behind_the_scene_prefix_gate():
    """It lives under /api/scenes, so check_gm_access covers it like the rest --
    the guard added in the port exists so a new route cannot open a hole."""
    rules = [r.rule for r in app.app.url_map.iter_rules()
             if r.rule.endswith('/tokens/<token_id>/image')]
    assert rules, 'route not registered'
    for rule in rules:
        assert any(rule.startswith(p) for p in app.GM_API_PREFIXES), rule
