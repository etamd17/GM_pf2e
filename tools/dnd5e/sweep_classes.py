"""Sweep every class x subclass (both editions) to level 20, filling every
choice the way a player would, and report engine errors, choices that cannot
be completed, and errors when levelling back down. Takes ~3 minutes.

Run from the repo root:  python tools/dnd5e/sweep_classes.py [ClassName ...]
Truncated levels keep the level-20 picks, so "pick at most N" rows below level
20 are expected (the real level-down trims them; see service.level_down)."""
import sys, time, json, collections
sys.path.insert(0, '.')
from systems.dnd5e import content as C
from systems.dnd5e.routes import _resolve_options
from systems.dnd5e.rules.character import compute

ABIL = ('str', 'dex', 'con', 'int', 'wis', 'cha')
CLASSES = ('Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter', 'Monk', 'Paladin', 'Ranger',
           'Rogue', 'Sorcerer', 'Warlock', 'Wizard')


def opts_of(ch, build):
    o = ch.get('options') or []
    if not o and ch.get('query') is not None:
        o = _resolve_options(ch['kind'], ch['query'], build, build['choices'])
    return [x['value'] if isinstance(x, dict) else x for x in o]


def fill(doc, max_pass=25):
    b = doc['build']
    for _ in range(max_pass):
        s = compute(doc)
        progress = False
        for ch in s['choices']:
            if ch['complete'] or ch['kind'] == 'subclass':
                continue
            k = ch['kind']
            count = ch.get('count') or 1
            if k == 'asi':
                val = {'asi': {'con': 2}}
            elif k == 'ability':
                ws = sorted(ch.get('weights') or [1], reverse=True)
                val = dict(zip(ch['options'], ws))
            elif k in ('alternative', 'size'):
                o = opts_of(ch, b)
                val = [o[0]] if o else None
            else:
                o = opts_of(ch, b)
                taken = set()
                same_kind = {c['id'] for c in s['choices'] if c['kind'] == k}
                for other_id, v in b['choices'].items():
                    if other_id != ch['id'] and other_id in same_kind and isinstance(v, list)                             and k in ('skill', 'expertise', 'language', 'tool'):
                        taken |= set(map(str, v))
                cur = list(b['choices'].get(ch['id']) or [])
                for x in o:
                    if len(cur) >= count:
                        break
                    if x not in cur and str(x) not in taken:
                        cur.append(x)
                val = cur or None
            if val and val != b['choices'].get(ch['id']):
                old = b['choices'].get(ch['id'])
                b['choices'][ch['id']] = val
                t = compute(doc)
                if t['errors'] and not s['errors']:
                    if old is None:
                        b['choices'].pop(ch['id'])
                    else:
                        b['choices'][ch['id']] = old
                    continue
                progress = True
                break           # recompute: later choices depend on this one
        if not progress:
            return s
    return compute(doc)


def subclasses_for(cid, ed):
    doc = {'name': 'x', 'build': {'edition': ed, 'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                                  'levels': [{'class': cid}] * 20, 'choices': {}, 'sources': []}, 'state': {}}
    s = compute(doc)
    ch = next((c for c in s['choices'] if c['kind'] == 'subclass'), None)
    return [o['value'] if isinstance(o, dict) else o for o in (ch['options'] if ch else [])], ch['id'] if ch else None


def main():
    report = collections.defaultdict(list)
    t0 = time.time()
    n_builds = 0
    only = sys.argv[1:] or None
    for src, ed in (('XPHB', 'one'), ('PHB', 'classic')):
        for name in CLASSES:
            if only and name not in only:
                continue
            cid = f'{name}|{src}'.lower()
            subs, sub_id = subclasses_for(cid, ed)
            for sub in subs:
                doc = {'name': 'x', 'build': {
                    'edition': ed, 'ability_method': 'manual', 'base_scores': {a: 14 for a in ABIL},
                    'race': 'human|xphb' if ed == 'one' else 'human|phb',
                    'background': 'soldier|xphb' if ed == 'one' else 'soldier|phb',
                    'levels': [{'class': cid, 'hp': None}] * 20,
                    'choices': {sub_id: [sub]}, 'sources': []}, 'state': {}}
                s = fill(doc)
                n_builds += 1
                tag = f'{cid} / {sub}'
                if s['errors']:
                    report['errors'].append((tag, s['errors']))
                bad = [(c['label'], c['kind']) for c in s['choices'] if not c['complete']]
                if bad:
                    report['incomplete'].append((tag, bad))
                if s.get('warnings'):
                    report['warnings'].append((tag, s['warnings'][:6]))
                # every level 1..20 with the same picks (level-up path)
                for n in range(1, 20):
                    d2 = json.loads(json.dumps(doc))
                    d2['build']['levels'] = d2['build']['levels'][:n]
                    s2 = compute(d2)
                    for e in s2['errors']:
                        import re as _re
                        report['level_error_kinds'].append(_re.sub(r'\d+', 'N', e))
                    if s2['level'] != n:
                        report['level_mismatch'].append((tag, n, s2['level']))
    print(f'{n_builds} builds in {time.time() - t0:.1f}s')
    report['level_error_kinds'] = sorted(collections.Counter(report['level_error_kinds']).items())
    for k, v in report.items():
        print(f'== {k}: {len(v)}')
        for row in v[:40]:
            print('  ', row)


main()
