"""Talent index + prerequisite checker (Stormlight Ch.4).

Loads the Foundry heroic-path talents and checks a build against each selected
talent's ``prerequisites`` (skill ranks, prerequisite talents, attribute
floors). Goal/connection prerequisites are narrative and can't be auto-checked,
so they're ignored. Checks are GUIDED — callers warn, they don't block.
"""
from __future__ import annotations

from systems.cosmere import load_pack, SKILL_NAMES

_BY_ID = None


def _build():
    global _BY_ID
    if _BY_ID is not None:
        return
    _BY_ID = {}
    for d in load_pack('heroic-paths'):
        if d.get('type') != 'talent':
            continue
        s = d.get('system', {})
        _BY_ID[d.get('_id')] = {
            'id': d.get('_id'), 'name': d.get('name', ''),
            'path': (s.get('path') or '').lower(),
            'prereqs': s.get('prerequisites') or {},
        }


def get(talent_id):
    _build()
    return _BY_ID.get(talent_id)


def unmet(talent_id, taken_names, skills, attributes) -> list:
    """Unmet-prerequisite descriptions for a talent (empty = met or unknown)."""
    rec = get(talent_id)
    if not rec:
        return []
    taken = {str(n).lower() for n in taken_names}
    out = []
    for grp in rec['prereqs'].values():
        if not isinstance(grp, dict):
            continue
        ty = grp.get('type')
        if ty == 'skill':
            need = grp.get('rank') or 0
            if skills.get(grp.get('skill'), 0) < need:
                out.append('%s rank %s' % (SKILL_NAMES.get(grp.get('skill'), grp.get('skill')), need))
        elif ty == 'talent':
            opts = grp.get('talents') or []
            if opts and not any(o.get('label', '').lower() in taken for o in opts):
                out.append(' or '.join(o.get('label', '?') for o in opts))
        elif ty == 'attribute':
            need = grp.get('value') or 0
            if attributes.get(grp.get('attribute'), 0) < need:
                out.append('%s %s' % (grp.get('attribute'), need))
        # 'goal' / 'connection' prerequisites are narrative — skipped.
    return out
