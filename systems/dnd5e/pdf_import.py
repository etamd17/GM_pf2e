"""Import a filled 5e character-sheet PDF.

Handles D&D Beyond's "Export to PDF" sheets and the official fillable 5e sheet
(and anything else using the same field names). D&D Beyond's export is
flattened: there is no form dictionary, but every value is still on a page
widget annotation (``/T`` name, ``/V`` value), so both sources are read.

A PDF records RESULTS (final scores, skill totals, max HP), not choices. The
PDF is turned into the same shape the D&D Beyond JSON importer reads, run
through it (name matching, choice filling, report), and then fitted: base
scores are solved so the computed scores equal the sheet's, and per-level hit
points are spread so the computed maximum equals the sheet's.
"""
from __future__ import annotations

import io
import itertools
import re

from systems.dnd5e import content as C
from systems.dnd5e import ddb_import as D
from systems.dnd5e.rules.character import compute
from systems.dnd5e.rules.tables import ABILITIES, SKILLS, ability_mod

ABBR = {'STR': 'str', 'DEX': 'dex', 'CON': 'con', 'INT': 'int', 'WIS': 'wis', 'CHA': 'cha'}
SAVE_FIELDS = {'st strength': 'str', 'st dexterity': 'dex', 'st constitution': 'con',
               'st intelligence': 'int', 'st wisdom': 'wis', 'st charisma': 'cha'}
SKILL_FIELDS = {'acrobatics': 'acrobatics', 'animal': 'animal handling', 'arcana': 'arcana',
                'athletics': 'athletics', 'deception': 'deception', 'history': 'history',
                'insight': 'insight', 'intimidation': 'intimidation', 'investigation': 'investigation',
                'medicine': 'medicine', 'nature': 'nature', 'perception': 'perception',
                'performance': 'performance', 'persuasion': 'persuasion', 'religion': 'religion',
                'sleightofhand': 'sleight of hand', 'stealth': 'stealth', 'survival': 'survival'}
MAX_BYTES = 20_000_000


class PdfImportError(ValueError):
    pass


# --- reading -----------------------------------------------------------------------

def read_fields(data: bytes) -> dict:
    """Every named field's text value (lower-cased, trimmed names)."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    if not data or len(data) > MAX_BYTES:
        raise PdfImportError('That file is empty or too large to be a character sheet.')
    if not data.lstrip().startswith(b'%PDF'):
        raise PdfImportError('That is not a PDF.')
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            try:
                r.decrypt('')
            except Exception:
                raise PdfImportError('That PDF is password-protected.') from None
        out = {}
        for name, f in (r.get_fields() or {}).items():
            v = f.get('/V') if isinstance(f, dict) else None
            if v is not None:
                out.setdefault(_key(name), _text(v))
        for p in r.pages[:40]:
            annots = p.get('/Annots')
            if annots is None:
                continue
            for a in annots.get_object():
                a = a.get_object()
                if a.get('/Subtype') != '/Widget':
                    continue
                t, v = a.get('/T'), a.get('/V')
                if t is None or v is None:
                    continue
                k = _key(t)
                if k not in out or not out[k]:
                    out[k] = _text(v)
    except PdfImportError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError):
        raise PdfImportError('That PDF could not be read.') from None
    if not out:
        raise PdfImportError('That PDF has no form fields. Upload a fillable or D&D Beyond-exported sheet.')
    return out


def _key(name) -> str:
    return re.sub(r'\s+', ' ', str(name)).strip().lower()


def _text(v) -> str:
    s = str(v)
    if s in ('/Off', '/0'):
        return ''
    if s.startswith('/'):
        return s[1:]
    # D&D Beyond writes a curly apostrophe that pypdf can't map ("Monk�s
    # Focus"); between letters it can only be an apostrophe.
    s = re.sub(r'(?<=[A-Za-z])�(?=[A-Za-z])', "'", s)
    return s.strip()


def _int(s, default=None):
    m = re.search(r'-?\d+', str(s or ''))
    return int(m.group(0)) if m else default


def _get(f: dict, *names) -> str:
    for n in names:
        v = f.get(n)
        if v:
            return v
    return ''


# --- interpretation -----------------------------------------------------------------

def _classes(text: str) -> list:
    """"Druid 8 / Monk 2", "Fighter 5 (Battle Master)", "Wizard 3, Cleric 2"."""
    out = []
    for part in re.split(r'[/,]', text or ''):
        m = re.match(r'\s*([A-Za-z][A-Za-z\' -]*?)\s+(\d{1,2})\s*(?:\((.+)\))?\s*$', part.strip())
        if m:
            out.append({'name': m.group(1).strip(), 'level': int(m.group(2)), 'subclass': (m.group(3) or '').strip()})
    return out


def _bullets(text: str) -> dict:
    """Section -> [bullet names] from D&D Beyond's "=== X ===" / "* Name - SRC p" text."""
    sec, out = '', {}
    for line in (text or '').splitlines():
        line = line.strip()
        h = re.match(r'^===\s*(.+?)\s*===$', line)
        if h:
            sec = h.group(1).upper()
            continue
        b = re.match(r'^\*\s*(?:\d+:\s*)?(.+?)\s*(?:[•·�–—-]\s*[A-Za-z][\w-]*(?:\s+\d+)?\s*)?$', line)
        if b and sec:
            out.setdefault(sec, []).append(b.group(1).strip())
            continue
        # "   | Magician •" -- the option picked for the feature above. (Lines
        # like "| Leave Druidic Message: Special" are actions, not options,
        # and carry no bullet.)
        o = re.match(r'^\|\s*([^:|]+?)\s*[•·�]\s*$', line)
        if o and sec:
            out.setdefault('OPTIONS', []).append(o.group(1).strip())
    return out


def _sections(text: str) -> dict:
    sec, out = '', {}
    for line in (text or '').splitlines():
        h = re.match(r'^\s*===\s*(.+?)\s*===\s*$', line)
        if h:
            sec = h.group(1).upper()
            continue
        if sec and line.strip():
            out.setdefault(sec, []).extend(x.strip() for x in line.split(',') if x.strip())
    return out


def _subclass_from_features(cls_name: str, edition: str, feature_names: set) -> str | None:
    rec = D._by_name('class', cls_name, edition)
    if not rec:
        return None
    best = (0, None)
    for s in C.subclasses_for(rec['name'], rec['source']):
        names = set()
        for grp in s.get('subclassFeatures') or []:
            for f in (grp if isinstance(grp, list) else [grp]):
                if isinstance(f, dict) and f.get('name'):
                    names.add(D._norm(f['name']))
                    for e in f.get('entries') or []:
                        if isinstance(e, dict) and e.get('name'):
                            names.add(D._norm(e['name']))
        hit = len(names & feature_names)
        if hit > best[0]:
            best = (hit, s['name'])
    return best[1] if best[0] >= 2 else None


def to_ddb_shape(f: dict) -> dict:
    """The PDF as a D&D Beyond character-service object (only the parts the
    importer reads), plus a ``_pdf`` block with the sheet's totals to fit to."""
    feats_text = '\n'.join(v for k, v in sorted(f.items(), key=lambda kv: (_int(kv[0], 0) or 0))
                           if k.startswith('featurestraits') or k.startswith('features and traits'))
    bullets = _bullets(feats_text)
    feature_names = {D._norm(n) for names in bullets.values() for n in names}
    edition = 'one' if 'PHB-2024' in feats_text or '2024' in _get(f, 'speed') else 'classic'

    cls_text = _get(f, 'class level', 'classlevel', 'class & level', 'class')
    classes = []
    for i, c in enumerate(_classes(cls_text)):
        sub = c['subclass'] or _subclass_from_features(c['name'], edition, feature_names) or ''
        classes.append({'id': 1000 + i, 'level': c['level'], 'isStartingClass': i == 0,
                        'definition': {'name': c['name'], 'classFeatures': [{'name': n} for n in (
                            bullets.get(f'{c["name"].upper()} FEATURES') or [])] + (
                            [{'name': 'Epic Boon'}] if edition == 'one' else [])},
                        'subclassDefinition': {'name': sub} if sub else None})
    totals = {}
    for ab in ABBR:
        v = _int(f.get(ab.lower()))
        if v:
            totals[ABBR[ab]] = v
    pb = _int(_get(f, 'profbonus', 'proficiency bonus'), 2)

    # proficiencies: explicit markers (D&D Beyond: P / E), else from totals
    mods = []
    # When the sheet has proficiency markers, trust only them: a total can
    # carry other bonuses (Magician's +WIS to Nature, Jack of All Trades...).
    def mark_key(fld, sk):                      # "animalhandlingprof" for the "animal" total
        return next((k for k in (fld + 'prof', sk.replace(' ', '') + 'prof') if k in f), None)

    has_marks = any(mark_key(fld, sk) for fld, sk in SKILL_FIELDS.items())
    for fld, sk in SKILL_FIELDS.items():
        mk = mark_key(fld, sk)
        mark = ((f.get(mk) or '') if mk else '').strip().upper()
        total = _int(f.get(fld))
        ab = SKILLS[sk]
        bonus = (total - ability_mod(totals.get(ab, 10))) if total is not None and ab in totals else None
        if has_marks:
            bonus = None
        expert = mark == 'E' or (bonus is not None and bonus >= 2 * pb)
        prof = expert or mark == 'P' or (bonus is not None and pb <= bonus < 2 * pb)
        sub = sk.replace(' ', '-')
        if prof:
            mods.append({'type': 'proficiency', 'subType': sub, 'friendlySubtypeName': sk.title()})
        if expert:
            mods.append({'type': 'expertise', 'subType': sub, 'friendlySubtypeName': sk.title()})
    for fld, ab in SAVE_FIELDS.items():
        total = _int(f.get(fld))
        if total is not None and ab in totals and total - ability_mod(totals[ab]) >= pb:
            word = {v: k for k, v in D.ABIL_WORD.items()}[ab]
            mods.append({'type': 'proficiency', 'subType': f'{word}-saving-throws'})
    sections = _sections(_get(f, 'proficiencieslang'))
    for lang in sections.get('LANGUAGES', []):
        mods.append({'type': 'language', 'subType': D._norm(lang).replace(' ', '-'), 'friendlySubtypeName': lang})
    for tool in sections.get('TOOLS', []):
        mods.append({'type': 'proficiency', 'subType': D._norm(tool).replace(' ', '-'), 'friendlySubtypeName': tool})
    # The sheet doesn't say which origin granted what, so every origin sees
    # every proficiency; the importer never assigns one twice.
    modifiers = {g: list(mods) for g in ('race', 'class', 'background', 'feat')}
    modifiers.update(item=[])

    feats = [n for n in bullets.get('FEATS', []) if not re.search(r'ability score improvements?$', n, re.I)]

    # spells: class list vs feat/species list, prepared marker 'O'/'P'
    by_class, other = {}, []
    i = 0
    while f.get(f'spellname{i}') is not None or i < 3:
        name = f.get(f'spellname{i}')
        if name is None and i >= 3:
            break
        if name:
            src = f.get(f'spellsource{i}', '')
            prep = (f.get(f'spellprepared{i}') or '').strip().upper()
            always = 'always prepared' in src.lower() or prep == 'A'
            entry = {'definition': {'name': name}, 'prepared': prep in ('O', 'P', 'X', 'Y') and not always,
                     'alwaysPrepared': always, 'countsAsKnownSpell': not always}
            base_src = re.sub(r'\s*\(always prepared\)\s*$', '', src, flags=re.I)
            match = next((c for c in classes if D._norm(c['definition']['name']) == D._norm(base_src)), None)
            if match:
                by_class.setdefault(match['id'], []).append(entry)
            else:
                other.append(entry)
        i += 1
        if i > 400:
            break
    class_spells = [{'characterClassId': cid, 'spells': sp} for cid, sp in by_class.items()]

    attuned = {D._norm(v) for k, v in f.items() if k.startswith('attuned name') and v}
    weapons = {D._norm(v) for k, v in f.items() if k.startswith('wpn name') and v}
    inventory = []
    for k, v in f.items():
        m = re.match(r'^eq name(\d+)$', k)
        if not m or not v:
            continue
        qty = _int(f.get(f'eq qty{m.group(1)}'), 1)
        if qty == 0:
            continue
        n = D._norm(v)
        inventory.append({'definition': {'name': _armor_name(v), 'weight': _int(f.get(f'eq weight{m.group(1)}'), 0) or 0},
                          'quantity': qty, 'equipped': n in attuned or n in weapons, 'isAttuned': n in attuned})
    _equip_worn_armor(inventory)
    if not inventory and f.get('equipment'):
        for line in re.split(r'[\n,]', f['equipment']):
            if line.strip():
                inventory.append({'definition': {'name': line.strip()}, 'quantity': 1, 'equipped': False,
                                  'isAttuned': False})

    al = _get(f, 'alignment')
    al_id = next((k for k, v in D.ALIGNMENTS.items() if v.lower() == al.lower()), None)
    return {
        'name': _get(f, 'charactername') or 'Imported',
        'stats': [{'id': i, 'value': totals.get(a, 10)} for i, a in D.STAT_IDS.items()],
        'race': {'fullName': _get(f, 'race'), 'baseRaceName': _get(f, 'race')},
        'background': {'definition': {'name': _get(f, 'background')}} if _get(f, 'background') else {},
        'classes': classes,
        'baseHitPoints': 0,
        'feats': [{'definition': {'name': n}} for n in feats],
        'modifiers': modifiers,
        'choices': {'class': [], 'choiceDefinitions': []},
        'options': {'class': [{'definition': {'name': n}} for names in bullets.values() for n in names]},
        'classSpells': class_spells,
        'spells': {'feat': other, 'race': [], 'background': []},
        'inventory': inventory,
        'currencies': {k: _int(f.get(k), 0) or 0 for k in ('cp', 'sp', 'ep', 'gp', 'pp')},
        'temporaryHitPoints': _int(_get(f, 'temphp', 'hptemp'), 0) or 0,
        'alignmentId': al_id,
        'age': _get(f, 'age'), 'height': _get(f, 'height'), 'weight': _get(f, 'weight'),
        'eyes': _get(f, 'eyes'), 'hair': _get(f, 'hair'), 'skin': _get(f, 'skin'), 'gender': _get(f, 'gender'),
        'faith': _get(f, 'faith'),
        'traits': {'personalityTraits': _get(f, 'personalitytraits'), 'ideals': _get(f, 'ideals'),
                   'bonds': _get(f, 'bonds'), 'flaws': _get(f, 'flaws'), 'appearance': _get(f, 'appearance')},
        'notes': {'backstory': _get(f, 'backstory'), 'allies': _get(f, 'alliesorganizations', 'allies'),
                  'otherNotes': '\n'.join(v for k, v in f.items() if k.startswith('additionalnotes') and v)},
        'currentXp': _int(_get(f, 'experience points', 'xp'), 0) or 0,
        '_pdf': {'totals': totals, 'max_hp': _int(_get(f, 'maxhp', 'hpmax')), 'ac': _int(_get(f, 'ac')),
                 'current_hp': _int(_get(f, 'hpcurrent', 'currenthp')), 'edition': edition},
    }


_ARMOR_SHORT = {'padded', 'leather', 'studded leather', 'hide', 'chain shirt', 'scale mail', 'breastplate',
                'half plate', 'ring mail', 'chain mail', 'splint', 'plate'}


def _armor_name(name: str) -> str:
    """D&D Beyond's sheet says "Leather" / "Half Plate"; 5etools says
    "Leather Armor" / "Half Plate Armor"."""
    n = D._norm(name)
    if n in _ARMOR_SHORT and not n.endswith('mail'):
        return f'{name} Armor'
    return name


def _equip_worn_armor(inventory: list):
    """The sheet doesn't mark what's worn. If there is exactly one suit of
    armour (or one shield), it is the one being worn."""
    kinds = {}
    for it in inventory:
        rec = D._by_name('item', it['definition']['name'], 'one')
        t = ((rec or {}).get('type') or '').split('|')[0]
        if t in ('LA', 'MA', 'HA'):
            kinds.setdefault('armor', []).append(it)
        elif t == 'S':
            kinds.setdefault('shield', []).append(it)
    for k in ('armor', 'shield'):
        if len(kinds.get(k, [])) == 1:
            kinds[k][0]['equipped'] = True


# --- fitting ------------------------------------------------------------------------

def _fit_worn_armor(doc: dict, want_ac: int, report: dict):
    """The sheet doesn't say what is worn, only the AC it gives. Armour and
    shields were assumed worn; take them off (fewest first) until the AC
    matches. Returns the new compute() result, or None (left as it was)."""
    inv = doc['state'].get('inventory') or []
    worn = []
    for it in inv:
        if not it.get('equipped') or not it.get('id'):
            continue
        rec = C.get('item', it['id']) or {}
        if (rec.get('type') or '').split('|')[0] in ('LA', 'MA', 'HA', 'S'):
            worn.append(it)
    for n in range(1, len(worn) + 1):
        for off in itertools.combinations(worn, n):
            for it in off:
                it['equipped'] = False
            s = compute(doc)
            if s['ac']['value'] == want_ac:
                names = ', '.join((C.get('item', it['id']) or {}).get('name', it['id']) for it in off)
                report['warnings'].append(f"{names}: carried, not worn (the sheet's AC {want_ac} says so). "
                                          'Equip it on the sheet if that is wrong.')
                return s
            for it in off:
                it['equipped'] = True
    return None


def import_pdf(data: bytes, *, edition: str | None = None) -> dict:
    return import_fields(read_fields(data), edition=edition)


def import_fields(fields: dict, *, edition: str | None = None) -> dict:
    """Same as import_pdf, from already-read field values (keys as _key makes them)."""
    shaped = to_ddb_shape(fields)
    if not shaped['classes']:
        raise PdfImportError('Could not find a class and level on that sheet (e.g. "Fighter 5").')
    pdf = shaped.pop('_pdf')
    ed = edition if edition in ('one', 'classic') else pdf['edition']
    res = D.import_character(shaped, edition=ed)
    doc = {'name': res['name'], 'build': res['build'], 'state': res['state']}
    report = res['report']
    report['mismatches'] = []           # the JSON importer's comparison doesn't apply to totals
    report['source'] = 'PDF'

    # Ability increases a PDF doesn't record: fill open ASI slots towards the
    # sheet's highest scores, then solve base scores so the totals match.
    def by_total(opts):
        return sorted(opts, key=lambda a: -pdf['totals'].get(a, 0))

    for _ in range(4):
        s = compute(doc)
        picks = doc['build']['choices']
        filled = False
        for ch in s['choices']:
            if ch['complete']:
                continue
            if ch['kind'] == 'alternative' and 'ability' in ch['label']:
                picks[ch['id']] = [ch['options'][0]['value']]          # +2/+1 (the usual pick)
                filled = True
            elif ch['kind'] == 'ability':
                order = by_total(ch['options'])
                weights = sorted(ch.get('weights') or [1], reverse=True)
                picks[ch['id']] = {a: w for a, w in zip(order, weights)}
                filled = True
            elif ch['kind'] == 'ability_pick' and ch['options']:
                picks[ch['id']] = [by_total(ch['options'])[0]]
                filled = True
        if filled:
            report['warnings'].append('Ability increases from species/background/feats were assigned to the '
                                      "sheet's highest scores (the PDF doesn't record them); totals still match.")
        else:
            break
    report['warnings'] = list(dict.fromkeys(report['warnings']))
    s = compute(doc)
    for ch in s['choices']:
        if ch['kind'] == 'asi' and not ch['complete']:
            best = sorted(ABILITIES, key=lambda a: -pdf['totals'].get(a, 0))
            doc['build']['choices'][ch['id']] = {'asi': {best[0]: 2}}
            report['warnings'].append(f'{ch["label"]}: the PDF doesn\'t say how it was spent; '
                                      f'assumed +2 {best[0].upper()} (scores still match the sheet).')
    for _ in range(3):
        s = compute(doc)
        changed = False
        for a in ABILITIES:
            want = pdf['totals'].get(a)
            if want is None:
                continue
            diff = want - s['abilities'][a]['score']
            if diff:
                doc['build']['base_scores'][a] = max(1, min(30, doc['build']['base_scores'][a] + diff))
                changed = True
        if not changed:
            break
    s = compute(doc)
    for a in ABILITIES:
        want = pdf['totals'].get(a)
        if want is not None and want != s['abilities'][a]['score']:
            report['mismatches'].append(f'{a.upper()}: sheet {want}, here {s["abilities"][a]["score"]}')

    # Hit points: spread the difference over levels 2+ within each die.
    if pdf['max_hp']:
        diff = pdf['max_hp'] - s['hp_max']
        levels = doc['build']['levels']
        for i in range(1, len(levels)):
            if not diff:
                break
            faces = (C.get('class', levels[i]['class']).get('hd') or {}).get('faces', 8)
            cur = levels[i]['hp'] if levels[i]['hp'] is not None else faces // 2 + 1
            new = max(1, min(faces, cur + diff))
            diff -= new - cur
            levels[i]['hp'] = new
        s = compute(doc)
        if s['hp_max'] != pdf['max_hp']:
            report['mismatches'].append(f'Max HP: sheet {pdf["max_hp"]}, here {s["hp_max"]}')
    if pdf.get('ac') and s['ac']['value'] != pdf['ac']:
        s = _fit_worn_armor(doc, pdf['ac'], report) or s
    if pdf.get('ac') and s['ac']['value'] != pdf['ac']:
        report['mismatches'].append(f'AC: sheet {pdf["ac"]}, here {s["ac"]["value"]} (check what is equipped)')
    st = doc['state']
    st['hp_max'] = s['hp_max']
    st['hp'] = max(0, min(s['hp_max'], pdf['current_hp'])) if pdf['current_hp'] is not None else s['hp_max']
    report['pending'] = [c['label'] for c in s['choices'] if not c['complete']]
    report['errors'] = s['errors']
    return {'name': res['name'], 'build': doc['build'], 'state': st, 'details': res['details'], 'report': report}
