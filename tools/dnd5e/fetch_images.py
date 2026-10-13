"""Download 5etools art, shrink it, and write the image index.

    python tools/dnd5e/fetch_images.py                 # everything (incremental)
    python tools/dnd5e/fetch_images.py --kinds monster --limit 50
    python tools/dnd5e/fetch_images.py --index-only    # rebuild the index from files on disk

Sources are the paths the 5etools data itself names:

* monster tokens -- ``bestiary/tokens/<source>/<name>.webp`` for every monster
  with ``hasToken`` (5etools' own ``Renderer.generic.getTokenUrl``).
* art -- the first internal image in each entity's fluff record
  (``fluff-*.json``), following a fluff ``_copy`` when the record has none.

They are fetched from the 5etools image mirror on GitHub, re-encoded as WebP
(tokens 128 px, art at most 600 px on the long side), and written under
``dnd5e_data/img/{tokens,art}/<hash>.webp`` (not in git -- see systems/dnd5e/paths.py). File names are a hash of the
source path: the originals differ only by case in places, which collides on
Windows and macOS. ``dnd5e_data/images.json`` maps entity id -> files.

Re-running only fetches what is missing. A path that 404s is remembered in
the index (``missing``) so it is not retried every run; pass ``--retry-missing``
to try those again.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RAW = os.path.join(ROOT, 'dnd5e_data', 'raw')
RESOLVED = os.path.join(ROOT, 'dnd5e_data', 'resolved')
DATA = os.environ.get('DND5E_DATA_DIR') or os.path.join(ROOT, 'dnd5e_data')
OUT = os.path.join(DATA, 'img')
INDEX = os.path.join(DATA, 'images.json')
BASE_URL = 'https://raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/'

TOKEN_PX = 128
ART_PX = 600
ART_QUALITY = 72
TOKEN_QUALITY = 80

FLUFF_KINDS = {'monsterFluff': 'monster', 'itemFluff': 'item', 'raceFluff': 'race',
               'backgroundFluff': 'background', 'classFluff': 'class', 'subclassFluff': 'subclass',
               'featFluff': 'feat', 'spellFluff': 'spell'}


def _ascii(name: str) -> str:
    """5etools' String.prototype.toAscii + Parser.nameToTokenName."""
    s = unicodedata.normalize('NFD', name)
    s = ''.join(ch for ch in s if not (0x300 <= ord(ch) <= 0x36f))
    return s.replace('Æ', 'AE').replace('æ', 'ae').replace('"', '')


def _eid(name, source):
    return f'{name}|{source or ""}'.lower()


def _sub_id(rec):
    return '|'.join((rec.get('name') or '', rec.get('source') or '',
                     rec.get('className') or '', rec.get('classSource') or '')).lower()


def _file_for(path: str) -> str:
    return hashlib.sha1(path.encode('utf-8')).hexdigest()[:20] + '.webp'


# --- what to fetch ------------------------------------------------------------------

def _load_resolved(kind):
    p = os.path.join(RESOLVED, f'{kind}.json')
    if not os.path.isfile(p):
        return []
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    return d if isinstance(d, list) else next(iter(d.values()))


def token_jobs() -> list:
    jobs = []
    for m in _load_resolved('monster'):
        if not m.get('hasToken'):
            continue
        tok = m.get('token') or {'name': m['name'], 'source': m['source']}
        path = f'bestiary/tokens/{tok["source"]}/{_ascii(tok["name"])}.webp'
        jobs.append(('monster', _eid(m['name'], m['source']), 'token', path))
    return jobs


def _first_image(rec):
    for im in rec.get('images') or []:
        href = (im or {}).get('href') or {}
        if href.get('type') == 'internal' and href.get('path'):
            return href['path']
    return None


def art_jobs(kinds=None) -> list:
    """(kind, entity id, 'art', path) from the fluff files."""
    recs = {}
    for f in glob.glob(os.path.join(RAW, '**', 'fluff-*.json'), recursive=True):
        with open(f, encoding='utf-8') as fh:
            d = json.load(fh)
        for prop, kind in FLUFF_KINDS.items():
            for r in d.get(prop) or []:
                if r.get('name') and r.get('source'):
                    recs[(prop, r['name'].lower(), r['source'].lower())] = r

    def image_of(prop, r, depth=0):
        p = _first_image(r)
        if p or depth > 4:
            return p
        cp = r.get('_copy')
        if isinstance(cp, dict) and cp.get('name'):
            tgt = recs.get((prop, cp['name'].lower(), (cp.get('source') or '').lower()))
            if tgt is not None and tgt is not r:
                return image_of(prop, tgt, depth + 1)
        return None

    jobs = []
    for (prop, _n, _s), r in recs.items():
        kind = FLUFF_KINDS[prop]
        if kinds and kind not in kinds:
            continue
        path = image_of(prop, r)
        if not path:
            continue
        eid = _sub_id(r) if kind == 'subclass' else _eid(r['name'], r['source'])
        jobs.append((kind, eid, 'art', path))
    return jobs


# --- fetch + shrink -----------------------------------------------------------------

def _shrink(data: bytes, role: str) -> bytes:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.mode not in ('RGB', 'RGBA'):
        im = im.convert('RGBA' if 'A' in im.getbands() or im.mode == 'P' else 'RGB')
    limit = TOKEN_PX if role == 'token' else ART_PX
    im.thumbnail((limit, limit), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, 'WEBP', quality=TOKEN_QUALITY if role == 'token' else ART_QUALITY, method=6)
    return out.getvalue()


def _fetch(path: str, tries=3) -> bytes | None:
    url = BASE_URL + urllib.parse.quote(path)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'gm-pf2e-dnd5e-image-sync'})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1 + 2 * i)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(1 + 2 * i)
    raise RuntimeError(f'could not fetch {path}')


def _work(role, path):
    dest_dir = os.path.join(OUT, 'tokens' if role == 'token' else 'art')
    dest = os.path.join(dest_dir, _file_for(path))
    if os.path.isfile(dest):
        return path, 'have'
    data = _fetch(path)
    if data is None:
        return path, 'missing'
    small = _shrink(data, role)
    os.makedirs(dest_dir, exist_ok=True)
    tmp = dest + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(small)
    os.replace(tmp, dest)
    return path, 'fetched'


def build_index(jobs, missing: set) -> dict:
    idx = {'version': 1, 'entities': {}, 'missing': sorted(missing)}
    for kind, eid, role, path in jobs:
        sub = 'tokens' if role == 'token' else 'art'
        name = _file_for(path)
        if not os.path.isfile(os.path.join(OUT, sub, name)):
            continue
        ent = idx['entities'].setdefault(kind, {}).setdefault(eid, {})
        ent.setdefault(role, f'{sub}/{name}')
    return idx


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--kinds', nargs='*', help='monster item race background class subclass feat spell')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--workers', type=int, default=16)
    ap.add_argument('--index-only', action='store_true')
    ap.add_argument('--retry-missing', action='store_true')
    a = ap.parse_args(argv)

    kinds = set(a.kinds or [])
    jobs = (token_jobs() if not kinds or 'monster' in kinds else []) + art_jobs(kinds or None)
    old_missing = set()
    if os.path.isfile(INDEX):
        with open(INDEX, encoding='utf-8') as f:
            old_missing = set(json.load(f).get('missing') or [])
    missing = set() if a.retry_missing else set(old_missing)

    if not a.index_only:
        todo, seen = [], set()
        for kind, eid, role, path in jobs:
            if (role, path) in seen or (path in missing):
                continue
            seen.add((role, path))
            todo.append((role, path))
        if a.limit:
            todo = todo[:a.limit]
        print(f'{len(jobs)} entity images, {len(todo)} distinct files to check', flush=True)
        counts = {'have': 0, 'fetched': 0, 'missing': 0, 'error': 0}
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(_work, role, path): path for role, path in todo}
            for n, fut in enumerate(as_completed(futs), 1):
                try:
                    path, status = fut.result()
                except Exception as e:                       # noqa: BLE001 -- keep going, report at the end
                    counts['error'] += 1
                    print('ERROR', futs[fut], e, file=sys.stderr, flush=True)
                    continue
                counts[status] += 1
                if status == 'missing':
                    missing.add(path)
                if n % 250 == 0:
                    print(n, counts, flush=True)
        print('done', counts, flush=True)

    # the index always covers every kind on disk, not just this run's
    all_jobs = jobs if not kinds else token_jobs() + art_jobs()
    idx = build_index(all_jobs, missing)
    tmp = INDEX + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(idx, f, separators=(',', ':'), sort_keys=True)
    os.replace(tmp, INDEX)
    total = sum(len(v) for v in idx['entities'].values())
    print(f'index: {total} entities with images, {len(idx["missing"])} missing paths')


if __name__ == '__main__':
    main()
