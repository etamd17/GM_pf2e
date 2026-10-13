"""Pictures: uploaded portraits / combatant art, and the 5etools art index."""
import io
import json
import os

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
