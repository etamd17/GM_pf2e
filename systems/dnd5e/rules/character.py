"""D&D 5e character engine: build log -> derived sheet + outstanding choices.

A character document stores only DECISIONS (``build``) and CONSUMABLE STATE
(``state``). Every number on the sheet is recomputed here from the build, so
leveling up, respeccing or fixing a past choice can never leave a stale stat
behind -- the same reason D&D Beyond recomputes rather than stores.

    build = {
      'edition': 'one' | 'classic',
      'sources': [...],                 # allowed source codes ([] = all)
      'race': 'elf; high elf lineage|xphb',
      'background': 'sage|xphb',
      'ability_method': 'standard'|'pointbuy'|'roll'|'manual',
      'base_scores': {'str': 15, ...},
      'custom_origin': bool,            # 2014 Tasha's: move racial ASIs freely
      'levels': [{'class': 'wizard|xphb', 'hp': None|int}, ...],   # index = level-1
      'choices': {choice_id: value},
    }

`compute(doc)` returns a plain dict (JSON-safe) -- see `Sheet` keys at the end
of the function. It never raises on bad content; problems land in
``sheet['errors']`` and unmade decisions in ``sheet['choices']`` with
``complete: False``.
"""
from __future__ import annotations

import math
import re

from systems.dnd5e import content as C
from systems.dnd5e.ids import entity_id, parse_ref, subclass_id  # noqa: F401  (parse_ref re-used by routes)
from systems.dnd5e.render import strip_tags
from systems.dnd5e.rules import effects as E
from systems.dnd5e.rules import grants as G
from systems.dnd5e.rules.tables import (
    ABILITIES, ABILITY_NAMES, SKILLS, POINT_BUY_BUDGET, POINT_BUY_COST, STANDARD_ARRAY,
    SPELL_SLOTS_BY_CASTER_LEVEL, ability_mod, multiclass_caster_level, proficiency_bonus,
)

MAX_LEVEL = 20

# --- content pools --------------------------------------------------------------

_POOL_CACHE: dict = {}


def pools_for(edition: str) -> G.Pools:
    if edition in _POOL_CACHE:
        return _POOL_CACHE[edition]
    core = 'XPHB' if edition == 'one' else 'PHB'
    langs = [l for l in C.all_records('language') if l.get('source') == core]
    std = sorted(l['name'].lower() for l in langs if l.get('type') == 'standard')
    alll = sorted(l['name'].lower() for l in langs if l.get('type') in ('standard', 'exotic', 'rare'))
    tools = C.search_items(base=None, sources=[core], magic=False, limit=2000)
    by_type: dict = {}
    for t in tools:
        by_type.setdefault(t['type'], []).append(t['name'].lower())
    generic = {"artisan's tools", 'musical instrument', 'gaming set'}
    weapons = [r for r in C.all_records('item', sources=[core]) if r.get('weapon') and r.get('_isBaseItem')]
    p = G.Pools(
        languages_standard=std, languages_all=alll,
        tools_artisan=sorted(n for n in by_type.get('AT', []) if n not in generic),
        tools_instrument=sorted(n for n in by_type.get('INS', []) if n not in generic),
        tools_gaming=sorted(n for n in by_type.get('GS', []) if n not in generic),
        tools_all=sorted(n for k in ('AT', 'INS', 'GS', 'T') for n in by_type.get(k, []) if n not in generic),
        weapons_simple=sorted(w['name'].lower() for w in weapons if w.get('weaponCategory') == 'simple'),
        weapons_martial=sorted(w['name'].lower() for w in weapons if w.get('weaponCategory') == 'martial'),
    )
    _POOL_CACHE[edition] = p
    return p


# --- prose-only feature choices ----------------------------------------------
# Choices that 5etools carries only in feature TEXT, not structured fields.
# Keyed by (feature name, class name); value: list of (kind, count, options)
# where options is a list or a callable(sheet_ctx) -> list.
_EXPERTISE_ANY_PROF = '__proficient_skills__'
FEATURE_CHOICES = {
    ('Expertise', 'Rogue'): [('expertise', 2, _EXPERTISE_ANY_PROF)],
    ('Expertise', 'Bard'): [('expertise', 2, _EXPERTISE_ANY_PROF)],
    ('Deft Explorer', 'Ranger'): [('expertise', 1, _EXPERTISE_ANY_PROF), ('language', 2, '__languages__')],
    ('Canny', 'Ranger'): [('expertise', 1, _EXPERTISE_ANY_PROF), ('language', 2, '__languages__')],
    ('Scholar', 'Wizard'): [('expertise', 1, ['arcana', 'history', 'investigation', 'medicine', 'nature', 'religion'])],
    ('Student of War', 'Fighter'): [('tool', 1, '__artisan__'), ('skill', 1, '__class_skills__')],
    ('Bonus Proficiencies', 'Bard'): [('skill', 3, sorted(SKILLS))],
    # 2014: Thieves' Cant IS the language. 2024: it also grants one more language.
    ('Thieves\' Cant', 'Rogue', 'one'): [('language', 1, '__languages__')],
    ('Thieves\' Cant', 'Rogue', 'classic'): [],
    ('Weapon Mastery', '*'): [('weapon_mastery', '__mastery_count__', '__mastery_weapons__')],
    ('Primal Knowledge', 'Barbarian'): [('skill', 1, '__class_skills__')],
    ('Favored Enemy', 'Ranger'): [],   # 2024 version grants Hunter's Mark (additionalSpells); 2014 is roleplay
    ('Blessed Strikes', 'Cleric'): [('feature_option', 1, ['Divine Strike', 'Potent Spellcasting'])],
    ('Divine Order', 'Cleric'): [('feature_option', 1, ['Protector', 'Thaumaturge'])],
    ('Primal Order', 'Druid'): [('feature_option', 1, ['Magician', 'Warden'])],
    ('Elemental Fury', 'Druid'): [('feature_option', 1, ['Potent Spellcasting', 'Primal Strike'])],
    # 2014-only prose choices (the 2024 features of the same name changed).
    ('Dragon Ancestor', 'Sorcerer', 'classic'): [('feature_option', 1, [
        'Black (acid)', 'Blue (lightning)', 'Brass (fire)', 'Bronze (lightning)', 'Copper (acid)',
        'Gold (fire)', 'Green (poison)', 'Red (fire)', 'Silver (cold)', 'White (cold)'])],
    ('Favored Enemy', 'Ranger', 'classic'): [
        ('feature_option', ('levels', (1, 6, 14)), [
            'Aberrations', 'Beasts', 'Celestials', 'Constructs', 'Dragons', 'Elementals', 'Fey', 'Fiends',
            'Giants', 'Monstrosities', 'Oozes', 'Plants', 'Undead', 'Two humanoid races']),
        ('language', ('levels', (1, 6, 14)), '__languages__')],
    ('Natural Explorer', 'Ranger', 'classic'): [
        ('feature_option', ('levels', (1, 6, 10)), [
            'Arctic', 'Coast', 'Desert', 'Forest', 'Grassland', 'Mountain', 'Swamp', 'Underdark'])],
}


# --- small helpers -----------------------------------------------------------

def _lower_list(xs) -> list:
    return [str(x).split('|')[0].lower() for x in xs or []]


def _lower_strings(xs) -> list:
    """Normalise fixed string grants without leaking choice objects to a sheet."""
    return [x.split('|')[0].lower() for x in xs or [] if isinstance(x, str)]


def _col_label(lbl) -> str:
    return strip_tags(lbl).strip()


def class_table(cls: dict, level: int) -> dict:
    """Class table columns at ``level`` -> value, e.g. {'Rages': 3, 'Rage Damage': '+2'}."""
    out = {}
    if level < 1:
        return out
    for grp in cls.get('classTableGroups') or []:
        rows = grp.get('rows')
        if not rows or len(rows) < level:
            continue
        for lbl, v in zip(grp.get('colLabels') or [], rows[level - 1]):
            if isinstance(v, dict):
                v = v.get('value', v.get('toRoll') and '+'.join(
                    f'{d.get("number", 1)}d{d.get("faces")}' for d in v['toRoll']) or strip_tags(str(v)))
            out[_col_label(lbl)] = v
    return out


def _slot_row(rec: dict, level: int) -> list:
    for grp in rec.get('classTableGroups') or rec.get('subclassTableGroups') or []:
        rows = grp.get('rowsSpellProgression')
        if rows and len(rows) >= level >= 1:
            return list(rows[level - 1])
    return []


def _features_of(groups, flat_header=True) -> list:
    """Flatten resolved (sub)class feature groups into feature dicts.

    A subclass level group is one header object whose entries inline the real
    features; we surface each named feature once and strip inlined children
    out of their parent so nothing is listed twice."""
    out = []
    _FEAT = ('classFeature', 'subclassFeature')

    def is_feature(e):
        return isinstance(e, dict) and e.get('__prop') in _FEAT and e.get('name')

    def walk(node, depth, option_of=None):
        if isinstance(node, list):
            for x in node:
                walk(x, depth, option_of)
            return
        if not isinstance(node, dict):
            return
        if is_feature(node):
            own, groups_here = [], []
            for e in node.get('entries') or []:
                if is_feature(e):
                    walk(e, depth + 1)
                elif (isinstance(e, dict) and e.get('type') == 'options'
                      and any(is_feature(x) for x in e.get('entries') or [])):
                    # "Choose one of the following options" (Hunter's Prey,
                    # Totem Spirit...): the children become a choice, and only
                    # the chosen ones are the character's features.
                    kids = [x for x in e['entries'] if is_feature(x)]
                    key = f'{node["name"]}|{node.get("level")}'
                    groups_here.append({'key': key, 'count': int(e.get('count') or 1),
                                        'names': [k['name'] for k in kids], 'parent': node['name'],
                                        'level': node.get('level')})
                    for k in kids:
                        walk(k, depth + 1, option_of=key)
                else:
                    own.append(e)
            out.append({'name': node['name'], 'source': node.get('source'), 'level': node.get('level'),
                        'className': node.get('className'), 'subclass': node.get('subclassShortName'),
                        'entries': own, 'gainSubclassFeature': bool(node.get('gainSubclassFeature')),
                        'variant': bool(node.get('isClassFeatureVariant')),
                        'consumes': node.get('consumes'), 'option_of': option_of,
                        'option_groups': groups_here})
        else:
            for e in node.get('entries') or []:
                if isinstance(e, dict):
                    walk(e, depth, option_of)
    walk(groups, 0)
    return out


def _eval_prepared_formula(expr: str, level: int, mods: dict) -> int:
    """Evaluate 5etools' ``preparedSpells`` formula, e.g.
    ``"<$level$> / 2 + <$cha_mod$>"`` (rounded down, minimum 1)."""
    s = expr.replace('<$level$>', str(level))
    for a in ABILITIES:
        s = s.replace(f'<${a}_mod$>', f'({mods[a]})')
    if not re.fullmatch(r'[\d\s+\-*/().]+', s):
        return 1
    try:
        return max(1, math.floor(eval(s, {'__builtins__': {}}, {})))  # noqa: S307 -- digits/ops only
    except Exception:
        return 1


def _check_requirements(req, scores: dict) -> bool:
    if not req:
        return True
    if 'or' in req:
        return any(_check_requirements(alt, scores) for alt in req['or'])
    return all(scores.get(a, 0) >= int(v) for a, v in req.items() if a in ABILITIES)


def multiclass_requirement(cls: dict) -> dict:
    """The 13+ ability requirement for multiclassing into/out of ``cls``.
    2014 data spells it out; 2024 data relies on ``primaryAbility``
    (a list of alternatives, each a dict of abilities that ALL must be 13)."""
    mc = cls.get('multiclassing') or {}
    if mc.get('requirements'):
        return mc['requirements']
    prim = cls.get('primaryAbility') or []
    alts = [{a: 13 for a, on in p.items() if on} for p in prim if isinstance(p, dict)]
    alts = [a for a in alts if a]
    if not alts:
        return {}
    return alts[0] if len(alts) == 1 else {'or': alts}


# --- prerequisites ------------------------------------------------------------

def _knows_spell_for(req, ctx: dict) -> bool:
    """One ``spell`` prerequisite: "eldritch blast#c" (2014 Agonizing Blast) or
    {"choose": "level=0|class=Warlock", "entry": "a Warlock Cantrip That Deals
    Damage"} (2024). Checked against the spells the character actually has."""
    known = ctx['known_spells']
    names = {k.split('|')[0] for k in known}
    if isinstance(req, str):
        # 2014 "eldritch blast#c": the spell itself, from any source
        return req.split('#')[0].split('|')[0].strip().lower() in names
    if isinstance(req, dict) and isinstance(req.get('choose'), str):
        from systems.dnd5e.rules.spells import spells_for_filter
        # 2024 "a Warlock cantrip": it must be known *as* that class's spell. A
        # Wizard 1 / Warlock 2 whose only damaging cantrip is a Wizard pick
        # (Chill Touch is on both lists) does not qualify.
        classes = [c.strip().lower() for part in req['choose'].split('|') if part.lower().startswith('class=')
                   for c in part.split('=', 1)[1].split(';') if c.strip()]
        if classes:
            by_class = ctx.get('known_by_class') or {}
            known = set().union(*(by_class.get(c, set()) for c in classes))
        allowed = {r['id'] for r in spells_for_filter(req['choose'], ctx.get('edition') or 'one')} & known
        if 'damage' in str(req.get('entry', '')).lower():
            allowed = {sid for sid in allowed if (C.get('spell', sid) or {}).get('damageInflict')}
        return bool(allowed)
    return True


def prereq_ok(prereq, ctx: dict) -> tuple:
    """(ok, unchecked_notes). ``ctx``: level, scores, spellcasting, armor, race."""
    if not prereq:
        return True, []
    notes = []
    for alt in prereq:   # list = alternatives; any one satisfied is enough
        ok = True
        for k, v in alt.items():
            if k == 'level':
                need = v if isinstance(v, int) else v.get('level', 0)
                if isinstance(v, dict) and v.get('class'):
                    cname = v['class'].get('name')
                    if ctx['class_levels_by_name'].get(cname, 0) < need:
                        ok = False
                elif ctx['level'] < need:
                    ok = False
            elif k == 'ability':
                if not any(all(ctx['scores'].get(a, 0) >= n for a, n in d.items()) for d in v):
                    ok = False
            elif k in ('spellcasting', 'spellcasting2020', 'spellcastingFeature', 'spellcastingPrepared'):
                if not ctx['spellcasting']:
                    ok = False
            elif k == 'proficiency':
                for d in v:
                    for typ, what in d.items():
                        if typ == 'armor' and what not in ctx['armor']:
                            ok = False
                        if typ == 'weapon' and what not in ctx['weapons']:
                            ok = False
            elif k == 'race':
                names = [r.get('name', '').lower() for r in v]
                if not any(n in ctx['race_name'].lower() for n in names):
                    ok = False
            elif k in ('feat',):
                have = ctx['feat_names']
                if not any(f.split('|')[0].lower() in have for f in v):
                    ok = False
            elif k == 'background':
                names = [b.get('name', '').lower() for b in v]
                if ctx['background_name'].lower() not in names:
                    ok = False
            elif k == 'pact' and 'pact_names' in ctx:
                # 2014 invocations: "Pact of the Blade" etc. is a class feature option
                if f'pact of the {str(v).lower()}' not in ctx['pact_names']:
                    ok = False
            elif k == 'optionalfeature' and 'optfeature_ids' in ctx:
                if not any(str(x).lower() in ctx['optfeature_ids'] for x in v):
                    ok = False
            elif k == 'spell' and 'known_spells' in ctx:
                if not all(_knows_spell_for(req, ctx) for req in v):
                    ok = False
            elif k in ('other', 'otherSummary', 'campaign', 'exclusiveFeatCategory', 'note'):
                notes.append(strip_tags(str(v if not isinstance(v, dict) else v.get('entry', v))))
            else:
                notes.append(f'{k}: not checked')
        if ok:
            return True, notes
    return False, notes


# --- the engine -------------------------------------------------------------

class _Ctx:
    """Mutable accumulator while computing a sheet."""

    def __init__(self, doc):
        b = doc.get('build') or {}
        self.doc = doc
        self.build = b
        self.state = doc.get('state') or {}
        self.edition = 'one' if b.get('edition') == 'one' else 'classic'
        self.sources = set(b.get('sources') or [])
        self.picks = dict(b.get('choices') or {})
        self.choices: list = []
        self.errors: list = []
        self.warnings: list = []
        self.grants = G.Grants()
        self.features: list = []
        self.feats: list = []      # (feat record, origin label, choice prefix)
        self.optfeatures: list = []
        self.pools = pools_for(self.edition)
        self.additional_spells: list = []   # {'spell': id, 'origin', 'mode': 'prepared'|'innate'|'known', 'ability'}
        self.flags: set = set()

    def allowed_source(self, src) -> bool:
        return not self.sources or src in self.sources

    def add_choice(self, ch: G.Choice):
        self.choices.append(ch)


def _load_classes(ctx: _Ctx):
    """[(class_record, class_id, class_level, first_char_level)] in order taken."""
    levels = ctx.build.get('levels') or []
    order = []
    counts: dict = {}
    for i, lv in enumerate(levels[:MAX_LEVEL]):
        cid = (lv or {}).get('class')
        if not cid:
            ctx.errors.append(f'Level {i + 1}: no class')
            continue
        counts[cid] = counts.get(cid, 0) + 1
        if cid not in [o[1] for o in order]:
            rec = C.get('class', cid)
            if rec is None:
                ctx.errors.append(f'Unknown class {cid!r}')
                continue
            order.append([rec, cid, 0, i + 1])
    for o in order:
        o[2] = counts[o[1]]
    return [tuple(o) for o in order]


def compute(doc: dict) -> dict:
    ctx = _Ctx(doc)
    b = ctx.build
    picks = ctx.picks
    level = min(MAX_LEVEL, len(b.get('levels') or []))

    # ---- ability score base ----
    method = b.get('ability_method') or 'manual'
    base = {a: int((b.get('base_scores') or {}).get(a, 10) or 10) for a in ABILITIES}
    _validate_base_scores(ctx, method, base)

    race = C.get('race', b.get('race') or '') if b.get('race') else None
    race_for_summary = race
    background = C.get('background', b.get('background') or '') if b.get('background') else None
    if b.get('race') and race is None:
        ctx.errors.append(f'Unknown species {b.get("race")!r}')
    if b.get('background') and background is None:
        ctx.errors.append(f'Unknown background {b.get("background")!r}')

    if race:
        race = _select_race_version(ctx, race)
        if race:
            race_for_summary = race

    classes = _load_classes(ctx)
    class_levels_by_name = {}
    for rec, cid, clv, _ in classes:
        class_levels_by_name[rec['name']] = class_levels_by_name.get(rec['name'], 0) + clv

    # ---- species ----
    if race:
        _apply_race(ctx, race)
    # ---- background ----
    if background:
        _apply_background(ctx, background)

    # 2024 rules: every character knows Common plus two Standard languages
    # (XPHB p.36); species/backgrounds no longer grant languages.
    if ctx.edition == 'one':
        ctx.grants.languages.add('common')
        G.expand_list('language', [{'anyStandard': 2}], 'origin:language', 'Languages', ctx.picks,
                      ctx.pools, ctx.grants, ctx.choices)

    # ---- classes: proficiencies, features, subclasses, ASIs ----
    subclasses = {}
    for idx, (rec, cid, clv, first_lvl) in enumerate(classes):
        sub = _apply_class(ctx, rec, cid, clv, primary=(idx == 0))
        if sub:
            subclasses[cid] = sub

    # Multiclass prerequisites (checked against final base+racial scores is the
    # common table ruling; the rules say "current" scores at the time -- we
    # check the scores as built, and report rather than block).
    if len(classes) > 1:
        pre_scores = {a: base[a] + ctx.grants.abilities.get(a, 0) for a in ABILITIES}
        for rec, cid, clv, _ in classes:
            req = multiclass_requirement(rec)
            if req and not _check_requirements(req, pre_scores):
                ctx.warnings.append(f'Multiclass requirement not met for {rec["name"]}: {_req_text(req)}')

    # ---- feats chosen anywhere (expanded iteratively: a feat can grant choices) ----
    feat_ctx_level = level
    _apply_feats(ctx, feat_ctx_level, base, class_levels_by_name)

    # ---- expertise / prose choices that depend on proficiencies ----
    _apply_feature_choices(ctx, classes, subclasses)

    # ---- optional features (invocations, maneuvers, metamagic, styles) ----
    _apply_optional_features(ctx, classes, subclasses, level)
    _check_optional_feature_prereqs(ctx, level, class_levels_by_name)

    feat_names = {f[0]['name'].lower() for f in ctx.feats}
    feature_names = {f['name'] for f in ctx.features} | {o['name'] for o in ctx.optfeatures}

    # ---- numeric layers: magic items, toggled effects, always-on features ----
    m = E.Mods()
    scanned = _scan_inventory(ctx)
    for _idx, _ent, rec, active in scanned:
        if rec:
            E.apply_item(m, rec, active=active, label=rec['name'])
    available_fx = E.available_effects(feature_names, class_levels_by_name)
    on_fx = [k for k in (ctx.state.get('effects') or []) if k in {e['key'] for e in available_fx}]
    if ctx.state.get('mage_armor') and 'mage_armor' not in on_fx:      # pre-effects flag
        on_fx.append('mage_armor')
    totem = next((f.split(':')[-1] for f in ctx.flags if f.startswith('option:Totem Spirit:')), None)
    E.apply_effects(m, on_fx, edition=ctx.edition, feature_names=feature_names,
                    rage_damage=rage_damage(classes), totem=totem)
    E.apply_features(m, feature_names=feature_names, options=ctx.flags, edition=ctx.edition,
                     class_levels=class_levels_by_name, feat_names=feat_names)

    # ---- ability scores ----
    scores = {}
    for a in ABILITIES:
        cap = max(20, ctx.grants.ability_max.get(a, 20))
        scores[a] = min(cap, base[a] + ctx.grants.abilities.get(a, 0))
        if m.ability_bonus.get(a):
            scores[a] = max(scores[a], min(cap, scores[a] + m.ability_bonus[a]))
    _apply_capstones(ctx, scores, class_levels_by_name)
    for a, v in m.ability_static.items():
        scores[a] = max(scores[a], v)          # Belt of Giant Strength etc. never lower a score
    mods = {a: ability_mod(scores[a]) for a in ABILITIES}
    pb = proficiency_bonus(max(1, level)) + m.pb_bonus

    def R(ref):
        return m.resolve(ref, mods, pb)

    # ---- spellcasting ----
    spellcasting = _spellcasting(ctx, classes, subclasses, mods, pb)
    for c in spellcasting['casters']:
        c['dc'] += m.spell_dc
        c['attack'] += m.spell_attack

    # ---- derived numbers ----
    skills_prof = ctx.grants.skills
    expertise = ctx.grants.expertise & skills_prof
    jack = 'Jack of All Trades' in feature_names
    remarkable = 'Remarkable Athlete' in feature_names and ctx.edition == 'classic'
    check_all = sum(R(x) for x in m.check_all)

    saves = {}
    save_all = sum(R(x) for x in m.save_all)
    universal = 'Diamond Soul' in feature_names or 'Disciplined Survivor' in feature_names
    for a in ABILITIES:
        prof = a in ctx.grants.saves or universal
        saves[a] = {'mod': mods[a] + (pb if prof else 0) + save_all + sum(R(x) for x in m.save.get(a, [])),
                    'proficient': prof}

    skills = {}
    for sk, ab in SKILLS.items():
        if sk in expertise:
            bonus, p = pb * 2, 2
        elif sk in skills_prof:
            bonus, p = pb, 1
        elif remarkable and ab in ('str', 'dex', 'con'):
            bonus, p = -(-pb // 2), 0.5
        elif jack:
            bonus, p = pb // 2, 0.5
        else:
            bonus, p = 0, 0
        extra = check_all + sum(R(x) for x in m.skill.get(sk, []))
        skills[sk] = {'ability': ab, 'mod': mods[ab] + bonus + extra, 'proficiency': p}
    passive = {k: 10 + skills[k]['mod'] for k in ('perception', 'investigation', 'insight')}
    if 'observant' in feat_names and ctx.edition == 'classic':
        passive['perception'] += 5
        passive['investigation'] += 5

    # Initiative is a DEX check: Jack of All Trades and (2014) Remarkable
    # Athlete both add half proficiency; they don't stack, take the larger.
    half = pb // 2 if jack else 0
    init = mods['dex'] + check_all
    for ref in m.initiative:
        if ref == ('remarkable',):
            half = max(half, -(-pb // 2))
        else:
            init += R(ref)
    init += half
    if 'alert' in feat_names:
        init += 5 if ctx.edition == 'classic' else pb

    # ---- hit points ----
    hp_max, hit_dice = _hit_points(ctx, classes, mods, feat_names, feature_names, class_levels_by_name)

    # ---- inventory: AC, attacks, weight ----
    spell_abilities = sorted({c['ability'] for c in spellcasting['casters']})
    inv = _inventory(ctx, scores, mods, pb, feature_names, class_levels_by_name, feat_names, m,
                     spell_abilities, level)

    _attack_riders(inv['attacks'], classes)

    speed = _speed(ctx, race, feature_names, class_levels_by_name, inv, m)

    resources = _resources(ctx, classes, subclasses, mods, pb, feature_names, class_levels_by_name)
    resources += _detected_resources(ctx, resources, mods, pb, race, scanned)

    # ---- assemble choices with completeness / validation ----
    choice_out = []
    seen = set()
    for ch in ctx.choices:
        if ch.id in seen:
            continue
        seen.add(ch.id)
        val = picks.get(ch.id)
        err = G.validate_choice(ch, val) if val not in (None, [], {}) else None
        if err:
            ctx.errors.append(err)
        d = ch.to_dict()
        d['value'] = val
        d['complete'] = G.is_complete(ch, val) and not err
        choice_out.append(d)
    stale = [k for k in picks if k not in seen]

    senses = dict(inv.get('senses') or {})
    if race and race.get('darkvision'):
        senses['darkvision'] = max(senses.get('darkvision', 0), int(race['darkvision']))
    if race and race.get('blindsight'):
        senses['blindsight'] = int(race['blindsight'])

    return {
        'name': ctx.doc.get('name') or b.get('name') or 'Unnamed',
        'edition': ctx.edition,
        'level': level,
        'proficiency_bonus': pb,
        'classes': [{
            'id': cid, 'name': rec['name'], 'source': rec['source'], 'level': clv,
            'hit_die': (rec.get('hd') or {}).get('faces', 8),
            'subclass': ({'id': subclass_id(subclasses[cid]), 'name': subclasses[cid]['name'],
                          'short_name': subclasses[cid].get('shortName')} if cid in subclasses else None),
            'table': class_table(rec, clv),
        } for rec, cid, clv, _ in classes],
        'race': ({'id': entity_id(race_for_summary['name'], race_for_summary['source']),
                  'name': race_for_summary['name'], 'source': race_for_summary['source'],
                  'size': race_for_summary.get('size') or ['M'],
                  'creature_type': (race_for_summary.get('creatureTypes') or ['humanoid'])[0]}
                 if race_for_summary else None),
        'background': ({'id': entity_id(background['name'], background['source']),
                        'name': background['name'], 'source': background['source']} if background else None),
        'abilities': {a: {'score': scores[a], 'base': base[a], 'mod': mods[a], 'save': saves[a]['mod'],
                          'save_proficient': saves[a]['proficient']} for a in ABILITIES},
        'skills': skills,
        'passive': passive,
        'initiative': init,
        'speed': speed,
        'ac': inv['ac'],
        'hp_max': hp_max,
        'hit_dice': hit_dice,
        'senses': senses,
        'resist': sorted((ctx.grants.resist | set(_lower_strings(race.get('resist') if race else [])) | m.resist)
                         - {''}),
        'immune': sorted(set(_lower_strings(race.get('immune') if race else [])) | m.immune),
        'vulnerable': sorted(m.vulnerable),
        'condition_immune': sorted(set(_lower_list(race.get('conditionImmune') if race else []))
                                   | m.condition_immune),
        'effects': {'available': available_fx, 'active': on_fx},
        'option_flags': sorted(f for f in ctx.flags if f.startswith('option:')),
        'pact_capable': inv['pact_capable'],
        'notes': m.notes,
        'proficiencies': {
            'armor': sorted(ctx.grants.armor), 'weapons': sorted(ctx.grants.weapons),
            'tools': sorted(ctx.grants.tools), 'languages': sorted(ctx.grants.languages),
            'skills': sorted(skills_prof), 'expertise': sorted(expertise),
            'saves': sorted(ctx.grants.saves),
        },
        'features': ctx.features,
        'feats': [{'id': entity_id(f[0]['name'], f[0]['source']), 'name': f[0]['name'], 'source': f[0]['source'],
                   'origin': f[1], 'entries': f[0].get('entries') or []} for f in ctx.feats],
        'optional_features': ctx.optfeatures,
        'attacks': inv['attacks'],
        'inventory': inv['items'],
        'carry': inv['carry'],
        'attuned': inv['attuned'],
        'spellcasting': spellcasting,
        'resources': resources,
        'choices': choice_out,
        'pending': [c['id'] for c in choice_out if not c['complete']],
        'stale_choices': stale,
        'errors': ctx.errors,
        'warnings': ctx.warnings,
    }


# --- base scores --------------------------------------------------------------

def _validate_base_scores(ctx, method, base):
    vals = [base[a] for a in ABILITIES]
    if method == 'standard':
        if sorted(vals) != sorted(STANDARD_ARRAY):
            ctx.errors.append('Standard array must assign 15, 14, 13, 12, 10, 8 once each')
    elif method == 'pointbuy':
        if any(v not in POINT_BUY_COST for v in vals):
            ctx.errors.append('Point buy scores must be between 8 and 15')
        elif sum(POINT_BUY_COST[v] for v in vals) > POINT_BUY_BUDGET:
            ctx.errors.append(f'Point buy exceeds {POINT_BUY_BUDGET} points')
    elif method == 'roll':
        rolled = ctx.build.get('rolled_scores')
        if rolled and sorted(vals) != sorted(int(x) for x in rolled):
            ctx.errors.append('Assigned scores do not match the rolled set')
        if any(not (3 <= v <= 18) for v in vals):
            ctx.errors.append('Rolled scores must be 3-18')
    else:
        if any(not (1 <= v <= 30) for v in vals):
            ctx.errors.append('Manual scores must be 1-30')


def _req_text(req) -> str:
    if 'or' in req:
        return ' or '.join(_req_text(r) for r in req['or'])
    return ' and '.join(f'{a.upper()} {n}' for a, n in req.items())


# --- species / background ----------------------------------------------------

def _species_version_label(version: dict) -> str:
    name = version['name']
    if '; ' in name:
        before, label = name.rsplit('; ', 1)
        if before.count('(') > before.count(')') and label.endswith(')'):
            label = label[:-1]
        return label
    if name.endswith(')') and '(' in name:
        return name.rsplit('(', 1)[1][:-1]
    return name


def _select_race_version(ctx: _Ctx, base: dict) -> dict | None:
    """Add a base species' version choice and return its selected full record.

    A legacy build can store a version id directly. Resolve that record through
    the same cached index so any content normalization also applies, but do not
    add a new choice.

    Until a valid version is selected, no base grants are expanded. Version
    records are authoritative and can replace more than one related field, so
    applying the base would expose independent choices that disagree with the
    eventual version.
    """
    if base.get('_versionBase_isVersion'):
        direct_id = entity_id(base['name'], base['source'])
        return next((version for version in C.race_versions(
            base.get('_versionBase_name'), base.get('_versionBase_source'))
            if entity_id(version['name'], version['source']) == direct_id), base)
    versions = C.race_versions(base['name'], base['source'])
    if not versions:
        return base
    options = [{'value': entity_id(v['name'], v['source']), 'label': _species_version_label(v)}
               for v in versions]
    label = ('Draconic Ancestry'
             if base['name'] == 'Dragonborn' and base['source'] in ('PHB', 'XPHB')
             else f'{base["name"]}: choose a version')
    origin = f'Species ({base["name"]})'
    ctx.add_choice(G.Choice(id='race:version', kind='species_version', label=label,
                            options=options, origin=origin))
    picked = ctx.picks.get('race:version')
    picked = picked[0] if isinstance(picked, list) and picked else picked
    if picked is None:
        return None
    return next((version for version in versions
                 if entity_id(version['name'], version['source']) == str(picked).lower()), None)


def _apply_race(ctx: _Ctx, race: dict):
    org = f'Species ({race["name"]})'
    g = ctx.grants
    if ctx.edition == 'classic' and ctx.build.get('custom_origin') and race.get('ability'):
        # Tasha's Customizing Your Origin: the same total, placed freely.
        alt = race['ability'][0] if isinstance(race['ability'], list) else race['ability']
        amounts = sorted([v for k, v in alt.items() if k in ABILITIES and isinstance(v, int)], reverse=True)
        if 'choose' in alt:
            ch = alt['choose']
            amounts += [int(ch.get('amount', 1))] * int(ch.get('count', 1))
        G.expand_ability([{'choose': {'weighted': {'from': list(ABILITIES), 'weights': amounts}}}],
                         'race:ability:custom', org, ctx.picks, g, ctx.choices)
    else:
        G.expand_ability(race.get('ability'), 'race:ability', org, ctx.picks, g, ctx.choices)
    for kind, fld in (('skill', 'skillProficiencies'), ('language', 'languageProficiencies'),
                      ('tool', 'toolProficiencies'), ('weapon', 'weaponProficiencies'),
                      ('armor', 'armorProficiencies'), ('skillToolLanguage', 'skillToolLanguageProficiencies')):
        G.expand_list(kind, race.get(fld), f'race:{kind}', org, ctx.picks, ctx.pools, g, ctx.choices)
    if race.get('resist'):
        g.resist.update(_lower_strings(race['resist']))
        choices = [r for r in race['resist'] if not isinstance(r, str)]
        G.expand_list('resist', choices, 'race:resist', org, ctx.picks, ctx.pools, g, ctx.choices)
    _feat_grants(ctx, race.get('feats'), 'race:feat', org)
    _additional_spells(ctx, race.get('additionalSpells'), 'race:spells', org, None, race.get('entries'))
    size = race.get('size') or ['M']
    if len(size) > 1:
        ctx.add_choice(G.Choice(id='race:size', kind='size', label=f'{org}: size', options=list(size), origin=org))
    for e in race.get('entries') or []:
        if isinstance(e, dict) and e.get('name'):
            ctx.features.append({'name': e['name'], 'source': race['source'], 'level': 1, 'origin': org,
                                 'entries': e.get('entries') or []})


def _apply_background(ctx: _Ctx, bg: dict):
    org = f'Background ({bg["name"]})'
    g = ctx.grants
    G.expand_ability(bg.get('ability'), 'bg:ability', org, ctx.picks, g, ctx.choices)
    for kind, fld in (('skill', 'skillProficiencies'), ('language', 'languageProficiencies'),
                      ('tool', 'toolProficiencies'), ('skillToolLanguage', 'skillToolLanguageProficiencies')):
        G.expand_list(kind, bg.get(fld), f'bg:{kind}', org, ctx.picks, ctx.pools, g, ctx.choices)
    _feat_grants(ctx, bg.get('feats'), 'bg:feat', org)
    _additional_spells(ctx, bg.get('additionalSpells'), 'bg:spells', org, None, bg.get('entries'))
    for e in bg.get('entries') or []:
        if isinstance(e, dict) and e.get('name') and e.get('type') != 'list':
            ctx.features.append({'name': e['name'], 'source': bg['source'], 'level': 1, 'origin': org,
                                 'entries': e.get('entries') or []})


def _feat_grants(ctx: _Ctx, feats_field, cid: str, origin: str, category_default=None):
    """A ``feats`` field: fixed feats, ``any: n`` or ``anyFromCategory``."""
    if not feats_field:
        return
    alts = feats_field if isinstance(feats_field, list) else [feats_field]
    alt = alts[0]
    if len(alts) > 1:
        ctx.add_choice(G.Choice(id=f'{cid}#alt', kind='alternative', label=f'{origin}: feat option',
                                options=[{'value': i, 'label': G.describe_alt(a)} for i, a in enumerate(alts)],
                                origin=origin))
        sel = ctx.picks.get(f'{cid}#alt')
        try:
            alt = alts[int(sel[0] if isinstance(sel, list) else sel)]
        except (TypeError, ValueError, IndexError):
            return
    n = 0
    for key, val in alt.items():
        if val is True:
            ctx.grants.feats.append((parse_ref(key, 'feat'), origin, f'{cid}:{n}'))
            n += 1
        elif key == 'any':
            for i in range(int(val)):
                sub = f'{cid}:any{i}'
                ctx.add_choice(G.Choice(id=sub, kind='feat', label=f'{origin}: choose a feat', origin=origin,
                                        query={'category': category_default}))
                v = ctx.picks.get(sub)
                if v:
                    ctx.grants.feats.append((str(v[0] if isinstance(v, list) else v).lower(), origin, sub))
        elif key == 'anyFromCategory':
            cats = val.get('category') or []
            for i in range(int(val.get('count', 1))):
                sub = f'{cid}:cat{i}'
                ctx.add_choice(G.Choice(id=sub, kind='feat', label=f'{origin}: choose a feat ({"/".join(cats)})',
                                        origin=origin, query={'category': cats}))
                v = ctx.picks.get(sub)
                if v:
                    ctx.grants.feats.append((str(v[0] if isinstance(v, list) else v).lower(), origin, sub))


def _additional_spells(ctx: _Ctx, field, cid, origin, class_level, owner_entries=None):
    """Record always-available spells from ``additionalSpells`` (fixed ones
    only; ``choose`` entries become spell choices)."""
    if not field:
        return
    alts = field if isinstance(field, list) else [field]
    alt = alts[0]
    if len(alts) > 1:
        # e.g. Circle of the Land's terrain lists, a Genie's kind: pick one.
        ctx.add_choice(G.Choice(id=f'{cid}#alt', kind='alternative', label=f'{origin}: spell list',
                                options=[{'value': i, 'label': a.get('name') or f'Option {i + 1}'}
                                         for i, a in enumerate(alts) if isinstance(a, dict)],
                                origin=origin))
        sel = ctx.picks.get(f'{cid}#alt')
        try:
            alt = alts[int(sel[0] if isinstance(sel, list) else sel)]
            cid = f'{cid}#{int(sel[0] if isinstance(sel, list) else sel)}'
        except (TypeError, ValueError, IndexError):
            return
    if not isinstance(alt, dict):
        return
    ability = alt.get('ability')
    if isinstance(ability, dict) and 'choose' in ability:
        ctx.add_choice(G.Choice(id=f'{cid}:ability', kind='ability_pick', label=f'{origin}: spellcasting ability',
                                options=list(ability['choose']), origin=origin))
        v = ctx.picks.get(f'{cid}:ability')
        ability = (v[0] if isinstance(v, list) else v) if v else None
    char_level = len(ctx.build.get('levels') or [])
    gate_level = class_level if class_level is not None else char_level
    n = 0
    for mode in ('known', 'prepared', 'innate', 'expanded'):
        blk = alt.get(mode)
        if not isinstance(blk, dict):
            continue
        for lvl_key, spells in blk.items():
            lvl_key = str(lvl_key)
            if lvl_key.startswith('s'):        # expanded lists keyed by spell level
                need = 0
            elif lvl_key == '_':
                need = 0
            else:
                try:
                    need = int(lvl_key)
                except ValueError:
                    need = 0
            if gate_level < need:
                continue
            for sp, uses, recharge in _iter_spell_uses(spells):
                if isinstance(sp, str):
                    name = sp.split('#')[0]
                    ctx.additional_spells.append({'spell': parse_ref(name, 'spell'), 'origin': origin,
                                                  'mode': mode, 'ability': ability,
                                                  'cantrip': sp.endswith('#c'), 'uses': uses,
                                                  'recharge': recharge, 'owner_entries': owner_entries})
                elif isinstance(sp, dict) and 'choose' in sp:
                    sub = f'{cid}:{mode}:{n}'
                    n += 1
                    filt = sp['choose'] if isinstance(sp['choose'], str) else ''
                    count = int(sp.get('count', 1)) if isinstance(sp, dict) else 1
                    ctx.add_choice(G.Choice(id=sub, kind='spell', label=f'{origin}: choose a spell ({filt})',
                                            count=count, origin=origin, query={'filter': filt}))
                    picked = [str(v).lower() for v in (ctx.picks.get(sub) or [])]
                    if picked:
                        from systems.dnd5e.rules.spells import spells_for_filter
                        allowed = {r['id'] for r in spells_for_filter(filt, ctx.edition)}
                        for v in picked:
                            if v not in allowed:
                                ctx.errors.append(f'{origin}: {v.split("|")[0].title()} is not a valid choice '
                                                  f'({filt})')
                        if len(picked) > count:
                            ctx.errors.append(f'{origin}: pick at most {count}')
                    for v in (ctx.picks.get(sub) or []):
                        vrec = C.get('spell', str(v).lower())
                        ctx.additional_spells.append({'spell': str(v).lower(), 'origin': origin, 'mode': mode,
                                                      'ability': ability,
                                                      'cantrip': bool(vrec) and vrec.get('level') == 0,
                                                      'uses': uses, 'recharge': recharge,
                                                      'owner_entries': owner_entries})


def _iter_spell_uses(spells, uses=None, recharge=None):
    """Yield (spell entry, uses, recharge) from an additionalSpells block:
    ``{"daily": {"1": [...]}}`` -> 1/long, ``{"rest": {"1e": [...]}}`` ->
    1/short ("each": every listed spell gets its own use), ``{"will": [...]}``
    -> at will, ``{"ritual": [...]}`` -> ritual only."""
    if isinstance(spells, list):
        for sp in spells:
            yield sp, uses, recharge
    elif isinstance(spells, dict):
        for key, val in spells.items():
            if key in ('daily', 'rest') and isinstance(val, dict):
                for n_key, lst in val.items():
                    try:
                        n = int(str(n_key).rstrip('e'))
                    except ValueError:
                        n = 1
                    yield from _iter_spell_uses(lst, n, 'long' if key == 'daily' else 'short')
            elif key in ('will', 'ritual'):
                yield from _iter_spell_uses(val, None, key)
            else:
                yield from _iter_spell_uses(val, uses, recharge)


def _iter_spell_entries(spells):
    if isinstance(spells, list):
        for s in spells:
            yield s
    elif isinstance(spells, dict):
        # {"daily": {"1": [...]}, "rest": {...}, "will": [...], "ritual": [...]}
        for v in spells.values():
            yield from _iter_spell_entries(v)


# --- classes -------------------------------------------------------------------

def _apply_class(ctx: _Ctx, rec: dict, cid: str, clv: int, primary: bool):
    org = f'{rec["name"]}'
    g = ctx.grants
    if primary:
        sp = rec.get('startingProficiencies') or {}
        g.saves |= set(rec.get('proficiency') or [])
        g.armor |= set(_lower_list(sp.get('armor')))
        g.weapons |= set(_weapon_prof_names(sp.get('weapons')))
        for kind, fld in (('skill', 'skills'), ('tool', 'toolProficiencies')):
            G.expand_list(kind, sp.get(fld), f'cls:{cid}:{kind}', org, ctx.picks, ctx.pools, g, ctx.choices)
        if sp.get('tools') and not sp.get('toolProficiencies'):
            for t in sp['tools']:
                ctx.flags.add(f'tool_text:{strip_tags(t)}')
    else:
        mc = (rec.get('multiclassing') or {}).get('proficienciesGained') or {}
        g.armor |= set(_lower_list(mc.get('armor')))
        g.weapons |= set(_weapon_prof_names(mc.get('weapons')))
        for kind, fld in (('skill', 'skills'), ('tool', 'toolProficiencies')):
            G.expand_list(kind, mc.get(fld), f'mc:{cid}:{kind}', f'{org} (multiclass)', ctx.picks,
                          ctx.pools, g, ctx.choices)

    feats = []
    for lvl in range(1, clv + 1):
        groups = (rec.get('classFeatures') or [])
        if lvl - 1 < len(groups):
            for f in _features_of(groups[lvl - 1]):
                # Tasha's "optional class features" are opt-in, as on D&D Beyond.
                if f.get('variant') and not ctx.build.get('optional_class_features'):
                    continue
                # "<Subclass> feature" placeholders just point at the subclass.
                if f.get('gainSubclassFeature') and f['name'].lower().endswith(' feature'):
                    continue
                f['origin'] = org
                f['level'] = lvl
                feats.append(f)

    # subclass
    sub = None
    sub_level = next((f['level'] for f in feats if f.get('gainSubclassFeature')), None)
    if sub_level is None:
        # the feature might be above our level; find the first gain level overall
        for lvl, grp in enumerate(rec.get('classFeatures') or [], start=1):
            if any(f.get('gainSubclassFeature') for f in _features_of(grp)):
                sub_level = lvl
                break
    if sub_level and clv >= sub_level:
        opts = [subclass_id(s) for s in C.subclasses_for(rec['name'], rec['source'])
                if ctx.allowed_source(s.get('source'))]
        ctx.add_choice(G.Choice(id=f'subclass:{cid}', kind='subclass', label=f'{org}: {rec.get("subclassTitle") or "Subclass"}',
                                options=opts, origin=org))
        sel = ctx.picks.get(f'subclass:{cid}')
        sel = sel[0] if isinstance(sel, list) and sel else sel
        if sel:
            sub = C.get('subclass', str(sel))
            if sub is None:
                ctx.errors.append(f'Unknown subclass {sel!r}')
    if sub:
        for grp in sub.get('subclassFeatures') or []:
            for f in _features_of(grp):
                if (f.get('level') or 99) <= clv:
                    f['origin'] = f'{org}: {sub["name"]}'
                    feats.append(f)
        _additional_spells(ctx, sub.get('additionalSpells'), f'sub:{cid}:spells', f'{sub["name"]}', clv)
    _additional_spells(ctx, rec.get('additionalSpells'), f'cls:{cid}:spells', org, clv)

    # ASI / feat slots
    asi_n = 0
    for f in feats:
        if f['name'] == 'Ability Score Improvement' and f.get('className') == rec['name'] and not f.get('subclass'):
            asi_n += 1
            _asi_choice(ctx, f'asi:{cid}:{f["level"]}', f'{org} level {f["level"]}: Ability Score Improvement')
    # 2024 featProgression (Fighting Style / Epic Boon...)
    for fp in rec.get('featProgression') or []:
        prog = fp.get('progression') or {}
        cats = fp.get('category') or []
        total = 0
        for lvl_s, n in (prog.items() if isinstance(prog, dict) else enumerate(prog, start=1)):
            if int(lvl_s) <= clv:
                total += int(n) if isinstance(prog, dict) else 0
        if isinstance(prog, list) and clv >= 1:
            total = int(prog[clv - 1])
        for i in range(total):
            sub_id = f'featprog:{cid}:{fp.get("name")}:{i}'
            ctx.add_choice(G.Choice(id=sub_id, kind='feat', label=f'{org}: {fp.get("name")}',
                                    origin=org, query={'category': cats}))
            v = ctx.picks.get(sub_id)
            if v:
                ctx.grants.feats.append((str(v[0] if isinstance(v, list) else v).lower(), f'{org}: {fp.get("name")}', sub_id))
    ctx.features.extend(_resolve_feature_options(ctx, feats, cid))
    return sub


def _resolve_feature_options(ctx: _Ctx, feats: list, cid: str) -> list:
    """Turn in-feature option blocks into choices and keep only chosen children.

    A later feature can carry the same option names (Totem Spirit at 3, Aspect
    of the Beast at 6 both list Bear/Eagle/...), so children are matched to
    their own group key, never by name alone."""
    chosen: dict = {}
    for f in feats:
        for g in f.get('option_groups') or []:
            choice_id = f'featopt:{cid}:{g["key"]}'.lower()
            ctx.add_choice(G.Choice(id=choice_id, kind='feature_option', label=f'{f["origin"]}: {g["parent"]}',
                                    count=g['count'], options=list(g['names']), origin=f['origin']))
            vals = ctx.picks.get(choice_id) or []
            vals = vals if isinstance(vals, list) else [vals]
            chosen[g['key']] = {str(v) for v in vals[:g['count']]}
            for v in chosen[g['key']]:
                ctx.flags.add(f'option:{g["parent"]}:{v.lower()}')
    out = []
    for f in feats:
        key = f.get('option_of')
        if key is None:
            out.append(f)
        elif f['name'] in chosen.get(key, set()):
            f['origin'] = f.get('origin') or ''
            out.append(f)
    return out


def _weapon_prof_names(weapons) -> list:
    out = []
    for w in weapons or []:
        if isinstance(w, dict):
            out += [k.split('|')[0].lower() for k, v in w.items() if v is True]
        else:
            # "{@item dagger|phb|daggers}" -> dagger ; "simple" -> simple
            m = re.match(r'\{@item ([^|}]+)', str(w))
            out.append((m.group(1) if m else strip_tags(str(w))).lower().rstrip('s')
                       if m else strip_tags(str(w)).lower())
    return out


def _asi_choice(ctx: _Ctx, cid: str, label: str):
    """An ASI slot: either +2/+1+1 to scores, or a feat."""
    ctx.add_choice(G.Choice(id=cid, kind='asi', label=label, origin=label,
                            options=['asi', 'feat']))
    v = ctx.picks.get(cid)
    if not isinstance(v, dict):
        return
    if v.get('feat'):
        ctx.grants.feats.append((str(v['feat']).lower(), label, cid))
    elif isinstance(v.get('asi'), dict):
        inc = {a: int(n) for a, n in v['asi'].items() if a in ABILITIES}
        if sorted(inc.values(), reverse=True) in ([2], [1, 1]):
            for a, n in inc.items():
                ctx.grants.abilities[a] = ctx.grants.abilities.get(a, 0) + n
        else:
            ctx.errors.append(f'{label}: an ASI is +2 to one score or +1 to two')


def _apply_feats(ctx: _Ctx, level, base, class_levels_by_name):
    """Expand every chosen feat (they can grant more choices, incl. more feats)."""
    done = set()
    guard = 0
    while guard < 50:
        guard += 1
        todo = [f for f in ctx.grants.feats if f[2] not in done]
        if not todo:
            break
        for fid, origin, prefix in todo:
            done.add(prefix)
            rec = C.get('feat', fid)
            if rec is None:
                ctx.errors.append(f'Unknown feat {fid!r} ({origin})')
                continue
            scores_now = {a: base[a] + ctx.grants.abilities.get(a, 0) for a in ABILITIES}
            ok, notes = prereq_ok(rec.get('prerequisite'), {
                'level': level, 'scores': scores_now,
                'spellcasting': any(f['name'] in ('Spellcasting', 'Pact Magic') for f in ctx.features),
                'armor': ctx.grants.armor, 'weapons': ctx.grants.weapons,
                'race_name': (ctx.build.get('race') or ''), 'feat_names': {f[0]['name'].lower() for f in ctx.feats},
                'background_name': (ctx.build.get('background') or '').split('|')[0],
                'class_levels_by_name': class_levels_by_name,
            })
            if not ok:
                ctx.warnings.append(f'{rec["name"]}: prerequisite not met')
            if not rec.get('repeatable') and any(f[0]['name'] == rec['name'] and f[0]['source'] == rec['source']
                                                for f in ctx.feats):
                ctx.errors.append(f'{rec["name"]} taken twice (not repeatable)')
            ctx.feats.append((rec, origin, prefix))
            org = f'Feat ({rec["name"]})'
            g = ctx.grants
            G.expand_ability(rec.get('ability'), f'{prefix}:ability', org, ctx.picks, g, ctx.choices)
            for kind, fld in (('skill', 'skillProficiencies'), ('language', 'languageProficiencies'),
                              ('tool', 'toolProficiencies'), ('weapon', 'weaponProficiencies'),
                              ('armor', 'armorProficiencies'), ('save', 'savingThrowProficiencies'),
                              ('expertise', 'expertise'), ('skillToolLanguage', 'skillToolLanguageProficiencies')):
                val = rec.get(fld)
                if kind == 'expertise' and val:
                    val = [{('anyProficientSkill' if k == 'anyProficientSkill' else k): v for k, v in a.items()}
                           for a in val]
                G.expand_list(kind, val, f'{prefix}:{kind}', org, ctx.picks, ctx.pools, g, ctx.choices)
            if rec.get('resist'):
                G.expand_list('resist', [{r: True} if isinstance(r, str) else r for r in rec['resist']],
                              f'{prefix}:resist', org, ctx.picks, ctx.pools, g, ctx.choices)
            _additional_spells(ctx, rec.get('additionalSpells'), f'{prefix}:spells', org, None,
                               rec.get('entries'))
            for ofp in rec.get('optionalfeatureProgression') or []:
                ctx.flags.add(f'featopt:{prefix}')
                _optfeat_choice(ctx, ofp, f'{prefix}:opt', org, level, star_count=True)


def _apply_capstones(ctx: _Ctx, scores, class_levels_by_name):
    names = {f['name'] for f in ctx.features}
    cap = 24 if ctx.edition == 'classic' else 25
    if 'Primal Champion' in names:
        for a in ('str', 'con'):
            scores[a] = min(cap, scores[a] + 4)
    if 'Body and Mind' in names and ctx.edition == 'one':
        for a in ('dex', 'wis'):
            scores[a] = min(25, scores[a] + 4)


def _proficient_skills_options(ctx: _Ctx) -> list:
    return sorted(ctx.grants.skills)


def _class_skill_list(rec) -> list:
    for s in ((rec.get('startingProficiencies') or {}).get('skills') or []):
        if isinstance(s, dict) and 'choose' in s:
            return [x.lower() for x in s['choose'].get('from') or []]
    return sorted(SKILLS)


def _apply_feature_choices(ctx: _Ctx, classes, subclasses):
    recs = {cid: rec for rec, cid, _, _ in classes}
    for f in ctx.features:
        cname = f.get('className')
        spec = (FEATURE_CHOICES.get((f['name'], cname, ctx.edition)) or FEATURE_CHOICES.get((f['name'], cname))
                or FEATURE_CHOICES.get((f['name'], '*')))
        if f['name'] == "Thieves' Cant" and cname == 'Rogue':
            ctx.grants.languages.add("thieves' cant")     # both editions know the cant itself
        if not spec or not cname:
            continue
        rec = next((r for r in recs.values() if r['name'] == cname), None)
        if rec is None:
            continue
        clv = next(c[2] for c in classes if c[0] is rec)
        for i, (kind, count, options) in enumerate(spec):
            cid = f'feature:{entity_id(rec["name"], rec["source"])}:{f["name"]}:{f["level"]}:{i}'.lower()
            if options == _EXPERTISE_ANY_PROF:
                options = _proficient_skills_options(ctx)
            elif options == '__languages__':
                options = ctx.pools.languages_all
            elif options == '__artisan__':
                options = ctx.pools.tools_artisan
            elif options == '__class_skills__':
                options = _class_skill_list(rec)
            elif options == '__mastery_weapons__':
                options = sorted(ctx.pools.weapons_simple + ctx.pools.weapons_martial)
            if count == '__mastery_count__':
                count = int(class_table(rec, clv).get('Weapon Mastery') or 2)
                cid = f'mastery:{entity_id(rec["name"], rec["source"])}'
            elif isinstance(count, tuple) and count[0] == 'levels':
                count = sum(1 for lv in count[1] if clv >= lv)       # more picks at higher levels
            ctx.add_choice(G.Choice(id=cid, kind=kind, label=f'{rec["name"]}: {f["name"]}', count=int(count),
                                    options=list(options), origin=f['name']))
            vals = ctx.picks.get(cid) or []
            vals = vals if isinstance(vals, list) else [vals]
            for v in vals[:int(count)]:
                v = str(v).lower()
                if kind == 'expertise':
                    ctx.grants.expertise.add(v)
                elif kind == 'skill':
                    ctx.grants.skills.add(v)
                elif kind == 'language':
                    ctx.grants.languages.add(v)
                elif kind == 'tool':
                    ctx.grants.tools.add(v)
                elif kind == 'weapon_mastery':
                    ctx.flags.add(f'mastery:{v}')
                elif kind == 'feature_option':
                    ctx.flags.add(f'option:{f["name"]}:{v}')
            if f['name'] == 'Dragon Ancestor':
                ctx.grants.languages.add('draconic')          # "You can speak, read, and write Draconic"
            if kind == 'feature_option' and vals:
                v = str(vals[0])
                if f['name'] == 'Divine Order' and v.lower() == 'protector':
                    ctx.grants.armor.add('heavy')
                    ctx.grants.weapons.add('martial')
                if f['name'] == 'Primal Order' and v.lower() == 'warden':
                    ctx.grants.armor.add('medium')
                    ctx.grants.weapons.add('martial')


def _optfeat_choice(ctx: _Ctx, ofp: dict, cid_prefix: str, origin: str, level: int, star_count=False):
    prog = ofp.get('progression') or {}
    if isinstance(prog, list):
        count = int(prog[min(level, len(prog)) - 1]) if level >= 1 and prog else 0
    else:
        count = 0
        for k, v in prog.items():
            if k == '*':
                count = int(v)
            elif int(k) <= level:
                count = max(count, int(v))
    if count <= 0:
        return
    types = ofp.get('featureType') or []
    cid = f'{cid_prefix}:{ofp.get("name")}'
    ctx.add_choice(G.Choice(id=cid, kind='optionalfeature', label=f'{origin}: {ofp.get("name")}', count=count,
                            origin=origin, query={'featureType': types, 'level': level}))
    for v in (ctx.picks.get(cid) or [])[:count]:
        rec = C.get('optionalfeature', str(v))
        if rec is None:
            ctx.errors.append(f'Unknown {ofp.get("name")} option {v!r}')
            continue
        if not set(rec.get('featureType') or []) & set(types):
            ctx.errors.append(f'{rec["name"]} is not a valid {ofp.get("name")} option')
            continue
        ctx.optfeatures.append({'id': entity_id(rec['name'], rec['source']), 'name': rec['name'],
                                'source': rec['source'], 'group': ofp.get('name'), 'origin': origin,
                                'entries': rec.get('entries') or []})
        _additional_spells(ctx, rec.get('additionalSpells'), f'{cid}:{rec["name"]}:spells', rec['name'], None,
                           rec.get('entries'))


def _check_optional_feature_prereqs(ctx: _Ctx, level, class_levels_by_name):
    """Invocations and the like carry prerequisites (a Warlock level, a pact,
    another invocation). Reported like feat prerequisites: a warning, so an
    imported character that broke one still loads."""
    names = {f['name'].lower() for f in ctx.features} | {o['name'].lower() for o in ctx.optfeatures}
    ids = {o['id'] for o in ctx.optfeatures}
    known = {str(x).lower() for k, v in ctx.picks.items() if k.startswith('spells:') and isinstance(v, list)
             for x in v} | {a['spell'] for a in ctx.additional_spells if a['mode'] != 'expanded'}
    # Which class each picked spell was learned as ("spells:warlock|xphb:cantrips").
    # Only class casters count; a subclass caster key (Eldritch Knight) names
    # the subclass, which no prerequisite asks for.
    known_by_class = {}
    for k, v in ctx.picks.items():
        if k.startswith('spells:') and isinstance(v, list):
            cname = k.split(':')[1].split('|')[0].lower()
            known_by_class.setdefault(cname, set()).update(str(x).lower() for x in v)
    for o in ctx.optfeatures:
        rec = C.get('optionalfeature', o['id']) or {}
        ok, _notes = prereq_ok(rec.get('prerequisite'), {
            'level': level, 'scores': {}, 'spellcasting': True, 'armor': ctx.grants.armor,
            'weapons': ctx.grants.weapons, 'race_name': '', 'feat_names': set(), 'background_name': '',
            'class_levels_by_name': class_levels_by_name, 'pact_names': names, 'optfeature_ids': ids,
            'known_spells': known, 'known_by_class': known_by_class, 'edition': ctx.edition,
        })
        if not ok:
            ctx.warnings.append(f'{o["name"]}: prerequisite not met')


def _apply_optional_features(ctx: _Ctx, classes, subclasses, level):
    for rec, cid, clv, _ in classes:
        for ofp in rec.get('optionalfeatureProgression') or []:
            _optfeat_choice(ctx, ofp, f'opt:{cid}', rec['name'], clv)
        sub = subclasses.get(cid)
        if sub:
            for ofp in sub.get('optionalfeatureProgression') or []:
                _optfeat_choice(ctx, ofp, f'opt:{subclass_id(sub)}', sub['name'], clv)


# --- spellcasting ----------------------------------------------------------------

def _caster_info(rec: dict, clv: int, mods: dict):
    """(progression, ability, cantrips, prepared, known, slot_row) for a class
    or subclass record at its class level."""
    prog = rec.get('casterProgression')
    ab = rec.get('spellcastingAbility')
    if not prog or not ab:
        return None
    cantrips = (rec.get('cantripProgression') or [None] * 20)[clv - 1] if rec.get('cantripProgression') else 0
    prepared = None
    if rec.get('preparedSpellsProgression'):
        prepared = rec['preparedSpellsProgression'][clv - 1]
    elif rec.get('preparedSpells'):
        prepared = _eval_prepared_formula(rec['preparedSpells'], clv, mods)
    known = None
    if rec.get('spellsKnownProgression'):
        known = rec['spellsKnownProgression'][clv - 1]
    elif rec.get('spellsKnownProgressionFixed'):
        known = sum(rec['spellsKnownProgressionFixed'][:clv])
    return prog, ab, cantrips or 0, prepared, known, _slot_row(rec, clv)


def _pact_slots(rec: dict, clv: int):
    t = class_table(rec, clv)
    if 'Spell Slots' in t and 'Slot Level' in t:
        lvl = t['Slot Level']
        if isinstance(lvl, str):
            m = re.search(r'\d', lvl)
            lvl = int(m.group(0)) if m else 1
        return {'count': int(t['Spell Slots']), 'level': int(lvl)}
    return None


def _free_cast_key(a) -> str:
    """Resource key of one grant's free casts. Per grant, not per spell: a High
    Elf with Fey-Touched has two separate Misty Step uses."""
    slug = re.sub(r'[^a-z0-9]+', '-', str(a['origin']).lower()).strip('-')
    return f"spell:{a['spell']}@{slug}"


def _classify_grants(ctx: _Ctx, mods, pb):
    """Fill in how each granted spell is paid for. Structured uses/recharge
    from the data win. A plain innate entry says nothing, so the trait or
    feature that grants it decides: a stated limit becomes uses, a stated cost
    (ki...) is recorded as such, an explicit "at will" / "no slot" stays free;
    anything else is left 'unknown' so the sheet says so rather than guess."""
    for a in ctx.additional_spells:
        if a['mode'] != 'innate' or a.get('cantrip') or a.get('classified'):
            continue
        a['classified'] = True
        if a.get('uses') or a.get('recharge'):
            continue
        rec = C.get('spell', a['spell'])
        if not rec or not rec.get('level'):
            continue
        terms = E.innate_terms(str(a['origin']), a.get('owner_entries') or [], rec['name'], mods=mods, pb=pb)
        if terms is None:
            a['cost'] = 'unknown'
        elif terms[0] == 'limited':
            a['uses'], a['recharge'] = terms[1], terms[2]
        elif terms[0] == 'cost':
            a['cost'] = terms[1]
        else:
            a['recharge'] = 'will'


def _spellcasting(ctx: _Ctx, classes, subclasses, mods, pb) -> dict:
    _classify_grants(ctx, mods, pb)
    ctx.expanded_ids = {a['spell'] for a in ctx.additional_spells if a['mode'] == 'expanded'}
    casters = []
    mc_parts = []
    pact = None
    for rec, cid, clv, _ in classes:
        sources = [(rec, cid)]
        if cid in subclasses:
            sources.append((subclasses[cid], cid))
        for src_rec, ccid in sources:
            info = _caster_info(src_rec, clv, mods)
            if not info:
                continue
            prog, ab, cantrips, prepared, known, row = info
            if prog == 'pact':
                pact = _pact_slots(rec, clv)
                max_lvl = pact['level'] if pact else 1
            else:
                mc_parts.append((prog, clv))
                max_lvl = max((i + 1 for i, n in enumerate(row) if n), default=0)
            list_class = src_rec.get('className') or src_rec['name']
            list_source = src_rec.get('classSource') or src_rec['source']
            if src_rec is not rec:
                # Eldritch Knight / Arcane Trickster cast from the Wizard list.
                list_class, list_source = _subclass_spell_list(src_rec)
            key = entity_id(src_rec['name'], src_rec['source'])
            entry = {
                'key': key, 'class': rec['name'], 'class_id': cid,
                'name': src_rec['name'], 'ability': ab,
                'dc': 8 + pb + mods[ab], 'attack': pb + mods[ab],
                'cantrips_max': cantrips, 'prepared_max': prepared, 'known_max': known,
                'max_spell_level': max_lvl, 'progression': prog, 'class_level': clv,
                'list': {'class': list_class, 'source': list_source},
                'ritual_casting': src_rec.get('name') in ('Wizard', 'Cleric', 'Druid', 'Bard', 'Artificer')
                or ctx.edition == 'one',
            }
            if (rec['name'] == 'Cleric' and 'option:Divine Order:thaumaturge' in ctx.flags) or \
                    (rec['name'] == 'Druid' and 'option:Primal Order:magician' in ctx.flags):
                if src_rec is rec:
                    entry['extra_cantrips'] = 1
            _spell_choices(ctx, entry, src_rec, cid, subclasses.get(cid))
            casters.append(entry)

    if len(mc_parts) == 1 and len([c for c in casters if c['progression'] != 'pact']) == 1:
        prog, clv = mc_parts[0]
        row = next(_caster_info(r, cl, mods)[5] for r, ci, cl, _ in classes
                   for r in ([r] + ([subclasses[ci]] if ci in subclasses else []))
                   if (_caster_info(r, cl, mods) or (None,))[0] not in (None, 'pact'))
        slots = [n for n in row]
    elif mc_parts:
        lvl = multiclass_caster_level(ctx.edition, mc_parts)
        slots = list(SPELL_SLOTS_BY_CASTER_LEVEL.get(lvl, ()))
    else:
        slots = []
    slots = {str(i + 1): n for i, n in enumerate(slots) if n}

    always = []
    expanded = sorted({a['spell'] for a in ctx.additional_spells if a['mode'] == 'expanded'})
    for c in casters:
        c['list']['extra'] = expanded
    for a in ctx.additional_spells:
        if a['mode'] == 'expanded':
            continue    # patron/domain "expanded" lists widen options; they are not known
        rec = C.get('spell', a['spell'])
        always.append({'id': a['spell'], 'name': rec['name'] if rec else a['spell'].split('|')[0],
                       'level': rec.get('level') if rec else None, 'origin': a['origin'], 'mode': a['mode'],
                       'ability': a.get('ability'), 'missing': rec is None,
                       'uses': a.get('uses'), 'recharge': a.get('recharge'), 'cost': a.get('cost'),
                       'free_key': (_free_cast_key(a) if a.get('uses') and a.get('recharge') in ('long', 'short')
                                    and not a.get('cantrip') else None)})
    return {'casters': casters, 'slots': slots, 'pact': pact, 'always': always}


def _subclass_spell_list(sub: dict):
    # 2014 + 2024 third-casters both draw from the Wizard list.
    src = 'XPHB' if sub.get('classSource') == 'XPHB' else 'PHB'
    return 'Wizard', src


ARCANUM_LEVELS = ((11, 6), (13, 7), (15, 8), (17, 9))   # (warlock level, spell level)


def _spell_choices(ctx: _Ctx, entry: dict, rec: dict, cid: str, sub):
    q = {'class': entry['list']['class'], 'class_source': entry['list']['source'],
         'subclass': (sub or {}).get('shortName'), 'max_level': entry['max_spell_level']}
    if entry['class'] == 'Bard' and any(f['name'] == 'Magical Secrets' and f.get('className') == 'Bard'
                                        for f in ctx.features):
        # 2024: prepare from the Bard, Cleric, Druid and Wizard lists.
        # 2014: Magical Secrets picks may come from any class's list.
        q['any_class'] = ['Bard', 'Cleric', 'Druid', 'Wizard'] if ctx.edition == 'one' else '*'
    if entry.get('extra_cantrips'):
        entry['cantrips_max'] += entry['extra_cantrips']
    base = f'spells:{entry["key"]}'
    if entry['cantrips_max']:
        ctx.add_choice(G.Choice(id=f'{base}:cantrips', kind='spell', label=f'{entry["name"]}: cantrips',
                                count=int(entry['cantrips_max']), origin=entry['name'],
                                query=dict(q, levels=[0])))
        _validate_spells(ctx, f'{base}:cantrips', q, [0])
    if entry['progression'] == 'pact' and entry['class'] == 'Warlock':
        # Mystic Arcanum: one spell of each level 6-9 at Warlock 11/13/15/17,
        # cast once per long rest without a slot (both editions).
        for at, spl in ARCANUM_LEVELS:
            if entry.get('class_level', 0) >= at:
                aid = f'{base}:arcanum:{spl}'
                ctx.add_choice(G.Choice(id=aid, kind='spell', label=f'{entry["name"]}: Mystic Arcanum (level {spl})',
                                        count=1, origin=entry['name'], query=dict(q, levels=[spl], max_level=spl)))
                _validate_spells(ctx, aid, q, [spl])
    levels = list(range(1, entry['max_spell_level'] + 1))
    if not levels:
        return
    known_style = entry['known_max'] is not None
    if known_style:
        ctx.add_choice(G.Choice(id=f'{base}:known', kind='spell',
                                label=f'{entry["name"]}: {"spellbook" if rec.get("spellsKnownProgressionFixed") else "spells known"}',
                                count=int(entry['known_max']), origin=entry['name'], query=dict(q, levels=levels)))
        _validate_spells(ctx, f'{base}:known', q, levels)
    if entry['prepared_max'] is not None:
        pq = dict(q, levels=levels)
        if known_style and rec.get('spellsKnownProgressionFixed'):
            pq['from_choice'] = f'{base}:known'      # prepare from the spellbook
        ctx.add_choice(G.Choice(id=f'{base}:prepared', kind='spell', label=f'{entry["name"]}: prepared spells',
                                count=int(entry['prepared_max']), origin=entry['name'], query=pq,
                                required=False))
        if pq.get('from_choice'):
            book = {str(s).lower() for s in (ctx.picks.get(pq['from_choice']) or [])}
            for s in ctx.picks.get(f'{base}:prepared') or []:
                if str(s).lower() not in book:
                    ctx.errors.append(f'{entry["name"]}: {s} is not in the spellbook')
        else:
            _validate_spells(ctx, f'{base}:prepared', q, levels)


def spell_on_list(spell_id: str, class_name: str, class_source: str, subclass_short=None) -> bool:
    rows = C._con().execute(
        'SELECT via, subclass_short FROM spell_class WHERE spell_id=? AND class_name=? AND class_source=?',
        (spell_id, class_name, class_source)).fetchall()
    for r in rows:
        if r['via'] != 'fromSubclass' or (subclass_short and r['subclass_short'] == subclass_short):
            return True
    return False


def _validate_spells(ctx: _Ctx, cid: str, q: dict, levels: list):
    for s in ctx.picks.get(cid) or []:
        sid = str(s).lower()
        rec = C.get('spell', sid)
        if rec is None:
            ctx.errors.append(f'Unknown spell {s!r}')
            continue
        if rec.get('level') not in levels:
            ctx.errors.append(f'{rec["name"]}: level {rec.get("level")} not available here')
            continue
        if sid in ctx.expanded_ids:
            continue
        wider = q.get('any_class')
        if wider == '*':
            continue
        if wider and any(spell_on_list(sid, c, q['class_source']) for c in wider):
            continue
        if not spell_on_list(sid, q['class'], q['class_source'], q.get('subclass')):
            # Magical Secrets etc. can widen the list; warn rather than reject.
            ctx.warnings.append(f'{rec["name"]} is not on the {q["class"]} spell list')


# --- hit points ---------------------------------------------------------------

def _hit_points(ctx, classes, mods, feat_names, feature_names, class_levels_by_name):
    levels = ctx.build.get('levels') or []
    total = 0
    dice: dict = {}
    for i, lv in enumerate(levels[:MAX_LEVEL]):
        rec = C.get('class', (lv or {}).get('class') or '')
        if rec is None:
            continue
        faces = (rec.get('hd') or {}).get('faces', 8)
        dice[f'd{faces}'] = dice.get(f'd{faces}', 0) + 1
        if i == 0:
            gain = faces
        else:
            roll = lv.get('hp')
            if roll is None:
                gain = faces // 2 + 1
            else:
                try:
                    roll = int(roll)
                except (TypeError, ValueError):
                    roll = faces // 2 + 1
                if not 1 <= roll <= faces:
                    ctx.errors.append(f'Level {i + 1}: HP roll {roll} outside 1-{faces}')
                    roll = max(1, min(faces, roll))
                gain = roll
        total += max(1, gain + mods['con'])
    lvl = len(levels[:MAX_LEVEL])
    if 'tough' in feat_names:
        total += 2 * lvl
    if 'Dwarven Toughness' in feature_names:
        total += lvl
    if 'Draconic Resilience' in feature_names:
        total += class_levels_by_name.get('Sorcerer', 0)
    return total, dice


# --- inventory / AC / attacks -----------------------------------------------------

_PROP_CODES = {'F': 'finesse', 'L': 'light', 'H': 'heavy', 'T': 'thrown', '2H': 'two-handed',
               'V': 'versatile', 'R': 'reach', 'A': 'ammunition', 'LD': 'loading', 'S': 'special',
               'RLD': 'reload', 'BF': 'burst fire'}


def _props(item) -> set:
    return {str(p).split('|')[0] for p in item.get('property') or []}


def _bonus(v) -> int:
    try:
        return int(str(v or '0').replace('+', ''))
    except ValueError:
        return 0


def _scan_inventory(ctx):
    """[(index, entry, record|None, active)] for the inventory. ``active`` =
    equipped, and attuned when the item requires attunement."""
    out = []
    for idx, ent in enumerate(ctx.state.get('inventory') or []):
        if not isinstance(ent, dict):
            continue
        rec = C.get('item', ent.get('id') or '') if ent.get('id') else None
        active = bool(rec) and bool(ent.get('equipped')) and (not rec.get('reqAttune') or bool(ent.get('attuned')))
        out.append((idx, ent, rec, active))
    return out


def _shillelagh_die(edition: str, level: int) -> str:
    if edition == 'classic':
        return '1d8'
    return '1d8' if level < 5 else ('1d10' if level < 11 else ('1d12' if level < 17 else '2d6'))


def _inventory(ctx, scores, mods, pb, feature_names, class_levels_by_name, feat_names, m, spell_abilities,
               level):
    scanned = _scan_inventory(ctx)
    items = []
    weight = 0.0
    armor = shield = None
    attuned = 0
    for idx, ent, rec, active in scanned:
        qty = int(ent.get('qty') or 1)
        w = float((rec or {}).get('weight') or ent.get('weight') or 0)
        weight += w * qty
        out = {'index': idx, 'uid': ent.get('uid'), 'id': ent.get('id'),
               'name': (rec or {}).get('name') or ent.get('name') or 'Item',
               'qty': qty, 'equipped': bool(ent.get('equipped')), 'attuned': bool(ent.get('attuned')),
               'pact': bool(ent.get('pact')), 'active': active,
               'weight': w, 'notes': ent.get('notes') or '', 'custom': rec is None,
               'type': ((rec or {}).get('type') or '').split('|')[0],
               'rarity': (rec or {}).get('rarity'), 'requires_attunement': bool((rec or {}).get('reqAttune')),
               'charges': E.item_charges(rec) if rec else None,
               'weapon': bool(rec and (rec.get('weapon') or rec.get('dmg1')))}
        items.append(out)
        if not rec:
            continue
        if out['attuned']:
            attuned += 1
        t = out['type']
        if out['equipped'] and t in ('LA', 'MA', 'HA'):
            if armor is None:
                armor = rec
            else:
                ctx.warnings.append('More than one suit of armor equipped')
        elif out['equipped'] and t == 'S':
            shield = rec
    if attuned > 3:
        ctx.warnings.append(f'{attuned} items attuned (maximum 3)')

    # --- AC ---
    dex = mods['dex']
    candidates = []
    if armor:
        base = int(armor.get('ac') or 10) + _bonus(armor.get('bonusAc'))
        t = (armor.get('type') or '').split('|')[0]
        cap = None if t == 'LA' else (0 if t == 'HA' else (3 if 'medium armor master' in feat_names else 2))
        d = dex if cap is None else min(dex, cap)
        candidates.append((base + d, f'{armor["name"]}'))
        if {'LA': 'light', 'MA': 'medium', 'HA': 'heavy'}[t] not in ctx.grants.armor:
            ctx.warnings.append(f'Not proficient with {armor["name"]}')
        if armor.get('strength') and scores['str'] < int(armor['strength']):
            ctx.warnings.append(f'{armor["name"]} requires STR {armor["strength"]} (speed -10)')
    else:
        candidates.append((10 + dex, 'Unarmored'))
        cls_names = {f.get('className') for f in ctx.features if f['name'] == 'Unarmored Defense'}
        if 'Barbarian' in cls_names:
            candidates.append((10 + dex + mods['con'], 'Unarmored Defense (Barbarian)'))
        if 'Monk' in cls_names and not shield:
            candidates.append((10 + dex + mods['wis'], 'Unarmored Defense (Monk)'))
        if 'Draconic Resilience' in feature_names:
            candidates.append(((13 + dex) if ctx.edition == 'classic' else (10 + dex + mods['cha']),
                               'Draconic Resilience'))
        for (base_v, ab), label in m.ac_unarmored:
            candidates.append((base_v + mods[ab], label))
    ac, src = max(candidates)
    breakdown = [src]
    if shield:
        ac += 2 + _bonus(shield.get('bonusAc'))
        breakdown.append(shield['name'])
    if armor and (any(o['name'] == 'Defense' for o in ctx.optfeatures)
                  or any(f[0]['name'] == 'Defense' for f in ctx.feats)):
        ac += 1
        breakdown.append('Defense')
    for ref, label in m.ac_bonus:
        v = m.resolve(ref, mods, pb)
        if v:
            ac += v
            breakdown.append(f'{label} {v:+d}')
    if ctx.state.get('ac_bonus'):
        ac += int(ctx.state['ac_bonus'])
        breakdown.append(f'misc {int(ctx.state["ac_bonus"]):+d}')
    for floor_v, label in m.ac_min:
        if ac < floor_v:
            ac = floor_v
            breakdown.append(f'{label} (minimum {floor_v})')

    # --- attacks ---
    attacks = []
    styles = {o['name'] for o in ctx.optfeatures} | {f[0]['name'] for f in ctx.feats}
    pact_ok = 'Hex Warrior' in feature_names or 'Pact of the Blade' in styles
    martial_arts = None
    monk_level = class_levels_by_name.get('Monk', 0)
    if monk_level:
        monk = next((C.get('class', lv['class']) for lv in ctx.build.get('levels') or []
                     if (C.get('class', lv['class']) or {}).get('name') == 'Monk'), None)
        if monk:
            martial_arts = class_table(monk, monk_level).get('Martial Arts')
    effects_on = set(ctx.state.get('effects') or [])
    weapons_equipped = [i for i in items if i['equipped'] and i['weapon'] and i['type'] not in ('S',)]
    for out in items:
        if not out['equipped'] or not out['id'] or not out['weapon']:
            continue
        rec = C.get('item', out['id'])
        props = _props(rec)
        ranged = (rec.get('type') or '').split('|')[0] == 'R'
        cat = rec.get('weaponCategory')
        base_name = (rec.get('baseItem') or rec['name']).split('|')[0].lower()
        prof = (cat in ctx.grants.weapons) or (base_name in ctx.grants.weapons) or \
               (rec['name'].lower() in ctx.grants.weapons)
        if ranged:
            ab = 'dex'
        elif 'F' in props:
            ab = 'dex' if mods['dex'] > mods['str'] else 'str'
        else:
            ab = 'str'
        die = rec.get('dmg1') or '1'
        notes = []
        if martial_arts and monk_level and (cat == 'simple' or 'L' in props) and not ranged and \
                'H' not in props and '2H' not in props:
            if mods['dex'] > mods[ab]:
                ab = 'dex'
            die = _bigger_die(die, martial_arts)
        if out['pact'] and pact_ok:
            prof = True                       # the pact weapon is always proficient
            if mods['cha'] > mods[ab]:
                ab = 'cha'
                notes.append('Pact weapon: uses Charisma')
        if 'shillelagh' in effects_on and base_name in ('club', 'quarterstaff') and spell_abilities:
            best = max(spell_abilities, key=lambda a: mods[a])
            if mods[best] >= mods[ab]:
                ab = best
            die = _shillelagh_die(ctx.edition, level)
            notes.append('Shillelagh')
        magic = _bonus(rec.get('bonusWeapon')) + _bonus(rec.get('bonusWeaponAttack'))
        to_hit = mods[ab] + (pb if prof else 0) + magic
        dmg_bonus = mods[ab] + _bonus(rec.get('bonusWeapon')) + _bonus(rec.get('bonusWeaponDamage'))
        if ranged and 'Archery' in styles:
            to_hit += 2
            notes.append('Archery +2')
        others = [w for w in weapons_equipped if w['index'] != out['index']]
        if not ranged and 'Dueling' in styles and '2H' not in props and not others:
            dmg_bonus += 2
            notes.append('Dueling +2')
        if not ranged and ab == 'str':
            for ref in m.melee_str_damage:
                v = m.resolve(ref, mods, pb)
                dmg_bonus += v
                notes.append(f'Rage +{v}')
        if 'T' in props and 'Thrown Weapon Fighting' in styles:
            notes.append('Thrown Weapon Fighting: +2 damage when thrown')
        if 'L' in props and others:
            notes.append('Off-hand (bonus action): ' + ('add your ability modifier (Two-Weapon Fighting)'
                                                         if 'Two-Weapon Fighting' in styles
                                                         else 'no ability modifier to damage'))
        if ('2H' in props or 'V' in props) and not ranged and 'Great Weapon Fighting' in styles:
            notes.append('Great Weapon Fighting: ' + ('treat 1s and 2s on damage dice as 3s' if ctx.edition == 'one'
                                                     else 'reroll 1s and 2s on damage dice'))
        kind_tags = {'all', 'ranged' if ranged else 'melee'} | ({'heavy'} if 'H' in props else set())
        for applies, text in m.attack_notes:
            if applies in kind_tags:
                notes.append(text)
        extra = [f'{dice} ({label})' for dice, applies, label in m.weapon_extra if applies in kind_tags]
        crit = min(m.crit_threshold, _crit_threshold(rec), _class_crit(feature_names))
        if crit < 20:
            notes.append(f'Critical hit on {crit}-20')
        mastery = None
        if ctx.edition == 'one' and rec.get('mastery'):
            mm = str(rec['mastery'][0]).split('|')[0]
            if f'mastery:{base_name}' in ctx.flags:
                mastery = mm
        attacks.append({
            'name': rec['name'], 'item_index': out['index'], 'ability': ab, 'proficient': prof,
            'to_hit': to_hit, 'damage': f'{die}{dmg_bonus:+d}' if dmg_bonus else die,
            'damage_type': rec.get('dmgType'), 'extra_damage': extra,
            # Two-handed use is impossible with a shield on the other arm.
            'versatile': (f'{rec["dmg2"]}{dmg_bonus:+d}' if rec.get('dmg2') and not shield else None),
            'range': rec.get('range') or ('5 ft.' if 'R' not in props else '10 ft.'),
            'properties': sorted(_PROP_CODES.get(p, p) for p in props), 'mastery': mastery, 'notes': notes,
        })
    # unarmed strike
    ua_ab = 'str'
    ua_die = '1'
    if martial_arts:
        ua_die = str(martial_arts) if str(martial_arts).startswith('1d') or str(martial_arts).startswith('d') else '1'
        if ua_die.startswith('d'):
            ua_die = '1' + ua_die
        if mods['dex'] > mods['str']:
            ua_ab = 'dex'
    ua_bonus = mods[ua_ab]
    ua_notes = []
    if ua_ab == 'str':
        for ref in m.melee_str_damage:
            v = m.resolve(ref, mods, pb)
            ua_bonus += v
            ua_notes.append(f'Rage +{v}')
    attacks.append({'name': 'Unarmed Strike', 'item_index': None, 'ability': ua_ab, 'proficient': True,
                    'to_hit': mods[ua_ab] + pb,
                    'damage': (f'{ua_die}{ua_bonus:+d}' if ua_die != '1' else str(max(1, 1 + ua_bonus))),
                    'damage_type': 'B', 'versatile': None, 'range': '5 ft.', 'properties': [],
                    'mastery': None, 'notes': ua_notes, 'extra_damage': []})

    size = (ctx.build.get('choices') or {}).get('race:size')
    mult = 2 if size in ('L', ['L']) else (0.5 if size in ('T', ['T']) else 1)
    capacity = scores['str'] * 15 * mult
    return {
        'ac': {'value': ac, 'breakdown': breakdown},
        'attacks': attacks,
        'items': items,
        'carry': {'weight': round(weight, 2), 'capacity': capacity,
                  'encumbered': weight > capacity},
        'attuned': attuned,
        'senses': {},
        'armor_type': ((armor.get('type') or '').split('|')[0] if armor else None),
        'pact_capable': pact_ok,
        'shield': bool(shield),
    }


def _crit_threshold(rec) -> int:
    try:
        return int(rec.get('critThreshold') or 20)
    except (TypeError, ValueError):
        return 20


def _class_crit(feature_names) -> int:
    # Champion: Improved Critical (3) 19-20, Superior Critical (15) 18-20.
    if 'Superior Critical' in feature_names:
        return 18
    if 'Improved Critical' in feature_names:
        return 19
    return 20


def _attack_riders(attacks: list, classes):
    """Conditional extra damage read off the class tables, shown on the attack
    rows it applies to (Sneak Attack depends on the moment, so it is a note)."""
    sneak = None
    for rec, _cid, clv, _ in classes:
        t = class_table(rec, clv)
        if rec['name'] == 'Rogue' and t.get('Sneak Attack'):
            sneak = str(t['Sneak Attack'])
    for a in attacks:
        props = a.get('properties') or []
        ranged = a.get('range') not in ('5 ft.', '10 ft.')
        if sneak and ('finesse' in props or ranged) and a.get('item_index') is not None:
            a['notes'].append(f'Sneak Attack: +{sneak} once per turn')


def rage_damage(classes):
    for rec, _cid, clv, _ in classes:
        if rec['name'] == 'Barbarian':
            v = class_table(rec, clv).get('Rage Damage')
            if v is not None:
                return int(str(v).replace('+', '') or 0)
    return None


def _bigger_die(a: str, b) -> str:
    def faces(s):
        mt = re.search(r'd(\d+)', str(s))
        return int(mt.group(1)) if mt else 0
    return a if faces(a) >= faces(b) else ('1' + str(b) if str(b).startswith('d') else str(b))


# --- speed -----------------------------------------------------------------------

def _speed(ctx, race, feature_names, class_levels_by_name, inv, m):
    sp = (race or {}).get('speed', 30)
    if isinstance(sp, int):
        out = {'walk': sp}
    else:
        out = {}
        for k, v in (sp or {}).items():
            if isinstance(v, int):
                out[k] = v
            elif v is True:
                out[k] = 'equal'
            elif isinstance(v, dict) and isinstance(v.get('number'), int):
                out[k] = v['number']
        if 'walk' not in out:
            out['walk'] = 30
    for k, v in list(out.items()):
        if v == 'equal':
            out[k] = out['walk']
    heavy = inv['armor_type'] == 'HA'
    any_armor = inv['armor_type'] is not None
    bonus = 0
    if 'Fast Movement' in feature_names and not heavy:
        bonus += 10
    monk = class_levels_by_name.get('Monk', 0)
    if 'Unarmored Movement' in feature_names and not any_armor and not inv['shield']:
        bonus += 10 + 5 * sum(1 for t in (6, 10, 14, 18) if monk >= t)
    if ('Roving' in feature_names and not heavy) or 'mobile' in {f[0]['name'].lower() for f in ctx.feats}:
        bonus += 10
    if 'speedy' in {f[0]['name'].lower() for f in ctx.feats}:
        bonus += 10
    out['walk'] += bonus + m.speed_bonus.get('walk', 0)
    for mode, v in m.speed_static.items():
        out[mode] = max(out.get(mode, 0), v)
    for mode, v in m.speed_bonus.items():
        if mode != 'walk' and mode in out:
            out[mode] += v
    for mode, src in m.speed_equal.items():
        out[mode] = max(out.get(mode, 0), out.get(src, out['walk']))
    if m.speed_mult != 1:
        out = {k: int(v * m.speed_mult) for k, v in out.items()}
    return out


# --- limited-use resources --------------------------------------------------------

def _resources(ctx, classes, subclasses, mods, pb, feature_names, class_levels_by_name):
    out = []
    one = ctx.edition == 'one'

    def add(key, name, mx, recharge, origin):
        if mx and mx > 0:
            out.append({'key': key, 'name': name, 'max': int(mx), 'recharge': recharge, 'origin': origin})

    for rec, cid, clv, _ in classes:
        t = class_table(rec, clv)
        n = rec['name']

        def num(label):
            v = t.get(label)
            try:
                return int(str(v).split()[0].replace('+', ''))
            except (TypeError, ValueError, IndexError):
                return None
        if n == 'Barbarian':
            r = num('Rages')
            add('rage', 'Rage', 99 if (r is None and str(t.get('Rages')).lower().startswith('unl')) else r,
                'long' if not one else 'long (1 on short)', n)
        elif n == 'Monk':
            add('focus', 'Focus Points' if one else 'Ki Points', num('Focus Points') or num('Ki Points'), 'short', n)
        elif n == 'Sorcerer':
            add('sorcery', 'Sorcery Points', num('Sorcery Points'), 'long', n)
        elif n == 'Cleric':
            cd = num('Channel Divinity') if one else (0 if clv < 2 else (1 if clv < 6 else (2 if clv < 18 else 3)))
            add('channel_divinity', 'Channel Divinity', cd, 'short' if not one else 'short (1) / long (all)', n)
        elif n == 'Paladin':
            cd = num('Channel Divinity') if one else (1 if clv >= 3 else 0)
            add('channel_divinity_pal', 'Channel Divinity', cd, 'short' if not one else 'short (1) / long (all)', n)
            add('lay_on_hands', 'Lay on Hands (HP pool)', 5 * clv, 'long', n)
            if not one:
                add('divine_sense', 'Divine Sense', 1 + mods['cha'], 'long', n)
        elif n == 'Bard':
            add('bardic_inspiration', 'Bardic Inspiration', max(1, mods['cha']),
                'short' if clv >= 5 else 'long', n)
        elif n == 'Fighter':
            add('second_wind', 'Second Wind', (num('Second Wind') if one else 1) or 1,
                'short' if not one else 'short (1) / long (all)', n)
            if clv >= 2:
                add('action_surge', 'Action Surge', 2 if clv >= 17 else 1, 'short', n)
            if clv >= 9:
                add('indomitable', 'Indomitable', 3 if clv >= 17 else (2 if clv >= 13 else 1), 'long', n)
        elif n == 'Druid':
            if clv >= 2:
                add('wild_shape', 'Wild Shape', (num('Wild Shape') if one else 2) or 2,
                    'short' if not one else 'short (1) / long (all)', n)
        elif n == 'Wizard':
            add('arcane_recovery', 'Arcane Recovery', 1, 'long', n)
        elif n == 'Ranger' and one:
            add('favored_enemy', "Favored Enemy (Hunter's Mark)", num('Favored Enemy') or 2, 'long', n)
        elif n == 'Warlock':
            if one and clv >= 2:
                add('magical_cunning', 'Magical Cunning', 1, 'long', n)
            # Each arcanum spell (level 6 at 11, 7 at 13, 8 at 15, 9 at 17) is
            # its own 1/long rest; the sheet's Cast button spends these.
            for at, spl in ARCANUM_LEVELS:
                if clv >= at:
                    add(f'mystic_arcanum_{spl}', f'Mystic Arcanum (level {spl})', 1, 'long', n)
        sub = subclasses.get(cid)
        if sub and sub.get('shortName') == 'Battle Master':
            add('superiority', 'Superiority Dice', 6 if clv >= 15 else (5 if clv >= 7 else 4), 'short', sub['name'])
    custom = (ctx.state.get('custom_resources') or [])
    for c in custom:
        if isinstance(c, dict) and c.get('key') and c.get('max'):
            add(str(c['key']), str(c.get('name') or c['key']), int(c['max']), str(c.get('recharge') or 'long'), 'Custom')
    return out


def _detected_resources(ctx, existing, mods, pb, race, scanned):
    """Limited uses the class tables don't carry:

    * features / species traits / feats whose text states a usage limit
      ("a number of times equal to your Proficiency Bonus... Long Rest"),
    * innate spells ("1/day" from species, feats, subclasses),
    * magic items with charges,
    * Lucky.

    Keys are stable strings so expended uses survive recomputation."""
    out = []
    have = {r['key'] for r in existing} | {r['name'] for r in existing}

    def add(key, name, mx, recharge, origin):
        if mx and mx > 0 and key not in have:
            have.add(key)
            out.append({'key': key, 'name': name, 'max': int(mx), 'recharge': recharge, 'origin': origin,
                        'auto': True})

    spell_origins = {a['origin'] for a in ctx.additional_spells}
    # Spells with their own free-cast use (tracked per spell below). A feature
    # whose limit is that same free cast ("you can cast Animate Dead without a
    # slot ... once per Long Rest") must not be counted a second time.
    free_cast_names = set()
    for a in ctx.additional_spells:
        if a.get('uses') and a.get('recharge') in ('long', 'short') and not a.get('cantrip'):
            rec = C.get('spell', a['spell'])
            if rec:
                free_cast_names.add(rec['name'].lower())
    for f in ctx.features:
        # Traits that grant spells are tracked per spell below, not as a trait.
        if f.get('origin', '').startswith('Species') and _casts_spells(f) and \
                any(o.startswith('Species') for o in spell_origins):
            continue
        res = E.detect_uses(f['name'], f.get('entries'), mods=mods, pb=pb)
        if res and free_cast_names and set(E.free_cast_spells(f.get('entries'))) & free_cast_names:
            res = None              # its limit is that spell's free cast, tracked per spell
        if res and f['name'] not in have:
            add(f'feature:{f["name"].lower()}', f['name'], res[0], res[1], f.get('origin') or '')
    for rec, origin, _prefix in ctx.feats:
        nm = rec['name']
        if nm.lower() == 'lucky':
            add('feat:lucky', 'Luck Points' if ctx.edition == 'one' else 'Lucky',
                pb if ctx.edition == 'one' else 3, 'long', 'Feat')
            continue
        if rec.get('additionalSpells'):
            continue
        res = E.detect_uses(nm, rec.get('entries'), mods=mods, pb=pb)
        if res:
            add(f'feat:{nm.lower()}', nm, res[0], res[1], 'Feat')
    for o in ctx.optfeatures:
        res = E.detect_uses(o['name'], o.get('entries'), mods=mods, pb=pb)
        if res:
            add(f'opt:{o["name"].lower()}', o['name'], res[0], res[1], o.get('group') or '')
    grants = [a for a in ctx.additional_spells
              if a.get('uses') and a.get('recharge') in ('long', 'short') and not a.get('cantrip')]
    for a in grants:
        rec = C.get('spell', a['spell'])
        nm = rec['name'] if rec else a['spell'].split('|')[0].title()
        twin = sum(1 for b in grants if b['spell'] == a['spell']) > 1
        label = f'{nm} (free cast, {a["origin"]})' if twin else f'{nm} (free cast)'
        add(_free_cast_key(a), label, a['uses'], a['recharge'], a['origin'])
    for idx, ent, rec, _active in scanned:
        if not rec:
            continue
        ch = E.item_charges(rec)
        if ch and (ent.get('attuned') or ent.get('equipped') or not rec.get('reqAttune')):
            uid = ent.get('uid') or f'i{idx}'
            add(f'item:{uid}', f'{rec["name"]} charges', ch, 'long' if rec.get('recharge') == 'dawn' else 'long',
                'Item' + (f' (regains {strip_tags(rec["rechargeAmount"])} at dawn)'
                          if rec.get('rechargeAmount') else ''))
    return out


def _casts_spells(feature) -> bool:
    text = E._flatten(feature.get('entries'))
    return '{@spell' in text and 'cast' in text.lower()
