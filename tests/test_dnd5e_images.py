"""Pictures: uploaded portraits / combatant art, and the 5etools art index."""
import io
import json
import os
import time
from pathlib import Path

import pytest


def _png(w=900, h=600, mode='RGB'):
    from PIL import Image
    buf = io.BytesIO()
    Image.new(mode, (w, h), (200, 30, 30) if mode == 'RGB' else (200, 30, 30, 128)).save(buf, 'PNG')
    return buf.getvalue()


@pytest.fixture
def camp(tmp_path, monkeypatch):
    from core import storage
    monkeypatch.setattr(storage, 'dnd5e_dir', lambda cid: str(tmp_path / cid / 'dnd5e'))
    return 'c' * 32


def test_upload_is_reencoded_and_shrunk(camp):
    from PIL import Image
    from systems.dnd5e import images
    name = images.save_upload(camp, _png())
    assert images.NAME_RE.match(name)
    p = images.path_for(camp, name)
    with Image.open(p) as im:          # closed before the delete below (Windows locks open files)
        assert im.format == 'WEBP' and max(im.size) == images.SIZE and im.size[0] > im.size[1]
    # transparency survives
    name2 = images.save_upload(camp, _png(64, 64, 'RGBA'), size=256)
    with Image.open(images.path_for(camp, name2)) as im2:
        assert im2.mode == 'RGBA'
    assert images.url_for_name(name) == '/dnd5e/img/' + name
    assert images.delete(camp, name) and images.path_for(camp, name) is None


def test_bad_uploads_are_refused(camp, monkeypatch):
    from systems.dnd5e import images
    for bad in (b'', b'GIF89a not really', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', b'%PDF-1.4'):
        with pytest.raises(images.ImageError):
            images.save_upload(camp, bad)
    monkeypatch.setattr(images, 'MAX_BYTES', 100)
    with pytest.raises(images.ImageError, match='too large'):
        images.save_upload(camp, _png())
    monkeypatch.setattr(images, 'MAX_BYTES', 10_000_000)
    monkeypatch.setattr(images, 'MAX_PIXELS', 1000)
    with pytest.raises(images.ImageError, match='too large'):
        images.save_upload(camp, _png(100, 100))
    # nothing half-written was left behind
    d = images.images_dir(camp)
    assert not os.path.isdir(d) or os.listdir(d) == []


def test_names_cannot_escape_the_images_dir(camp, tmp_path):
    from systems.dnd5e import images
    secret = tmp_path / 'secret.webp'
    secret.write_bytes(b'x')
    for name in ('../secret.webp', '..\\secret.webp', 'secret.webp', '/etc/passwd', 'A' * 32 + '.webp',
                 '0' * 32 + '.png', '', None):
        assert images.path_for(camp, name) is None
        assert images.url_for_name(name) is None
        assert images.delete(camp, name) is False
    assert secret.exists()


def test_image_index_lookup(tmp_path, monkeypatch):
    from systems.dnd5e import content as C
    idx = {'version': 1, 'base': '/dnd5e/art/', 'missing': [],
           'entities': {'monster': {'goblin|mm': {'token': 'tokens/a.webp', 'art': 'art/b.webp'}},
                        'subclass': {'battle master|phb|fighter|phb': {'art': 'art/c.webp'}}}}
    p = tmp_path / 'images.json'
    p.write_text(json.dumps(idx), encoding='utf-8')
    monkeypatch.setattr(C.paths, 'images_index_path', lambda: str(p))
    monkeypatch.setitem(C._images, 'mtime', None)
    assert C.image_for('monster', 'Goblin|MM', 'token') == '/dnd5e/art/tokens/a.webp'
    assert C.image_for('monster', 'goblin|mm') == '/dnd5e/art/art/b.webp'
    assert C.image_for('monster', 'orc|mm') is None
    # the PHB subclass reprinted for the XPHB class shares its art
    assert C.image_for('subclass', 'battle master|phb|fighter|xphb') == '/dnd5e/art/art/c.webp'
    monkeypatch.setattr(C.paths, 'images_index_path', lambda: str(tmp_path / 'nope.json'))
    assert C.image_for('monster', 'goblin|mm') is None


def test_fetch_script_paths_match_5etools():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'fetch_images', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tools', 'dnd5e', 'fetch_images.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # Parser.nameToTokenName: diacritics stripped, AE ligature expanded, quotes dropped
    assert m._ascii('Chárd "Big" Æther') == 'Chard Big AEther'
    # file names are case-safe hashes (the mirror has paths differing only by case)
    assert m._file_for('bestiary/tokens/MM/Goblin.webp') != m._file_for('bestiary/tokens/MM/goblin.webp')
    assert m._file_for('x').endswith('.webp')


def _uploaded_image(camp, *, age=700):
    from systems.dnd5e import images
    name = images.save_upload(camp, _png(32, 32))
    path = Path(images.images_dir(camp)) / name
    modified = time.time() - age
    os.utime(path, (modified, modified))
    return name, path


def test_gc_deletes_only_old_unreferenced_uploads(camp):
    from systems.dnd5e import images
    orphan, orphan_path = _uploaded_image(camp)
    kept, kept_path = _uploaded_image(camp)
    _, young_path = _uploaded_image(camp, age=599)
    _, future_path = _uploaded_image(camp, age=-60)
    _, other_campaign_path = _uploaded_image('d' * 32)
    directory = orphan_path.parent
    unrelated = directory / 'notes.txt'
    unrelated.write_text('Not an upload.', encoding='utf-8')
    unfinished = directory / (orphan + '.tmp')
    unfinished.write_bytes(b'pending upload')
    invalid_name = directory / ('a' * 32 + '.webp\n') if os.name != 'nt' else directory / ('a' * 32 + '.png')
    invalid_name.write_bytes(b'not a managed upload')
    nested = directory / ('b' * 32 + '.webp')
    nested.mkdir()
    child = nested / orphan
    child.write_bytes(b'not a direct upload')

    assert images.collect_garbage(camp, {kept}) == 1
    assert not orphan_path.exists()
    assert all(p.exists() for p in (kept_path, young_path, future_path, other_campaign_path,
                                   unrelated, unfinished, invalid_name, child))
    assert images.collect_garbage(camp, {kept}) == 0


def test_gc_with_missing_directory_does_not_create_it(camp):
    from systems.dnd5e import images
    directory = Path(images.images_dir(camp))
    assert images.collect_garbage(camp, set()) == 0
    assert not directory.exists()


def test_gc_does_not_follow_upload_symlinks(camp, tmp_path):
    from systems.dnd5e import images
    _, regular = _uploaded_image(camp)
    outside = tmp_path / 'outside.webp'
    outside.write_bytes(b'outside upload directory')
    link = regular.parent / ('e' * 32 + '.webp')
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('This Windows account cannot create symlinks')
    assert images.collect_garbage(camp, set()) == 1
    assert link.is_symlink() and outside.read_bytes() == b'outside upload directory'


@pytest.fixture
def image_client(camp, monkeypatch):
    """Real blueprint/store; only host hooks and data-availability gate are substituted.

    No rule records are needed for quick combatants. Central app authorization
    remains covered by test_dnd5e_app_integration, not by this isolated host.
    """
    from flask import Flask
    from systems.dnd5e import content, routes, store
    store.forget_cache(camp)
    monkeypatch.setattr(content, 'available', lambda: True)
    app = Flask(__name__)
    app.config['TESTING'] = True
    host = routes.Host(campaign_id=lambda: camp, is_gm=lambda: True, current_user=lambda: None,
                       account_mode=lambda: False, broadcast=lambda *args, **kwargs: None,
                       campaign_doc=lambda: {})
    app.register_blueprint(routes.create_blueprint(host))
    yield app.test_client()
    store.forget_cache(camp)


@pytest.mark.parametrize('action', ['replace', 'clear_image', 'remove', 'clear', 'replace_plan'])
def test_combat_image_cleanup_preserves_portraits_and_live_shared_images(camp, image_client, action):
    from systems.dnd5e import combat, store
    old, old_path = _uploaded_image(camp)
    portrait, portrait_path = _uploaded_image(camp)
    shared, shared_path = _uploaded_image(camp)
    _, pending_path = _uploaded_image(camp, age=30)
    doc = store.new_pc(camp, name='Portrait owner')
    doc['portrait'] = portrait
    store.save_pc(camp, doc)
    target = combat.new_quick_combatant('Target', hp=12, ac=10)
    target['image'] = old
    others = [combat.new_quick_combatant('Shared '+str(i), hp=10, ac=10) for i in range(2)]
    for other in others:
        other['image'] = shared
    store.mutate_encounter(camp, lambda enc: enc['combatants'].extend([target, *others]))

    if action in ('replace', 'clear_image'):
        data = {'cid': target['cid']}
        if action == 'replace':
            data['file'] = (io.BytesIO(_png(32, 32)), 'replacement.png')
        else:
            data['clear'] = '1'
        response = image_client.post('/api/dnd5e/gm/combat/image', data=data)
    elif action == 'replace_plan':
        plan = store.save_plan(camp, {'name': 'Empty plan', 'monsters': []})
        response = image_client.post('/api/dnd5e/gm/combat/stage_plan',
                                     json={'plan_id': plan['id'], 'replace': True})
    else:
        response = image_client.post('/api/dnd5e/gm/combat/'+action, json={'cid': target['cid']})

    assert response.status_code == 200, response.data
    assert not old_path.exists()
    assert portrait_path.exists() and pending_path.exists()
    assert shared_path.exists() == (action not in ('clear', 'replace_plan'))
    current = store.load_encounter(camp)
    if action == 'replace':
        replacement = next(c for c in current['combatants'] if c['cid'] == target['cid'])['image']
        assert (old_path.parent / replacement).exists()
        assert response.get_json()['image'] == '/dnd5e/img/'+replacement
    elif action == 'clear_image':
        assert 'image' not in next(c for c in current['combatants'] if c['cid'] == target['cid'])


@pytest.mark.parametrize('action', ['replace', 'remove'])
def test_replaced_or_removed_image_survives_on_another_combatant(camp, image_client, action):
    from systems.dnd5e import combat, store
    name, path = _uploaded_image(camp)
    rows = [combat.new_quick_combatant(n, hp=10, ac=10) for n in ('One', 'Two')]
    for row in rows:
        row['image'] = name
    store.mutate_encounter(camp, lambda enc: enc['combatants'].extend(rows))
    if action == 'replace':
        response = image_client.post('/api/dnd5e/gm/combat/image',
                                     data={'cid': rows[0]['cid'], 'file': (io.BytesIO(_png(32, 32)), 'new.png')})
    else:
        response = image_client.post('/api/dnd5e/gm/combat/remove', json={'cid': rows[0]['cid']})
    assert response.status_code == 200, response.data
    assert path.exists()
    assert next(c for c in store.load_encounter(camp)['combatants'] if c['cid'] == rows[1]['cid'])['image'] == name


@pytest.mark.parametrize('action', ['replace', 'clear_image', 'remove', 'clear', 'replace_plan'])
def test_cleanup_io_failure_never_fails_combat_action(camp, image_client, monkeypatch, action):
    from systems.dnd5e import combat, images, store
    old, old_path = _uploaded_image(camp)
    row = combat.new_quick_combatant('Target', hp=10, ac=10)
    row['image'] = old
    store.mutate_encounter(camp, lambda enc: enc['combatants'].append(row))

    def failed_collection(cid, keep):
        raise OSError('Unreadable image directory')

    monkeypatch.setattr(images, 'collect_garbage', failed_collection)
    if action in ('replace', 'clear_image'):
        data = {'cid': row['cid']}
        if action == 'replace':
            data['file'] = (io.BytesIO(_png(32, 32)), 'new.png')
        else:
            data['clear'] = '1'
        response = image_client.post('/api/dnd5e/gm/combat/image', data=data)
    elif action == 'replace_plan':
        plan = store.save_plan(camp, {'name': 'Empty plan', 'monsters': []})
        response = image_client.post('/api/dnd5e/gm/combat/stage_plan',
                                     json={'plan_id': plan['id'], 'replace': True})
    else:
        response = image_client.post('/api/dnd5e/gm/combat/'+action, json={'cid': row['cid']})
    assert response.status_code == 200, response.data
    assert old_path.exists()
    rows = store.load_encounter(camp)['combatants']
    if action in ('clear', 'remove', 'replace_plan'):
        assert rows == []
    elif action == 'clear_image':
        assert 'image' not in rows[0]
    else:
        assert rows[0]['image'] != old


def test_locked_orphan_does_not_block_other_cleanup(camp, image_client, monkeypatch):
    from systems.dnd5e import images
    paths = [_uploaded_image(camp)[1] for _ in range(4)]
    original_remove = images.os.remove
    locked = []

    def remove_unless_locked(path):
        if not locked:
            locked.append(Path(path))
            raise PermissionError('Picture is locked')
        return original_remove(path)

    monkeypatch.setattr(images.os, 'remove', remove_unless_locked)
    response = image_client.post('/api/dnd5e/gm/combat/clear', json={})

    assert response.status_code == 200, response.data
    assert len(locked) == 1 and locked[0].exists()
    assert sum(not path.exists() for path in paths) == 3


def test_cleanup_preserves_files_when_a_character_reference_cannot_be_read(camp, image_client, monkeypatch):
    from core import storage
    from systems.dnd5e import combat, store
    portrait, portrait_path = _uploaded_image(camp)
    doc = store.new_pc(camp, name='Portrait owner')
    doc['portrait'] = portrait
    store.save_pc(camp, doc)
    row = combat.new_quick_combatant('Target', hp=10, ac=10)
    store.mutate_encounter(camp, lambda enc: enc['combatants'].append(row))
    original_load = storage.load_json

    def unreadable_character(path, *args, **kwargs):
        if os.fspath(path) == store.pc_path(camp, doc['id']):
            return None       # load_json returns a default on corrupt/unreadable JSON
        return original_load(path, *args, **kwargs)

    monkeypatch.setattr(storage, 'load_json', unreadable_character)
    response = image_client.post('/api/dnd5e/gm/combat/clear', json={})
    assert response.status_code == 200, response.data
    assert portrait_path.exists()
