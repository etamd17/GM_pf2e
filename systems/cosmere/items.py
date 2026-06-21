"""Cosmere item catalog + character inventory.

Normalizes the ingested Foundry ``items`` pack (weapons / armor / equipment)
into clean dicts and provides an ``Inventory`` that derives the rulebook
gear effects a sheet/tracker needs: a worn armor's **Deflect** value and the
**Strikes** from held weapons (Ch.7).
"""
from __future__ import annotations

import re

from systems.cosmere import load_pack

_CATALOG = None          # id -> normalized item
_RAW = None              # id -> original Foundry doc


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get('value', '')
    if not isinstance(value, str):
        return ''
    return re.sub(r'<[^>]+>', '', value).strip()


def _fmt_traits(traits) -> list:
    """Active traits as display strings (e.g. 'Cumbersome 3', 'Two Handed')."""
    out = []
    if not isinstance(traits, dict):
        return out
    for name, cfg in traits.items():
        if not isinstance(cfg, dict) or not cfg.get('defaultActive'):
            continue
        label = str(name).replace('_', ' ').title()
        val = cfg.get('defaultValue')
        out.append(f"{label} {val}" if val not in (None, '') else label)
    return out


def _normalize(doc) -> dict:
    sys = doc.get('system', {}) if isinstance(doc.get('system'), dict) else {}
    kind = doc.get('type', 'equipment')
    dmg = sys.get('damage') if isinstance(sys.get('damage'), dict) else None
    damage = None
    if dmg and dmg.get('formula'):
        damage = {
            'formula': dmg.get('formula', ''),
            'type': dmg.get('type', ''),
            'skill': dmg.get('skill', ''),
            'attribute': dmg.get('attribute'),
        }
    attack = sys.get('attack') if isinstance(sys.get('attack'), dict) else {}
    equip = sys.get('equip') if isinstance(sys.get('equip'), dict) else {}
    price = sys.get('price') if isinstance(sys.get('price'), dict) else {}
    return {
        'id': doc.get('_id'),
        'name': doc.get('name', 'Unknown'),
        'kind': kind,
        'damage': damage,
        'deflect': sys.get('deflect') if isinstance(sys.get('deflect'), int) else None,
        'traits': _fmt_traits(sys.get('traits')),
        'hands': equip.get('hold'),
        'attack_type': attack.get('type'),
        'range': attack.get('range') if isinstance(attack.get('range'), dict) else None,
        'weight': sys.get('weight'),
        'price': price.get('value') if isinstance(price, dict) else sys.get('price'),
        'description': _text(sys.get('description'))[:280],
    }


def _build():
    global _CATALOG, _RAW
    if _CATALOG is not None:
        return
    _CATALOG, _RAW = {}, {}
    seen_names = set()
    # Base system items first, then the ingested Stormlight Handbook items; a
    # name clash keeps the base item (stable ids for existing builds + kits).
    for pack in ('items', 'handbook-items'):
        for doc in load_pack(pack):
            iid = doc.get('_id')
            if not iid:
                continue
            nm = (doc.get('name') or '').strip().lower()
            if nm in seen_names:
                continue
            seen_names.add(nm)
            _RAW[iid] = doc
            _CATALOG[iid] = _normalize(doc)


def catalog() -> list:
    """All normalized items (weapons/armor/equipment/loot), sorted by name."""
    _build()
    return sorted(_CATALOG.values(), key=lambda i: (i['kind'], i['name'].lower()))


def by_kind(kind) -> list:
    return [i for i in catalog() if i['kind'] == kind]


def weapons():   return by_kind('weapon')
def armor():     return by_kind('armor')
def equipment(): return by_kind('equipment')


def get(item_id) -> dict | None:
    _build()
    return _CATALOG.get(item_id)


def get_raw(item_id) -> dict | None:
    """The original Foundry item doc (for embedding into an actor's items)."""
    _build()
    return _RAW.get(item_id)


_FABRIALS = None


def _fab_text(doc) -> str:
    desc = doc.get('system', {}).get('description', {}) if isinstance(doc.get('system'), dict) else {}
    raw = (desc.get('value') or desc.get('chat') or '') if isinstance(desc, dict) else str(desc or '')
    return re.sub(r'<[^>]+>', ' ', raw).replace('\xa0', ' ')


def fabrials() -> list:
    """Catalog of Fabrial devices (Ch.7), parsed from handbook-items: each is
    {id, name, tier, charges, effect}. Tier + charge count live in the
    description text (not structured fields), so we parse them out; a fabrial is
    detected by 'Fabrial' in the name/description or a stated charge count. The
    "Fabrials" section-header doc is skipped. Crafting rules aren't in the packs,
    so this is purchase/reward + charge-tracking only."""
    global _FABRIALS
    if _FABRIALS is not None:
        return _FABRIALS
    out = []
    for d in load_pack('handbook-items'):
        nm = (d.get('name') or '').strip()
        if not nm or nm.lower() == 'fabrials':
            continue
        txt = _fab_text(d)
        is_fab = ('fabrial' in nm.lower() or 'Fabrial Effect' in txt
                  or re.search(r'[Cc]harges?\s+\d|\d+\s*charge', txt))
        if not is_fab:
            continue
        tier = re.search(r'Tier\s*(\d)', txt)
        ch = re.search(r'Charges\s+(\d+)', txt) or re.search(r'(\d+)\s*charges?\b', txt)
        # Effect = the description sentence after the stat preamble (either the
        # "...Fabrial Effect" lead-in, or the "Price ...; Charges N;" block).
        eff = re.sub(r'^.*?(?:Fabrial Effect|Charges\s+\d+\s*;)\s*', '', txt, count=1)
        eff = re.sub(r'\s+', ' ', eff).strip()[:240]
        out.append({'id': d.get('_id'), 'name': nm,
                    'tier': int(tier.group(1)) if tier else 0,
                    'charges': int(ch.group(1)) if ch else 3,
                    'effect': eff})
    out.sort(key=lambda f: (f['tier'], f['name'].lower()))
    _FABRIALS = out
    return out


def fabrial(fid) -> dict | None:
    return next((f for f in fabrials() if f['id'] == fid), None)


def by_name(name) -> dict | None:
    """A normalized catalog item by (case-insensitive) name."""
    _build()
    n = (name or '').strip().lower()
    for it in _CATALOG.values():
        if it['name'].lower() == n:
            return it
    return None


_DEFLECT_TYPES = {'impact': True, 'keen': True, 'energy': True,
                  'spirit': False, 'vital': False, 'heal': False}


class Inventory:
    """A character's carried gear. Entries are ``{id, qty, equipped}``."""

    def __init__(self, entries=None):
        self.entries = [dict(e) for e in (entries or []) if isinstance(e, dict) and e.get('id')]

    def resolved(self) -> list:
        out = []
        for e in self.entries:
            it = get(e.get('id'))
            if it:
                out.append({**it, 'qty': int(e.get('qty', 1) or 1), 'equipped': bool(e.get('equipped'))})
        return out

    def equipped(self) -> list:
        return [it for it in self.resolved() if it['equipped']]

    def deflect_value(self) -> int:
        """Deflect from worn armor. Armor doesn't stack, so take the highest."""
        vals = [it['deflect'] for it in self.equipped() if it['kind'] == 'armor' and it.get('deflect')]
        return max(vals) if vals else 0

    def strikes(self) -> list:
        """Held weapons as strike entries (name/damage/type/skill)."""
        out = []
        for it in self.equipped():
            if it['kind'] == 'weapon' and it.get('damage'):
                d = it['damage']
                out.append({'name': it['name'], 'damage': d.get('formula', ''),
                            'type': d.get('type', ''), 'skill': d.get('skill', '')})
        return out

    def total_weight(self) -> float:
        return round(sum((it.get('weight') or 0) * it.get('qty', 1) for it in self.resolved()), 2)

    def deflect_block(self) -> dict:
        v = self.deflect_value()
        return {'natural': 0, 'bonus': 0, 'override': v, 'useOverride': bool(v),
                'source': 'armor', 'types': dict(_DEFLECT_TYPES)}

    def foundry_weapon_items(self) -> list:
        """Raw Foundry docs for equipped weapons, so CosmereActor parses Strikes."""
        out = []
        for e in self.entries:
            if not e.get('equipped'):
                continue
            raw = get_raw(e.get('id'))
            if raw and raw.get('type') == 'weapon':
                out.append(raw)
        return out

    def to_list(self) -> list:
        return [dict(e) for e in self.entries]
