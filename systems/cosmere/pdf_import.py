"""Import a filled Cosmere character-sheet PDF into a CosmereBuild dict.

The community/official fillable Cosmere sheet is an AcroForm with ~265 cleanly
named fields (char_strength, char_athletics, char_athletics_rank_N, char_name,
char_paths, char_talent_name_N, ...). We extract those and map them to the
build-dict shape CosmereBuild consumes. Derived stats (defenses, health, focus,
skill mods) are recomputed by CosmereBuild from attributes + level + ranks and
match the sheet under RAW rules; where the sheet's authoritative total differs
(an ancestry/talent bonus the build doesn't model), the caller can apply the
sheet value as an override via `authoritative` below.

Self-contained (only pypdf); no dependency on the Flask app.
"""
from __future__ import annotations

import re


def _parse_weapon_line(line):
    """Parse a sheet weapon line into {name, attack, damage, type, traits}.

    Lines look like:  "Knife: +5 (1d4 keen damage) [Melee, Thrown [20/60], ...]"
    or "Soravar's Gauntlet: +7 (1d10 impact damage) [Melee, ...]" or, with no
    damage, "Improvised Weapon: +0 [Melee, Fragile, 1 action]".
    """
    s = (line or '').strip()
    m = re.match(r'^(?P<name>.+?):\s*(?P<atk>[+-]?\d+)?\s*(?:\((?P<dmg>[^)]*)\))?\s*(?:\[(?P<traits>.*)\])?\s*$', s)
    if not m:
        return {'name': s.split(':', 1)[0].strip(), 'attack': None, 'damage': '', 'type': '', 'traits': ''}
    name = (m.group('name') or '').strip()
    atk = m.group('atk')
    attack = int(atk) if atk not in (None, '') else None
    dmg = (m.group('dmg') or '').strip()
    formula, dtype = '', ''
    if dmg:
        dm = re.search(r'\d+\s*d\s*\d+(?:\s*[+-]\s*\d+)?', dmg)
        if dm:
            formula = dm.group(0).replace(' ', '')
            dmg = dmg.replace(dm.group(0), ' ')
        dmg = re.sub(r'\bdamage\b', ' ', dmg, flags=re.I).strip().strip(',').strip()
        dtype = dmg.split()[0] if dmg.split() else ''
    return {'name': name, 'attack': attack, 'damage': formula, 'type': dtype.lower(),
            'traits': (m.group('traits') or '').strip()}

# PDF skill field stem -> app 3-letter skill code.
_PDF_SKILL = {
    'agility': 'agi', 'athletics': 'ath', 'crafting': 'cra', 'deception': 'dec',
    'deduction': 'ded', 'discipline': 'dis', 'heavy_weapon': 'hwp', 'intimidation': 'inm',
    'insight': 'ins', 'leadership': 'lea', 'lore': 'lor', 'light_weapon': 'lwp',
    'medicine': 'med', 'perception': 'prc', 'persuasion': 'prs', 'stealth': 'stl',
    'survival': 'sur', 'thievery': 'thv',
    # Surgebinding skills (Radiants)
    'abrasion': 'abr', 'adhesion': 'adh', 'cohesion': 'chs', 'division': 'dvs',
    'gravitation': 'grv', 'illumination': 'ill', 'progression': 'prg',
    'transportation': 'trp', 'transformation': 'trs', 'tension': 'tsn',
}
_PDF_ATTR = {'strength': 'str', 'speed': 'spd', 'intellect': 'int',
             'willpower': 'wil', 'awareness': 'awa', 'presence': 'pre'}

# Known Radiant orders, so "Scholar, Truthwatcher" splits into heroic path +
# radiant order. Matched case-insensitively against the path string.
_RADIANT_ORDERS = (
    'windrunner', 'skybreaker', 'dustbringer', 'edgedancer', 'truthwatcher',
    'lightweaver', 'elsecaller', 'willshaper', 'stoneward', 'bondsmith',
)


def extract_fields(src):
    """src: a path, file-like, or bytes. Returns {field_name: value_str_or_None}."""
    from pypdf import PdfReader
    import io
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(src)
    reader = PdfReader(src)
    out = {}
    for k, v in (reader.get_fields() or {}).items():
        val = v.get('/V') if hasattr(v, 'get') else v
        out[k] = (str(val) if val is not None else None)
    return out


def parse_cosmere_pdf(src):
    """Return (build_dict, authoritative, extras).

    build_dict   -> feed straight to CosmereBuild
    authoritative-> the sheet's stated final numbers (defenses/health/focus/
                    deflect) for override when they diverge from the computed build
    extras       -> weapons / equipment / talent descriptions (display-only)

    `src` may be a path / file-like / bytes, OR an already-extracted
    {field: value} dict (the field-mapping is then exercised without a PDF).
    """
    f = src if isinstance(src, dict) else extract_fields(src)

    def s(key, default=''):
        v = f.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else default

    def i(key, default=0):
        try:
            return int(float(s(key) or default))
        except (TypeError, ValueError):
            return default

    def rank(stem):
        return sum(1 for n in range(1, 6) if f.get(f'char_{stem}_rank_{n}') == '/Yes')

    attributes = {code: i('char_' + pdf) for pdf, code in _PDF_ATTR.items()}
    skills = {}
    for pdf, code in _PDF_SKILL.items():
        r = rank(pdf)
        if r > 0:
            skills[code] = r

    # Path: split a "Heroic, RadiantOrder" string into heroic path + radiant order.
    raw_path = s('char_paths')
    heroic_path, radiant_order = raw_path, ''
    parts = [p.strip() for p in raw_path.replace('/', ',').split(',') if p.strip()]
    for p in parts:
        if p.lower() in _RADIANT_ORDERS:
            radiant_order = p.lower()
        elif not radiant_order or p.lower() != heroic_path.lower():
            heroic_path = p
    if parts:
        heroic_path = next((p for p in parts if p.lower() not in _RADIANT_ORDERS), parts[0])

    talents = []
    for n in range(1, 16):
        nm = s(f'char_talent_name_{n}')
        if nm:
            talents.append({'name': nm})

    expertises = [e.strip() for e in s('char_expertise').replace(';', ',').split(',') if e.strip()]

    build = {
        'name': s('char_name') or 'Imported Hero',
        'level': i('char_level', 1) or 1,
        'ancestry': s('char_ancestry') or 'Human',
        'path': heroic_path.lower(),
        'attributes': attributes,
        'skills': skills,
        'expertises': expertises,
        'talents': talents,
        'purpose': s('char_purpose'),
        'obstacle': s('char_obstacle'),
    }
    if radiant_order:
        build['radiant_order'] = radiant_order
        build['is_radiant'] = True

    authoritative = {
        'phy': i('char_phys_def'), 'cog': i('char_cog_def'), 'spi': i('char_spirit_def'),
        'health': i('char_health_max'), 'focus': i('char_focus_max'),
        'investiture': i('char_invest_max') or i('char_invest_current'),
        'deflect': i('char_deflect'),
        'skill_mods': {code: i('char_' + pdf) for pdf, code in _PDF_SKILL.items()
                       if s('char_' + pdf) != ''},
        'movement': s('char_movement'), 'recovery': s('char_recovery'),
    }

    def _listfield(stem):
        # AcroForm array fields come back as char_weapons.0, char_weapons.1, ...
        out = []
        for n in range(0, 40):
            v = s(f'char_{stem}.{n}')
            if v:
                out.append(v)
        return out

    extras = {
        'weapons': _listfield('weapons'),
        'equipment': _listfield('equipment'),
        'talent_descriptions': [s(f'char_talent_desc_{n}') for n in range(1, 16) if s(f'char_talent_desc_{n}')],
        'spheres': s('char_spheres'),
        'health_current': i('char_health_current') or i('char_health_max'),
        'focus_current': i('char_focus_current') or i('char_focus_max'),
    }
    return build, authoritative, extras


def build_from_pdf(src, homebrew=None):
    """Parse a sheet PDF into a finalized (build, play_state, extras) ready to
    store. Computes `stat_bonuses` deltas (sheet total minus what the build
    derives) for health/focus/deflect/defenses/investiture, so the imported
    character reproduces the sheet's authoritative numbers exactly even when a
    bonus (e.g. the Hardy talent's +5 health, or armor Deflect) can't be derived
    from a talent name alone."""
    from systems.cosmere.build import CosmereBuild
    from systems.cosmere.actor import CosmereActor
    build, auth, extras = parse_cosmere_pdf(src)
    probe = CosmereActor(CosmereBuild(dict(build), homebrew=homebrew).to_actor_doc())
    deltas = {}

    def _delta(key, sheet_val, computed):
        try:
            if sheet_val and int(sheet_val) != int(computed):
                deltas[key] = int(sheet_val) - int(computed)
        except (TypeError, ValueError):
            pass

    _delta('health', auth['health'], probe.health_max)
    _delta('focus', auth['focus'], probe.focus_max)
    _delta('deflect', auth['deflect'], (probe.deflect or {}).get('value', 0))
    _delta('def:phy', auth['phy'], probe.defenses.get('phy', 0))
    _delta('def:cog', auth['cog'], probe.defenses.get('cog', 0))
    _delta('def:spi', auth['spi'], probe.defenses.get('spi', 0))
    if auth.get('investiture'):
        _delta('investiture', auth['investiture'], getattr(probe, 'investiture_max', 0))
    if deltas:
        build['stat_bonuses'] = deltas

    # Map the sheet's weapons to catalog items (by name) + equip them, so the
    # imported PC has Strikes on the tracker/sheet. The Strike's attack mod comes
    # from the governing skill the catalog weapon declares (which the sheet's
    # ranks already set), so it matches the PDF. Unmatched names (e.g. a generic
    # "Improvised Weapon") are skipped silently.
    try:
        from systems.cosmere import items as _items
        inv, custom, seen, seen_names = [], [], set(), set()
        for line in extras.get('weapons', []):
            w = _parse_weapon_line(line)
            nm = w['name']
            if not nm or nm.lower() in seen_names:
                continue
            hit = _items.by_name(nm)
            iid = (hit.get('id') or hit.get('_id')) if hit else None
            if iid and (hit.get('kind') == 'weapon' or hit.get('type') == 'weapon'):
                if iid not in seen:
                    seen.add(iid)
                    seen_names.add(nm.lower())
                    inv.append({'id': iid, 'qty': 1, 'equipped': True})
            elif w['damage']:
                # No catalog match -> a homebrew / one-off weapon (e.g. "Soravar's
                # Gauntlet"). Keep it as a custom Strike with its explicit attack
                # bonus + damage so it isn't dropped. (Bare entries with no damage,
                # like a generic "Improvised Weapon", are skipped.)
                seen_names.add(nm.lower())
                custom.append({'name': nm, 'attack': w['attack'] or 0,
                               'damage': w['damage'], 'type': w['type']})
        if inv:
            build['inventory'] = inv
        if custom:
            build['custom_weapons'] = custom
    except Exception:
        pass

    _hp_max = (auth['health'] + deltas.get('health', 0)) if auth['health'] else probe.health_max
    _foc_max = (auth['focus'] + deltas.get('focus', 0)) if auth['focus'] else probe.focus_max
    play_state = {
        # Respect the sheet's current values, clamped to max (some sheets carry a
        # current above max from temp HP / mid-session edits).
        'health': min(extras.get('health_current') or _hp_max, _hp_max),
        'focus': min(extras.get('focus_current') or _foc_max, _foc_max),
    }
    return build, play_state, extras
