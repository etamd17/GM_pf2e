#!/usr/bin/env python3
"""Ingest the official Foundry ``cosmere-rpg`` system content into the repo.

Unpacks each LevelDB compendium pack (via the ``fvtt`` CLI) and consolidates it
into a single ``systems/cosmere/content/<pack>.json`` array, stripping volatile
Foundry bookkeeping. The committed JSON is what the app ships; this script just
regenerates it from a local Foundry install (it is NOT run in CI).

Requirements (developer machine only):
  - the Foundry CLI on PATH:  npm i -g @foundryvtt/foundryvtt-cli
  - the cosmere-rpg system installed in Foundry's data dir

Usage:
  python3 tools/ingest_cosmere.py
  COSMERE_FOUNDRY=/path/to/systems/cosmere-rpg python3 tools/ingest_cosmere.py
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

_DEFAULT_FOUNDRY = os.path.expanduser(
    '~/Library/Application Support/FoundryVTT/Data/systems/cosmere-rpg'
)

PACKS = (
    'ancestries', 'cultures', 'heroic-paths', 'actions', 'items',
    'companions-and-adversaries', 'tables', 'starter-rules',
)

# Foundry bookkeeping that adds churn/noise but no game value.
_STRIP_KEYS = ('_stats', 'ownership', 'sort', '_key')


def _strip(doc):
    if isinstance(doc, dict):
        return {k: _strip(v) for k, v in doc.items() if k not in _STRIP_KEYS}
    if isinstance(doc, list):
        return [_strip(v) for v in doc]
    return doc


def main() -> int:
    foundry = os.environ.get('COSMERE_FOUNDRY', _DEFAULT_FOUNDRY)
    packs_dir = os.path.join(foundry, 'packs')
    if not os.path.isdir(packs_dir):
        print(f"ERROR: no packs dir at {packs_dir!r}. Set COSMERE_FOUNDRY.", file=sys.stderr)
        return 2
    if not shutil.which('fvtt'):
        print("ERROR: the 'fvtt' CLI is not on PATH (npm i -g @foundryvtt/foundryvtt-cli).", file=sys.stderr)
        return 2

    os.makedirs(_OUT_DIR, exist_ok=True)
    total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for pack in PACKS:
            out = os.path.join(tmp, pack)
            r = subprocess.run(
                ['fvtt', 'package', 'unpack', pack, '--id', 'cosmere-rpg',
                 '--type', 'System', '--in', packs_dir, '--out', out],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                print(f"  {pack:28} FAILED\n{r.stderr}", file=sys.stderr)
                return 1
            docs = []
            for fn in sorted(os.listdir(out)):
                if fn.endswith('.json'):
                    with open(os.path.join(out, fn), encoding='utf-8') as f:
                        docs.append(_strip(json.load(f)))
            docs.sort(key=lambda d: (d.get('type', ''), (d.get('name') or '').lower()))
            dest = os.path.join(_OUT_DIR, f'{pack}.json')
            with open(dest, 'w', encoding='utf-8') as f:
                json.dump(docs, f, ensure_ascii=False, indent=1, sort_keys=True)
            total += len(docs)
            print(f"  {pack:28} -> {len(docs):4} docs")
    print(f"Ingested {total} documents into {os.path.relpath(_OUT_DIR, _REPO)}/")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
