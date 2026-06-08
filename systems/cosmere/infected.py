"""Infected Arts — a homebrew Invested-disease system (Ashyn, AS 10,000).

Each Infected Art pairs a DISEASE (a permanent cost — often a concrete stat
penalty or a permanent condition) with an ART (granted abilities). The cost's
mechanizable parts are encoded as STRUCTURED ``effects`` the engine applies to a
build's derived stats (the same target keys the homebrew system uses, plus a
``mode``): ``add`` sums into the stat, ``set`` overrides it (e.g. Hypercoagulable
sets Strength to 9, Chronic Pain sets max Focus to 0). Everything an engine can't
model (conditions, d-tables, narrative powers) rides as ``cost`` text + the
``abilities`` list, surfaced on the sheet exactly like the Stormlight actions.

Content lives in ``content/infected-arts.json``; this module loads it and
resolves a selection into (adds, sets, records) for :class:`CosmereBuild`.
"""
from __future__ import annotations

import json
import os

_ARTS = None


def load_arts() -> list:
    """All Infected Art records (cached)."""
    global _ARTS
    if _ARTS is None:
        path = os.path.join(os.path.dirname(__file__), 'content', 'infected-arts.json')
        try:
            with open(path, encoding='utf-8') as f:
                _ARTS = json.load(f)
        except (OSError, ValueError):
            _ARTS = []
    return _ARTS


def by_id(art_id) -> dict | None:
    return next((a for a in load_arts() if a.get('id') == art_id), None)


def resolve(selected_ids):
    """A selection of art ids -> (adds, sets, records).

      adds    {target: summed int}     -- additive stat effects (mode 'add')
      sets    {target: int}            -- override stat effects (mode 'set'; last wins)
      records [art, ...]               -- the selected art dicts, in catalog order
    """
    sel = {a for a in (selected_ids or []) if a}
    adds, sets, records = {}, {}, []
    for art in load_arts():
        if art.get('id') not in sel:
            continue
        records.append(art)
        for e in (art.get('effects') or []):
            tgt = e.get('target')
            if not tgt:
                continue
            val = int(e.get('value', 0) or 0)
            if (e.get('mode') or 'add') == 'set':
                sets[tgt] = val
            else:
                adds[tgt] = adds.get(tgt, 0) + val
    return adds, sets, records
