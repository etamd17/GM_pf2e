"""Import a filled official Paizo PF2e character-sheet PDF into a Pathbuilder-
shaped ``build`` dict that ``Character()`` consumes.

The official Paizo fillable sheet is an AcroForm (~1087 fields). We read the
character's **inputs** -- attribute modifiers and proficiency RANKS (Trained /
Expert / Master / Legendary checkboxes) -- which are exactly the inputs to
PF2e's deterministic stat math. Fed a build with those, ``Character`` re-derives
AC / saves / skills / Perception to the sheet's numbers on its own (verified to
the point on a real L10 sheet), so the imported PC stays fully interactive (its
sheet still recomputes when conditions/grips change) rather than being a frozen
snapshot. HP is the one value not fully determined by those inputs (ancestry/
class HP + HP feats like Toughness), so it's pinned via the in-band
``attributes.bonushp`` field to the sheet's Maximum HP.

Self-contained (only ``pypdf``); no dependency on the Flask app except an
optional weapon-catalog match done by the caller.
"""
from __future__ import annotations

import re

_ATTRS = [('STRENGTH', 'str'), ('DEXTERITY', 'dex'), ('CONSTITUTION', 'con'),
          ('INTELLIGENCE', 'int'), ('WISDOM', 'wis'), ('CHARISMA', 'cha')]

# Skill display name -> checkbox prefix on the sheet (note Paizo's ATHELETICS typo)
# and Pathbuilder proficiency key (lowercase).
_SKILLS = {
    'Acrobatics': 'ACROBATICS', 'Arcana': 'ARCANA', 'Athletics': 'ATHELETICS',
    'Crafting': 'CRAFTING', 'Deception': 'DECEPTION', 'Diplomacy': 'DIPLOMACY',
    'Intimidation': 'INTIMIDATION', 'Medicine': 'MEDICINE', 'Nature': 'NATURE',
    'Occultism': 'OCCULTISM', 'Performance': 'PERFORMANCE', 'Religion': 'RELIGION',
    'Society': 'SOCIETY', 'Stealth': 'STEALTH', 'Survival': 'SURVIVAL',
    'Thievery': 'THIEVERY',
}
_RANK = {'TRAINED': 2, 'EXPERT': 4, 'MASTER': 6, 'LEGENDARY': 8}
_TRADITIONS = [('PRIMAL', 'castingPrimal', 'primal'), ('ARCANE', 'castingArcane', 'arcane'),
               ('DIVINE', 'castingDivine', 'divine'), ('OCCULT', 'castingOccult', 'occult')]
_YES = ('/Yes', '/On', 'Yes', 'On')


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


def parse_pf2e_pdf(src):
    """Return (build_dict, authoritative).

    build_dict   -> feed straight to Character({'build': build_dict})
    authoritative-> the sheet's stated finals (ac/hp/saves/perception/...) so the
                    caller can verify the derivation and pin HP.
    ``src`` may be a path / file-like / bytes, OR an already-extracted
    {field: value} dict (so the mapping is testable without a PDF).
    """
    F = src if isinstance(src, dict) else extract_fields(src)

    def s(key, default=''):
        v = F.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else default

    def i(key, default=0):
        try:
            return int(re.sub(r'[^0-9+-]', '', s(key)) or default)
        except (TypeError, ValueError):
            return default

    def ck(key):
        return F.get(key) in _YES

    def rank_cb(prefix):
        for tier in ('LEGENDARY', 'MASTER', 'EXPERT', 'TRAINED'):
            if ck('%s %s' % (prefix, tier)):
                return _RANK[tier]
        return 0

    level = i('LEVEL', 1) or 1
    abilities = {code: i('%s STAT' % pdf) for pdf, code in _ATTRS}

    proficiencies = {
        'perception': rank_cb('PERCEPTION'),
        'fortitude': rank_cb('FORTITUDE'), 'reflex': rank_cb('REFLEX'), 'will': rank_cb('WILL'),
        'unarmored': rank_cb('UNARMORED'), 'light': rank_cb('LIGHT'),
        'medium': rank_cb('MEDIUM'), 'heavy': rank_cb('HEAVY'),
        'unarmed': rank_cb('UNARMED'), 'simple': rank_cb('SIMPLE WEAPONS'),
        'martial': rank_cb('MARTIAL WEAPONS'), 'advanced': rank_cb('ADVANCED WEAPON'),
    }
    for disp, prefix in _SKILLS.items():
        proficiencies[disp.lower()] = rank_cb(prefix)
    # Class DC + spellcasting proficiency come as "rank + level" breakdown fields.
    cdc_prof = i('CLASS DC PROFICIENCY')
    if cdc_prof:
        proficiencies['classDC'] = max(0, cdc_prof - level)
    spell_rank = rank_cb('SPELL ATTACK')
    tradition = 'primal'
    for pdf, key, trad in _TRADITIONS:
        if ck(pdf):
            proficiencies[key] = spell_rank
            tradition = trad
            break

    # Lores: name + rank checkbox (LORE1 / LORE2 prefixes).
    lores = []
    for n in (1, 2):
        nm = s('LORE CATAGORY %d' % n) or s('LORE CATEGORY %d' % n)
        rk = rank_cb('LORE%d' % n)
        if nm and rk:
            lores.append([nm, rk])

    # Feats: CLASS/SKILL/GENERAL FEAT <level>-<n>, plus the single-slot ancestry /
    # background feats. Pathbuilder feat rows are [name, id, category, level].
    feats = []
    _CAT = [('CLASS FEAT', 'Class Feat'), ('SKILL FEAT', 'Skill Feat'),
            ('GENERAL FEAT', 'General Feat'), ('ANCESTRY FEAT', 'Ancestry Feat')]
    for key, val in F.items():
        if not (isinstance(val, str) and val.strip()):
            continue
        for prefix, cat in _CAT:
            m = re.match(r'^%s(?:\s+(\d+)-\d+)?$' % re.escape(prefix), key)
            if m:
                feats.append([val.strip(), None, cat, int(m.group(1) or 1)])
                break
    if s('BACKGROUND SKILL FEAT'):
        feats.append([s('BACKGROUND SKILL FEAT'), None, 'Skill Feat', 1])
    # Class features / ancestry abilities (newline lists) -> specials (senses etc.)
    specials = []
    for blob in (s('CLASS FEATS & FEATURES'), s('ANCESTRY & HERITAGE ABILITIES'),
                 s('ANCESTRY FEAT'), s('Heritage and Traits')):
        specials += [ln.strip() for ln in blob.replace(',', '\n').split('\n') if ln.strip()]

    # Strikes -> Pathbuilder weapon rows (Character enriches from BUILDER_WEAPONS
    # by name; we carry the sheet's attack/damage so a homebrew weapon still works).
    weapons = []
    for kind, prof_default in (('MELEE', 'simple'), ('RANGED', 'martial')):
        for n in range(1, 6):
            nm = s('%s STRIKE %d' % (kind, n))
            if not nm:
                continue
            dmg = s('%s STRIKE %d DAMAGE' % (kind, n))       # e.g. "1d4+1 B"
            dm = re.match(r'\s*(\d*)d(\d+)\s*([+-]\s*\d+)?\s*([A-Za-z]*)', dmg)
            die = 'd%s' % dm.group(2) if dm else 'd4'
            dbon = int(re.sub(r'\s', '', dm.group(3) or '0')) if dm else 0
            dtype = (dm.group(4) if dm else '') or 'B'
            weapons.append({
                'name': nm, 'qty': 1, 'prof': prof_default, 'die': die, 'pot': 0,
                'str': '', 'mat': None, 'display': nm, 'runes': [], 'damageType': dtype,
                'attack': i('%s STRIKE %d ATTACK BONUS' % (kind, n)), 'damageBonus': dbon,
                'extraDamage': [], 'increasedDice': False, 'isInventor': False, 'grade': '',
                'traits': s('%s STRIKE %d TRAITS AND NOTES' % (kind, n)),
            })

    # Spellcasting: one prepared caster built from the cantrip + spell lists.
    cantrips = [s('CANTRIP NAME %d' % n) for n in range(1, 19) if s('CANTRIP NAME %d' % n)]
    by_rank = {}
    for n in range(1, 30):
        nm = s('SPELL %d' % n)
        if nm:
            by_rank.setdefault(i('SPELL RANK %d' % n, 1), []).append(nm)
    spell_casters = []
    if cantrips or by_rank:
        spells = [{'spellLevel': 0, 'list': cantrips}]
        for lvl in range(1, 11):
            spells.append({'spellLevel': lvl, 'list': by_rank.get(lvl, [])})
        # Key ability = the attribute whose modifier equals the spell key mod.
        key_mod = i('SPELL ATTACK KEY')
        ability = next((c for _p, c in _ATTRS if abilities.get(c) == key_mod), 'wis')
        per_day = [i('CANTRIPS PER DAY') if s('CANTRIPS PER DAY') not in ('', '-') else 5]
        per_day += [i('SPELLS PER DAY %d' % r) for r in range(1, 11)]
        spell_casters.append({
            'name': s('Class') or 'Spellcaster', 'magicTradition': tradition,
            'spellcastingType': 'prepared', 'ability': ability, 'proficiency': spell_rank,
            'focusPoints': sum(1 for fp in ('FP1', 'FP2', 'FP 3') if ck(fp)),
            'innate': False, 'perDay': per_day, 'spells': spells, 'prepared': [], 'blendedSpells': [],
        })
    focus_spells = [s('FOCUS SPELL %d' % n) for n in range(1, 9) if s('FOCUS SPELL %d' % n)]

    # Equipment (held + worn) -> Pathbuilder [name, qty] rows.
    equipment = []
    for prefix, rng in (('HELD', range(1, 21)), ('WORN', range(1, 6))):
        for n in rng:
            nm = s('%s %d' % (prefix, n) if prefix == 'HELD' else '%s %d' % (prefix, n))
            if nm:
                equipment.append([nm, 1])
    worn_armor = s('WORN 1')
    armor = []
    if worn_armor:
        armor.append({'name': worn_armor, 'qty': 1, 'prof': 'light', 'pot': 0, 'res': '',
                      'mat': None, 'display': worn_armor, 'worn': True, 'runes': [], 'grade': ''})

    build = {
        'name': s('Character Name') or 'Imported Hero',
        'level': level,
        'class': s('Class') or 'Fighter',
        'ancestry': s('Ancestry') or 'Human',
        'heritage': s('Heritage and Traits'),
        'background': re.sub(r'\s*\([^)]*\)\s*$', '', s('Background')),
        'abilities': abilities,
        'proficiencies': proficiencies,
        'lores': lores,
        'feats': feats,
        'specials': specials,
        'weapons': weapons,
        'armor': armor,
        'equipment': equipment,
        'spellCasters': spell_casters,
        'focus_spells': focus_spells,
        'languages': [l.strip() for l in s('LANGUAGES').replace(';', ',').split(',') if l.strip()],
        'money': {'cp': i('COPPER'), 'sp': i('SILVER'), 'gp': i('GOLD'), 'pp': i('PLATINUM')},
        'deity': s('Deity or Philosophy'),
        'keyability': next((c for _p, c in _ATTRS if abilities.get(c) == i('CLASS DC KEY')), None),
        'attributes': {'ancestryhp': 0, 'classhp': 0, 'bonushp': 0, 'bonushpPerLevel': 0,
                       'speed': i('SPEED', 25) or 25, 'speedBonus': 0},
        'size': 2, 'xp': i('XP'), 'hero_points': 1,
    }

    authoritative = {
        'ac': i('AC'), 'hp': i('MAXIMUM HIT POINTS'),
        'fortitude': i('FORTITUDE'), 'reflex': i('REFLEX'), 'will': i('WILL'),
        'perception': i('PERCEPTION'), 'speed': i('SPEED'),
        'class_dc': i('CLASS DC'), 'spell_dc': i('SPELL SAVE DC'),
        'skills': {disp: i(prefix if disp != 'Athletics' else 'ATHLETICS') for disp, prefix in
                   [(d, d.upper()) for d in _SKILLS]},
        'current_hp': i('Current HP') or i('MAXIMUM HIT POINTS'),
    }
    return build, authoritative


def build_from_pdf(src, character_factory=None):
    """Parse a Paizo sheet PDF into a finalized (build, play_state) ready to store
    as a Pathbuilder character. Pins Maximum HP via ``attributes.bonushp`` (the HP
    that ancestry/class + HP feats add on top of the con-derived base isn't
    recoverable from the sheet's inputs alone). ``character_factory`` is the app's
    ``Character`` class, used to probe the derived HP; if omitted, HP isn't pinned.
    """
    build, auth = parse_pf2e_pdf(src)

    if character_factory is not None and auth.get('hp'):
        try:
            probe = character_factory({'success': True, 'build': dict(build, attributes=dict(build['attributes']))})
            delta = int(auth['hp']) - int(probe.hp)
            if delta:
                build['attributes'] = dict(build['attributes'], bonushp=delta)
        except Exception:
            pass

    play_state = {'current_hp': auth.get('current_hp') or auth.get('hp') or 0}
    return build, play_state
