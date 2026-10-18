"""Render 5etools ``entries`` trees and ``{@tag ...}`` markup to safe HTML.

Every piece of text from the data is HTML-escaped; the only markup emitted is
built here. Output conventions (styled in static/css/dnd5e.css, wired by
static/js/dnd5e.js via event delegation -- never inline handlers):

* ``<span class="d5-roll" data-roll="1d20+4">``     clickable dice
* ``<a class="d5-ref" data-kind="spell" data-id="fireball|xphb">`` compendium popover
* ``<em class="d5-lbl">Hit:</em>``                   attack/save labels

Unknown tags degrade to their display text; unknown block types render their
nested ``entries`` if any. Nothing raises on odd data -- a bad record must not
take down a statblock.
"""
from __future__ import annotations

import re
from html import escape

from systems.dnd5e.ids import default_source, entity_id
from systems.dnd5e.rules.tables import ABILITY_NAMES, SCHOOL_NAMES

__all__ = ['render_entries', 'render_text', 'strip_tags', 'render_entry']


def _e(s) -> str:
    return escape(str(s), quote=True)


# --- inline tag parsing ------------------------------------------------------

def _split_tags(text: str):
    """Yield ('text', str) / ('tag', name, body) pieces, honouring nested braces."""
    i, n = 0, len(text)
    buf = []
    while i < n:
        if text.startswith('{@', i):
            depth, j = 0, i
            while j < n:
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:  # unbalanced: treat the rest as text
                buf.append(text[i:])
                break
            if buf:
                yield ('text', ''.join(buf))
                buf = []
            inner = text[i + 2:j]
            m = re.match(r'(\w+)\s?(.*)', inner, re.S)
            if m:
                yield ('tag', m.group(1), m.group(2))
            i = j + 1
        else:
            buf.append(text[i])
            i += 1
    if buf:
        yield ('text', ''.join(buf))


def _parts(body: str) -> list:
    """Split a tag body on top-level ``|`` (pipes inside nested tags survive)."""
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        if ch == '|' and depth == 0:
            out.append(''.join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append(''.join(cur))
    return out


_ATK = {'m': 'Melee', 'r': 'Ranged', 'g': 'Magical', 'a': 'Area',
        'mw': 'Melee Weapon', 'rw': 'Ranged Weapon', 'ms': 'Melee Spell', 'rs': 'Ranged Spell',
        'mw,rw': 'Melee or Ranged Weapon', 'ms,rs': 'Melee or Ranged Spell', 'm,r': 'Melee or Ranged'}

# tag -> data kind for reference links (only kinds present in our database).
_REF_KIND = {
    'spell': 'spell', 'item': 'item', 'creature': 'monster', 'condition': 'condition',
    'status': 'status', 'disease': 'disease', 'variantrule': 'variantrule',
    'action': 'action', 'skill': 'skill', 'sense': 'sense', 'feat': 'feat',
    'race': 'race', 'background': 'background', 'class': 'class',
    'optfeature': 'optionalfeature', 'itemMastery': 'itemMastery',
    'language': 'language', 'legroup': 'legendaryGroup',
}
# Tags whose display text sits somewhere other than parts[2].
_DISPLAY_AT = {'classFeature': 5, 'subclassFeature': 7, 'deity': 3, 'quickref': 4,
               'card': 3, 'filter': 0, 'book': 0, 'adventure': 0, '5etools': 0,
               'link': 0, 'footnote': 0, 'tip': 0, 'color': 0, 'highlight': 0}
_FORMAT = {'b': 'strong', 'bold': 'strong', 'i': 'em', 'italic': 'em', 'u': 'u',
           'underline': 'u', 's': 's', 'strike': 's', 'sup': 'sup', 'sub': 'sub',
           'code': 'code', 'kbd': 'kbd', 'note': 'em', 'style': 'span', 'font': 'span'}
_DICE_RE = re.compile(r'^[\dd+\-*/×x ()kh]+$', re.I)


def _roll(expr: str, label: str, display: str | None = None) -> str:
    expr = expr.replace('×', '*').replace(' ', '')
    shown = display if display is not None else expr
    if not _DICE_RE.match(expr):
        return _e(shown)
    return (f'<span class="d5-roll" data-roll="{_e(expr)}" data-label="{_e(label)}">'
            f'{_e(shown)}</span>')


def _tag(name: str, body: str, ctx: dict) -> str:
    p = _parts(body)
    first = p[0]
    if name in _FORMAT:
        return f'<{_FORMAT[name]}>{render_text(first, ctx)}</{_FORMAT[name]}>'
    if name == 'h':
        return '<em class="d5-lbl">Hit:</em> '
    if name == 'm':
        return '<em class="d5-lbl">Miss:</em> '
    if name == 'hom':
        return '<em class="d5-lbl">Hit or Miss:</em> '
    if name in ('atk', 'atkr'):
        kind = _ATK.get(first.strip().lower(), first)
        suffix = 'Attack Roll' if name == 'atkr' else 'Attack'
        return f'<em class="d5-lbl">{_e(kind)} {suffix}:</em>'
    if name == 'actSave':
        ab = ABILITY_NAMES.get(first.strip().lower(), first.title())
        return f'<em class="d5-lbl">{_e(ab)} Saving Throw:</em>'
    if name in ('actSaveFail', 'actSaveSuccess', 'actSaveSuccessOrFail', 'actTrigger',
                'actResponse', 'actSaveFailBy'):
        lbl = {'actSaveFail': 'Failure:', 'actSaveSuccess': 'Success:',
               'actSaveSuccessOrFail': 'Failure or Success:', 'actTrigger': 'Trigger:',
               'actResponse': 'Response:'}.get(name)
        if name == 'actSaveFail' and first.strip().isdigit():
            lbl = f'Failure by {first.strip()} or More:'
        if name == 'actSaveFailBy':
            lbl = f'Failure by {first.strip()} or More:'
        return f'<em class="d5-lbl">{_e(lbl)}</em>'
    if name in ('hit', 'd20'):
        try:
            v = int(first.strip())
        except ValueError:
            return _e(first)
        shown = p[1] if len(p) > 1 and p[1] else f'{v:+d}'
        return _roll(f'1d20{v:+d}', ctx.get('label', ''), shown)
    if name in ('damage', 'dice', 'autodice'):
        shown = p[1] if len(p) > 1 and p[1] else first
        return _roll(first, ctx.get('label', ''), shown)
    if name in ('scaledamage', 'scaledice'):
        shown = p[3] if len(p) > 3 and p[3] else (p[2] if len(p) > 2 else first)
        return _roll(shown, ctx.get('label', ''), shown)
    if name == 'dc':
        return f'DC {_e(p[1] if len(p) > 1 and p[1] else first)}'
    if name == 'chance':
        return _e(p[1] if len(p) > 1 and p[1] else f'{first} percent')
    if name == 'recharge':
        v = first.strip()
        if not v or v == '6':
            return '(Recharge 6)'
        return f'(Recharge {_e(v)}–6)'
    if name == 'hitYourSpellAttack':
        return _e(first or 'your spell attack modifier')
    if name == 'dcYourSpellSave':
        return _e(first or 'your spell save DC')
    if name == 'skillCheck':
        return _e(p[2] if len(p) > 2 and p[2] else first.replace('_', ' ').title())
    if name == 'link':
        href = p[1] if len(p) > 1 else ''
        if href.startswith(('http://', 'https://')):
            return (f'<a href="{_e(href)}" target="_blank" rel="noopener noreferrer">'
                    f'{render_text(first, ctx)}</a>')
        return render_text(first, ctx)

    at = _DISPLAY_AT.get(name, 2)
    display = p[at] if len(p) > at and p[at] else first
    kind = _REF_KIND.get(name)
    if kind:
        src = p[1] if len(p) > 1 and p[1] else default_source(name)
        rid = entity_id(first, src)
        return (f'<a class="d5-ref" data-kind="{kind}" data-id="{_e(rid)}">'
                f'{render_text(display, ctx)}</a>')
    return render_text(display, ctx)


def render_text(text, ctx: dict | None = None) -> str:
    """Render one string that may contain ``{@tags}``."""
    if text is None:
        return ''
    ctx = ctx or {}
    out = []
    for piece in _split_tags(str(text)):
        if piece[0] == 'text':
            out.append(_e(piece[1]))
        else:
            try:
                out.append(_tag(piece[1], piece[2], ctx))
            except Exception:  # never let one odd tag break a block
                out.append(_e(_parts(piece[2])[0]))
    return ''.join(out)


def strip_tags(text) -> str:
    """Plain text for search / tooltips / SSE payloads."""
    if text is None:
        return ''
    out = []
    for piece in _split_tags(str(text)):
        if piece[0] == 'text':
            out.append(piece[1])
            continue
        name, body = piece[1], piece[2]
        p = _parts(body)
        if name in ('h', 'm', 'hom'):
            out.append({'h': 'Hit: ', 'm': 'Miss: ', 'hom': 'Hit or Miss: '}[name])
        elif name in ('atk', 'atkr'):
            out.append(_ATK.get(p[0].strip().lower(), p[0]) +
                       (' Attack Roll:' if name == 'atkr' else ' Attack:'))
        elif name == 'hit':
            try:
                out.append(f'{int(p[0]):+d}')
            except ValueError:
                out.append(p[0])
        elif name == 'dc':
            out.append(f'DC {p[0]}')
        elif name == 'recharge':
            out.append('(Recharge 6)' if not p[0].strip() or p[0].strip() == '6'
                       else f'(Recharge {p[0].strip()}-6)')
        else:
            at = _DISPLAY_AT.get(name, 2)
            if name in ('damage', 'dice', 'hit', 'd20'):
                at = 1
            if name in ('scaledamage', 'scaledice'):
                at = 2
            disp = p[at] if len(p) > at and p[at] else p[0]
            out.append(strip_tags(disp))
    return ''.join(out)


# --- block rendering ---------------------------------------------------------

def _name_prefix(entry: dict, ctx: dict) -> str:
    name = entry.get('name')
    if not name:
        return ''
    return f'<strong class="d5-entry-name">{render_text(name, ctx)}.</strong> '


def _render_list(entries, ctx, depth) -> str:
    return ''.join(render_entry(e, ctx, depth) for e in entries or [])


def _cell(c, ctx) -> str:
    if isinstance(c, dict):
        if c.get('type') == 'cell':
            r = c.get('roll') or {}
            if 'exact' in r:
                return _e(r['exact'])
            if 'min' in r:
                return f'{_e(r["min"])}–{_e(r.get("max", ""))}'
            return render_entry(c.get('entry', ''), ctx, 9)
        return render_entry(c, ctx, 9)
    return render_text(c, ctx)


def _spellcasting(entry, ctx, depth) -> str:
    """Monster spellcasting block (both the 2014 slots form and 2024 daily form)."""
    out = [f'<div class="d5-spellcasting">{_name_prefix(entry, ctx)}']
    out.append(''.join(f'<p>{render_text(h, ctx)}</p>' if isinstance(h, str)
                       else render_entry(h, ctx, depth + 1)
                       for h in entry.get('headerEntries') or []))
    rows = []
    for key, label in (('constant', 'Constant'), ('will', 'At will')):
        if entry.get(key):
            rows.append((label, entry[key]))
    for k, v in sorted((entry.get('daily') or {}).items()):
        n = k.rstrip('e')
        rows.append((f'{n}/day' + (' each' if k.endswith('e') else ''), v))
    for k, v in sorted((entry.get('rest') or {}).items()):
        rows.append((f'{k.rstrip("e")}/rest' + (' each' if k.endswith('e') else ''), v))
    for lvl, info in sorted((entry.get('spells') or {}).items(), key=lambda kv: int(kv[0])):
        lvl_i = int(lvl)
        if lvl_i == 0:
            label = 'Cantrips (at will)'
        else:
            slots = info.get('slots')
            label = f'Level {lvl_i}' + (f' ({slots} slot{"s" if slots != 1 else ""})' if slots else '')
        rows.append((label, info.get('spells') or []))
    if rows:
        out.append('<ul class="d5-spell-rows">')
        for label, spells in rows:
            out.append(f'<li><span class="d5-lbl">{_e(label)}:</span> '
                       + ', '.join(render_text(s, ctx) if isinstance(s, str)
                                   else render_text(s.get('entry', ''), ctx) for s in spells)
                       + '</li>')
        out.append('</ul>')
    out.append(''.join(f'<p>{render_text(h, ctx)}</p>' if isinstance(h, str)
                       else render_entry(h, ctx, depth + 1)
                       for h in entry.get('footerEntries') or []))
    out.append('</div>')
    return ''.join(out)


def render_entry(entry, ctx: dict | None = None, depth: int = 0) -> str:
    ctx = ctx or {}
    if entry is None:
        return ''
    if isinstance(entry, (str, int, float)):
        return f'<p>{render_text(entry, ctx)}</p>'
    if isinstance(entry, list):
        return _render_list(entry, ctx, depth)
    if not isinstance(entry, dict):
        return ''
    t = entry.get('type', 'entries')
    try:
        if t in ('entries', 'section', 'inset', 'insetReadaloud', 'variant', 'variantInner',
                 'variantSub', 'quote', 'wrapper', 'inline', 'inlineBlock', 'options'):
            body = entry.get('entries') or []
            if entry.get('wrapped') is not None and not body:
                body = [entry['wrapped']]
            name = entry.get('name')
            cls = {'inset': 'd5-inset', 'insetReadaloud': 'd5-inset d5-readaloud',
                   'variant': 'd5-inset', 'quote': 'd5-quote'}.get(t, 'd5-entries')
            if t in ('inline', 'inlineBlock'):
                return ''.join(render_text(e, ctx) if isinstance(e, str)
                               else render_entry(e, ctx, depth) for e in body)
            if name and body and isinstance(body[0], str) and t not in ('inset', 'variant', 'section'):
                head = (f'<p>{_name_prefix(entry, ctx)}{render_text(body[0], ctx)}</p>')
                rest = _render_list(body[1:], ctx, depth + 1)
                return f'<div class="{cls}">{head}{rest}</div>'
            title = ''
            if name:
                tag = 'h4' if depth == 0 else 'h5'
                if t == 'variant':
                    name = f'Variant: {name}'
                title = f'<{tag} class="d5-entry-title">{render_text(name, ctx)}</{tag}>'
            by = ''
            if t == 'quote' and entry.get('by'):
                by = f'<p class="d5-quote-by">— {render_text(entry["by"], ctx)}</p>'
            return f'<div class="{cls}">{title}{_render_list(body, ctx, depth + 1)}{by}</div>'
        if t == 'list':
            items = []
            for it in entry.get('items') or []:
                if isinstance(it, dict) and it.get('type') in ('item', 'itemSub', 'itemSpell'):
                    inner = it.get('entries') or ([it['entry']] if 'entry' in it else [])
                    head = f'<strong>{render_text(it.get("name", ""), ctx)}</strong> ' if it.get('name') else ''
                    txt = ''.join(render_text(x, ctx) if isinstance(x, str)
                                  else render_entry(x, ctx, depth + 1) for x in inner)
                    items.append(f'<li>{head}{txt}</li>')
                elif isinstance(it, str):
                    items.append(f'<li>{render_text(it, ctx)}</li>')
                else:
                    items.append(f'<li>{render_entry(it, ctx, depth + 1)}</li>')
            return f'<ul class="d5-list">{"".join(items)}</ul>'
        if t in ('item', 'itemSub', 'itemSpell'):
            inner = entry.get('entries') or ([entry['entry']] if 'entry' in entry else [])
            head = f'<strong>{render_text(entry.get("name", ""), ctx)}</strong> ' if entry.get('name') else ''
            return f'<p>{head}' + ''.join(render_text(x, ctx) if isinstance(x, str)
                                          else render_entry(x, ctx, depth + 1) for x in inner) + '</p>'
        if t in ('table', 'tableGroup'):
            if t == 'tableGroup':
                return ''.join(render_entry(x, ctx, depth) for x in entry.get('tables') or [])
            cap = f'<caption>{render_text(entry["caption"], ctx)}</caption>' if entry.get('caption') else ''
            head = ''
            if entry.get('colLabels'):
                head = '<thead><tr>' + ''.join(f'<th>{render_text(c, ctx)}</th>'
                                               for c in entry['colLabels']) + '</tr></thead>'
            rows = []
            for row in entry.get('rows') or []:
                if isinstance(row, dict) and row.get('type') == 'row':
                    row = row.get('row') or []
                rows.append('<tr>' + ''.join(f'<td>{_cell(c, ctx)}</td>' for c in row) + '</tr>')
            foot = _render_list(entry.get('footnotes'), ctx, depth + 1)
            return (f'<div class="d5-table-wrap"><table class="d5-table">{cap}{head}'
                    f'<tbody>{"".join(rows)}</tbody></table>{foot}</div>')
        if t == 'spellcasting':
            return _spellcasting(entry, ctx, depth)
        if t == 'dice':
            parts = []
            for d in entry.get('toRoll') or []:
                s = f'{d.get("number", 1)}d{d.get("faces", 6)}'
                if d.get('modifier'):
                    s += f'{int(d["modifier"]):+d}'
                parts.append(s)
            expr = '+'.join(parts)
            return _roll(expr, ctx.get('label', '')) if expr else ''
        if t == 'abilityDc':
            abs_ = ' + '.join(f'your {ABILITY_NAMES.get(a, a)} modifier' for a in entry.get('attributes') or [])
            return (f'<p class="d5-formula"><strong>{render_text(entry.get("name", ""), ctx)} save DC</strong>'
                    f' = 8 + your proficiency bonus + {_e(abs_)}</p>')
        if t == 'abilityAttackMod':
            abs_ = ' + '.join(f'your {ABILITY_NAMES.get(a, a)} modifier' for a in entry.get('attributes') or [])
            return (f'<p class="d5-formula"><strong>{render_text(entry.get("name", ""), ctx)} attack modifier</strong>'
                    f' = your proficiency bonus + {_e(abs_)}</p>')
        if t == 'abilityGeneric':
            return f'<p class="d5-formula">{render_text(entry.get("text", ""), ctx)}</p>'
        if t == 'bonus':
            return _e(f'{int(entry.get("value", 0)):+d}')
        if t == 'bonusSpeed':
            return _e(f'{int(entry.get("value", 0)):+d} ft.')
        if t == 'hr':
            return '<hr class="d5-hr">'
        if t in ('image', 'gallery'):
            title = entry.get('title')
            return f'<p class="d5-muted">[Image{": " + _e(title) if title else ""}]</p>'
        if t == 'link':
            return render_text(entry.get('text', ''), ctx)
        if t == 'statblock':
            kind = _REF_KIND.get(entry.get('tag', ''), entry.get('prop', ''))
            src = entry.get('source') or default_source(entry.get('tag', ''))
            rid = entity_id(entry.get('name', ''), src)
            return (f'<p><a class="d5-ref" data-kind="{_e(kind)}" data-id="{_e(rid)}">'
                    f'{_e(entry.get("displayName") or entry.get("name", ""))}</a></p>')
        if t in ('refClassFeature', 'refSubclassFeature', 'refOptionalfeature'):
            ref = entry.get('classFeature') or entry.get('subclassFeature') or entry.get('optionalfeature') or ''
            name = ref.split('|')[0]
            if t == 'refOptionalfeature':
                return (f'<p><a class="d5-ref" data-kind="optionalfeature" '
                        f'data-id="{_e(entity_id(name, (ref.split("|") + ["", ""])[1] or "PHB"))}">'
                        f'{_e(name)}</a></p>')
            return f'<p class="d5-feature-ref">{_e(name)}</p>'
        if t == 'cell':
            return _cell(entry, ctx)
        # unknown block: render what it contains
        if entry.get('entries'):
            return _render_list(entry['entries'], ctx, depth + 1)
        if entry.get('entry'):
            return render_entry(entry['entry'], ctx, depth + 1)
        return ''
    except Exception:
        return '<p class="d5-muted">[unrenderable entry]</p>'


def render_entries(entries, ctx: dict | None = None) -> str:
    return render_entry(entries or [], ctx or {}, 0)


def school_name(code: str) -> str:
    return SCHOOL_NAMES.get(code, code or '')
