"""Per-campaign Cosmere homebrew: a GM-authored content shelf that the builder
and engine treat exactly like canon content.

A homebrew *entry* is a small uniform record::

    {id, type, name, effects: [{target, value}], notes, ...type fields}

* ``type`` is one of :data:`TYPES` (talent / item / ancestry / culture /
  heroic_path / radiant_path / surge).
* ``effects`` are STRUCTURED stat bonuses the engine applies automatically; each
  ``target`` is a key from :func:`effect_targets` (a defense / resource /
  attribute / skill) and ``value`` an integer.
* ``notes`` is free-form text for rules the engine can't model (shown, not applied).

The store (one ``homebrew.json`` per campaign) is ``{type: [entry, ...]}``. The
app loads it and hands it to :class:`~systems.cosmere.build.CosmereBuild` and the
builder context; everything else (sheet, tracker, validation) then sees homebrew
through the same code paths as canon, because the bonuses are baked into the
Foundry actor doc and the picker data carries a ``homebrew`` flag.
"""
from __future__ import annotations

import re

from systems.cosmere import SKILL_NAMES, SKILL_ATTR

_ATTR_CODES = ('str', 'spd', 'int', 'wil', 'awa', 'pre')

TYPES = ('talent', 'item', 'ancestry', 'culture', 'heroic_path', 'radiant_path', 'surge')

TYPE_LABELS = {
    'talent': 'Talent', 'item': 'Item', 'ancestry': 'Ancestry', 'culture': 'Culture',
    'heroic_path': 'Heroic Path', 'radiant_path': 'Radiant Order', 'surge': 'Surge',
}

# Structured-effect targets the engine knows how to apply (key -> label), grouped
# for the editor's dropdown. Skills are appended from the canonical skill table.
_STAT_TARGETS = (
    ('def:phy', 'Physical defense'), ('def:cog', 'Cognitive defense'),
    ('def:spi', 'Spiritual defense'), ('deflect', 'Deflect'),
    ('health', 'Health (max)'), ('focus', 'Focus (max)'), ('investiture', 'Investiture (max)'),
)
_ATTR_TARGETS = (
    ('attr:str', 'Strength'), ('attr:spd', 'Speed'), ('attr:int', 'Intellect'),
    ('attr:wil', 'Willpower'), ('attr:awa', 'Awareness'), ('attr:pre', 'Presence'),
)


def effect_targets() -> list:
    """Every selectable structured-effect target: {key, label, group}."""
    out = [{'key': k, 'label': l, 'group': 'Statistics'} for k, l in _STAT_TARGETS]
    out += [{'key': k, 'label': l, 'group': 'Attributes'} for k, l in _ATTR_TARGETS]
    out += [{'key': 'skill:%s' % c, 'label': SKILL_NAMES.get(c, c), 'group': 'Skills'}
            for c in SKILL_ATTR]
    return out


_VALID_TARGETS = {t['key'] for t in effect_targets()}


def slugify(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def normalize(entry, type=None) -> dict:
    """Coerce a raw entry (from the editor or storage) into the uniform schema.
    Unknown effect targets and zero-value rows are dropped."""
    e = dict(entry or {})
    t = (type or e.get('type') or 'talent')
    if t not in TYPES:
        t = 'talent'
    name = (e.get('name') or '').strip() or 'Unnamed'
    eid = e.get('id') or 'hb:' + slugify(name) + '-' + slugify(t)
    effects = []
    for row in (e.get('effects') or []):
        if not isinstance(row, dict):
            continue
        tgt, val = row.get('target'), _int(row.get('value'))
        if tgt in _VALID_TARGETS and val:
            effects.append({'target': tgt, 'value': val})
    out = {'id': eid, 'type': t, 'name': name, 'homebrew': True,
           'effects': effects, 'notes': (e.get('notes') or '').strip()}
    # type-specific fields (kept minimal; used for surfacing in the right picker)
    if t == 'talent':
        out['path'] = (e.get('path') or 'any')            # heroic path id / radiant order key / surge code / 'any'
        out['prereq'] = (e.get('prereq') or '').strip()
    elif t == 'item':
        out['kind'] = e.get('kind') if e.get('kind') in ('weapon', 'armor', 'equipment') else 'equipment'
        out['damage'] = (e.get('damage') or '').strip()           # e.g. "1d6 impact"
        out['deflect'] = _int(e.get('deflect')) or None
    elif t == 'culture':
        out['expertises'] = [x.strip() for x in (e.get('expertises') or []) if str(x).strip()]
    elif t == 'heroic_path':
        out['slug'] = e.get('slug') or slugify(name)
        out['key_talent'] = (e.get('key_talent') or '').strip()
        out['start_skill'] = e.get('start_skill') if e.get('start_skill') in SKILL_ATTR else ''
    elif t == 'radiant_path':
        out['slug'] = e.get('slug') or slugify(name)
        out['spren'] = (e.get('spren') or '').strip()
        out['surges'] = [c for c in (e.get('surges') or []) if c][:2]
        out['philosophy'] = (e.get('philosophy') or '').strip()
    elif t == 'surge':
        out['code'] = (e.get('code') or slugify(name)[:3])
        # A governing attribute makes the surge a REAL skill (mod = ranks + attr).
        out['attribute'] = e.get('attribute') if e.get('attribute') in _ATTR_CODES else 'wil'
        out['desc'] = (e.get('desc') or '').strip()
    return out


def blank_entry(type='talent') -> dict:
    """An empty entry of `type` for the editor."""
    return normalize({'name': ''}, type)


def normalize_store(store) -> dict:
    """A whole homebrew store ({type: [entry]}) normalized; missing types -> []."""
    store = store if isinstance(store, dict) else {}
    return {t: [normalize(e, t) for e in (store.get(t) or [])] for t in TYPES}


# --- engine-facing lookups -------------------------------------------------
def radiant_order(store, key) -> dict | None:
    """A homebrew Radiant order by its slug/key, shaped like radiant.RADIANT_ORDERS
    values (so the build's surge logic treats it like canon)."""
    for e in normalize_store(store).get('radiant_path', []):
        if e['slug'] == key or slugify(e['name']) == key:
            return {'name': e['name'], 'spren': e.get('spren', ''),
                    'surges': tuple(e.get('surges') or ()), 'philosophy': e.get('philosophy', ''),
                    'homebrew': True}
    return None


def heroic_path(store, slug) -> dict | None:
    """A homebrew heroic path by slug -> {key_talent, start_skill} or None."""
    for e in normalize_store(store).get('heroic_path', []):
        if e['slug'] == slug or slugify(e['name']) == slug:
            return e
    return None


def surge_skills(store) -> dict:
    """{code: {'name', 'attribute'}} for homebrew surges usable as REAL skills
    (a governing attribute => skill mod = ranks + that attribute, like canon
    surge skills). Unlocked, like the canon surges, by a Radiant order that
    lists the code and has sworn its First Ideal."""
    out = {}
    for e in normalize_store(store).get('surge', []):
        code, attr = e.get('code'), e.get('attribute')
        if code and attr:
            out[code] = {'name': e['name'], 'attribute': attr}
    return out


def _parse_damage(s) -> dict | None:
    """'1d6 impact' -> {formula, type, skill}. Formula is the first token, the
    damage type the rest (the builder enters it free-form)."""
    parts = (s or '').strip().split()
    if not parts:
        return None
    return {'formula': parts[0], 'type': ' '.join(parts[1:]).lower(), 'skill': ''}


def weapon_docs(build, store) -> list:
    """Foundry-shaped weapon item docs for the EQUIPPED homebrew weapons a build
    carries, so CosmereActor parses them into Strikes (canon weapons already flow
    through the Inventory; homebrew items are invisible to it)."""
    store = normalize_store(store)
    by_id = {e['id']: e for e in store['item']}
    out = []
    for ent in (build.get('inventory') or []):
        if not (isinstance(ent, dict) and ent.get('equipped')):
            continue
        e = by_id.get(ent.get('id'))
        if e and e.get('kind') == 'weapon':
            dmg = _parse_damage(e.get('damage'))
            if dmg:
                out.append({'type': 'weapon', 'name': e['name'], 'system': {'damage': dmg}})
    return out


def resolve_bonuses(build, store):
    """Aggregate the STRUCTURED stat bonuses a build earns from the homebrew it
    has selected. Returns (bonus_map, sources, dangling):

      bonus_map  {target: total int}     -- summed across all selected entries
      sources    [name, ...]             -- the homebrew entries actually applied
      dangling   [ref, ...]              -- selected 'hb:' talents/items not found
    """
    store = normalize_store(store)
    tal_by_id = {e['id']: e for e in store['talent']}
    tal_by_name = {e['name'].strip().lower(): e for e in store['talent']}
    item_by_id = {e['id']: e for e in store['item']}

    bonus, sources, dangling = {}, [], []

    def apply(entry):
        if not entry:
            return
        for row in entry.get('effects', []):
            bonus[row['target']] = bonus.get(row['target'], 0) + row['value']
        sources.append(entry['name'])

    def by_name(type, name):
        nl = (name or '').strip().lower()
        return next((e for e in store[type] if e['name'].strip().lower() == nl), None) if nl else None

    def by_slug(type, key):
        return next((e for e in store[type] if e.get('slug') == key or slugify(e['name']) == key), None) if key else None

    for t in (build.get('talents') or []):
        if not isinstance(t, dict):
            continue
        e = tal_by_id.get(t.get('id')) or tal_by_name.get((t.get('name') or '').strip().lower())
        if e:
            apply(e)
        elif str(t.get('id') or '').startswith('hb:'):
            dangling.append(t.get('name') or t.get('id'))

    apply(by_name('ancestry', build.get('ancestry')))
    apply(by_name('culture', build.get('culture')))
    apply(by_slug('heroic_path', (build.get('path') or '')))
    apply(by_slug('radiant_path', (build.get('radiant_order') or '')))

    for ent in (build.get('inventory') or []):
        if isinstance(ent, dict) and ent.get('equipped'):
            e = item_by_id.get(ent.get('id'))
            if e:
                apply(e)
                # A homebrew item is invisible to the canon-only Inventory, so its
                # armor Deflect is bridged here (canon armor's deflect flows through
                # Inventory instead).
                if e.get('kind') == 'armor' and e.get('deflect'):
                    bonus['deflect'] = bonus.get('deflect', 0) + int(e['deflect'])
            elif str(ent.get('id') or '').startswith('hb:'):
                dangling.append(ent.get('id'))

    return bonus, sources, dangling
