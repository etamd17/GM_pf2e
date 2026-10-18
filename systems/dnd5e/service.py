"""D&D 5e application services: engine + combat rules + storage. No Flask.

The single most important invariant: a PC's combat vitals (HP, temp HP,
conditions, death saves, concentration, exhaustion) live ONLY in the character
document's ``state``. The tracker's PC combatant is a pointer (``pc_id``). So
damage applied from the tracker and HP edited on the sheet are the same write,
and both views repaint from the same broadcast -- there is no second copy to
drift.

Every mutator returns ``(payload, events)`` where payload is what the caller
should broadcast. Routes never touch the documents directly.
"""
from __future__ import annotations

import contextlib
import copy
import re
import uuid

from systems.dnd5e import combat as K
from systems.dnd5e import content as C
from systems.dnd5e import dice, images, store
from systems.dnd5e.render import strip_tags
from systems.dnd5e.rules.character import compute
from systems.dnd5e.rules.tables import ABILITIES, CONDITIONS, XP_FOR_LEVEL, encounter_difficulty

VITAL_KEYS = ('hp', 'temp_hp', 'conditions', 'exhaustion', 'death_saves', 'stable', 'dead',
              'concentration', 'max_hp_reduction')


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


# --- character views --------------------------------------------------------------

def sheet_for(doc: dict) -> dict:
    s = compute(doc)
    st = doc.setdefault('state', {})
    K.normalize_vitals(st, s['hp_max'])
    _migrate_resource_keys(st, s)
    return s


_OLD_ARCANUM = re.compile(r'^feature:mystic arcanum \((\d)th level\)$')


def _migrate_resource_keys(st: dict, s: dict):
    """Carry saved usage across renamed resource keys (5E-07), in place:

    * free casts were keyed by spell (``spell:misty step|xphb``) and are now
      keyed by grant (``spell:misty step|xphb@feat-fey-touched``). The old
      single resource was the first grant of that spell, so its usage moves to
      the first matching grant; any other grant starts unused.
    * 2014 Mystic Arcanum was ``feature:mystic arcanum (6th level)``; it is now
      ``mystic_arcanum_6`` like 2024.

    Keys that match nothing are left alone (a resource can be absent for a
    while, e.g. an unequipped item), never deleted."""
    used = st.get('resources_used')
    if not used:
        return
    keys = [r['key'] for r in s['resources']]
    for old in [k for k in used if k not in keys]:
        new = None
        if old.startswith('spell:') and '@' not in old:
            new = next((k for k in keys if k.startswith(old + '@')), None)
        else:
            m = _OLD_ARCANUM.match(old)
            if m and f'mystic_arcanum_{m.group(1)}' in keys:
                new = f'mystic_arcanum_{m.group(1)}'
        if new:
            if new not in used:
                used[new] = used[old]
            del used[old]


def pc_live(doc: dict, sheet: dict | None = None) -> dict:
    """Compact live state for SSE + the tracker (what changes during play)."""
    s = sheet or sheet_for(doc)
    st = doc.get('state') or {}
    K.normalize_vitals(st, s['hp_max'])
    sc = s['spellcasting']
    return {
        'id': doc['id'], 'name': doc.get('name'), 'level': s['level'],
        'portrait': images.url_for_name(doc.get('portrait')),
        'classes': ' / '.join(f'{c["name"]} {c["level"]}' for c in s['classes']),
        'hp': st['hp'], 'hp_max': K.effective_max(st), 'temp_hp': st['temp_hp'],
        'ac': s['ac']['value'], 'initiative': s['initiative'],
        'dex': s['abilities']['dex']['score'],
        'passive_perception': s['passive']['perception'], 'speed': s['speed'].get('walk'),
        'conditions': st['conditions'], 'exhaustion': st['exhaustion'],
        'death_saves': st['death_saves'], 'stable': st['stable'], 'dead': st['dead'],
        'concentration': st['concentration'], 'inspiration': bool(st.get('inspiration')),
        'effects': s['effects']['active'], 'resist': s['resist'],
        'slots': {lvl: {'max': n, 'used': int((st.get('slots_used') or {}).get(lvl, 0))}
                  for lvl, n in sc['slots'].items()},
        'pact': ({'max': sc['pact']['count'], 'level': sc['pact']['level'],
                  'used': int(st.get('pact_used') or 0)} if sc['pact'] else None),
        'resources': [{'key': r['key'], 'name': r['name'], 'max': r['max'],
                       'used': int((st.get('resources_used') or {}).get(r['key'], 0)),
                       'recharge': r['recharge']} for r in s['resources']],
        'hit_dice': {d: {'max': n, 'used': int((st.get('hit_dice_used') or {}).get(d, 0))}
                     for d, n in s['hit_dice'].items()},
        'revision': doc.get('revision', 0),
    }


def _mut(cid, pid, fn):
    try:
        doc, result = store.mutate_pc(cid, pid, fn)
    except KeyError:
        raise ServiceError('Unknown character', 404) from None
    return doc, result


def vitals_action(cid: str, pid: str, action: str, data: dict, *, edition_hint=None):
    """Apply one vitals change to a PC (from the sheet or the tracker)."""
    def fn(doc):
        s = sheet_for(doc)
        st = doc['state']
        name = doc.get('name') or 'Character'
        ed = s['edition']
        if action == 'damage':
            amt, note = K.adjust_for_defenses(_int(data.get('amount')), data.get('type'),
                                              resist=s['resist'], immune=s['immune'])
            ev = K.apply_damage(st, amt, is_pc=True, crit=bool(data.get('crit')), name=name, edition=ed)
            if note:
                ev.insert(0, {'type': 'log', 'text': f'{name}: damage {note}'})
        elif action == 'heal':
            ev = K.heal(st, _int(data.get('amount')), name=name)
        elif action == 'set_hp':
            st['hp'] = max(0, min(K.effective_max(st), _int(data.get('hp'))))
            if st['hp'] > 0:
                K._clear_dying(st)
            ev = [{'type': 'log', 'text': f'{name} HP set to {st["hp"]}'}]
        elif action == 'temp_hp':
            if data.get('replace'):
                st['temp_hp'] = max(0, _int(data.get('amount')))
                ev = []
            else:
                ev = K.set_temp_hp(st, _int(data.get('amount')), name=name)
        elif action == 'condition':
            if str(data.get('condition') or '').lower() not in CONDITIONS:
                raise ServiceError('Unknown condition')
            ev = K.set_condition(st, str(data.get('condition') or ''), on=bool(data.get('on', True)),
                                 rounds=data.get('rounds'), until=data.get('until') or 'end',
                                 source_cid=data.get('source_cid'), save=data.get('save'), name=name)
        elif action == 'exhaustion':
            ev = K.set_exhaustion(st, _int(data.get('level')), name=name)
        elif action == 'death_save':
            if data.get('natural') is not None:
                nat = _int(data.get('natural'))
            else:
                nat = dice.d20()['natural']
            ev = [{'type': 'roll', 'name': name, 'what': 'death save', 'natural': nat}] + \
                K.death_save(st, nat, name=name)
        elif action == 'stabilize':
            ev = K.stabilize(st, name=name)
        elif action == 'concentration':
            spell = (data.get('spell') or '').strip()[:80] or None
            st['concentration'] = spell
            ev = [{'type': 'log', 'text': f'{name} {"concentrates on " + spell if spell else "drops concentration"}'}]
        elif action == 'inspiration':
            st['inspiration'] = bool(data.get('on'))
            ev = []
        elif action == 'revive':
            st['dead'] = False
            st['hp'] = max(1, _int(data.get('hp') or 1))
            K._clear_dying(st)
            ev = [{'type': 'log', 'text': f'{name} is revived'}]
        else:
            raise ServiceError(f'unknown action {action!r}')
        return s, ev
    doc, (s, ev) = _mut(cid, pid, fn)
    return pc_live(doc, s), ev


def resource_action(cid: str, pid: str, kind: str, data: dict):
    """Spend/restore spell slots, pact slots, class resources, hit dice."""
    def fn(doc):
        s = sheet_for(doc)
        st = doc['state']
        ev = []
        delta = _int(data.get('delta', 1))
        if kind == 'slot':
            lvl = str(_int(data.get('level')))
            mx = int(s['spellcasting']['slots'].get(lvl, 0))
            used = st.setdefault('slots_used', {})
            used[lvl] = max(0, min(mx, int(used.get(lvl, 0)) + delta))
        elif kind == 'pact':
            p = s['spellcasting']['pact']
            if not p:
                raise ServiceError('No pact slots')
            st['pact_used'] = max(0, min(p['count'], int(st.get('pact_used') or 0) + delta))
        elif kind == 'resource':
            key = str(data.get('key'))
            r = next((r for r in s['resources'] if r['key'] == key), None)
            if not r:
                raise ServiceError('Unknown resource')
            used = st.setdefault('resources_used', {})
            used[key] = max(0, min(r['max'], int(used.get(key, 0)) + delta))
        elif kind == 'hit_die':
            die = str(data.get('die'))
            mx = int(s['hit_dice'].get(die, 0))
            used = st.setdefault('hit_dice_used', {})
            if delta > 0:
                if int(used.get(die, 0)) >= mx:
                    raise ServiceError('No hit dice of that size left')
                faces = int(die[1:])
                roll = data.get('roll')
                roll = dice.roll(f'1d{faces}')['total'] if roll is None else max(1, min(faces, _int(roll)))
                gain = max(0, roll + s['abilities']['con']['mod'])
                used[die] = int(used.get(die, 0)) + 1
                ev += [{'type': 'roll', 'name': doc.get('name'), 'what': f'hit die {die}', 'natural': roll}]
                ev += K.heal(st, gain, name=doc.get('name'))
            else:
                used[die] = max(0, int(used.get(die, 0)) - 1)
        else:
            raise ServiceError('unknown resource kind')
        return s, ev
    doc, (s, ev) = _mut(cid, pid, fn)
    return pc_live(doc, s), ev


def rest(cid: str, pid: str, kind: str):
    def fn(doc):
        s = sheet_for(doc)
        st = doc['state']
        name = doc.get('name')
        one = s['edition'] == 'one'
        used = st.setdefault('resources_used', {})
        for r in s['resources']:
            rc = r['recharge']
            if kind == 'long' or rc == 'short':
                used[r['key']] = 0
            elif rc.startswith('short (1)') or rc.startswith('long (1 on short)'):
                used[r['key']] = max(0, int(used.get(r['key'], 0)) - 1)
        st['pact_used'] = 0
        # Short-lived effects end with any rest; everything ends with a long one.
        st['effects'] = [] if kind == 'long' else [
            k for k in st.get('effects') or [] if k not in ('rage', 'shield', 'dodge', 'bladesong', 'bless')]
        if kind == 'long':
            st['hp'] = K.effective_max(st)
            st['max_hp_reduction'] = 0
            st['hp'] = K.effective_max(st)
            if one:
                st['temp_hp'] = 0
            st['slots_used'] = {}
            hd = st.setdefault('hit_dice_used', {})
            if one:
                st['hit_dice_used'] = {}
            else:
                # regain spent dice up to half the character's total (min 1)
                regain = max(1, s['level'] // 2)
                for d in sorted(hd, key=lambda x: -int(x[1:])):
                    back = min(regain, int(hd[d]))
                    hd[d] = int(hd[d]) - back
                    regain -= back
            K._clear_dying(st)
            if st.get('exhaustion'):
                st['exhaustion'] = max(0, int(st['exhaustion']) - 1)
            ev = [{'type': 'log', 'text': f'{name} finishes a long rest'}]
        else:
            ev = [{'type': 'log', 'text': f'{name} finishes a short rest'}]
        # Recompute: ending Rage/Bladesong changes resistances, AC and speed.
        return sheet_for(doc), ev
    doc, (s, ev) = _mut(cid, pid, fn)
    return pc_live(doc, s), ev


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# --- building ------------------------------------------------------------------------

# `rolled_scores` is deliberately absent: only roll_ability_scores() sets it.
BUILD_KEYS = ('edition', 'sources', 'race', 'background', 'ability_method', 'base_scores',
              'custom_origin', 'optional_class_features', 'levels', 'choices')


def update_build(cid: str, pid: str, patch: dict, *, expected_revision=None):
    """Replace build fields; reject only INVALID builds (incomplete is fine --
    the sheet shows what is still to choose, like D&D Beyond)."""
    def fn(doc):
        # Compared against the BUILD revision, not the document's: HP, slots
        # and conditions change constantly in play and must not invalidate an
        # open builder tab.
        if expected_revision is not None and int(expected_revision) != int(doc.get('build_rev') or 0):
            raise ServiceError('This character was rebuilt in another window. Reload and try again.', 409)
        trial = copy.deepcopy(doc)
        b = trial.setdefault('build', {})
        for k in BUILD_KEYS:
            if k in patch:
                b[k] = patch[k]
        if 'name' in patch:
            trial['name'] = str(patch['name'] or '').strip()[:80] or trial.get('name')
        if 'details' in patch and isinstance(patch['details'], dict):
            trial['details'] = {k: str(v)[:4000] for k, v in patch['details'].items()
                                if k in ('alignment', 'appearance', 'personality', 'ideals', 'bonds',
                                         'flaws', 'backstory', 'faith', 'age', 'height', 'weight',
                                         'eyes', 'hair', 'skin', 'gender', 'portrait_url')}
        if len(b.get('levels') or []) > 20:
            raise ServiceError('Maximum level is 20')
        s = compute(trial)
        if s['errors']:
            raise ServiceError('; '.join(s['errors'][:5]), 422)
        # Picks for choices that no longer exist (changed species, removed a
        # level, swapped a feat) are dropped so they cannot resurface later.
        for k in s['stale_choices']:
            b['choices'].pop(k, None)
        s['stale_choices'] = []
        trial['build_rev'] = int(doc.get('build_rev') or 0) + 1
        doc.clear()
        doc.update(trial)
        K.normalize_vitals(doc.setdefault('state', {}), s['hp_max'])
        return s
    doc, s = _mut(cid, pid, fn)
    return doc, s


def roll_ability_scores(cid: str, pid: str):
    """4d6-drop-lowest six times, server-side. Assigns them in order; the
    player rearranges with the 'roll' method (which only accepts this set)."""
    def fn(doc):
        rolls = sorted((dice.roll('4d6kh3')['total'] for _ in range(6)), reverse=True)
        b = doc.setdefault('build', {})
        b['ability_method'] = 'roll'
        b['rolled_scores'] = rolls
        b['base_scores'] = dict(zip(ABILITIES, rolls))
        b.setdefault('roll_history', []).append(rolls)
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def preview(doc: dict, patch: dict) -> dict:
    trial = copy.deepcopy(doc)
    b = trial.setdefault('build', {})
    for k in BUILD_KEYS:
        if k in patch:
            b[k] = patch[k]
    return compute(trial)


def level_up(cid: str, pid: str, class_id: str, hp_roll=None, *, roll_hp: bool = False):
    """Append one level. Validates multiclass prerequisites (hard rule) and the
    resulting build; the new level's choices show up as pending."""
    def fn(doc):
        b = doc.setdefault('build', {})
        levels = b.setdefault('levels', [])
        if len(levels) >= 20:
            raise ServiceError('Already level 20')
        rec = C.get('class', class_id)
        if rec is None:
            raise ServiceError('Unknown class')
        current = {lv['class'] for lv in levels}
        if levels and class_id not in current:
            from systems.dnd5e.rules.character import multiclass_requirement, _check_requirements
            s0 = compute(doc)
            scores = {a: s0['abilities'][a]['score'] for a in ABILITIES}
            for cls_id in current | {class_id}:
                crec = C.get('class', cls_id)
                req = multiclass_requirement(crec)
                if req and not _check_requirements(req, scores):
                    raise ServiceError(f'Multiclassing requires {crec["name"]} prerequisites', 422)
        faces = (rec.get('hd') or {}).get('faces', 8)
        hp = None
        if levels:
            if roll_hp:
                hp = dice.roll(f'1d{faces}')['total']
            elif hp_roll is not None:
                hp = max(1, min(faces, _int(hp_roll)))
        levels.append({'class': class_id, 'hp': hp})
        doc['build_rev'] = int(doc.get('build_rev') or 0) + 1
        s = compute(doc)
        if s['errors']:
            raise ServiceError('; '.join(s['errors'][:5]), 422)
        st = doc.setdefault('state', {})
        old_max = st.get('hp_max') or s['hp_max']
        K.normalize_vitals(st, s['hp_max'])
        st['hp'] = min(K.effective_max(st), st['hp'] + max(0, s['hp_max'] - old_max))
        return s
    return _mut(cid, pid, fn)


def level_down(cid: str, pid: str):
    def fn(doc):
        levels = doc.setdefault('build', {}).setdefault('levels', [])
        if not levels:
            raise ServiceError('No levels to remove')
        levels.pop()
        doc['build_rev'] = int(doc.get('build_rev') or 0) + 1
        s = compute(doc)
        picks = doc['build'].setdefault('choices', {})
        for k in s['stale_choices']:
            picks.pop(k, None)
        # A choice that allows fewer picks at the lower level (cantrips, spells
        # known, invocations, masteries...) keeps the earliest ones.
        trimmed = False
        for ch in s['choices']:
            v = picks.get(ch['id'])
            if not isinstance(v, list):
                continue
            levels = (ch.get('query') or {}).get('levels')
            if ch['kind'] == 'spell' and levels:
                # spells above what the lower level can learn come off
                keep = [x for x in v if (C.get('spell', str(x)) or {}).get('level', 0) in levels]
                if keep != v:
                    v = picks[ch['id']] = keep
                    trimmed = True
            if ch.get('count') and len(v) > ch['count']:
                picks[ch['id']] = v[:ch['count']]
                trimmed = True
        if trimmed or s['stale_choices']:
            s = compute(doc)
        s['stale_choices'] = []
        K.normalize_vitals(doc.setdefault('state', {}), s['hp_max'])
        return s
    return _mut(cid, pid, fn)


# --- inventory --------------------------------------------------------------------

def inventory_action(cid: str, pid: str, action: str, data: dict):
    def fn(doc):
        st = doc.setdefault('state', {})
        inv = st.setdefault('inventory', [])
        idx = _int(data.get('index'), -1)
        if action == 'add':
            iid = str(data.get('id') or '').lower()
            if iid:
                if C.get('item', iid) is None:
                    raise ServiceError('Unknown item')
                inv.append({'id': iid, 'qty': max(1, _int(data.get('qty'), 1)), 'uid': uuid.uuid4().hex[:12]})
            else:
                nm = str(data.get('name') or '').strip()[:80]
                if not nm:
                    raise ServiceError('Item needs a name')
                inv.append({'name': nm, 'qty': max(1, _int(data.get('qty'), 1)),
                            'weight': float(data.get('weight') or 0), 'uid': uuid.uuid4().hex[:12]})
        elif 0 <= idx < len(inv):
            ent = inv[idx]
            if action == 'remove':
                inv.pop(idx)
            elif action == 'qty':
                q = _int(data.get('qty'), 1)
                if q <= 0:
                    inv.pop(idx)
                else:
                    ent['qty'] = q
            elif action == 'equip':
                ent['equipped'] = bool(data.get('on'))
            elif action == 'attune':
                on = bool(data.get('on'))
                if on and sum(1 for i in inv if i.get('attuned')) >= 3:
                    raise ServiceError('You can attune to at most 3 items')
                ent['attuned'] = on
            elif action == 'notes':
                ent['notes'] = str(data.get('notes') or '')[:500]
            elif action == 'pact':
                for other in inv:
                    other.pop('pact', None)          # one pact weapon at a time
                if data.get('on'):
                    ent['pact'] = True
            else:
                raise ServiceError('unknown inventory action')
        else:
            raise ServiceError('No such inventory entry')
        return sheet_for(doc)
    doc, s = _mut(cid, pid, fn)
    return doc, s


# 5etools `equipmentType` placeholders -> how to list the concrete choices.
_EQUIPMENT_TYPES = {
    'weaponSimple': {'category': 'simple'}, 'weaponMartial': {'category': 'martial'},
    'weaponSimpleMelee': {'category': 'simple', 'melee': True},
    'weaponMartialMelee': {'category': 'martial', 'melee': True},
    'weaponSimpleRanged': {'category': 'simple', 'ranged': True},
    'weaponMartialRanged': {'category': 'martial', 'ranged': True},
    'instrumentMusical': {'type': 'INS'}, 'toolArtisan': {'type': 'AT'}, 'setGaming': {'type': 'GS'},
    'focusSpellcastingArcane': {'type': 'SCF', 'focus': 'arcane'},
    'focusSpellcastingDruidic': {'type': 'SCF', 'focus': 'druid'},
    'focusSpellcastingHoly': {'type': 'SCF', 'focus': 'holy'},
}
_EQUIPMENT_TYPE_LABELS = {
    'weaponSimple': 'a simple weapon', 'weaponMartial': 'a martial weapon',
    'weaponSimpleMelee': 'a simple melee weapon', 'weaponMartialMelee': 'a martial melee weapon',
    'weaponSimpleRanged': 'a simple ranged weapon', 'weaponMartialRanged': 'a martial ranged weapon',
    'instrumentMusical': 'a musical instrument', 'toolArtisan': "a set of artisan's tools",
    'setGaming': 'a gaming set', 'focusSpellcastingArcane': 'an arcane focus',
    'focusSpellcastingDruidic': 'a druidic focus', 'focusSpellcastingHoly': 'a holy symbol',
}
_GENERIC_ITEMS = {"artisan's tools", 'musical instrument', 'gaming set'}


def equipment_type_options(etype: str, edition: str) -> list:
    spec = _EQUIPMENT_TYPES.get(etype)
    if not spec:
        return []
    core = 'XPHB' if edition == 'one' else 'PHB'
    out = []
    for r in C.all_records('item', sources=[core]):
        t = (r.get('type') or '').split('|')[0]
        if 'category' in spec:
            if not r.get('_isBaseItem') or r.get('weaponCategory') != spec['category']:
                continue
            if spec.get('melee') and t != 'M':
                continue
            if spec.get('ranged') and t != 'R':
                continue
        elif t != spec['type']:
            continue
        if spec.get('focus') and spec['focus'] not in str(r.get('scfType') or '').lower():
            continue
        if r['name'].lower() in _GENERIC_ITEMS:
            continue
        out.append({'id': f'{r["name"]}|{r["source"]}'.lower(), 'name': r['name']})
    return sorted(out, key=lambda x: x['name'])


def _equip_entry(e, edition) -> dict:
    if isinstance(e, str):
        rec = C.get_ref('item', e)
        return {'id': e.lower() if rec else None, 'name': rec['name'] if rec else e.split('|')[0].title(), 'qty': 1}
    if 'item' in e:
        rec = C.get_ref('item', e['item'])
        return {'id': e['item'].lower() if rec else None,
                'name': e.get('displayName') or (rec['name'] if rec else e['item'].split('|')[0].title()),
                'qty': int(e.get('quantity') or 1), 'contains_cp': int(e.get('containsValue') or 0)}
    if 'equipmentType' in e:
        return {'choose': e['equipmentType'], 'qty': int(e.get('quantity') or 1),
                'options': equipment_type_options(e['equipmentType'], edition),
                'name': _EQUIPMENT_TYPE_LABELS.get(e['equipmentType'], e['equipmentType'])}
    if 'value' in e:
        return {'gold_cp': int(e['value']), 'name': f'{int(e["value"]) / 100:g} gp'}
    if 'special' in e:
        return {'id': None, 'name': str(e['special']), 'qty': int(e.get('quantity') or 1),
                'contains_cp': int(e.get('containsValue') or 0)}
    return {'name': str(e), 'qty': 1}


def equipment_packages(doc: dict) -> dict:
    """Starting equipment groups for the first class and the background."""
    b = doc.get('build') or {}
    ed = 'one' if b.get('edition') == 'one' else 'classic'
    out = {'groups': [], 'taken': bool((doc.get('state') or {}).get('starting_equipment_taken'))}
    levels = b.get('levels') or []
    srcs = []
    if levels:
        rec = C.get('class', levels[0]['class'])
        if rec and rec.get('startingEquipment'):
            srcs.append((rec['name'], rec['startingEquipment']))
    if b.get('background'):
        bg = C.get('background', b['background'])
        if bg and bg.get('startingEquipment'):
            srcs.append((bg['name'], {'defaultData': bg['startingEquipment']}))
    for origin, se in srcs:
        for i, grp in enumerate(se.get('defaultData') or []):
            opts = {k: [_equip_entry(e, ed) for e in v] for k, v in grp.items()}
            out['groups'].append({'origin': origin, 'key': f'{origin}:{i}', 'options': opts})
        if se.get('goldAlternative'):
            out['gold_alternative'] = strip_tags(se['goldAlternative'])
    return out


def grant_equipment(cid: str, pid: str, picks: list, *, gold_cp: int = 0):
    """Add chosen starting equipment once. ``picks``: [{id?, name, qty}]."""
    def fn(doc):
        st = doc.setdefault('state', {})
        if st.get('starting_equipment_taken'):
            raise ServiceError('Starting equipment was already added', 409)
        inv = st.setdefault('inventory', [])
        for p in picks[:80]:
            iid = str(p.get('id') or '').lower()
            qty = max(1, min(999, _int(p.get('qty'), 1)))
            if iid and C.get('item', iid):
                inv.append({'id': iid, 'qty': qty, 'uid': uuid.uuid4().hex[:12]})
            elif p.get('name'):
                inv.append({'name': str(p['name'])[:80], 'qty': qty, 'uid': uuid.uuid4().hex[:12]})
        cur = st.setdefault('currency', {})
        cp = max(0, _int(gold_cp))
        cur['gp'] = int(cur.get('gp', 0)) + cp // 100
        cur['sp'] = int(cur.get('sp', 0)) + (cp % 100) // 10
        cur['cp'] = int(cur.get('cp', 0)) + cp % 10
        st['starting_equipment_taken'] = True
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def set_currency(cid: str, pid: str, data: dict):
    def fn(doc):
        cur = doc.setdefault('state', {}).setdefault('currency', {})
        for k in ('cp', 'sp', 'ep', 'gp', 'pp'):
            if k in data:
                cur[k] = max(0, _int(data[k]))
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def set_notes(cid: str, pid: str, data: dict):
    def fn(doc):
        notes = doc.setdefault('state', {}).setdefault('notes', {})
        for k, v in (data or {}).items():
            if k in ('general', 'allies', 'organizations', 'enemies', 'other', 'session'):
                notes[k] = str(v or '')[:20000]
        return None
    return _mut(cid, pid, fn)


def toggle_effect(cid: str, pid: str, key: str, on: bool):
    """Turn a sheet effect (Rage, Bladesong, Shield...) on or off. Only
    effects the character can use are accepted (compute() lists them)."""
    def fn(doc):
        s = sheet_for(doc)
        if key not in {e['key'] for e in s['effects']['available']}:
            raise ServiceError('That effect is not available to this character')
        st = doc.setdefault('state', {})
        cur = [k for k in st.get('effects') or [] if k != key]
        if on:
            cur.append(key)
        st['effects'] = cur
        if key == 'mage_armor' and not on:
            st.pop('mage_armor', None)
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def custom_resource(cid: str, pid: str, data: dict):
    """Add or remove a hand-tracked resource (anything the engine doesn't know)."""
    def fn(doc):
        st = doc.setdefault('state', {})
        lst = [c for c in st.get('custom_resources') or [] if isinstance(c, dict)]
        if data.get('remove'):
            lst = [c for c in lst if c.get('key') != data['remove']]
        else:
            name = str(data.get('name') or '').strip()[:60]
            mx = _int(data.get('max'))
            if not name or not 1 <= mx <= 99:
                raise ServiceError('A resource needs a name and 1-99 uses')
            recharge = 'short' if data.get('recharge') == 'short' else 'long'
            lst.append({'key': 'custom:' + uuid.uuid4().hex[:8], 'name': name, 'max': mx, 'recharge': recharge})
        st['custom_resources'] = lst
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def is_beast(mon: dict) -> bool:
    t = mon.get('type')
    t = t.get('type') if isinstance(t, dict) else t
    return str(t or '').lower() == 'beast'


def extras_action(cid: str, pid: str, action: str, data: dict, *, allow_any: bool):
    """Companions on the sheet (Wild Shape forms, familiars, beast companions,
    steeds). Each keeps its own HP. Players may add beasts; the GM anything."""
    def fn(doc):
        st = doc.setdefault('state', {})
        extras = [e for e in st.get('extras') or [] if isinstance(e, dict)]
        if action == 'add':
            mid = str(data.get('monster_id') or '').lower()
            mon = C.get('monster', mid)
            if mon is None:
                raise ServiceError('Unknown creature')
            if not allow_any and not is_beast(mon):
                raise ServiceError('Ask your GM to add non-beast companions', 403)
            if len(extras) >= 20:
                raise ServiceError('Too many extras')
            hp = (mon.get('hp') or {}).get('average') or 1
            extras.append({'uid': uuid.uuid4().hex[:10], 'monster_id': mid, 'name': mon['name'],
                           'label': str(data.get('label') or '')[:40], 'hp': hp, 'hp_max': hp})
        else:
            ent = next((e for e in extras if e.get('uid') == data.get('uid')), None)
            if ent is None:
                raise ServiceError('No such extra', 404)
            if action == 'remove':
                extras.remove(ent)
            elif action == 'hp':
                ent['hp'] = max(0, min(int(ent['hp_max']), _int(data.get('hp'))))
            elif action == 'label':
                ent['label'] = str(data.get('label') or '')[:40]
            else:
                raise ServiceError('unknown action')
        st['extras'] = extras
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def set_flag(cid: str, pid: str, key: str, value):
    allowed = {'mage_armor': bool, 'ac_bonus': int, 'xp': int}
    if key not in allowed:
        raise ServiceError('unknown setting')

    def fn(doc):
        doc.setdefault('state', {})[key] = allowed[key](value) if value is not None else None
        return sheet_for(doc)
    return _mut(cid, pid, fn)


def xp_progress(xp: int, level: int) -> dict:
    nxt = XP_FOR_LEVEL.get(min(20, level + 1))
    return {'xp': xp, 'next': nxt if level < 20 else None,
            'ready': bool(nxt and xp >= nxt and level < 20)}


# --- encounters ----------------------------------------------------------------------

def _vitals_getter(cid: str, pc_docs: dict, locks=None):
    def get(c):
        if c['kind'] == 'pc':
            doc = pc_docs.get(c['pc_id'])
            if doc is None:
                if locks is not None:
                    # Lock order: encounter (held by our caller) -> PC. Sheet
                    # routes only ever take the PC lock, so this cannot deadlock.
                    locks.enter_context(store.pc_lock(cid, c['pc_id']))
                doc = store.load_pc(cid, c['pc_id'])
                if doc is None:
                    return K.new_vitals(1)
                pc_docs[c['pc_id']] = doc
            st = doc.setdefault('state', {})
            if 'hp_max' not in st:
                sheet_for(doc)
            return st
        return c
    return get


def _save_touched(cid, pc_docs: dict, before: dict):
    for pid, doc in pc_docs.items():
        if before.get(pid) != doc.get('state'):
            store.save_pc(cid, doc, fsync=False)


def encounter_view(cid: str, enc: dict | None = None, *, for_player: bool = False) -> dict:
    enc = enc or store.load_encounter(cid)
    out = {'round': enc['round'], 'turn': enc['turn'], 'started': enc['started'],
           'revision': enc.get('revision', 0), 'log': enc['log'][-60:], 'combatants': []}
    for i, c in enumerate(enc['combatants']):
        if for_player and c.get('hidden'):
            continue
        row = {k: c.get(k) for k in ('cid', 'kind', 'name', 'initiative', 'init_bonus', 'ac', 'hidden',
                                     'reaction_used', 'notes', 'monster_id', 'pc_id', 'cr', 'legendary',
                                     'legendary_resistance', 'recharge', 'resist', 'immune', 'vulnerable',
                                     'condition_immune')}
        row['active'] = enc['started'] and i == enc['turn']
        if c['kind'] == 'pc':
            doc = store.load_pc(cid, c['pc_id'])
            if doc is None:
                row.update({'missing': True, 'hp': 0, 'hp_max': 1})
            else:
                live = pc_live(doc)
                row.update({k: live[k] for k in ('hp', 'hp_max', 'temp_hp', 'conditions', 'exhaustion',
                                                 'death_saves', 'stable', 'dead', 'concentration',
                                                 'passive_perception', 'speed', 'effects', 'resist')})
                row['ac'] = live['ac']
                row['name'] = live['name']
                row['image'] = live['portrait']
                if not for_player:
                    # what the PC has left, so the GM can follow casting from the tracker
                    row.update({k: live[k] for k in ('slots', 'pact', 'resources')})
        else:
            row.update({k: c.get(k) for k in ('hp', 'hp_max', 'temp_hp', 'conditions', 'exhaustion',
                                              'death_saves', 'stable', 'dead', 'concentration')})
        if c['kind'] != 'pc':
            row['image'] = (images.url_for_name(c.get('image'))
                            or (C.image_for('monster', c['monster_id'], 'token') if c.get('monster_id') else None))
        elif images.url_for_name(c.get('image')):
            row['image'] = images.url_for_name(c.get('image'))
        if for_player and c['kind'] != 'pc':
            status = K.hp_status(c)
            for k in ('hp', 'hp_max', 'temp_hp', 'ac', 'notes', 'cr', 'legendary', 'legendary_resistance',
                      'recharge', 'resist', 'immune', 'vulnerable', 'condition_immune', 'init_bonus',
                      'monster_id', 'concentration'):
                row.pop(k, None)
            row['status'] = status
        out['combatants'].append(row)
    if for_player:
        out['log'] = []     # the GM log can name hidden creatures and rolls
    return out


def combat_action(cid: str, action: str, data: dict, *, rng=None):
    """Every tracker mutation. Returns (encounter_dict, events, touched_pc_ids)."""
    pc_docs: dict = {}
    before: dict = {}
    locks = contextlib.ExitStack()

    def fn(enc):
        get = _vitals_getter(cid, pc_docs, locks)

        def vit(c):
            v = get(c)
            if c['kind'] == 'pc' and c['pc_id'] not in before:
                before[c['pc_id']] = copy.deepcopy(pc_docs[c['pc_id']].get('state'))
            return v
        ev: list = []
        target = K.find(enc, data.get('cid')) if data.get('cid') else None
        if action in ('damage', 'heal', 'temp_hp', 'condition', 'exhaustion', 'death_save', 'remove',
                      'set_initiative', 'legendary', 'legendary_resistance', 'recharge', 'reaction',
                      'hide', 'rename', 'notes', 'set_hp', 'concentration', 'stabilize') and target is None:
            raise ServiceError('No such combatant', 404)
        if action == 'add_monster':
            mid = str(data.get('monster_id') or '').lower()
            mon = C.get('monster', mid)
            if mon is None:
                raise ServiceError('Unknown monster')
            for _ in range(max(1, min(20, _int(data.get('count'), 1)))):
                c = K.new_monster_combatant(mon, mid, label=K.number_duplicates(enc, mon['name']),
                                            roll_hp=bool(data.get('roll_hp')), rng=rng,
                                            hidden=bool(data.get('hidden')))
                enc['combatants'].append(c)
                ev.append({'type': 'log', 'text': f'{c["name"]} joins'})
                if enc['started']:
                    ev += K.roll_initiative(enc, [c['cid']], rng=rng)
        elif action == 'add_pc':
            pid = str(data.get('pc_id') or '')
            if any(c.get('pc_id') == pid for c in enc['combatants']):
                raise ServiceError('Already in the encounter')
            doc = store.load_pc(cid, pid)
            if doc is None:
                raise ServiceError('Unknown character', 404)
            live = pc_live(doc)
            enc['combatants'].append(K.new_pc_combatant(pid, live['name'], live['initiative'],
                                                         live['dex'], live['ac']))
            ev.append({'type': 'log', 'text': f'{live["name"]} joins'})
        elif action == 'add_party':
            have = {c.get('pc_id') for c in enc['combatants']}
            for doc in store.list_pcs(cid):
                if doc['id'] in have or not (doc.get('build') or {}).get('levels'):
                    continue
                live = pc_live(doc)
                enc['combatants'].append(K.new_pc_combatant(doc['id'], live['name'], live['initiative'],
                                                             live['dex'], live['ac']))
                ev.append({'type': 'log', 'text': f'{live["name"]} joins'})
        elif action == 'add_quick':
            c = K.new_quick_combatant(str(data.get('name') or 'Creature')[:60], hp=_int(data.get('hp'), 1),
                                      ac=_int(data.get('ac'), 10), init_bonus=_int(data.get('init_bonus')))
            enc['combatants'].append(c)
            ev.append({'type': 'log', 'text': f'{c["name"]} joins'})
        elif action == 'roll_initiative':
            ev += K.roll_initiative(enc, data.get('cids'), group=bool(data.get('group')), rng=rng)
        elif action == 'set_initiative':
            target['initiative'] = _int(data.get('initiative'))
            K.sort_order(enc)
        elif action == 'start':
            missing = [c['name'] for c in enc['combatants'] if c.get('initiative') is None]
            if missing:
                ev += K.roll_initiative(enc, [c['cid'] for c in enc['combatants']
                                              if c.get('initiative') is None and c['kind'] != 'pc'], rng=rng)
                still = [c['name'] for c in enc['combatants'] if c.get('initiative') is None]
                if still:
                    raise ServiceError('Waiting on initiative for: ' + ', '.join(still), 409)
            ev += K.start(enc) + K._begin_turn(enc, vit, rng)
        elif action == 'next':
            ev += K.advance(enc, vit, rng=rng)
        elif action == 'prev':
            ev += K.back(enc)
        elif action == 'end':
            ev += K.end(enc)
        elif action == 'clear':
            enc.clear()
            enc.update(K.new_encounter())
        elif action == 'remove':
            ev += K.remove(enc, target['cid'])
        elif action in ('damage', 'heal', 'temp_hp', 'condition', 'exhaustion', 'death_save', 'set_hp',
                        'concentration', 'stabilize'):
            v = vit(target)
            is_pc = target['kind'] == 'pc'
            name = target['name']
            if action == 'damage':
                if is_pc:
                    s = sheet_for(pc_docs[target['pc_id']])
                    res, imm, vul = s['resist'], s['immune'], []
                else:
                    res, imm, vul = target.get('resist') or [], target.get('immune') or [], target.get('vulnerable') or []
                amt, note = K.adjust_for_defenses(_int(data.get('amount')), data.get('type'), res, imm, vul)
                if note:
                    ev.append({'type': 'log', 'text': f'{name}: {note}'})
                ev += K.apply_damage(v, amt, is_pc=is_pc, crit=bool(data.get('crit')), name=name,
                                     edition=enc.get('edition', 'one'))
            elif action == 'heal':
                ev += K.heal(v, _int(data.get('amount')), name=name)
            elif action == 'set_hp':
                v['hp'] = max(0, min(K.effective_max(v), _int(data.get('hp'))))
                if not is_pc and v['hp'] > 0:
                    v['dead'] = False
            elif action == 'temp_hp':
                ev += K.set_temp_hp(v, _int(data.get('amount')), name=name)
            elif action == 'condition':
                cond = str(data.get('condition') or '').lower()
                if cond not in CONDITIONS:
                    raise ServiceError('Unknown condition')
                if data.get('on', True) and cond in (target.get('condition_immune') or []):
                    raise ServiceError(f'{name} is immune to {cond}')
                ev += K.set_condition(v, cond, on=bool(data.get('on', True)), rounds=data.get('rounds'),
                                      until=data.get('until') or 'end', source_cid=data.get('source_cid'),
                                      save=data.get('save'), name=name)
            elif action == 'exhaustion':
                ev += K.set_exhaustion(v, _int(data.get('level')), name=name)
            elif action == 'death_save':
                nat = _int(data.get('natural')) if data.get('natural') is not None else dice.d20(rng=rng)['natural']
                ev.append({'type': 'roll', 'name': name, 'what': 'death save', 'natural': nat})
                ev += K.death_save(v, nat, name=name)
            elif action == 'stabilize':
                ev += K.stabilize(v, name=name)
            elif action == 'concentration':
                v['concentration'] = (str(data.get('spell') or '').strip()[:80] or None)
        elif action == 'legendary':
            lg = target.get('legendary')
            if not lg:
                raise ServiceError('No legendary actions')
            lg['used'] = max(0, min(lg['max'], lg['used'] + _int(data.get('delta'), 1)))
        elif action == 'legendary_resistance':
            lr = target.get('legendary_resistance')
            if not lr:
                raise ServiceError('No legendary resistance')
            lr['used'] = max(0, min(lr['max'], lr['used'] + _int(data.get('delta'), 1)))
            ev.append({'type': 'log', 'text': f'{target["name"]} uses Legendary Resistance'})
        elif action == 'recharge':
            r = (target.get('recharge') or {}).get(str(data.get('name')))
            if not r:
                raise ServiceError('No such recharge ability')
            r['ready'] = bool(data.get('ready'))
        elif action == 'reaction':
            target['reaction_used'] = bool(data.get('used'))
        elif action == 'hide':
            target['hidden'] = bool(data.get('hidden'))
        elif action == 'rename':
            target['name'] = str(data.get('name') or target['name'])[:60]
        elif action == 'notes':
            target['notes'] = str(data.get('notes') or '')[:2000]
        else:
            raise ServiceError(f'unknown action {action!r}')
        K.append_log(enc, ev)
        _save_touched(cid, pc_docs, before)
        return ev

    with locks:
        enc, ev = store.mutate_encounter(cid, fn)
    return enc, ev, sorted(before)


def difficulty(edition: str, party_levels: list, monster_ids: list) -> dict:
    crs = []
    for mid in monster_ids:
        m = C.get('monster', str(mid).lower())
        if m:
            cr = m.get('cr')
            crs.append(cr.get('cr') if isinstance(cr, dict) else cr)
    return encounter_difficulty(edition, party_levels, crs)
