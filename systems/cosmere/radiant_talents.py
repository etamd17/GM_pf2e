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
    return _first_sentence(txt)


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
            labels = [x.get('label', '?') for x in (g.get('talents') or {}).values()]
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
            tal = by_slug.get(node.get('talentId'))
            if not tal or tal.get('name') in seen:
                continue
            seen.add(tal['name'])
            lst.append({'name': tal['name'], 'prereq': _prereq_from_node(node),
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
            node = local.get(d['system'].get('id'))
            lst.append({'name': d['name'],
                        'prereq': _prereq_from_node(node) if node else {'ideal': 1},
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
