"""Build the D&D 5e runtime database from the resolved 5etools export.

    node tools/dnd5e/export_5etools.mjs <5etools-src> dnd5e_data/resolved
    python tools/dnd5e/build_dnd5e_db.py [dnd5e_data/resolved] [dnd5e_data/dnd5e.db]

The resolved export (``dnd5e_data/resolved/*.json``) is committed and is the
diffable source of truth; ``dnd5e.db`` is derived from it and committed too so
the app needs no build step at deploy (same model as ``pf2e_database.db``).
``tests/test_dnd5e_db.py`` rebuilds it in a temp dir and checks it matches.

Layout:
  * ``entity``   -- every record of every kind, full resolved JSON in ``data``.
  * ``*_idx``    -- narrow per-kind filter tables (monsters, spells, items) so
                    the builder/search never has to parse JSON to filter.
  * ``spell_class`` -- which class/subclass lists each spell is on.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from fractions import Fraction

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from systems.dnd5e.ids import entity_id, subclass_id  # noqa: E402
from systems.dnd5e.rules.tables import xp_for_cr  # noqa: E402

KINDS = (
    'class', 'subclass', 'spell', 'monster', 'race', 'background', 'feat',
    'optionalfeature', 'item', 'condition', 'disease', 'status', 'action',
    'skill', 'sense', 'language', 'variantrule', 'itemProperty', 'itemType',
    'itemMastery', 'legendaryGroup', 'charoption', 'source',
)

# Sources first published on or after the 2024 core rules. Records that do not
# carry an explicit `edition` are classed by their source's publication date.
_EDITION_ONE_FROM = '2024-09-01'


def cr_to_num(cr) -> float | None:
    if isinstance(cr, dict):
        cr = cr.get('cr')
    if cr is None or cr == 'Unknown' or cr == '—':
        return None
    try:
        return float(Fraction(str(cr)))
    except (ValueError, ZeroDivisionError):
        return None


def cr_str(cr) -> str | None:
    if isinstance(cr, dict):
        cr = cr.get('cr')
    return None if cr is None else str(cr)


def monster_type(m) -> str:
    t = m.get('type')
    if isinstance(t, dict):
        t = t.get('type')
        if isinstance(t, dict):  # {"choose": [...]}
            t = '/'.join(t.get('choose') or [])
    return str(t or '').lower()


def first_int(v, key=None):
    if isinstance(v, list) and v:
        v = v[0]
    if isinstance(v, dict):
        v = v.get(key or 'ac', v.get('average'))
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build(resolved_dir: str, db_path: str) -> dict:
    with open(os.path.join(resolved_dir, '_meta.json'), encoding='utf-8') as f:
        meta = json.load(f)
    data = {}
    for kind in KINDS:
        with open(os.path.join(resolved_dir, f'{kind}.json'), encoding='utf-8') as f:
            data[kind] = json.load(f)

    source_date = {s['source']: (s.get('date') or '') for s in data['source']}

    def edition_of(rec) -> str:
        ed = rec.get('edition')
        if ed in ('classic', 'one'):
            return ed
        d = source_date.get(rec.get('source'), '')
        return 'one' if d and d >= _EDITION_ONE_FROM else 'classic'

    tmp = db_path + '.tmp'
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    cur = con.cursor()
    cur.executescript('''
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE entity (
            kind TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL,
            source TEXT, edition TEXT, srd INTEGER, data TEXT NOT NULL,
            PRIMARY KEY (kind, id));
        CREATE INDEX entity_kind_name ON entity(kind, name COLLATE NOCASE);
        CREATE TABLE monster_idx (
            id TEXT PRIMARY KEY, name TEXT, source TEXT, edition TEXT,
            cr TEXT, cr_num REAL, xp INTEGER, type TEXT, size TEXT,
            legendary INTEGER, environment TEXT, ac INTEGER, hp INTEGER);
        CREATE INDEX monster_idx_cr ON monster_idx(cr_num);
        CREATE TABLE spell_idx (
            id TEXT PRIMARY KEY, name TEXT, source TEXT, edition TEXT,
            level INTEGER, school TEXT, ritual INTEGER, concentration INTEGER);
        CREATE TABLE spell_class (
            spell_id TEXT, class_name TEXT, class_source TEXT,
            subclass_short TEXT, subclass_source TEXT, via TEXT);
        CREATE INDEX spell_class_cls ON spell_class(class_name, class_source);
        CREATE TABLE item_idx (
            id TEXT PRIMARY KEY, name TEXT, source TEXT, edition TEXT,
            rarity TEXT, type TEXT, attunement INTEGER, is_magic INTEGER,
            is_base INTEGER, weight REAL, value INTEGER);
    ''')

    counts = {}
    for kind in KINDS:
        rows = []
        seen = set()
        for rec in data[kind]:
            if kind == 'source':
                rid = rec['source'].lower()
                name = rec['full']
            elif kind == 'subclass':
                rid = subclass_id(rec)
                name = rec['name']
            elif kind in ('itemProperty', 'itemType'):
                # Referenced by abbreviation ("V|XPHB", "M|XPHB"), not by name.
                rid = entity_id(rec['abbreviation'], rec.get('source'))
                name = rec.get('name') or next(
                    (e.get('name') for e in rec.get('entries') or []
                     if isinstance(e, dict) and e.get('name')), rec['abbreviation'])
            else:
                name = rec.get('name')
                if not name:
                    continue
                rid = entity_id(name, rec.get('source'))
            if rid in seen:
                raise SystemExit(f'duplicate {kind} id {rid!r}')
            seen.add(rid)
            srd = 1 if (rec.get('srd') or rec.get('srd52')) else 0
            rows.append((kind, rid, name, rec.get('source'), edition_of(rec), srd,
                         json.dumps(rec, separators=(',', ':'), ensure_ascii=False)))
            if kind == 'monster':
                env = rec.get('environment') or []
                cur.execute('INSERT INTO monster_idx VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    rid, name, rec.get('source'), edition_of(rec), cr_str(rec.get('cr')),
                    cr_to_num(rec.get('cr')), xp_for_cr(cr_str(rec.get('cr'))),
                    monster_type(rec), ''.join(rec.get('size') or []),
                    1 if rec.get('legendary') else 0,
                    '|' + '|'.join(env) + '|' if isinstance(env, list) else '',
                    first_int(rec.get('ac'), 'ac'), first_int(rec.get('hp'), 'average')))
            elif kind == 'spell':
                dur = rec.get('duration') or []
                conc = any(isinstance(d, dict) and d.get('concentration') for d in dur)
                cur.execute('INSERT INTO spell_idx VALUES (?,?,?,?,?,?,?,?)', (
                    rid, name, rec.get('source'), edition_of(rec), rec.get('level'),
                    rec.get('school'), 1 if (rec.get('meta') or {}).get('ritual') else 0,
                    1 if conc else 0))
                cls = rec.get('classes') or {}
                for via in ('fromClassList', 'fromClassListVariant'):
                    for c in cls.get(via) or []:
                        cur.execute('INSERT INTO spell_class VALUES (?,?,?,?,?,?)',
                                    (rid, c.get('name'), c.get('source'), None, None, via))
                for c in cls.get('fromSubclass') or []:
                    k, s = c.get('class') or {}, c.get('subclass') or {}
                    cur.execute('INSERT INTO spell_class VALUES (?,?,?,?,?,?)', (
                        rid, k.get('name'), k.get('source'), s.get('shortName'),
                        s.get('source'), 'fromSubclass'))
            elif kind == 'item':
                rarity = rec.get('rarity') or 'none'
                cur.execute('INSERT INTO item_idx VALUES (?,?,?,?,?,?,?,?,?,?,?)', (
                    rid, name, rec.get('source'), edition_of(rec), rarity,
                    (rec.get('type') or '').split('|')[0],
                    1 if rec.get('reqAttune') else 0,
                    0 if rarity in ('none', 'unknown') else 1,
                    1 if rec.get('_isBaseItem') else 0,
                    rec.get('weight'), rec.get('value')))
        cur.executemany('INSERT INTO entity VALUES (?,?,?,?,?,?,?)', rows)
        counts[kind] = len(rows)

    cur.executemany('INSERT INTO meta VALUES (?,?)', [
        ('5etools_version', meta.get('version', '')),
        ('counts', json.dumps(counts, sort_keys=True)),
    ])
    con.commit()
    cur.execute('VACUUM')
    con.close()
    os.replace(tmp, db_path)
    return counts


def main(argv):
    resolved = argv[1] if len(argv) > 1 else os.path.join(ROOT, 'dnd5e_data', 'resolved')
    out = argv[2] if len(argv) > 2 else os.path.join(ROOT, 'dnd5e_data', 'dnd5e.db')
    counts = build(resolved, out)
    for k, v in counts.items():
        print(f'{k:16} {v}')
    print('->', out, f'{os.path.getsize(out) / 1e6:.1f} MB')


if __name__ == '__main__':
    main(sys.argv)
