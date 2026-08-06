#!/usr/bin/env python3
"""Ingest Cosmere Foundry MODULE content into the repo, alongside the base
``cosmere-rpg`` system content (see ingest_cosmere.py).

Two outputs under systems/cosmere/content/:
  - module-adversaries.json : every adversary from the module Actor packs AND
    the actors embedded in module Adventure packs (e.g. the 91 in Stonewalkers),
    deduped by name -> fills the bestiary.
  - handbook-<pack>.json : the Stormlight Handbook's canon builder content
    (ancestries / cultures / heroic-paths / radiant-paths / surges / actions /
    items) as real Foundry data -> for the walkthrough builder (replaces the
    earlier PDF-mined Radiant approximations).

Dev-only: needs the local Foundry modules + the fvtt CLI
(npm i -g @foundryvtt/foundryvtt-cli). The committed JSON is what ships; this is
NOT run in CI.

Usage:
  python3 tools/ingest_cosmere_modules.py
  COSMERE_MODULES=/path/to/FoundryVTT/Data/modules python3 tools/ingest_cosmere_modules.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OUT_DIR = os.path.join(_REPO, 'systems', 'cosmere', 'content')
_DEFAULT_MODS = os.path.expanduser('~/Library/Application Support/FoundryVTT/Data/modules')
_STRIP_KEYS = ('_stats', 'ownership', 'sort', '_key')

# (module_id, pack_name, kind). kind 'actors' = a pack of Actor docs;
# 'adventure' = an Adventure pack whose docs embed an `actors` array.
ADVERSARY_SOURCES = (
    ('cosmere-rpg-stormlight-worldguide', 'stormlight-world-guide-adversaries-and-bestiary', 'actors'),
    ('cosmere-rpg-stormlight-chasmfiend-compendium', 'adversaries', 'actors'),
    ('cosmere-rpg-stormlight-handbook', 'animal-companions', 'actors'),
    ('cosmere-rpg-stormlight-chasmfiend-compendium', 'adventures', 'adventure'),
    ('cosmere-rpg-stormlight-first-step', 'the-first-step', 'adventure'),
    ('cosmere-rpg-stormlight-stonewalkers', 'stonewalkers', 'adventure'),
)
HANDBOOK_ID = 'cosmere-rpg-stormlight-handbook'
HANDBOOK_PACKS = ('ancestries', 'cultures', 'heroic-paths', 'radiant-paths',
                  'surges', 'actions', 'items')


def _strip(doc):
    if isinstance(doc, dict):
        return {k: _strip(v) for k, v in doc.items() if k not in _STRIP_KEYS}
    if isinstance(doc, list):
        return [_strip(v) for v in doc]
    return doc


def _unpack(mods_dir, module_id, pack, tmp):
    out = os.path.join(tmp, '%s-%s' % (module_id, pack))
    os.makedirs(out, exist_ok=True)
    r = subprocess.run(
        ['fvtt', 'package', 'unpack', pack, '--id', module_id, '--type', 'Module',
         '--in', os.path.join(mods_dir, module_id, 'packs'), '--out', out],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError('unpack %s/%s failed:\n%s' % (module_id, pack, r.stderr))
    docs = []
    for fn in sorted(os.listdir(out)):
        if fn.endswith('.json'):
            with open(os.path.join(out, fn), encoding='utf-8') as f:
                docs.append(json.load(f))
    return docs


def _write(name, docs):
    docs.sort(key=lambda d: (str(d.get('type', '')), (d.get('name') or '').lower()))
    dest = os.path.join(_OUT_DIR, '%s.json' % name)
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump([_strip(d) for d in docs], f, ensure_ascii=False, indent=1, sort_keys=True)
    print('  %-34s -> %4d docs' % (name, len(docs)))


def main() -> int:
    mods_dir = os.environ.get('COSMERE_MODULES', _DEFAULT_MODS)
    if not os.path.isdir(mods_dir):
        print('ERROR: no modules dir at %r. Set COSMERE_MODULES.' % mods_dir, file=sys.stderr)
        return 2
    if not shutil.which('fvtt'):
        print("ERROR: the 'fvtt' CLI is not on PATH (npm i -g @foundryvtt/foundryvtt-cli).", file=sys.stderr)
        return 2
    os.makedirs(_OUT_DIR, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        # --- adversaries (bestiary fill), deduped by name across all sources ---
        seen, advs = set(), []
        for module_id, pack, kind in ADVERSARY_SOURCES:
            docs = _unpack(mods_dir, module_id, pack, tmp)
            if kind == 'adventure':
                actors = [a for d in docs for a in (d.get('actors') or [])]
            else:
                actors = docs
            for a in actors:
                if not isinstance(a, dict) or a.get('type') != 'adversary':
                    continue            # skip Foundry folder ("Actor") docs
                key = (a.get('name') or '').strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                advs.append(a)
        _write('module-adversaries', advs)

        # --- handbook canon builder content (for the walkthrough builder) ---
        for pack in HANDBOOK_PACKS:
            _write('handbook-%s' % pack, _unpack(mods_dir, HANDBOOK_ID, pack, tmp))
    print('Done -> %s/' % os.path.relpath(_OUT_DIR, _REPO))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
