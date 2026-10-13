"""Build and install D&D 5e data bundles.

The rules data is not in git (see paths.py). A bundle is a set of tar parts,
each under the app's upload limit, made on a machine that has the data:

    python tools/dnd5e/bundle.py            -> dist/dnd5e-data-<version>-NN.tar[.gz]

and installed through the site-admin page ``/dnd5e/admin/data``, one part at a
time. Parts are independent: the core part replaces the database and indexes
(atomically, after validating the new database); image parts only add
content-hash-named files, so re-uploading or uploading in any order is safe.

Installation trusts nothing in the archive: only regular files with names on
the allow-list below are accepted, sizes are capped, and every write is to a
path built here (never a path taken from the archive).
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tarfile
import uuid

CORE_FILES = {'dnd5e.db': 400_000_000, '_tags.json': 5_000_000,
              'images.json': 50_000_000, 'VERSION': 10_000}
IMG_RE = re.compile(r'^img/(tokens|art)/([0-9a-f]{20}\.webp)$')
IMG_MAX = 5_000_000
PART_LIMIT = 48 * 1024 * 1024          # app MAX_CONTENT_LENGTH is 64 MB


class BundleError(ValueError):
    pass


def member_kind(name: str):
    """'core' / 'img' for an allowed archive name, else None."""
    if name in CORE_FILES:
        return 'core'
    if IMG_RE.match(name):
        return 'img'
    return None


# --- building (dev machine) ---------------------------------------------------------

def build(data_dir: str, out_dir: str, *, limit: int = PART_LIMIT) -> list:
    """Write the parts; returns their paths. Part 01 is the core (gzipped:
    the database compresses well); the rest are image batches."""
    if not os.path.isfile(os.path.join(data_dir, 'dnd5e.db')):
        raise BundleError(f'no dnd5e.db in {data_dir}')
    version = 'data'
    vp = os.path.join(data_dir, 'VERSION')
    if os.path.isfile(vp):
        with open(vp, encoding='utf-8') as f:
            m = re.search(r'(\d+\.\d+\.\d+)', f.read())
            version = m.group(1) if m else version
    os.makedirs(out_dir, exist_ok=True)
    tags = os.path.join(data_dir, '_tags.json')
    if not os.path.isfile(tags):
        tags = os.path.join(data_dir, 'resolved', '_tags.json')
    out = []
    core = os.path.join(out_dir, f'dnd5e-data-{version}-01-core.tar.gz')
    with tarfile.open(core, 'w:gz', format=tarfile.USTAR_FORMAT) as t:
        for name, src in (('dnd5e.db', os.path.join(data_dir, 'dnd5e.db')), ('_tags.json', tags),
                          ('images.json', os.path.join(data_dir, 'images.json')), ('VERSION', vp)):
            if os.path.isfile(src):
                t.add(src, arcname=name)
    if os.path.getsize(core) > limit:
        raise BundleError('the core part is over the upload limit')
    out.append(core)
    files = []
    for kind in ('tokens', 'art'):
        d = os.path.join(data_dir, 'img', kind)
        if os.path.isdir(d):
            files += [(f'img/{kind}/{n}', os.path.join(d, n)) for n in sorted(os.listdir(d))
                      if IMG_RE.match(f'img/{kind}/{n}')]
    # USTAR, not the default PAX: PAX adds ~1 KB of headers per member, which
    # for thousands of small tokens pushed a part 5 MB over the limit.
    # Exact size: 512-byte header + data rounded up to 512, plus the end
    # blocks rounded to tarfile's 10 KB record.
    def blocks(n):
        return 512 + (n + 511) // 512 * 512

    batch, size, n = [], 0, 2
    for arc, src in files + [(None, None)]:
        sz = blocks(os.path.getsize(src)) if src else 0
        if batch and (src is None or size + sz + 20480 > limit):
            p = os.path.join(out_dir, f'dnd5e-data-{version}-{n:02d}-img.tar')
            with tarfile.open(p, 'w', format=tarfile.USTAR_FORMAT) as t:
                for a, s in batch:
                    t.add(s, arcname=a)
            out.append(p)
            batch, size, n = [], 0, n + 1
        if src:
            batch.append((arc, src))
            size += sz
    for p in out:
        if os.path.getsize(p) > limit:
            raise BundleError(f'{os.path.basename(p)} came out over the upload limit')
    return out


# --- installing (server) ------------------------------------------------------------

def install(archive_path: str, dest: str) -> dict:
    """Validate and install one bundle part into ``dest``. Returns a summary.
    Raises BundleError (nothing changed) on anything unexpected in the archive."""
    try:
        t = tarfile.open(archive_path, 'r:*')
    except (tarfile.TarError, OSError):
        raise BundleError('That is not a data bundle (expected a .tar or .tar.gz part).') from None
    with t:
        members = t.getmembers()
        if not members:
            raise BundleError('That bundle part is empty.')
        for m in members:
            kind = member_kind(m.name)
            if kind is None or not m.isreg():
                raise BundleError(f'Unexpected entry in the bundle: {m.name[:80]!r}')
            cap = CORE_FILES.get(m.name, IMG_MAX)
            if m.size > cap:
                raise BundleError(f'{m.name} is larger than allowed.')
        os.makedirs(dest, exist_ok=True)
        stage = os.path.join(dest, f'.stage-{uuid.uuid4().hex}')
        os.makedirs(stage)
        try:
            core, imgs = {}, 0
            # 1. stage + validate the core files before anything is written to dest
            for m in members:
                if member_kind(m.name) != 'core':
                    continue
                src = t.extractfile(m)
                if src is None:
                    raise BundleError(f'Unreadable entry: {m.name}')
                tmp = os.path.join(stage, m.name)
                with open(tmp, 'wb') as f:
                    shutil.copyfileobj(src, f)
                core[m.name] = tmp
            if 'dnd5e.db' in core:
                _check_db(core['dnd5e.db'])
            # 2. images: additive, content-hash names
            for m in members:
                if member_kind(m.name) != 'img':
                    continue
                src = t.extractfile(m)
                if src is None:
                    raise BundleError(f'Unreadable entry: {m.name}')
                sub, fname = IMG_RE.match(m.name).groups()
                d = os.path.join(dest, 'img', sub)
                os.makedirs(d, exist_ok=True)
                final = os.path.join(d, fname)
                tmp = final + '.tmp'
                with open(tmp, 'wb') as f:
                    shutil.copyfileobj(src, f)
                os.replace(tmp, final)
                imgs += 1
            # 3. database first, indexes after: a reader between the two sees a
            # new database with an old (still valid) image index, never the reverse
            for name in ('dnd5e.db', '_tags.json', 'VERSION', 'images.json'):
                if name in core:
                    os.replace(core[name], os.path.join(dest, name))
            return {'core': sorted(core), 'images': imgs}
        finally:
            shutil.rmtree(stage, ignore_errors=True)


def _check_db(path: str):
    try:
        con = sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro', uri=True)
        try:
            n = con.execute('SELECT COUNT(*) FROM entity').fetchone()[0]
            con.execute('SELECT key, value FROM meta').fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        raise BundleError('The database in that bundle is not a 5e rules database.') from None
    if n < 1000:
        raise BundleError('The database in that bundle is nearly empty; refusing to install it.')


def status(dest: str) -> dict:
    out = {'dir': dest, 'installed': os.path.isfile(os.path.join(dest, 'dnd5e.db')),
           'version': None, 'tokens': 0, 'art': 0, 'db_mb': None}
    vp = os.path.join(dest, 'VERSION')
    if os.path.isfile(vp):
        with open(vp, encoding='utf-8') as f:
            out['version'] = f.readline().strip()
    if out['installed']:
        out['db_mb'] = round(os.path.getsize(os.path.join(dest, 'dnd5e.db')) / 1e6, 1)
    for k in ('tokens', 'art'):
        d = os.path.join(dest, 'img', k)
        out[k] = len([n for n in os.listdir(d) if n.endswith('.webp')]) if os.path.isdir(d) else 0
    return out
