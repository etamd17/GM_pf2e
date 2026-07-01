"""Radiant talent trees + surge powers, sourced from the ingested Stormlight
Handbook Foundry data (``handbook-radiant-paths`` + ``handbook-surges``) -- the
real, canon dataset the base ``cosmere-rpg`` system never shipped. This
SUPERSEDES the earlier rulebook-PDF-mined approximations (which carried
two-column OCR artifacts, cross-order duplication and truncated effects).

The builder's Radiant talent picker reads three tables:
  * ``SURGE_TALENTS`` -- {surge code -> [{name, prereq, effect}]} (10 surge trees)
  * ``ORDER_TALENTS`` -- {order key (plural) -> [{name, prereq, effect}]} (9 orders)
  * ``SURGE_POWERS``  -- {surge code -> {name, cost, investiture, effect}}  (the
    10 castable surges -- the real ``power`` docs the handbook adds)

``prereq`` keeps the exact shape the builder template already understands:
  {'ideal': 1}        an entry talent (available once the First Ideal is sworn)
  {'talent': 'Name'}  requires another talent
  {'text': '...'}     a level / skill / other requirement

radiant.py's ``RADIANT_ORDERS`` / ``SURGES`` stay the canonical order + surge
tables (used by actor.py / roster / sheet); this module only enriches the
builder picker by MAPPING the handbook talents onto those canonical order keys
(plural) and 3-letter surge codes.
"""
from __future__ import annotations

import re

from systems.cosmere import load_pack, SKILL_NAMES
from systems.cosmere.radiant import SURGES, RADIANT_ORDERS
from systems.cosmere.enrich import enrich


def _uuid_id(uuid):
    m = re.search(r'Item\.([A-Za-z0-9]+)$', uuid or '')
    return m.group(1) if m else None


_IDEAL_NUM = {'first': 1, 'second': 2, 'third': 3, 'fourth': 4, 'fifth': 5}


def _ideal_num(slug):
    m = re.match(r'(first|second|third|fourth|fifth)-ideal', (slug or '').lower())
    return _IDEAL_NUM[m.group(1)] if m else None


_RADIANT_TREE_GRAPHS = None


def radiant_tree_graphs() -> dict:
    """Positioned Radiant talent-tree graphs for the builder's visual tree, keyed
    by order-singular slug (the order's "<Order> Talents" + "<Spren> Bond" trees)
    and by surge code (the 10 surge trees). Ideal NODES are excluded (the Ideal
    track owns them); a node gated by the Nth Ideal carries idealReq=N instead.
    Nodes use iid='radiant:<name>' to match the builder's radiant-talent convention."""
    global _RADIANT_TREE_GRAPHS
    if _RADIANT_TREE_GRAPHS is not None:
        return _RADIANT_TREE_GRAPHS
    name_to_surge = {v['name'].lower(): code for code, v in SURGES.items()}
    seen = {}
    for pack in ('handbook-radiant-paths', 'handbook-surges'):
        docs = load_pack(pack)
        by_iid = {d['_id']: d for d in docs if d.get('type') == 'talent' and d.get('_id')}
        for d in docs:
            if d.get('type') != 'talent_tree':
                continue
            name = (d.get('name') or '').strip()
            base = name.lower().replace(' talents', '').strip()
            # Surge trees come from the surges pack (canonical); skip their
            # duplicates in the radiant-paths pack so they aren't double-listed.
            if pack == 'handbook-radiant-paths' and base in name_to_surge:
                continue
            sysd = d.get('system', {}) or {}
            nlist, slug2id, order_grp = [], {}, None
            for nid, n in (sysd.get('nodes', {}) or {}).items():
                if n.get('type') != 'talent':
                    continue
                item = by_iid.get(_uuid_id(n.get('uuid')))
                if not item:
                    continue
                slug = n.get('talentId')
                if _ideal_num(slug):            # the Ideal track owns Ideal nodes
                    continue
                p = ((item.get('system') or {}).get('path') or '').lower()
                if p:
                    order_grp = order_grp or p
                pos = n.get('position') or {}
                deps, skillreq, attrreq, levelreq, idealreq = [], [], [], 0, 0
                for g in (n.get('prerequisites') or {}).values():
                    ty = g.get('type')
                    if ty == 'talent':
                        for ds, info in (g.get('talents') or {}).items():
                            ino = _ideal_num(ds)
                            if ino:
                                idealreq = max(idealreq, ino)
                            else:
                                deps.append({'slug': ds, 'name': (info or {}).get('label', ds)})
                    elif ty == 'skill':
                        skillreq.append({'skill': g.get('skill'), 'rank': g.get('rank') or 0})
                    elif ty == 'attribute':
                        attrreq.append({'attr': g.get('attribute'), 'value': g.get('value') or 0})
                    elif ty == 'level':
                        levelreq = max(levelreq, int(g.get('level') or 0))
                tname = item.get('name', '')
                nlist.append({'id': nid, 'slug': slug, 'name': tname, 'iid': 'radiant:' + tname,
                              'x': pos.get('x', 0), 'y': pos.get('y', 0), 'deps': deps, 'edges': [],
                              'skillReq': skillreq, 'attrReq': attrreq,
                              'levelReq': levelreq, 'idealReq': idealreq})
                if slug:
                    slug2id[slug] = nid
            if not nlist:
                continue
            grp = order_grp or name_to_surge.get(base)   # order slug, else surge code
            if not grp:
                continue
            for nd in nlist:
                nd['edges'] = [slug2id[dp['slug']] for dp in nd['deps'] if dp['slug'] in slug2id]
            vb = sysd.get('viewBounds') or {}
            tree = {'name': name,
                    'vb': {'x': vb.get('x', 0), 'y': vb.get('y', 0),
                           'w': vb.get('width', 300), 'h': vb.get('height', 300)},
                    'nodes': nlist}
            key = (grp, name)
            if key not in seen or len(nlist) > len(seen[key]['nodes']):
                seen[key] = tree
    out = {}
    for (grp, _n), tree in seen.items():
        out.setdefault(grp, []).append(tree)
    _RADIANT_TREE_GRAPHS = out
    return out


def _first_sentence(s: str) -> str:
    s = ' '.join(s.split())
    m = re.match(r'(.+?[.!?])(\s|$)', s)
    return (m.group(1) if m else s)[:200]


def _effect(value) -> str:
    """A one-line effect summary. Handbook talent/power descriptions lead with an
    "Activation:" glyph block; the readable one-liner is the italic flavour
    sentence (``<em>...</em>``). Fall back to stripping the lead-in if absent."""
    raw = value.get('value', '') if isinstance(value, dict) else (value or '')
    if not isinstance(raw, str):
        return ''
    m = re.search(r'<em[^>]*>(.*?)</em>', raw, re.S)
    src = m.group(1) if m else raw
    txt = re.sub(r'<[^>]+>', '', src).replace('\xa0', ' ').strip()
    if not m:
        # No flavour <em>: drop the "Radiant Orders: ... Activation: <glyph>" lead-in.
        txt = re.sub(r'^\s*(Radiant Orders:.*?)?Activation:\s*[^A-Za-z]*', '', txt).strip()
    # Clean any Foundry enrichers that survive into the flavour sentence
    # ([[lookup @actor.name]], @UUID links, [[damage ...]]) so talent summaries
    # on the sheet/builder read as prose. (Today's content is already clean here;
    # this makes it a guaranteed invariant -- see tests/test_cosmere_enrich.py.)
    return enrich(_first_sentence(txt))


def _prereq_from_node(node) -> dict:
    """Reduce a talent-tree node's Foundry prerequisites to the {ideal|talent|text}
    shape the builder understands. A node gated only by the order's "First Ideal"
    talent (or with no prerequisites) is an ENTRY talent -> {'ideal': 1}."""
    groups = (node or {}).get('prerequisites', {}) or {}
    talent_parts, other_parts, ideal_entry = [], [], False
    for g in groups.values():
        if not isinstance(g, dict):
            continue
        t = g.get('type')
        if t == 'talent':
            # Tree nodes key ``talents`` by slug (a dict); a talent doc's own
            # ``system.prerequisites`` carries them as a list. Accept either.
            raw_talents = g.get('talents') or {}
            tvals = raw_talents.values() if isinstance(raw_talents, dict) else raw_talents
            labels = [x.get('label', '?') for x in tvals]
            # A "First Ideal (...)" prerequisite means "once you swear the First
            # Ideal" -- the builder models that as an ideal entry, not a talent.
            non_ideal = [l for l in labels if not l.lower().startswith('first ideal')]
            if len(non_ideal) < len(labels):
                ideal_entry = True
            if non_ideal:
                talent_parts.append(' or '.join(non_ideal))
        elif t == 'level':
            other_parts.append('Level %s+' % g.get('level'))
        elif t == 'skill':
            other_parts.append('%s %s+' % (SKILL_NAMES.get(g.get('skill'), g.get('skill')), g.get('rank')))
        elif t == 'connection':
            other_parts.append(g.get('description') or 'a connection')
    parts = talent_parts + other_parts
    if not parts:
        return {'ideal': 1}
    if len(parts) == 1 and len(talent_parts) == 1 and not ideal_entry:
        return {'talent': talent_parts[0]}
    return {'text': ', '.join(parts)}


# Rulebook corrections where the handbook source data ITSELF disagrees with the
# Stormlight core rulebook (both the talent doc and its tree node are wrong), so
# resolution alone can't fix it. Keyed by talent slug -> the corrected prereq.
_SURGE_PREREQ_FIX = {
    # Unleashed Entropy (Division): the handbook ships "Inescapable Spark or Gout
    # of Flame"; the rulebook (SL:15816) requires "Spark Sending talent or Gout
    # of Flame talent" -- Spark Sending is the shallower, intended gate.
    'unleashed-entropy': {'talent': 'Spark Sending or Gout of Flame'},
}


def _build_surge_talents() -> dict:
    """{surge code -> [{name, prereq, effect}]} from the 10 surge talent trees."""
    name_to_code = {v['name'].lower(): code for code, v in SURGES.items()}
    docs = load_pack('handbook-surges')
    by_slug = {d['system'].get('id'): d for d in docs if d.get('type') == 'talent'}
    out = {}
    for tree in (d for d in docs if d.get('type') == 'talent_tree'):
        code = name_to_code.get((tree.get('name') or '').replace(' Talents', '').strip().lower())
        if not code:
            continue
        lst, seen = [], set()
        for node in tree.get('system', {}).get('nodes', {}).values():
            slug = node.get('talentId')
            tal = by_slug.get(slug)
            if not tal or tal.get('name') in seen:
                continue
            seen.add(tal['name'])
            prereq = _SURGE_PREREQ_FIX.get(slug) or _prereq_from_node(node)
            lst.append({'name': tal['name'], 'prereq': prereq,
                        'effect': _effect(tal.get('system', {}).get('description'))})
        out[code] = lst
    return out


def _build_order_talents() -> dict:
    """{order key (plural) -> [{name, prereq, effect}]} from the spren-bond +
    order talent trees, deduped by name (collapsing the (Canon)/(Nale)/
    (Enlightened) variants)."""
    docs = load_pack('handbook-radiant-paths')
    tals = [d for d in docs if d.get('type') == 'talent']
    trees = [d for d in docs if d.get('type') == 'talent_tree']
    slug_paths = {}
    for d in tals:
        slug_paths.setdefault(d['system'].get('id'), set()).add(d['system'].get('path'))
    out = {}
    for plural, meta in RADIANT_ORDERS.items():
        singular = plural[:-1]                       # windrunners -> windrunner
        spren = (meta.get('spren') or '').lower()
        order_slugs = {d['system'].get('id') for d in tals if d['system'].get('path') == singular}
        uniq_slugs = {s for s in order_slugs if slug_paths.get(s) == {singular}}
        # Resolve prereqs ONLY from this order's own trees: the generic bond
        # talents (deepened-bond / invested / wound-regeneration) reuse a single
        # slug across all nine orders, so a global node index would cross-
        # contaminate (e.g. a Windrunner talent inheriting a Dustbringer prereq).
        local = {}
        for tree in trees:
            nm = (tree.get('name') or '').lower()
            owned = (nm == f"{spren} bond" or nm.startswith(singular)
                     or any(node.get('talentId') in uniq_slugs
                            for node in tree.get('system', {}).get('nodes', {}).values()))
            if not owned:
                continue
            for node in tree.get('system', {}).get('nodes', {}).values():
                s = node.get('talentId')
                if s in order_slugs and s not in local:
                    local[s] = node
        lst, seen = [], set()
        for d in tals:
            if d['system'].get('path') != singular or d.get('name') in seen:
                continue
            seen.add(d['name'])
            # Prefer this order's own tree-node prereq. When no owning tree node
            # claims the talent -- the generic bond talents (Invested / Deepened
            # Bond / Wound Regeneration) can live only in a cross-order tree this
            # order doesn't own, e.g. Lightweaver/Windrunner Wound Regeneration --
            # fall back to the talent doc's own (path-specific) prerequisites
            # before defaulting to an ideal entry. _prereq_from_node reads
            # ``prerequisites`` off whichever it's handed and yields {'ideal': 1}
            # when empty, preserving the old default. (Verified against the
            # rulebook: e.g. Lightweaver Invested IS a First-Ideal entry talent,
            # SL:11568, so its {'ideal': 1} is correct -- not a doc-prereq.)
            node = local.get(d['system'].get('id'))
            lst.append({'name': d['name'],
                        'prereq': _prereq_from_node(node or d.get('system')),
                        'effect': _effect(d.get('system', {}).get('description'))})
        out[plural] = lst
    return out


def _build_surge_powers() -> dict:
    """{surge code -> {name, cost, investiture, effect}} -- the 10 castable surges
    (the handbook's real ``power`` docs)."""
    out = {}
    for d in load_pack('handbook-surges'):
        if d.get('type') != 'power':
            continue
        s = d.get('system', {})
        code = s.get('id')
        if not code:
            continue
        act = s.get('activation', {}) or {}
        cost = (act.get('cost') or {}).get('value')
        inv = None
        for c in (act.get('consume') or []):
            if isinstance(c, dict) and c.get('resource') == 'inv':
                inv = (c.get('value') or {}).get('max')
        out[code] = {'name': d.get('name') or SURGES.get(code, {}).get('name', code),
                     'cost': cost, 'investiture': inv,
                     'effect': _effect(s.get('description'))}
    return out


def _safe(fn) -> dict:
    try:
        return fn()
    except Exception:            # the builder must still render if content is missing
        return {}


SURGE_TALENTS = _safe(_build_surge_talents)
ORDER_TALENTS = _safe(_build_order_talents)
SURGE_POWERS = _safe(_build_surge_powers)


_TALENT_GATES = None


def talent_gates() -> dict:
    """{talent-name (lower) -> {'ideal': N, 'level': M}} for radiant talents, from
    the tree graphs' per-node idealReq / levelReq. Lets the build engine enforce
    Ideal/level gates at save the same way the visual tree locks them client-side."""
    global _TALENT_GATES
    if _TALENT_GATES is not None:
        return _TALENT_GATES
    gates = {}
    try:
        for trees in radiant_tree_graphs().values():
            for tr in trees:
                for n in tr.get('nodes', []):
                    key = (n.get('name') or '').lower()
                    if not key:
                        continue
                    g = gates.setdefault(key, {'ideal': 0, 'level': 0})
                    g['ideal'] = max(g['ideal'], int(n.get('idealReq') or 0))
                    g['level'] = max(g['level'], int(n.get('levelReq') or 0))
    except Exception:
        pass
    _TALENT_GATES = gates
    return gates
