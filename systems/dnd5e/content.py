"""Read-only access to the D&D 5e content database (``paths.db_path()``).

One SQLite connection per thread (sqlite3 connections are not shareable across
threads, and gevent greenlets each run on the worker's thread, so in practice
production holds one). Full records are parsed from JSON on demand and kept in
a small LRU so a hot statblock or class is parsed once.

Nothing here imports Flask.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from functools import lru_cache

from systems.dnd5e import paths
from systems.dnd5e.ids import entity_id, parse_ref, reset_tag_cache

_local = threading.local()
# Bumped by reload() after a data bundle is installed: every thread's
# connection and every cache from the old database is dropped.
_generation = [0]


def _con() -> sqlite3.Connection:
    con = getattr(_local, 'con', None)
    if con is not None and getattr(_local, 'gen', None) != _generation[0]:
        try:
            con.close()
        except sqlite3.Error:
            pass
        con = None
    if con is None:
        # Read-only URI: a stray write can never touch the database.
        uri = 'file:' + paths.db_path().replace('\\', '/') + '?mode=ro'
        con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con = con
        _local.gen = _generation[0]
    return con


def available() -> bool:
    return os.path.isfile(paths.db_path())


def reload():
    """Forget everything read from the old data (after a bundle install)."""
    _generation[0] += 1
    _get_cached.cache_clear()
    _images['mtime'] = None
    reset_tag_cache()


def meta() -> dict:
    return {r['key']: r['value'] for r in _con().execute('SELECT key, value FROM meta')}


# --- images (tools/dnd5e/fetch_images.py) ---------------------------------------------

_images = {'mtime': None, 'path': None, 'data': {}}
_images_lock = threading.Lock()


def _image_index() -> dict:
    path = paths.images_index_path()
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    if _images['mtime'] != mtime or _images['path'] != path:
        with _images_lock:
            if _images['mtime'] != mtime or _images['path'] != path:
                try:
                    with open(path, encoding='utf-8') as f:
                        _images['data'] = json.load(f)
                except (OSError, ValueError):
                    _images['data'] = {}
                _images['mtime'] = mtime
                _images['path'] = path
    return _images['data']


def image_for(kind: str, rid: str, role: str = 'art') -> str | None:
    """URL of an entity's token ('token') or art ('art'), or None.

    A subclass reprinted under the other edition's class (the PHB Battle
    Master as a ``_copy`` for the XPHB Fighter) shares its original's art."""
    idx = _image_index()
    ents = (idx.get('entities') or {}).get(kind) or {}
    rid = (rid or '').lower()
    hit = ents.get(rid)
    if hit is None and kind == 'subclass':
        head = '|'.join(rid.split('|')[:2]) + '|'
        hit = next((v for k, v in ents.items() if k.startswith(head)), None)
    path = (hit or {}).get(role)
    # served by routes.dnd5e_art from paths.img_dir() (not /static: the art isn't in git)
    return '/dnd5e/art/' + path if path else None


@lru_cache(maxsize=2048)
def _get_cached(kind: str, rid: str):
    row = _con().execute('SELECT data FROM entity WHERE kind=? AND id=?', (kind, rid)).fetchone()
    return None if row is None else json.loads(row['data'])


def get(kind: str, rid: str):
    """Full record by id, or None. The returned dict is shared -- deep-copy it
    before mutating."""
    return _get_cached(kind, (rid or '').lower())


def get_by_name(kind: str, name: str, source: str | None = None):
    if source:
        return get(kind, entity_id(name, source))
    row = _con().execute(
        'SELECT id FROM entity WHERE kind=? AND name=? COLLATE NOCASE ORDER BY edition DESC LIMIT 1',
        (kind, name)).fetchone()
    return None if row is None else get(kind, row['id'])


def get_ref(kind: str, ref: str):
    """Resolve a 5etools reference string (``"shield|xphb"``, ``"Fireball"``)."""
    return get(kind, parse_ref(ref, kind))


def list_summaries(kind: str, *, sources=None, edition=None, q: str = '', limit: int = 0):
    """Lightweight (id, name, source, edition, srd) rows for a kind."""
    sql = 'SELECT id, name, source, edition, srd FROM entity WHERE kind=?'
    args: list = [kind]
    if sources:
        sql += f' AND source IN ({",".join("?" * len(sources))})'
        args += list(sources)
    if edition:
        sql += ' AND edition=?'
        args.append(edition)
    if q:
        sql += ' AND name LIKE ? COLLATE NOCASE'
        args.append(f'%{q}%')
    sql += ' ORDER BY name COLLATE NOCASE, source'
    if limit:
        sql += f' LIMIT {int(limit)}'
    return [dict(r) for r in _con().execute(sql, args)]


def all_records(kind: str, *, sources=None, edition=None):
    """Every full record of a kind (parsed). Use for small kinds only
    (classes, races, backgrounds, feats, optional features)."""
    sql = 'SELECT data FROM entity WHERE kind=?'
    args: list = [kind]
    if sources:
        sql += f' AND source IN ({",".join("?" * len(sources))})'
        args += list(sources)
    if edition:
        sql += ' AND edition=?'
        args.append(edition)
    return [json.loads(r['data']) for r in _con().execute(sql, args)]


def search_monsters(*, q='', cr_min=None, cr_max=None, types=None, sizes=None,
                    sources=None, edition=None, legendary=None, environment=None,
                    limit=200, offset=0):
    sql = 'SELECT * FROM monster_idx WHERE 1=1'
    args: list = []
    if q:
        sql += ' AND name LIKE ? COLLATE NOCASE'
        args.append(f'%{q}%')
    if cr_min is not None:
        sql += ' AND cr_num >= ?'
        args.append(float(cr_min))
    if cr_max is not None:
        sql += ' AND cr_num <= ?'
        args.append(float(cr_max))
    if types:
        sql += f' AND type IN ({",".join("?" * len(types))})'
        args += [t.lower() for t in types]
    if sizes:
        sql += ' AND (' + ' OR '.join('size LIKE ?' for _ in sizes) + ')'
        args += [f'%{s}%' for s in sizes]
    if sources:
        sql += f' AND source IN ({",".join("?" * len(sources))})'
        args += list(sources)
    if edition:
        sql += ' AND edition=?'
        args.append(edition)
    if legendary is not None:
        sql += ' AND legendary=?'
        args.append(1 if legendary else 0)
    if environment:
        sql += ' AND environment LIKE ?'
        args.append(f'%|{environment}|%')
    sql += ' ORDER BY cr_num IS NULL, cr_num, name COLLATE NOCASE LIMIT ? OFFSET ?'
    args += [int(limit), int(offset)]
    return [dict(r) for r in _con().execute(sql, args)]


def search_spells(*, q='', levels=None, school=None, class_name=None, class_source=None,
                  subclass_short=None, sources=None, edition=None, ritual=None,
                  concentration=None, limit=500):
    sql = 'SELECT DISTINCT s.* FROM spell_idx s'
    args: list = []
    if class_name:
        sql += ' JOIN spell_class c ON c.spell_id = s.id'
    sql += ' WHERE 1=1'
    if class_name:
        sql += ' AND c.class_name=?'
        args.append(class_name)
        if class_source:
            sql += ' AND c.class_source=?'
            args.append(class_source)
        if subclass_short:
            sql += " AND (c.via != 'fromSubclass' OR c.subclass_short=?)"
            args.append(subclass_short)
        else:
            sql += " AND c.via != 'fromSubclass'"
    if q:
        sql += ' AND s.name LIKE ? COLLATE NOCASE'
        args.append(f'%{q}%')
    if levels is not None:
        levels = list(levels)
        sql += f' AND s.level IN ({",".join("?" * len(levels))})'
        args += levels
    if school:
        sql += ' AND s.school=?'
        args.append(school)
    if sources:
        sql += f' AND s.source IN ({",".join("?" * len(sources))})'
        args += list(sources)
    if edition:
        sql += ' AND s.edition=?'
        args.append(edition)
    if ritual is not None:
        sql += ' AND s.ritual=?'
        args.append(1 if ritual else 0)
    if concentration is not None:
        sql += ' AND s.concentration=?'
        args.append(1 if concentration else 0)
    sql += ' ORDER BY s.level, s.name COLLATE NOCASE LIMIT ?'
    args.append(int(limit))
    return [dict(r) for r in _con().execute(sql, args)]


def search_items(*, q='', rarity=None, magic=None, base=None, type_=None, sources=None,
                 edition=None, attunement=None, limit=200, offset=0):
    sql = 'SELECT * FROM item_idx WHERE 1=1'
    args: list = []
    if q:
        sql += ' AND name LIKE ? COLLATE NOCASE'
        args.append(f'%{q}%')
    if rarity:
        sql += ' AND rarity=?'
        args.append(rarity)
    if magic is not None:
        sql += ' AND is_magic=?'
        args.append(1 if magic else 0)
    if base is not None:
        sql += ' AND is_base=?'
        args.append(1 if base else 0)
    if type_:
        sql += ' AND type=?'
        args.append(type_)
    if sources:
        sql += f' AND source IN ({",".join("?" * len(sources))})'
        args += list(sources)
    if edition:
        sql += ' AND edition=?'
        args.append(edition)
    if attunement is not None:
        sql += ' AND attunement=?'
        args.append(1 if attunement else 0)
    sql += ' ORDER BY name COLLATE NOCASE, source LIMIT ? OFFSET ?'
    args += [int(limit), int(offset)]
    return [dict(r) for r in _con().execute(sql, args)]


def monster_facets() -> dict:
    """Distinct filter values for the bestiary/encounter builder UI."""
    c = _con()
    return {
        'types': [r[0] for r in c.execute(
            "SELECT DISTINCT type FROM monster_idx WHERE type != '' ORDER BY type")],
        'sources': [r[0] for r in c.execute(
            'SELECT source FROM monster_idx GROUP BY source ORDER BY COUNT(*) DESC')],
    }


def sources() -> list:
    return [json.loads(r['data']) for r in _con().execute(
        "SELECT data FROM entity WHERE kind='source'")]


def subclasses_for(class_name: str, class_source: str):
    return [json.loads(r['data']) for r in _con().execute(
        "SELECT data FROM entity WHERE kind='subclass' AND json_extract(data,'$.className')=? "
        "AND json_extract(data,'$.classSource')=? ORDER BY name", (class_name, class_source))]
