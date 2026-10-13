"""D&D 5e combat rules -- pure functions over plain dicts (no Flask, no I/O).

Two shapes:

* **vitals** -- the mutable combat numbers of one creature::

      {'hp', 'hp_max', 'temp_hp', 'conditions': {name: {...}}, 'exhaustion',
       'death_saves': {'success', 'failure'}, 'stable', 'dead', 'concentration'}

  For a monster these live on its combatant entry. For a PC they live in the
  character document's ``state`` (the sheet and the tracker edit the SAME
  dict, which is what keeps them in sync); the service layer hands that dict in.

* **encounter** -- turn order + combatants::

      {'round', 'turn', 'started', 'edition', 'combatants': [...], 'log': [...]}

Every mutating function returns a list of *events* (dicts) describing what
happened -- prompts the GM must act on (concentration save, death save,
recharge) as well as plain log lines. Callers broadcast them; nothing here
decides presentation.
"""
from __future__ import annotations

import re
import uuid

from systems.dnd5e import dice
from systems.dnd5e.rules.tables import CONDITIONS, ability_mod, cr_proficiency_bonus

MAX_LOG = 200

# Conditions that make a creature Incapacitated as well (PHB/XPHB).
_INCAPACITATING = {'paralyzed', 'petrified', 'stunned', 'unconscious'}


def new_vitals(hp_max: int, hp: int | None = None) -> dict:
    return {'hp': hp_max if hp is None else hp, 'hp_max': hp_max, 'temp_hp': 0,
            'conditions': {}, 'exhaustion': 0, 'death_saves': {'success': 0, 'failure': 0},
            'stable': False, 'dead': False, 'concentration': None}


def normalize_vitals(v: dict, hp_max: int) -> dict:
    """Fill defaults in place (old docs, hand edits) and clamp to the max."""
    v['hp_max'] = int(hp_max)
    v.setdefault('hp', hp_max)
    v.setdefault('temp_hp', 0)
    if not isinstance(v.get('conditions'), dict):
        v['conditions'] = {}
    v.setdefault('exhaustion', 0)
    if not isinstance(v.get('death_saves'), dict):
        v['death_saves'] = {'success': 0, 'failure': 0}
    v.setdefault('stable', False)
    v.setdefault('dead', False)
    v.setdefault('concentration', None)
    v['hp'] = max(0, min(int(v['hp']), effective_max(v)))
    return v


def effective_max(v: dict) -> int:
    return max(1, int(v.get('hp_max', 1)) - int(v.get('max_hp_reduction', 0) or 0))


# --- damage / healing ------------------------------------------------------------

def adjust_for_defenses(amount: int, dtype: str | None, resist=(), immune=(), vulnerable=()) -> tuple:
    """(final_amount, note). Immunity beats everything; resistance and
    vulnerability both apply if both are present (resistance first, PHB p.197)."""
    amount = max(0, int(amount))
    t = (dtype or '').lower()
    if not t:
        return amount, ''
    if t in immune:
        return 0, 'immune'
    note = []
    if t in resist:
        amount //= 2
        note.append('resisted')
    if t in vulnerable:
        amount *= 2
        note.append('vulnerable')
    return amount, ' '.join(note)


def apply_damage(v: dict, amount: int, *, is_pc: bool, crit: bool = False, name: str = '',
                 edition: str = 'one') -> list:
    """Apply already-adjusted damage. Handles temp HP, 0 HP, massive damage,
    damage at 0 HP (death save failures) and the concentration prompt."""
    ev = []
    amount = max(0, int(amount))
    if amount == 0 or v.get('dead'):
        return ev
    temp = int(v.get('temp_hp') or 0)
    absorbed = min(temp, amount)
    v['temp_hp'] = temp - absorbed
    rest = amount - absorbed
    was_zero = v['hp'] <= 0
    if was_zero:
        if is_pc and rest > 0:
            if rest >= effective_max(v):
                _die(v, ev, name, 'massive damage at 0 HP')
            else:
                fails = 2 if crit else 1
                ev += _add_death_failures(v, fails, name)
        return ev + [_log(f'{name} takes {amount} damage at 0 HP')]
    new_hp = v['hp'] - rest
    if new_hp <= 0:
        overflow = -new_hp
        v['hp'] = 0
        if is_pc:
            if overflow >= effective_max(v):
                _die(v, ev, name, 'massive damage')
            else:
                v['conditions']['unconscious'] = {'source': 'dying'}
                v['death_saves'] = {'success': 0, 'failure': 0}
                v['stable'] = False
                ev.append({'type': 'dying', 'name': name})
                ev.append(_log(f'{name} drops to 0 HP and is dying'))
        else:
            v['dead'] = True
            ev.append({'type': 'defeated', 'name': name})
            ev.append(_log(f'{name} is defeated'))
        if v.get('concentration'):
            ev.append(_log(f'{name} loses concentration on {v["concentration"]}'))
            v['concentration'] = None
    else:
        v['hp'] = new_hp
        ev.append(_log(f'{name} takes {amount} damage ({v["hp"]}/{effective_max(v)})'))
        if v.get('concentration') and rest + absorbed > 0:
            dc = max(10, amount // 2)
            if edition == 'one':
                dc = min(30, dc)
            ev.append({'type': 'concentration_check', 'name': name, 'dc': dc,
                       'spell': v['concentration']})
    return ev


def heal(v: dict, amount: int, *, name: str = '') -> list:
    amount = max(0, int(amount))
    if amount == 0 or v.get('dead'):
        return []
    ev = []
    if v['hp'] <= 0:
        _clear_dying(v)
        ev.append(_log(f'{name} regains consciousness'))
    v['hp'] = min(effective_max(v), v['hp'] + amount)
    ev.append(_log(f'{name} heals {amount} ({v["hp"]}/{effective_max(v)})'))
    return ev


def set_temp_hp(v: dict, amount: int, *, name: str = '') -> list:
    """Temporary HP don't stack: keep the higher (PHB p.198)."""
    amount = max(0, int(amount))
    if amount > int(v.get('temp_hp') or 0):
        v['temp_hp'] = amount
        return [_log(f'{name} gains {amount} temporary HP')]
    return []


def _clear_dying(v: dict):
    v['death_saves'] = {'success': 0, 'failure': 0}
    v['stable'] = False
    c = v['conditions'].get('unconscious')
    if c and c.get('source') == 'dying':
        v['conditions'].pop('unconscious', None)


def _die(v, ev, name, why):
    v['hp'] = 0
    v['dead'] = True
    v['concentration'] = None
    ev.append({'type': 'death', 'name': name, 'reason': why})
    ev.append(_log(f'{name} dies ({why})'))


def _add_death_failures(v, n, name) -> list:
    ds = v['death_saves']
    ds['failure'] = min(3, ds['failure'] + n)
    v['stable'] = False
    ev = [_log(f'{name}: {ds["failure"]} death save failure{"s" if ds["failure"] != 1 else ""}')]
    if ds['failure'] >= 3:
        _die(v, ev, name, 'three failed death saves')
    return ev


def death_save(v: dict, natural: int, *, bonus: int = 0, name: str = '') -> list:
    """Resolve a death saving throw from the kept d20 (PHB p.197 / XPHB p.30)."""
    if v.get('dead') or v['hp'] > 0 or v.get('stable'):
        return []
    total = natural + bonus
    ds = v['death_saves']
    if natural == 20:
        v['hp'] = 1
        _clear_dying(v)
        return [_log(f'{name} rolls a natural 20 and regains 1 HP')]
    if natural == 1:
        return _add_death_failures(v, 2, name)
    if total >= 10:
        ds['success'] += 1
        ev = [_log(f'{name}: {ds["success"]} death save success{"es" if ds["success"] != 1 else ""}')]
        if ds['success'] >= 3:
            v['stable'] = True
            ds['success'] = ds['failure'] = 0
            ev.append(_log(f'{name} is stable'))
        return ev
    return _add_death_failures(v, 1, name)


def stabilize(v: dict, *, name: str = '') -> list:
    if v['hp'] > 0 or v.get('dead'):
        return []
    v['stable'] = True
    v['death_saves'] = {'success': 0, 'failure': 0}
    return [_log(f'{name} is stabilized')]


# --- conditions -----------------------------------------------------------------

def set_condition(v: dict, cond: str, *, on: bool = True, rounds: int | None = None,
                  until: str = 'end', source_cid: str | None = None, save: dict | None = None,
                  name: str = '') -> list:
    """Add/remove a condition. ``rounds`` counts the affected creature's own
    turns (decremented at the start/end of ITS turn per ``until``), or the
    source's turns when ``source_cid`` is given -- matching how spell durations
    like "until the end of your next turn" are written."""
    c = (cond or '').strip().lower()
    if c == 'exhaustion':
        return set_exhaustion(v, int(rounds or 0) if on else 0, name=name)
    if c not in CONDITIONS:
        raise ValueError(f'unknown condition {cond!r}')
    if not on:
        if v['conditions'].pop(c, None) is not None:
            return [_log(f'{name} is no longer {c}')]
        return []
    entry = {}
    if rounds:
        entry['rounds'] = int(rounds)
        entry['until'] = 'start' if until == 'start' else 'end'
    if source_cid:
        entry['source_cid'] = source_cid
    if save and save.get('dc'):
        entry['save'] = {'ability': save.get('ability', 'con'), 'dc': int(save['dc'])}
    v['conditions'][c] = entry
    ev = [_log(f'{name} is {c}' + (f' ({rounds} rd)' if rounds else ''))]
    if c in _INCAPACITATING and v.get('concentration'):
        ev.append(_log(f'{name} loses concentration on {v["concentration"]}'))
        v['concentration'] = None
    return ev


def set_exhaustion(v: dict, level: int, *, name: str = '') -> list:
    level = max(0, min(6, int(level)))
    v['exhaustion'] = level
    ev = [_log(f'{name} exhaustion {level}')]
    if level >= 6:
        _die(v, ev, name, 'exhaustion 6')
    return ev


def exhaustion_effects(level: int, edition: str) -> str:
    if not level:
        return ''
    if edition == 'one':
        return f'-{2 * level} to D20 Tests; speed -{5 * level} ft.'
    table = ['Disadvantage on ability checks', 'Speed halved', 'Disadvantage on attacks and saves',
             'HP maximum halved', 'Speed 0', 'Death']
    return '; '.join(table[:level])


def tick_conditions(v: dict, *, phase: str, owner_cid: str, ending_cid: str, name: str = '') -> list:
    """Decrement timed conditions at the start/end of ``ending_cid``'s turn."""
    ev = []
    for c, e in list(v['conditions'].items()):
        if not e.get('rounds'):
            continue
        clock = e.get('source_cid') or owner_cid
        if clock != ending_cid or e.get('until', 'end') != phase:
            continue
        e['rounds'] -= 1
        if e['rounds'] <= 0:
            v['conditions'].pop(c, None)
            ev.append(_log(f'{name}: {c} ends'))
    return ev


# --- combatants -------------------------------------------------------------------

def _flatten_defense(field) -> list:
    out = []
    for x in field or []:
        if isinstance(x, str):
            out.append(x.lower())
        elif isinstance(x, dict):
            for k in ('resist', 'immune', 'vulnerable'):
                if isinstance(x.get(k), list):
                    # conditional entries (e.g. "from nonmagical attacks") are
                    # kept: the GM sees the note; most table damage qualifies.
                    out += _flatten_defense(x[k])
    return sorted(set(out))


def _legendary_resistances(mon: dict) -> int:
    for t in mon.get('trait') or []:
        m = re.search(r'Legendary Resistance \((\d+)/Day', t.get('name', ''))
        if m:
            return int(m.group(1))
    return 0


def _recharge_actions(mon: dict) -> dict:
    out = {}
    for blk in ('action', 'bonus', 'reaction', 'trait'):
        for a in mon.get(blk) or []:
            m = re.search(r'\{@recharge\s*(\d*)\}', a.get('name', ''))
            if m:
                nm = re.sub(r'\s*\{@recharge[^}]*\}', '', a['name']).strip()
                out[nm] = {'min': int(m.group(1) or 6), 'ready': True}
    return out


def monster_init_bonus(mon: dict) -> int:
    dex_mod = ability_mod(mon.get('dex', 10))
    ini = mon.get('initiative')
    if isinstance(ini, int):
        return ini
    if isinstance(ini, dict):
        if 'initiative' in ini and isinstance(ini['initiative'], int):
            return ini['initiative']
        prof = int(ini.get('proficiency', 0) or 0)
        return dex_mod + prof * cr_proficiency_bonus((mon.get('cr') or {}).get('cr') if isinstance(mon.get('cr'), dict) else mon.get('cr'))
    return dex_mod


def monster_ac(mon: dict) -> int:
    a = (mon.get('ac') or [10])[0]
    if isinstance(a, dict):
        return int(a.get('ac') or 10)
    try:
        return int(a)
    except (TypeError, ValueError):
        return 10


def new_monster_combatant(mon: dict, mon_id: str, *, label: str | None = None, roll_hp: bool = False,
                          rng=None, hidden: bool = False) -> dict:
    hp_info = mon.get('hp') or {}
    hp = hp_info.get('average')
    if roll_hp and hp_info.get('formula'):
        try:
            hp = max(1, dice.roll(hp_info['formula'], rng)['total'])
        except ValueError:
            pass
    if not isinstance(hp, int):
        hp = 1
    leg = mon.get('legendaryActions') or (3 if mon.get('legendary') else 0)
    c = {
        'cid': uuid.uuid4().hex, 'kind': 'monster', 'monster_id': mon_id,
        'name': label or mon['name'], 'base_name': mon['name'],
        'initiative': None, 'init_bonus': monster_init_bonus(mon), 'dex': int(mon.get('dex', 10)),
        'ac': monster_ac(mon), 'hidden': hidden, 'group': mon_id,
        'cr': (mon.get('cr') or {}).get('cr') if isinstance(mon.get('cr'), dict) else mon.get('cr'),
        'resist': _flatten_defense(mon.get('resist')), 'immune': _flatten_defense(mon.get('immune')),
        'vulnerable': _flatten_defense(mon.get('vulnerable')),
        'condition_immune': _flatten_defense(mon.get('conditionImmune')),
        'legendary': {'max': int(leg), 'used': 0} if leg else None,
        'legendary_resistance': ({'max': _legendary_resistances(mon), 'used': 0}
                                 if _legendary_resistances(mon) else None),
        'recharge': _recharge_actions(mon),
        'reaction_used': False, 'notes': '',
    }
    c.update(new_vitals(hp))
    return c


def new_pc_combatant(pc_id: str, name: str, init_bonus: int, dex: int, ac: int) -> dict:
    return {'cid': uuid.uuid4().hex, 'kind': 'pc', 'pc_id': pc_id, 'name': name,
            'initiative': None, 'init_bonus': int(init_bonus), 'dex': int(dex), 'ac': int(ac),
            'hidden': False, 'reaction_used': False, 'notes': ''}


def new_quick_combatant(name: str, *, hp: int, ac: int, init_bonus: int = 0, kind: str = 'npc') -> dict:
    c = {'cid': uuid.uuid4().hex, 'kind': kind, 'name': name, 'initiative': None,
         'init_bonus': int(init_bonus), 'dex': 10, 'ac': int(ac), 'hidden': False,
         'resist': [], 'immune': [], 'vulnerable': [], 'condition_immune': [],
         'legendary': None, 'legendary_resistance': None, 'recharge': {},
         'reaction_used': False, 'notes': ''}
    c.update(new_vitals(max(1, int(hp))))
    return c


# --- encounter ------------------------------------------------------------------

def new_encounter(edition: str = 'one') -> dict:
    return {'round': 0, 'turn': 0, 'started': False, 'edition': edition, 'combatants': [], 'log': [],
            'revision': 0}


def number_duplicates(enc: dict, base_name: str) -> str:
    n = sum(1 for c in enc['combatants'] if c.get('base_name') == base_name)
    return f'{base_name} {n + 1}' if n else base_name


def roll_initiative(enc: dict, cids=None, *, group: bool = False, rng=None, mode_by_cid=None) -> list:
    """Roll for the given combatants (default: every monster/npc without one).
    ``group``: creatures sharing a ``group`` key share one roll (DMG p.270)."""
    ev = []
    shared = {}
    for c in enc['combatants']:
        if cids is not None and c['cid'] not in cids:
            continue
        if cids is None and (c['kind'] == 'pc' or c.get('initiative') is not None):
            continue
        key = c.get('group') if group and c.get('group') else None
        if key and key in shared:
            c['initiative'] = shared[key]
            continue
        mode = (mode_by_cid or {}).get(c['cid'], 'normal')
        r = dice.d20(c.get('init_bonus', 0), mode, rng)
        c['initiative'] = r['total']
        if key:
            shared[key] = r['total']
        ev.append(_log(f'{c["name"]} rolls initiative {r["total"]}'))
    sort_order(enc)
    return ev


def sort_order(enc: dict):
    """Highest initiative first; ties by DEX score, then name (stable)."""
    current = _current(enc)
    enc['combatants'].sort(key=lambda c: (-(c['initiative'] if c.get('initiative') is not None else -999),
                                          -int(c.get('dex', 10)), c['name'].lower()))
    if current is not None:
        enc['turn'] = next((i for i, c in enumerate(enc['combatants']) if c['cid'] == current['cid']), 0)


def _current(enc):
    cs = enc['combatants']
    if not cs or not enc.get('started'):
        return None
    return cs[enc['turn'] % len(cs)]


def start(enc: dict) -> list:
    if not enc['combatants']:
        return []
    sort_order(enc)
    enc['started'] = True
    enc['round'] = 1
    enc['turn'] = 0
    return [_log('Combat begins (round 1)')]


def _skippable(c, vitals) -> bool:
    return c['kind'] != 'pc' and vitals.get('dead')


def advance(enc: dict, vitals_of, *, rng=None) -> list:
    """End the current turn and begin the next. ``vitals_of(c)`` returns the
    vitals dict for a combatant (combatant itself for monsters)."""
    cs = enc['combatants']
    if not cs:
        return []
    if not enc.get('started'):
        return start(enc) + _begin_turn(enc, vitals_of, rng)
    ev = []
    cur = cs[enc['turn'] % len(cs)]
    for c in cs:
        ev += tick_conditions(vitals_of(c), phase='end', owner_cid=c['cid'], ending_cid=cur['cid'], name=c['name'])
    for _ in range(len(cs)):
        enc['turn'] += 1
        if enc['turn'] >= len(cs):
            enc['turn'] = 0
            enc['round'] += 1
            ev.append(_log(f'Round {enc["round"]}'))
        if not _skippable(cs[enc['turn']], vitals_of(cs[enc['turn']])):
            break
    return ev + _begin_turn(enc, vitals_of, rng)


def back(enc: dict) -> list:
    cs = enc['combatants']
    if not cs or not enc.get('started'):
        return []
    enc['turn'] -= 1
    if enc['turn'] < 0:
        if enc['round'] <= 1:
            enc['turn'] = 0
            return []
        enc['turn'] = len(cs) - 1
        enc['round'] -= 1
    return [_log(f'Back to {cs[enc["turn"]]["name"]} (round {enc["round"]})')]


def _begin_turn(enc, vitals_of, rng) -> list:
    cs = enc['combatants']
    c = cs[enc['turn']]
    v = vitals_of(c)
    ev = [{'type': 'turn', 'cid': c['cid'], 'name': c['name'], 'round': enc['round']},
          _log(f'{c["name"]}\'s turn')]
    c['reaction_used'] = False
    if c.get('legendary'):
        c['legendary']['used'] = 0
    for o in cs:
        ev += tick_conditions(vitals_of(o), phase='start', owner_cid=o['cid'], ending_cid=c['cid'], name=o['name'])
    for nm, r in (c.get('recharge') or {}).items():
        if not r.get('ready'):
            roll = dice.roll('1d6', rng)['total']
            if roll >= r['min']:
                r['ready'] = True
                ev.append(_log(f'{c["name"]}: {nm} recharges ({roll})'))
            else:
                ev.append(_log(f'{c["name"]}: {nm} does not recharge ({roll})'))
    if c['kind'] == 'pc' and v['hp'] <= 0 and not v.get('dead') and not v.get('stable'):
        ev.append({'type': 'death_save_due', 'cid': c['cid'], 'name': c['name']})
    for cond, e in (v.get('conditions') or {}).items():
        if e.get('save'):
            ev.append({'type': 'save_ends_reminder', 'cid': c['cid'], 'name': c['name'], 'condition': cond,
                       'save': e['save']})
    return ev


def remove(enc: dict, cid: str) -> list:
    cs = enc['combatants']
    idx = next((i for i, c in enumerate(cs) if c['cid'] == cid), None)
    if idx is None:
        return []
    c = cs.pop(idx)
    if enc.get('started') and cs:
        # Removing someone before the current creature shifts it left; removing
        # the current creature hands the turn to whoever followed it.
        if idx < enc['turn']:
            enc['turn'] -= 1
        enc['turn'] %= len(cs)
    if not cs:
        enc['started'] = False
        enc['round'] = 0
        enc['turn'] = 0
    return [_log(f'{c["name"]} removed')]


def end(enc: dict) -> list:
    enc['started'] = False
    enc['round'] = 0
    enc['turn'] = 0
    return [_log('Combat ends')]


def find(enc: dict, cid: str):
    return next((c for c in enc['combatants'] if c['cid'] == cid), None)


def _log(text: str) -> dict:
    return {'type': 'log', 'text': text}


def append_log(enc: dict, events: list):
    for e in events:
        if e.get('type') == 'log':
            enc['log'].append({'round': enc.get('round', 0), 'text': e['text']})
    if len(enc['log']) > MAX_LOG:
        del enc['log'][:-MAX_LOG]


def hp_status(v: dict) -> str:
    """Player-facing health word for a creature whose HP is hidden."""
    if v.get('dead'):
        return 'Dead'
    mx = effective_max(v)
    if v['hp'] <= 0:
        return 'Down'
    if v['hp'] * 2 <= mx:
        return 'Bloodied'
    if v['hp'] < mx:
        return 'Wounded'
    return 'Unhurt'
