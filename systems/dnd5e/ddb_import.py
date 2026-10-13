"""Import a D&D Beyond character (character-service v5 JSON) into a 5e build.

Input is what D&D Beyond's own sheet loads, from
``https://character-service.dndbeyond.com/character/v5/character/<id>``, either
the whole response (``{"success":..., "data": {...}}``) or its ``data``.

D&D Beyond stores RESULTS (modifiers like ``bonus/charisma-score``,
``proficiency/athletics``, chosen option labels, feat and spell lists), while
our builds store DECISIONS. So the importer:

1. maps species / background / classes / subclasses by name,
2. builds the level list (starting class first; D&D Beyond doesn't keep
   multiclass order) and spreads the recorded hit points over the levels,
3. repeatedly computes the build and fills each outstanding choice from the
   D&D Beyond results that fit it, until nothing more can be filled,
4. copies inventory, coins, current HP, death saves, conditions, spell-slot
   use, notes and description,
5. reports what it couldn't map and where our computed numbers differ from
   what D&D Beyond implies, so the player knows exactly what to check.

Nothing here touches storage; the route saves the returned document.
"""
from __future__ import annotations

import json
import re

from systems.dnd5e import content as C
from systems.dnd5e.ids import entity_id
from systems.dnd5e.rules.character import compute
from systems.dnd5e.rules.tables import ABILITIES, CONDITIONS, SKILLS

STAT_IDS = {1: 'str', 2: 'dex', 3: 'con', 4: 'int', 5: 'wis', 6: 'cha'}
ABIL_WORD = {'strength': 'str', 'dexterity': 'dex', 'constitution': 'con', 'intelligence': 'int',
             'wisdom': 'wis', 'charisma': 'cha'}
# D&D Beyond condition ids (alphabetical, exhaustion carries a level).
DDB_CONDITIONS = {1: 'blinded', 2: 'charmed', 3: 'deafened', 4: 'exhaustion', 5: 'frightened',
                  6: 'grappled', 7: 'incapacitated', 8: 'invisible', 9: 'paralyzed', 10: 'petrified',
                  11: 'poisoned', 12: 'prone', 13: 'restrained', 14: 'stunned', 15: 'unconscious'}
# D&D Beyond counts single pieces of ammunition; 5etools sells bundles of 20.
AMMO = {'Arrows': ['Arrows (20)'], 'Arrow': ['Arrows (20)'],
        'Crossbow Bolts': ['Bolts (20)', 'Crossbow Bolts (20)'], 'Crossbow Bolt': ['Bolts (20)', 'Crossbow Bolts (20)'],
        'Bolts': ['Bolts (20)', 'Crossbow Bolts (20)'],
        'Sling Bullets': ['Sling Bullets (20)', 'Firearm Bullets (10)'], 'Blowgun Needles': ['Needles (50)', 'Blowgun Needles (50)']}
ALIGNMENTS = {1: 'Lawful Good', 2: 'Neutral Good', 3: 'Chaotic Good', 4: 'Lawful Neutral', 5: 'Neutral',
              6: 'Chaotic Neutral', 7: 'Lawful Evil', 8: 'Neutral Evil', 9: 'Chaotic Evil'}
CORE = {'one': ('XPHB', 'XDMG', 'XMM'), 'classic': ('PHB', 'DMG', 'MM')}


class ImportError_(ValueError):
    pass


def _norm(s) -> str:
    s = str(s or '').lower().replace('’', "'")
    return re.sub(r'[^a-z0-9+\']+', ' ', s).strip()


# --- lookups --------------------------------------------------------------------

def _by_name(kind: str, name: str, edition: str, extra_names=(), prefer_edition=False) -> dict | None:
    """Best record of ``kind`` named ``name`` (or one of ``extra_names``),
    preferring this edition's core books, then this edition, then anything."""
    wanted = [_norm(name)] + [_norm(n) for n in extra_names]
    rows = [r for r in C.list_summaries(kind) if _norm(r['name']) in wanted]
    if not rows:
        return None
    core = CORE[edition]

    def rank(r):
        by_book = (0 if r['source'] in core else 1, 0 if r['edition'] == edition else 1)
        by_name = wanted.index(_norm(r['name']))
        # Items: the 2024 "Bolts (20)" beats the 2014 "Crossbow Bolts (20)" for a
        # 2024 character, even though the 2014 spelling matched D&D Beyond's name.
        return (by_book + (by_name,) if prefer_edition else (by_name,) + by_book) + (0 if r['srd'] else 1,)
    rows.sort(key=rank)
    return C.get(kind, rows[0]['id'])


def _race_candidates(race: dict) -> list:
    full = race.get('fullName') or ''
    base = race.get('baseRaceName') or race.get('baseName') or ''
    sub = race.get('subRaceShortName') or ''
    names = [full]
    if sub:
        names += [f'{base} ({sub})', f'{base}; {sub} Lineage', f'{base}; {sub}']
    # "Hill Dwarf" -> "Dwarf (Hill)"
    m = re.match(r'^(\w+(?:-\w+)?) (\w+(?:-\w+)?)$', full)
    if m:
        names += [f'{m.group(2)} ({m.group(1)})', f'{m.group(2)}; {m.group(1)} Lineage']
    names.append(base)
    return [n for n in dict.fromkeys(names) if n]


def _item_name_variants(name: str) -> list:
    """D&D Beyond writes "Lantern, Hooded" / "Rope, Hempen (50 feet)"; 5etools
    writes "Hooded Lantern" / "Hempen Rope (50 feet)"."""
    out = [name]
    m = re.match(r'^([^,(]+),\s*([^(]+?)(\s*\(.*\))?$', name)
    if m and not re.match(r'^\s*\+\d', m.group(2)):
        out.append(f'{m.group(2).strip()} {m.group(1).strip()}{m.group(3) or ""}')
    m = re.match(r'^(.*), (\+\d)$', name)                 # "Wand of the War Mage, +1"
    if m:
        out += [f'{m.group(2)} {m.group(1)}', f'{m.group(1)} {m.group(2)}']
    m = re.match(r'^Potion of (\w+) \((Greater|Superior|Supreme)\)$', name)   # "Potion of Healing (Greater)"
    if m:
        out.append(f'Potion of {m.group(2)} {m.group(1)}')
    m = re.match(r'^(Ioun Stone) of (.+)$', name)
    if m:
        out.append(f'{m.group(1)}, {m.group(2)}')
    if name in AMMO:
        out += AMMO[name]
    if name.lower().startswith('clothes, ') or name.lower().startswith('clothing, '):
        out.append(name.split(', ', 1)[1].title() + ' Clothes')
    return out


# --- D&D Beyond result sets ---------------------------------------------------------

class _Ddb:
    def __init__(self, c: dict):
        self.c = c
        mods = c.get('modifiers') or {}
        self.mods = {k: [m for m in (v or []) if isinstance(m, dict)] for k, v in mods.items()}
        self.labels = {}
        for d in ((c.get('choices') or {}).get('choiceDefinitions') or []):
            for o in d.get('options') or []:
                self.labels[o.get('id')] = o.get('label')
        self.chosen = {}          # group -> [label]
        for grp, lst in (c.get('choices') or {}).items():
            if not isinstance(lst, list) or grp in ('choiceDefinitions',):
                continue
            for ch in lst:
                if isinstance(ch, dict) and ch.get('optionValue') is not None:
                    lbl = self.labels.get(ch['optionValue'])
                    if lbl:
                        self.chosen.setdefault(grp, []).append(lbl)
        self.option_names = [o['definition']['name'] for grp in (c.get('options') or {}).values()
                             for o in (grp or []) if isinstance(o, dict) and o.get('definition')]
        self.feats = [f['definition']['name'] for f in c.get('feats') or [] if f.get('definition')]

    def bonus_by_group(self, group: str) -> dict:
        out = {}
        for m in self.mods.get(group, []):
            if m.get('type') == 'bonus' and str(m.get('subType', '')).endswith('-score'):
                ab = ABIL_WORD.get(m['subType'][:-6])
                if ab and m.get('value'):
                    out[ab] = out.get(ab, 0) + int(m['value'])
        return out

    def proficiencies(self, group: str | None = None) -> dict:
        """kind -> set of normalized names, for one origin group (race /
        background / class / feat / item) or every origin when ``group`` is None."""
        out = {'skill': set(), 'expertise': set(), 'language': set(), 'tool': set(), 'save': set()}
        groups = [self.mods.get(group, [])] if group else list(self.mods.values())
        for grp in groups:
            for m in grp:
                t, st = m.get('type'), str(m.get('subType') or '')
                name = st.replace('-', ' ')
                if t == 'proficiency':
                    if name in SKILLS:
                        out['skill'].add(name)
                    elif st.endswith('-saving-throws'):
                        out['save'].add(ABIL_WORD.get(st.split('-')[0], ''))
                    else:
                        out['tool'].add(_norm(m.get('friendlySubtypeName') or name))
                elif t == 'expertise' and name in SKILLS:
                    out['expertise'].add(name)
                elif t == 'language':
                    out['language'].add(_norm(m.get('friendlySubtypeName') or name))
        for grp, lbls in self.chosen.items():
            if group and grp != group:
                continue
            for lb in lbls:
                n = _norm(lb)
                if n in SKILLS:
                    out['skill'].add(n)
        return out

    def options_for(self, group: str | None) -> list:
        c = self.c.get('options') or {}
        grps = [group] if group else list(c)
        names = [o['definition']['name'] for g in grps for o in (c.get(g) or [])
                 if isinstance(o, dict) and o.get('definition')]
        return names + [x for g, v in self.chosen.items() if not group or g == group for x in v]

    def all_labels(self) -> set:
        return {_norm(x) for lbls in self.chosen.values() for x in lbls} | \
               {_norm(x) for x in self.option_names} | {_norm(x) for x in self.feats}


# --- main entry ----------------------------------------------------------------------

def parse(raw) -> dict:
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise ImportError_('That is not valid JSON.') from None
    if not isinstance(raw, dict):
        raise ImportError_('Expected a D&D Beyond character JSON object.')
    c = raw.get('data') if isinstance(raw.get('data'), dict) else raw
    if not isinstance(c.get('classes'), list) or not isinstance(c.get('stats'), list):
        raise ImportError_('This does not look like a D&D Beyond character (no classes / stats).')
    return c


_DDB_URL = re.compile(r'^(?:https?://(?:www\.)?dndbeyond\.com/(?:profile/[^/]+/)?characters/)?(\d{3,12})(?:[/?#].*)?$')
CHARACTER_SERVICE = 'https://character-service.dndbeyond.com/character/v5/character/'


def fetch_public(url_or_id: str, *, timeout: float = 10.0, max_bytes: int = 8_000_000) -> dict:
    """Fetch a PUBLIC D&D Beyond character. Only the character id is taken
    from the input; the request always goes to D&D Beyond's own character
    service (never to a URL the caller chose)."""
    import urllib.error
    import urllib.request
    m = _DDB_URL.match((url_or_id or '').strip())
    if not m:
        raise ImportError_('Give a D&D Beyond character link like https://www.dndbeyond.com/characters/12345678.')
    req = urllib.request.Request(CHARACTER_SERVICE + m.group(1),
                                 headers={'Accept': 'application/json', 'User-Agent': 'gm-table-dnd5e-import'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:      # noqa: S310 -- fixed https host
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            raise ImportError_('D&D Beyond would not share that character. Make it public (Character '
                               'Settings > Privacy: Public) or download its JSON and upload it instead.') from None
        raise ImportError_(f'D&D Beyond answered {e.code}; try uploading the JSON instead.') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ImportError_('Could not reach D&D Beyond; try uploading the JSON instead.') from None
    if len(body) > max_bytes:
        raise ImportError_('That response is too large to be a character.')
    try:
        data = json.loads(body.decode('utf-8'))
    except ValueError:
        raise ImportError_('D&D Beyond did not return character data.') from None
    if isinstance(data, dict) and data.get('success') is False:
        raise ImportError_('D&D Beyond would not share that character. Make it public or upload its JSON.')
    return data


def detect_edition(c: dict) -> str:
    """2024 or 2014 rules. Every 2024 class has an "Epic Boon" feature (level
    19) and D&D Beyond lists all of a class's features in its definition, so
    that is the signal. (``isLegacy`` is not usable: older 2014 characters
    carry ``false``.) The import form lets the player override this."""
    for cl in c.get('classes') or []:
        names = {f.get('name') for f in (cl.get('definition') or {}).get('classFeatures') or []}
        if 'Epic Boon' in names:
            return 'one'
    return 'classic'


def import_character(raw, *, edition: str | None = None) -> dict:
    """Return {'name', 'build', 'state', 'details', 'report'}."""
    c = parse(raw)
    ed = edition if edition in ('one', 'classic') else detect_edition(c)
    ddb = _Ddb(c)
    report = {'edition': ed, 'mapped': [], 'unmatched': [], 'warnings': [], 'mismatches': []}

    # ---- species / background ----
    race_rec = None
    race = c.get('race') or {}
    if race:
        cands = _race_candidates(race)
        race_rec = _by_name('race', cands[0], ed, cands[1:])
        if race_rec:
            report['mapped'].append(f'Species: {race.get("fullName")} → {race_rec["name"]} ({race_rec["source"]})')
        else:
            report['unmatched'].append(f'Species "{race.get("fullName")}"')
    bg_rec = None
    bg = c.get('background') or {}
    bg_name = (bg.get('definition') or {}).get('name') or (bg.get('customBackground') or {}).get('name')
    if bg_name:
        bg_rec = _by_name('background', bg_name, ed)
        if bg_rec:
            report['mapped'].append(f'Background: {bg_name} → {bg_rec["name"]} ({bg_rec["source"]})')
        else:
            report['unmatched'].append(f'Background "{bg_name}" (custom backgrounds are not imported)')

    # ---- classes / levels / hit points ----
    classes = sorted(c.get('classes') or [], key=lambda x: (not x.get('isStartingClass'), -int(x.get('level') or 0)))
    levels, class_map, sub_picks = [], {}, {}
    for cl in classes:
        d = cl.get('definition') or {}
        rec = _by_name('class', d.get('name'), ed)
        if rec is None:
            report['unmatched'].append(f'Class "{d.get("name")}" ({cl.get("level")} levels skipped)')
            continue
        cid = entity_id(rec['name'], rec['source'])
        class_map[cl.get('id')] = cid
        sd = cl.get('subclassDefinition') or {}
        if sd.get('id'):
            class_map[sd['id']] = cid               # subclass always-prepared spells use this id
        report['mapped'].append(f'Class: {d.get("name")} {cl.get("level")} → {rec["name"]} ({rec["source"]})')
        for _ in range(int(cl.get('level') or 0)):
            levels.append({'class': cid, 'hp': None})
        if sd.get('name'):
            sub = _match_subclass(rec, sd['name'])
            if sub:
                sub_picks[f'subclass:{cid}'] = [sub]
            else:
                report['unmatched'].append(f'Subclass "{sd["name"]}" for {rec["name"]}')
    levels = levels[:20]
    _spread_hp(levels, int(c.get('baseHitPoints') or 0), report)
    if c.get('overrideHitPoints'):
        report['warnings'].append(f'D&D Beyond had a maximum HP override ({c["overrideHitPoints"]}); '
                                  'it is not imported.')

    # ---- base scores ----
    base = {STAT_IDS[s['id']]: int(s.get('value') or 10) for s in c.get('stats') or [] if s.get('id') in STAT_IDS}
    for s in c.get('overrideStats') or []:
        if s.get('value') and s.get('id') in STAT_IDS:
            report['warnings'].append(f'{STAT_IDS[s["id"]].upper()} had a score override ({s["value"]}) on '
                                      'D&D Beyond; set that score by hand if you still want it.')
    for s in c.get('bonusStats') or []:
        if s.get('value') and s.get('id') in STAT_IDS:
            report['warnings'].append(f'{STAT_IDS[s["id"]].upper()} had a misc bonus (+{s["value"]}) that is '
                                      'not imported.')

    build = {
        'edition': ed, 'sources': [],
        'race': entity_id(race_rec['name'], race_rec['source']) if race_rec else None,
        'background': entity_id(bg_rec['name'], bg_rec['source']) if bg_rec else None,
        'ability_method': 'manual',
        'base_scores': {a: base.get(a, 10) for a in ABILITIES},
        'optional_class_features': bool((c.get('preferences') or {}).get('enableOptionalClassFeatures')),
        'levels': levels,
        'choices': dict(sub_picks),
    }
    doc = {'name': c.get('name') or 'Imported', 'build': build, 'state': {'inventory': []}}

    _fill_choices(doc, ddb, c, class_map, report)

    # ---- inventory / coins ----
    inv = []
    unmatched_items = []
    for it in c.get('inventory') or []:
        d = it.get('definition') or {}
        nm = d.get('name')
        if not nm:
            continue
        variants = _item_name_variants(nm)
        rec = _by_name('item', variants[0], ed, variants[1:], prefer_edition=True)
        qty = max(1, int(it.get('quantity') or 1))
        if rec and nm in AMMO and rec['name'] in AMMO[nm]:
            bundle = int(re.search(r'\((\d+)\)', rec['name']).group(1))
            qty = max(1, -(-qty // bundle))
        ent = {'qty': qty, 'equipped': bool(it.get('equipped')), 'attuned': bool(it.get('isAttuned'))}
        if rec:
            ent['id'] = entity_id(rec['name'], rec['source'])
        else:
            ent['name'] = nm[:80]
            ent['weight'] = float(d.get('weight') or 0)
            unmatched_items.append(nm)
        inv.append(ent)
    for it in c.get('customItems') or []:
        if it.get('name') and int(it.get('quantity') or 0) > 0:
            inv.append({'name': str(it['name'])[:80], 'qty': int(it['quantity']), 'weight': float(it.get('weight') or 0)})
    if unmatched_items:
        report['warnings'].append(f'{len(unmatched_items)} item(s) kept as plain named items (no rules match): '
                                  + ', '.join(unmatched_items[:12]) + ('...' if len(unmatched_items) > 12 else ''))
    state = doc['state']
    state['inventory'] = inv
    cur = c.get('currencies') or {}
    state['currency'] = {k: int(cur.get(k) or 0) for k in ('cp', 'sp', 'ep', 'gp', 'pp')}
    state['starting_equipment_taken'] = True
    state['xp'] = int(c.get('currentXp') or 0)
    state['inspiration'] = bool(c.get('inspiration'))

    # ---- live state (needs the computed maximum) ----
    s = compute(doc)
    hp_max = s['hp_max']
    state['hp_max'] = hp_max
    state['hp'] = max(0, hp_max - int(c.get('removedHitPoints') or 0))
    state['temp_hp'] = int(c.get('temporaryHitPoints') or 0)
    ds = c.get('deathSaves') or {}
    state['death_saves'] = {'success': int(ds.get('successCount') or 0), 'failure': int(ds.get('failCount') or 0)}
    state['stable'] = bool(ds.get('isStabilized'))
    conds = {}
    for cd in c.get('conditions') or []:
        name = DDB_CONDITIONS.get(cd.get('id'))
        if name == 'exhaustion':
            state['exhaustion'] = int(cd.get('level') or 1)
        elif name in CONDITIONS:
            conds[name] = {}
    state['conditions'] = conds
    used = {}
    for sl in c.get('spellSlots') or []:
        if sl.get('used'):
            used[str(sl['level'])] = int(sl['used'])
    state['slots_used'] = used
    state['pact_used'] = sum(int(p.get('used') or 0) for p in c.get('pactMagic') or [])
    notes = c.get('notes') or {}
    state['notes'] = {'allies': notes.get('allies') or '', 'organizations': notes.get('organizations') or '',
                      'enemies': notes.get('enemies') or '',
                      'other': '\n\n'.join(x for x in (notes.get('otherNotes'), notes.get('personalPossessions'),
                                                       notes.get('otherHoldings')) if x)}
    traits = c.get('traits') or {}
    details = {
        'alignment': ALIGNMENTS.get(c.get('alignmentId'), ''), 'faith': c.get('faith') or '',
        'age': str(c.get('age') or ''), 'height': c.get('height') or '', 'weight': str(c.get('weight') or ''),
        'eyes': c.get('eyes') or '', 'hair': c.get('hair') or '', 'skin': c.get('skin') or '',
        'gender': c.get('gender') or '', 'personality': traits.get('personalityTraits') or '',
        'ideals': traits.get('ideals') or '', 'bonds': traits.get('bonds') or '', 'flaws': traits.get('flaws') or '',
        'appearance': traits.get('appearance') or '', 'backstory': notes.get('backstory') or '',
    }
    details = {k: str(v)[:4000] for k, v in details.items() if v}

    _compare(s, c, ddb, report)
    missed = _ddb_spells(c, class_map)['missed']
    if missed:
        report['warnings'].append('Spells with no rules match (homebrew?): ' + ', '.join(missed[:15])
                                  + ('...' if len(missed) > 15 else ''))
    # What D&D Beyond had that this build couldn't place, so the player can
    # decide (a homebrew subclass's feat, a language the option list excludes).
    have_feats = {_norm(f['name']) for f in s['feats']}
    for f in ddb.feats:
        if _norm(f) not in have_feats:
            report['warnings'].append(f'Feat "{f}" was on D&D Beyond but has no slot here '
                                      '(it may come from homebrew); add it on the Choices step if it belongs.')
    final = {k: set(v) for k, v in s['proficiencies'].items()}
    allp = ddb.proficiencies()
    for kind, key in (('skill', 'skills'), ('language', 'languages')):
        # Druidic / Thieves' Cant are class features here, not language entries
        missing = sorted(x for x in allp[kind] if x and x not in final[key]
                         and x not in ('druidic', "thieves' cant", 'thieves cant'))
        if missing:
            report['warnings'].append(f'D&D Beyond also had {kind} proficiency in: ' + ', '.join(missing))
    report['pending'] = [ch['label'] for ch in s['choices'] if not ch['complete']]
    report['errors'] = s['errors']
    return {'name': doc['name'][:80], 'build': build, 'state': state, 'details': details, 'report': report,
            'ddb_id': c.get('id')}


def _match_subclass(cls_rec: dict, name: str) -> str | None:
    want = _norm(name)
    best = None
    own = 'XPHB' if cls_rec['source'] == 'XPHB' else 'PHB'
    for s in C.subclasses_for(cls_rec['name'], cls_rec['source']):
        if want in (_norm(s['name']), _norm(s.get('shortName'))):
            score = 0 if s['source'] == own else (1 if s['source'] in ('XPHB', 'PHB') else 2)
            if best is None or score < best[0]:
                best = (score, s)
    if best:
        from systems.dnd5e.ids import subclass_id
        return subclass_id(best[1])
    return None


def _spread_hp(levels: list, base_hp: int, report: dict):
    """D&D Beyond keeps one total of hit-die results (no CON). Level 1 is the
    die's maximum; the rest is spread over the other levels (clamped per die)."""
    if len(levels) <= 1 or not base_hp:
        return
    faces = []
    for lv in levels:
        rec = C.get('class', lv['class'])
        faces.append((rec.get('hd') or {}).get('faces', 8))
    rest = base_hp - faces[0]
    n = len(levels) - 1
    if rest < n or rest > sum(faces[1:]):
        report['warnings'].append('Hit points could not be split per level; using averages.')
        return
    for i in range(1, len(levels)):
        left = n - (i - 1)
        share = max(1, min(faces[i], round(rest / left)))
        levels[i]['hp'] = share
        rest -= share


# --- choice filling -----------------------------------------------------------------

def _feat_by_name(name: str, ed: str, categories=None):
    rec = _by_name('feat', name, ed)
    if rec is None:
        # D&D Beyond prints fighting-style feats as "Fighting Style: Defense"? try the tail.
        tail = name.split(':')[-1].strip()
        rec = _by_name('feat', tail, ed)
    if rec and categories and rec.get('category') not in categories:
        return None
    return rec


def _fill_choices(doc: dict, ddb: _Ddb, c: dict, class_map: dict, report: dict):
    ed = doc['build']['edition']
    picks = doc['build']['choices']
    prof = ddb.proficiencies()
    labels = ddb.all_labels()
    feats_left = list(ddb.feats)
    class_asi = ddb.bonus_by_group('class')
    group_bonus = {'race': ddb.bonus_by_group('race'), 'bg': ddb.bonus_by_group('background'),
                   'feat': ddb.bonus_by_group('feat')}
    spells = _ddb_spells(c, class_map)
    tried = set()

    asi_plan = {'left': sum(1 for ch in compute(doc)['choices'] if ch['kind'] == 'asi' and not ch['complete'])}
    for _pass in range(12):
        s = compute(doc)
        have_feats = {_norm(f['name']) for f in s['feats']}
        feats_left = [f for f in feats_left if _norm(f) not in have_feats]
        progress = False
        for ch_id in [x['id'] for x in s['choices']]:
            ch = next((x for x in s['choices'] if x['id'] == ch_id), None)
            if ch is None or (ch['id'], _pass) in tried:
                continue
            # Prepared spells are an optional choice (complete while empty), but
            # D&D Beyond's prepared list should still come across.
            optional_empty = not ch['required'] and not ch.get('value')
            if ch['complete'] and not optional_empty:
                continue
            tried.add((ch['id'], _pass))
            asi_plan['left'] = sum(1 for x in s['choices'] if x['kind'] == 'asi' and not x['complete'])
            placed = {_norm(x['name']) for x in s['feats']}
            feats_left = [x for x in ddb.feats if _norm(x) not in placed]
            val = _value_for(ch, s, ddb, prof, labels, feats_left, class_asi, group_bonus, spells, ed, doc,
                             asi_plan)
            if val is not None and val != [] and val != picks.get(ch['id']):
                old = picks.get(ch['id'], None)
                picks[ch['id']] = val
                t = compute(doc)
                if t['errors'] and not s['errors']:
                    # never introduce an invalid build; leave this one for the player
                    if old is None:
                        picks.pop(ch['id'], None)
                    else:
                        picks[ch['id']] = old
                    continue
                progress = True
                s = t                                     # later picks see this one
                if ch['kind'] == 'asi' and isinstance(val, dict) and val.get('asi'):
                    for a, n in val['asi'].items():
                        class_asi[a] = class_asi.get(a, 0) - n
        if not progress:
            break


_FEAT_NESTED = re.compile(r'^(asi:[^:]+:\d+:|featprog:[^:]+:[^:]+:\d+:|(race|bg):feat:[^:]+:)')


def _group_of(cid: str):
    """Which D&D Beyond modifier group answers this choice."""
    if _FEAT_NESTED.match(cid):
        return 'feat'
    if cid.startswith('race:'):
        return 'race'
    if cid.startswith('bg:'):
        return 'background'
    if cid.startswith('origin:'):
        return None                      # 2024 languages: any origin
    return 'class'


def _value_for(ch, s, ddb, prof, labels, feats_left, class_asi, group_bonus, spells, ed, doc, asi_plan=None):
    kind = ch['kind']
    opts = [o['value'] if isinstance(o, dict) else o for o in ch.get('options') or []]
    count = ch.get('count') or 1
    cur = doc['build']['choices'].get(ch['id']) or []
    cur = cur if isinstance(cur, list) else [cur]
    grp = _group_of(ch['id'])
    if kind in ('skill', 'expertise', 'language', 'tool', 'save'):
        pool = ddb.proficiencies(grp).get(kind, set()) or (prof.get(kind, set()) if grp is None else set())
        have = set(s['proficiencies'].get({'skill': 'skills', 'expertise': 'expertise', 'language': 'languages',
                                           'tool': 'tools', 'save': 'saves'}[kind], []))
        out = [str(x) for x in cur]
        for o in opts:
            if len(out) >= count:
                break
            if _norm(o) in pool and o not in have and o not in out:
                out.append(o)
        return out if len(out) > len(cur) else None
    if kind == 'skill_tool_language':
        pool = prof['skill'] | prof['tool']
        out = [o for o in opts if _norm(o) in pool and o not in s['proficiencies']['skills']][:count]
        return out or None
    if kind == 'ability':
        origin = 'race' if ch['id'].startswith('race:') else ('bg' if ch['id'].startswith('bg:') else 'feat')
        target = dict(group_bonus.get(origin) or {})
        # what fixed (non-choice) bonuses from this origin already cover
        weights = sorted(ch.get('weights') or [], reverse=True)
        out = {}
        for w in weights:
            cand = [a for a in ch['options'] if a not in out and target.get(a, 0) >= w]
            if not cand and origin == 'feat':
                cand = [a for a in ch['options'] if a not in out and class_asi.get(a, 0) + target.get(a, 0) >= w]
            if not cand:
                return None
            best = max(cand, key=lambda a: target.get(a, 0))
            out[best] = w
            target[best] = target.get(best, 0) - w
        return out
    if kind == 'alternative':
        # choose the alternative whose label mentions what D&D Beyond chose
        for o in ch['options']:
            lbl = _norm(o['label'])
            if any(x and x in lbl for x in labels):
                return [o['value']]
        if 'ability' in ch['label'] or 'ability' in ch['id']:
            return _best_ability_alt(ch, s, doc, group_bonus)
        return None
    if kind == 'subclass':
        return None
    if kind == 'feat':
        cats = (ch.get('query') or {}).get('category')
        cats = [cats] if isinstance(cats, str) else cats
        for f in feats_left:
            rec = _feat_by_name(f, ed, cats)
            if rec:
                return [entity_id(rec['name'], rec['source'])]
        # fighting styles appear as option labels in some exports
        for lb in ddb.option_names + [x for v in ddb.chosen.values() for x in v]:
            rec = _feat_by_name(lb, ed, cats)
            if rec and cats:
                return [entity_id(rec['name'], rec['source'])]
        return None
    if kind == 'asi':
        # D&D Beyond's class "bonus <ability>-score" modifiers are the ASI picks.
        # Fill slots with them while there are enough points for every slot
        # left; only then use a slot for a feat. (A feat on the sheet can come
        # from somewhere else entirely, e.g. a subclass.)
        points = sum(n for n in class_asi.values() if n > 0)
        slots_left = asi_plan['left'] if asi_plan else 1
        pool = sorted(((a, n) for a, n in class_asi.items() if n > 0), key=lambda x: -x[1])
        if points >= 2 * slots_left and pool:
            if pool[0][1] >= 2:
                return {'asi': {pool[0][0]: 2}}
            if len(pool) >= 2:
                return {'asi': {pool[0][0]: 1, pool[1][0]: 1}}
        # An ASI slot can hold a general feat (2014: any uncategorised feat;
        # 2024: General or Origin -- the 2024 ASI feature allows "another feat
        # for which you qualify", origin feats included). Fighting Style and
        # Epic Boon feats come from their own slots and are left for those.
        for f in list(feats_left):
            rec = _feat_by_name(f, ed)
            if rec and rec.get('category') in (None, 'G', 'O'):
                return {'feat': entity_id(rec['name'], rec['source'])}
        pool = sorted(((a, n) for a, n in class_asi.items() if n > 0), key=lambda x: -x[1])
        if pool and pool[0][1] >= 2:
            return {'asi': {pool[0][0]: 2}}
        if len(pool) >= 2:
            return {'asi': {pool[0][0]: 1, pool[1][0]: 1}}
        return None
    if kind in ('optionalfeature', 'feature_option', 'weapon_mastery', 'size'):
        if kind == 'optionalfeature':
            types = set((ch.get('query') or {}).get('featureType') or [])
            names = set()
            for n in ddb.options_for(grp):
                rec = _by_name('optionalfeature', n, ed)
                if rec and types & set(rec.get('featureType') or []):
                    names.add(entity_id(rec['name'], rec['source']))
            taken = {o['id'] for o in s['optional_features']}
            out = [x for x in cur] + [x for x in sorted(names) if x not in cur and x not in taken]
            return out[:count] or None
        if kind == 'size':
            return ['M'] if 'M' in opts else None
        out = [o for o in opts if _norm(o) in labels or _norm(str(o).split(' (')[0]) in labels][:count]
        return out or None
    if kind == 'spell':
        q = ch.get('query') or {}
        key = ch['id']
        pool = []
        if key.startswith('spells:'):
            caster_key = key.split(':')[1]
            src = spells['by_caster'].get(caster_key) or spells['by_class_name'].get(_class_name_of(caster_key), [])
            levels = q.get('levels') or []
            if key.endswith(':prepared'):
                pool = [x for x in src if x['level'] in levels and (x['prepared'] or x['known_caster'])]
                if q.get('from_choice'):
                    book = set(doc['build']['choices'].get(q['from_choice']) or [])
                    pool = [x for x in pool if x['id'] in book and x['prepared']]
            elif key.endswith(':known'):
                pool = [x for x in src if x['level'] in levels]
            else:
                pool = [x for x in src if x['level'] == 0]
        else:
            pool = spells['other']
        ids = []
        for x in pool:
            if x['id'] and x['id'] not in ids:
                ids.append(x['id'])
        return ids[:count] or None
    return None


def _class_name_of(caster_key: str) -> str:
    return caster_key.split('|')[0]


def _best_ability_alt(ch, s, doc, group_bonus):
    origin = 'race' if ch['id'].startswith('race:') else 'bg'
    target = group_bonus.get(origin) or {}
    total = sum(target.values())
    for o in ch['options']:
        lbl = o['label']
        nums = [int(x) for x in re.findall(r'\+(\d)', lbl)]
        if nums and sum(nums) == total and sorted(nums, reverse=True) == sorted(target.values(), reverse=True):
            return [o['value']]
    return None


def _ddb_spells(c: dict, class_map: dict) -> dict:
    """D&D Beyond spells by our caster key (class id) plus a pool for
    species/feat spell choices."""
    by_caster: dict = {}
    by_class_name: dict = {}
    missed: list = []
    for cs in c.get('classSpells') or []:
        cid = class_map.get(cs.get('characterClassId'))
        for sp in cs.get('spells') or []:
            d = sp.get('definition') or {}
            rec = _spell_rec(d.get('name'), cid)
            if rec is None:
                missed.append(d.get('name'))
                continue
            row = {'id': entity_id(rec['name'], rec['source']), 'level': rec.get('level', 0),
                   'prepared': bool(sp.get('prepared')) and not sp.get('alwaysPrepared'),
                   'known_caster': bool(sp.get('countsAsKnownSpell')) and not sp.get('alwaysPrepared')}
            if cid:
                by_caster.setdefault(cid, []).append(row)
                by_class_name.setdefault(cid.split('|')[0], []).append(row)
    other = []
    for grp in ('race', 'feat', 'background'):
        for sp in (c.get('spells') or {}).get(grp) or []:
            d = sp.get('definition') or {}
            rec = _spell_rec(d.get('name'), None)
            if rec:
                other.append({'id': entity_id(rec['name'], rec['source']), 'level': rec.get('level', 0)})
            elif d.get('name'):
                missed.append(d['name'])
    return {'by_caster': by_caster, 'by_class_name': by_class_name, 'other': other,
            'missed': sorted(set(missed))}


def _spell_rec(name, class_id):
    if not name:
        return None
    name = re.sub(r'\s*\[[A-Z]+\]\s*$', '', str(name)).strip()      # "Find Familiar [R]"
    ed = 'one' if class_id and class_id.endswith('|xphb') else 'classic'
    return _by_name('spell', name, ed)


# --- report ----------------------------------------------------------------------------

def _compare(s: dict, c: dict, ddb: _Ddb, report: dict):
    """Flag where our numbers differ from what D&D Beyond's data implies."""
    expect = {STAT_IDS[x['id']]: int(x.get('value') or 10) for x in c.get('stats') or [] if x.get('id') in STAT_IDS}
    for grp in ('race', 'background', 'class', 'feat'):
        for a, n in ddb.bonus_by_group(grp).items():
            expect[a] = expect.get(a, 10) + n
    for x in c.get('overrideStats') or []:
        if x.get('value') and x.get('id') in STAT_IDS:
            expect[STAT_IDS[x['id']]] = int(x['value'])
    for a in ABILITIES:
        ours = s['abilities'][a]['score']
        if a in expect and min(expect[a], 20) != ours and expect[a] <= 30:
            report['mismatches'].append(f'{a.upper()}: D&D Beyond {expect[a]}, here {ours}')
    level = s['level']
    con = s['abilities']['con']['mod']
    ddb_max = int(c.get('baseHitPoints') or 0) + con * level + int(c.get('bonusHitPoints') or 0)
    if c.get('overrideHitPoints'):
        ddb_max = int(c['overrideHitPoints'])
    if ddb_max and ddb_max != s['hp_max']:
        report['mismatches'].append(f'Max HP: D&D Beyond about {ddb_max} (before feat/species bonuses), '
                                    f'here {s["hp_max"]}')
