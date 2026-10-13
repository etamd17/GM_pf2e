"""The 5e rules data lives outside git: where it is looked for, and how a
bundle is built and (safely) installed. Synthetic data only -- these run in CI
whether or not the real data was built."""
import io
import json
import os
import sqlite3
import tarfile

import pytest

from systems.dnd5e import datapack, paths


def _fake_data(d, rows=1200, images=3):
    os.makedirs(d, exist_ok=True)
    con = sqlite3.connect(os.path.join(d, 'dnd5e.db'))
    con.execute('CREATE TABLE entity (kind TEXT, id TEXT, data TEXT)')
    con.execute('CREATE TABLE meta (key TEXT, value TEXT)')
    con.executemany('INSERT INTO entity VALUES (?,?,?)', [('spell', f's{i}', '{}') for i in range(rows)])
    con.execute("INSERT INTO meta VALUES ('version', 'test')")
    con.commit()
    con.close()
    with open(os.path.join(d, '_tags.json'), 'w', encoding='utf-8') as f:
        json.dump([{'tag': 'spell', 'defaultSource': 'XPHB'}], f)
    with open(os.path.join(d, 'images.json'), 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'entities': {}}, f)
    with open(os.path.join(d, 'VERSION'), 'w', encoding='utf-8') as f:
        f.write('5etools-src 9.9.9 (test)\n')
    for k in ('tokens', 'art'):
        os.makedirs(os.path.join(d, 'img', k), exist_ok=True)
        for i in range(images):
            with open(os.path.join(d, 'img', k, f'{i:020x}.webp'), 'wb') as f:
                f.write(b'RIFF' + bytes([i]) * 2000)


def _tar(path, entries, mode='w'):
    with tarfile.open(path, mode) as t:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == 'sym':
                info.type = tarfile.SYMTYPE
                info.linkname = data
                t.addfile(info)
            else:
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))
    return path


def test_paths_resolution_order(tmp_path, monkeypatch):
    for k in ('DND5E_DATA_DIR', 'DND5E_DB_PATH', 'DATA_DIR'):
        monkeypatch.delenv(k, raising=False)
    assert paths.data_dir() == paths.REPO_DATA
    vol = tmp_path / 'vol'
    monkeypatch.setenv('DATA_DIR', str(vol))
    # the volume is used once it holds a database; installs go there regardless
    assert paths.data_dir() == paths.REPO_DATA
    assert paths.install_dir() == str(vol / 'dnd5e_data')
    _fake_data(str(vol / 'dnd5e_data'), images=0)
    assert paths.data_dir() == str(vol / 'dnd5e_data')
    assert paths.tags_path().endswith('_tags.json') and paths.img_dir().endswith('img')
    monkeypatch.setenv('DND5E_DATA_DIR', str(tmp_path / 'explicit'))
    assert paths.data_dir() == paths.install_dir() == str(tmp_path / 'explicit')
    monkeypatch.setenv('DND5E_DB_PATH', str(tmp_path / 'x.db'))
    assert paths.db_path() == str(tmp_path / 'x.db')


def test_build_then_install_round_trip(tmp_path):
    src, out, dest = str(tmp_path / 'src'), str(tmp_path / 'out'), str(tmp_path / 'dest')
    _fake_data(src, images=40)
    parts = datapack.build(src, out, limit=60_000)   # tiny limit -> several image parts
    assert os.path.basename(parts[0]) == 'dnd5e-data-9.9.9-01-core.tar.gz'
    assert len(parts) > 2 and all(os.path.getsize(p) <= 60_000 for p in parts)
    # image parts first (any order), core last: still ends complete
    total = 0
    for p in parts[1:] + parts[:1]:
        total += datapack.install(p, dest)['images']
    assert total == 80
    st = datapack.status(dest)
    assert st['installed'] and st['tokens'] == 40 and st['art'] == 40 and st['version'].startswith('5etools-src 9.9.9')
    # re-installing is harmless
    assert datapack.install(parts[1], dest)['images'] > 0
    assert not [n for n in os.listdir(dest) if n.startswith('.stage')]


@pytest.mark.parametrize('entries,msg', [
    ([('../evil.db', b'x', 'file')], 'Unexpected'),
    ([('/etc/passwd', b'x', 'file')], 'Unexpected'),
    ([('img/art/../../dnd5e.db', b'x', 'file')], 'Unexpected'),
    ([('img/art/' + 'a' * 20 + '.webp', '../../x', 'sym')], 'Unexpected'),
    ([('dnd5e.db', b'not a database at all' * 100, 'file')], 'not a 5e rules database'),
    ([('notes.txt', b'hi', 'file')], 'Unexpected'),
    # a bad database must stop the images in the same part from being written too
    ([('img/art/' + 'c' * 20 + '.webp', b'RIFF', 'file'), ('dnd5e.db', b'junk' * 500, 'file')],
     'not a 5e rules database'),
])
def test_hostile_or_broken_bundles_change_nothing(tmp_path, entries, msg):
    dest = str(tmp_path / 'dest')
    _fake_data(dest, images=1)
    before = sorted(os.listdir(dest))
    with open(os.path.join(dest, 'dnd5e.db'), 'rb') as f:
        db_before = f.read()
    with pytest.raises(datapack.BundleError, match=msg):
        datapack.install(_tar(str(tmp_path / 'p.tar'), entries), dest)
    assert sorted(os.listdir(dest)) == before
    assert sorted(os.listdir(os.path.join(dest, 'img', 'art'))) == ['0' * 20 + '.webp']
    with open(os.path.join(dest, 'dnd5e.db'), 'rb') as f:
        assert f.read() == db_before
    assert not os.path.exists(str(tmp_path / 'evil.db'))


def test_nearly_empty_database_is_refused(tmp_path):
    small = str(tmp_path / 'small')
    _fake_data(small, rows=10, images=0)
    with open(os.path.join(small, 'dnd5e.db'), 'rb') as f:
        data = f.read()
    p = _tar(str(tmp_path / 'p.tar.gz'), [('dnd5e.db', data, 'file')], mode='w:gz')
    with pytest.raises(datapack.BundleError, match='nearly empty'):
        datapack.install(p, str(tmp_path / 'dest'))


def test_oversized_and_non_tar_inputs(tmp_path, monkeypatch):
    junk = tmp_path / 'junk.tar'
    junk.write_bytes(b'definitely not a tar file' * 50)
    with pytest.raises(datapack.BundleError, match='not a data bundle'):
        datapack.install(str(junk), str(tmp_path / 'dest'))
    monkeypatch.setattr(datapack, 'IMG_MAX', 10)
    p = _tar(str(tmp_path / 'p.tar'), [('img/art/' + 'b' * 20 + '.webp', b'x' * 100, 'file')])
    with pytest.raises(datapack.BundleError, match='larger than allowed'):
        datapack.install(p, str(tmp_path / 'dest'))
