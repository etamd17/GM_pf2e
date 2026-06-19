"""Talent index + prerequisite checker (Stormlight Ch.4).

Loads heroic-path talents from BOTH the base ``heroic-paths`` pack and the
``handbook-heroic-paths`` expansion. The base pack alone omits whole specialties
(Tracker, Artifabrian, Shardbearer, ...), so checking only it left every
handbook talent ungated.

**Prerequisite source of truth: the talent-tree NODE graph.** Upstream Foundry
stores the authoritative prerequisites on each path tree's nodes; the per-item
``system.prerequisites`` is frequently missing (null) and, in a few cases, wrong
(Strategize/Turning Point ship each other's prereqs; Composed points at a
non-existent "Predict"; some prereq labels are stale, e.g. "Valiant Stand" for
what is really Resolute Stand). So we resolve each talent's prereqs from its
tree node (matched by path + slug), and fall back to the item's own
prerequisites only where no node carries any (e.g. Feral Connection). This both
corrects the wrong/missing data and stays self-maintaining across re-ingest.

Goal/connection prerequisites are narrative and can't be auto-checked, so they
are ignored. Checks are GUIDED -- callers warn, they don't block.
"""
from __future__ import annotations

import re

from systems.cosmere import load_pack, SKILL_NAMES

_PACKS = ('heroic-paths', 'handbook-heroic-paths')

# A talent whose ``system.path`` is set to a SPECIALTY name is corrupt data:
# Customary Garb (Officer) ships path="champion". "champion" is a Leader
# specialty, never a heroic path, so such a record belongs to its parent path.
PATH_FIX = {'champion': 'leader'}

_BY_ID = None        # talent _id -> {id, name, path, slug, prereqs (normalized list)}
_TREE = None         # (path, slug) -> raw node prerequisites dict (authoritative)


def norm_path(p) -> str:
    p = (p or '').lower()
    return PATH_FIX.get(p, p)


_SPECIALTY = None


def talent_specialty() -> dict:
    """{talent _id: specialty display name}. Each heroic path's talents are
    divided across talent-tree nodes (rulebook Ch.4: 3 specialties per path); a
    talent's specialty is the tree whose node references it. Base trees named
    '<Path> Talents' map to '' (core path talents, no specialty). Lets the
    builder GROUP a path's talent picker by specialty instead of one flat list."""
    global _SPECIALTY
    if _SPECIALTY is not None:
        return _SPECIALTY
    out = {}
    docs = []
    for pack in _PACKS:
        docs.extend(load_pack(pack))
    for d in docs:
        if d.get('type') != 'talent_tree':
            continue
        name = (d.get('name') or '').strip()
        spec = '' if name.lower().endswith('talents') else name
        for node in (d.get('system', {}).get('nodes', {}) or {}).values():
            iid = _uuid_id(node.get('uuid'))
            if iid and iid not in out:
                out[iid] = spec
    _SPECIALTY = out
    return out


def _uuid_id(uuid):
    m = re.search(r'Item\.([A-Za-z0-9]+)$', uuid or '')
    return m.group(1) if m else None


def _normalize(pr) -> list:
    """A Foundry prerequisites dict (item- or node-shaped) -> a uniform list of
    groups. Tree nodes key ``talents`` by slug (a dict); item docs use a list --
    accept either. Skill prereqs carry the rank in ``rank`` (the sibling
    ``value`` field is a non-rank flag and must NOT be read as the rank)."""
    out = []
    if not isinstance(pr, dict):
        return out
    for g in pr.values():
        if not isinstance(g, dict):
            continue
        ty = g.get('type')
        if ty == 'skill':
            out.append({'type': 'skill', 'skill': g.get('skill'), 'rank': g.get('rank') or 0})
        elif ty == 'attribute':
            out.append({'type': 'attribute', 'attribute': g.get('attribute'), 'value': g.get('value') or 0})
        elif ty == 'talent':
            raw = g.get('talents') or {}
            vals = raw.values() if isinstance(raw, dict) else raw
            tals = [{'id': t.get('id'), 'label': t.get('label', '?'), 'uuid': t.get('uuid')}
                    for t in vals if isinstance(t, dict)]
            if tals:
                out.append({'type': 'talent', 'mode': g.get('mode') or 'any-of', 'talents': tals})
        elif ty == 'level':
            out.append({'type': 'level', 'level': g.get('level')})
        elif ty in ('goal', 'connection'):
            out.append({'type': ty})
    return out


def _build():
    global _BY_ID, _TREE
    if _BY_ID is not None:
        return
    _BY_ID, _TREE = {}, {}
    docs = []
    for pack in _PACKS:
        docs.extend(load_pack(pack))
    by_iid = {d.get('_id'): d for d in docs if d.get('type') == 'talent' and d.get('_id')}

    # Tree-node prerequisites, keyed (path, slug). Disambiguate the path per node
    # via node.uuid -> item -> path (a slug like "composed" recurs across paths).
    for d in docs:
        if d.get('type') != 'talent_tree':
            continue
        for node in (d.get('system', {}).get('nodes', {}) or {}).values():
            slug = node.get('talentId')
            pr = node.get('prerequisites')
            if not slug or not _normalize(pr):
                continue
            item = by_iid.get(_uuid_id(node.get('uuid')))
            path = norm_path((item or {}).get('system', {}).get('path'))
            _TREE.setdefault((path, slug), pr)

    # Talents (base first; first _id wins, matching the picker's merge order).
    # The tree node is authoritative for WHICH talents gate a node (it fixes the
    # swapped/stale/null item prereqs). But a few nodes omit a skill/attribute
    # floor the item still carries (e.g. Know Your Moment keeps Deduction 2 only
    # on the item), so when a node is present we keep its talent gates and
    # BACKFILL any skill/attribute dimension the node doesn't mention from the
    # item's own prereqs. With no node at all, the item's prereqs stand
    # (e.g. Feral Connection).
    for d in docs:
        if d.get('type') != 'talent':
            continue
        iid = d.get('_id')
        if not iid or iid in _BY_ID:
            continue
        s = d.get('system', {}) or {}
        slug, path = s.get('id'), norm_path(s.get('path'))
        item_groups = _normalize(s.get('prerequisites'))
        node_pr = _TREE.get((path, slug))
        if node_pr is None:
            groups = item_groups
        else:
            groups = _normalize(node_pr)
            cov_sk = {g['skill'] for g in groups if g['type'] == 'skill'}
            cov_at = {g['attribute'] for g in groups if g['type'] == 'attribute'}
            for g in item_groups:
                if g['type'] == 'skill' and g['skill'] not in cov_sk:
                    groups.append(g)
                elif g['type'] == 'attribute' and g['attribute'] not in cov_at:
                    groups.append(g)
        _BY_ID[iid] = {'id': iid, 'name': d.get('name', ''), 'path': path,
                       'slug': slug, 'prereqs': groups}


def get(talent_id):
    _build()
    return _BY_ID.get(talent_id)


def resolved_prereqs(talent_id) -> dict:
    """Resolved prerequisites as a Foundry-shaped dict (talents as a list), for
    the builder's prereq-summary display. Empty dict if unknown."""
    rec = get(talent_id)
    if not rec:
        return {}
    return {str(i): g for i, g in enumerate(rec['prereqs'])}


def unmet(talent_id, taken_names, skills, attributes) -> list:
    """Unmet-prerequisite descriptions for a talent (empty = met or unknown)."""
    rec = get(talent_id)
    if not rec:
        return []
    taken = {str(n).lower() for n in taken_names}
    out = []
    for g in rec['prereqs']:
        ty = g['type']
        if ty == 'skill':
            need = g.get('rank') or 0
            if skills.get(g.get('skill'), 0) < need:
                out.append('%s rank %s' % (SKILL_NAMES.get(g.get('skill'), g.get('skill')), need))
        elif ty == 'talent':
            opts = g.get('talents') or []
            if opts and not any((o.get('label') or '').lower() in taken for o in opts):
                out.append(' or '.join(o.get('label', '?') for o in opts))
        elif ty == 'attribute':
            need = g.get('value') or 0
            if attributes.get(g.get('attribute'), 0) < need:
                out.append('%s %s' % (g.get('attribute'), need))
        # 'goal' / 'connection' / 'level' prerequisites are narrative -- skipped.
    return out
