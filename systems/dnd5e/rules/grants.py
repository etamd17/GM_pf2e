"""Turn 5etools "grant" grammar into fixed grants + player choices.

5etools expresses every proficiency / ability-score / language / feat grant
with one small grammar, used by races, backgrounds, feats, classes alike:

    [ {alt 1}, {alt 2}, ... ]          a LIST is a set of alternatives (pick one)
    {"athletics": true}                fixed grant
    {"any": 2} / {"anyStandard": 1}    choose N from a named pool
    {"choose": {"from": [...], "count": 2}}
    {"choose": {"weighted": {"from": [...], "weights": [2, 1]}}}   (abilities)

`expand()` turns one grant field into Choice specs (with stable ids) plus the
fixed values, given whatever the player already picked. Validation is the same
code path as option listing, so the builder UI can never offer something the
engine would then reject.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from systems.dnd5e.rules.tables import ABILITIES, SKILLS

DAMAGE_TYPES = ('acid', 'bludgeoning', 'cold', 'fire', 'force', 'lightning', 'necrotic',
                'piercing', 'poison', 'psychic', 'radiant', 'slashing', 'thunder')


@dataclass
class Choice:
    """One decision the player must make."""
    id: str
    kind: str                 # skill | language | tool | ability | feat | spell | optionalfeature |
    #                           subclass | expertise | weapon | alternative | resist | save | asi |
    #                           equipment | weapon_mastery | size | species_version |
    #                           ability_score_method
    label: str
    count: int = 1
    options: list = field(default_factory=list)   # concrete option values (may be [] = "query")
    query: dict = field(default_factory=dict)     # for large pools: filter the UI should query
    amount: int = 1                               # ability bonus per pick
    weights: tuple = ()                           # weighted ability choice
    origin: str = ''                              # human label of what granted it
    required: bool = True
    max_score: int = 20

    def to_dict(self) -> dict:
        d = {'id': self.id, 'kind': self.kind, 'label': self.label, 'count': self.count,
             'options': self.options, 'origin': self.origin, 'required': self.required}
        if self.query:
            d['query'] = self.query
        if self.kind == 'ability':
            d['amount'] = self.amount
            d['weights'] = list(self.weights)
            d['max_score'] = self.max_score
        return d


@dataclass
class Grants:
    """Accumulated fixed grants for one field from one origin."""
    skills: set = field(default_factory=set)
    languages: set = field(default_factory=set)
    tools: set = field(default_factory=set)
    weapons: set = field(default_factory=set)
    armor: set = field(default_factory=set)
    saves: set = field(default_factory=set)
    expertise: set = field(default_factory=set)
    resist: set = field(default_factory=set)
    abilities: dict = field(default_factory=dict)   # ability -> bonus
    ability_max: dict = field(default_factory=dict)  # ability -> cap raised to
    feats: list = field(default_factory=list)        # [(feat_id, origin_id)]


# --- option pools ------------------------------------------------------------

def _norm(s: str) -> str:
    return (s or '').split('|')[0].strip().lower()


class Pools:
    """Option pools that depend on content (languages, tools, weapons). The
    engine builds one per edition; tests can pass a stub."""

    def __init__(self, languages_standard=(), languages_all=(), tools_artisan=(),
                 tools_instrument=(), tools_gaming=(), tools_all=(), weapons_simple=(),
                 weapons_martial=()):
        self.languages_standard = list(languages_standard)
        self.languages_all = list(languages_all)
        self.tools_artisan = list(tools_artisan)
        self.tools_instrument = list(tools_instrument)
        self.tools_gaming = list(tools_gaming)
        self.tools_all = list(tools_all)
        self.weapons_simple = list(weapons_simple)
        self.weapons_martial = list(weapons_martial)

    def named(self, key: str, kind: str) -> list:
        k = key
        if k in ('any', 'anySkill') and kind in ('skill', 'expertise', 'skillToolLanguage'):
            return sorted(SKILLS)
        if k == 'anyProficientSkill':
            return sorted(SKILLS)   # narrowed by the engine to proficient skills
        if k in ('anyStandard',):
            return self.languages_standard
        if k in ('any', 'anyLanguage', 'anyExotic', 'anyRare') and kind in ('language', 'skillToolLanguage'):
            return self.languages_all
        if k == 'anyArtisansTool':
            return self.tools_artisan
        if k == 'anyMusicalInstrument':
            return self.tools_instrument
        if k == 'anyGamingSet':
            return self.tools_gaming
        if k in ('any', 'anyTool') and kind in ('tool', 'skillToolLanguage'):
            return self.tools_all
        if k in ('anySimpleWeapon',):
            return self.weapons_simple
        if k in ('anyMartialWeapon',):
            return self.weapons_martial
        if k == 'any' and kind == 'weapon':
            return self.weapons_simple + self.weapons_martial
        if k == 'any' and kind == 'save':
            return list(ABILITIES)
        if k == 'any' and kind == 'resist':
            return list(DAMAGE_TYPES)
        return []


# --- expansion ---------------------------------------------------------------

_SET_FIELD = {'skill': 'skills', 'language': 'languages', 'tool': 'tools',
              'weapon': 'weapons', 'armor': 'armor', 'save': 'saves',
              'expertise': 'expertise', 'resist': 'resist'}

_KIND_LABEL = {'skill': 'skill', 'language': 'language', 'tool': 'tool',
               'weapon': 'weapon', 'save': 'saving throw',
               'expertise': 'expertise', 'resist': 'damage resistance',
               'skillToolLanguage': 'skill, tool or language'}


def _picked(choices: dict, cid: str) -> list:
    v = choices.get(cid)
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def expand_list(kind: str, value, cid: str, origin: str, choices: dict, pools: Pools,
                out_grants: Grants, out_choices: list) -> None:
    """Expand a proficiency-style grant field (skills/languages/tools/...)."""
    if not value:
        return
    alts = value if isinstance(value, list) else [value]
    alts = [a for a in alts if isinstance(a, (dict, str))]
    if not alts:
        return
    if len(alts) > 1:
        labels = [describe_alt(a) for a in alts]
        out_choices.append(Choice(id=f'{cid}#alt', kind='alternative', label=f'{origin}: {_KIND_LABEL.get(kind, kind)} option',
                                  options=[{'value': i, 'label': l} for i, l in enumerate(labels)],
                                  origin=origin))
        sel = _picked(choices, f'{cid}#alt')
        try:
            idx = int(sel[0]) if sel else None
        except (TypeError, ValueError):
            idx = None
        if idx is None or not (0 <= idx < len(alts)):
            return
        alt = alts[idx]
        cid = f'{cid}#{idx}'
    else:
        alt = alts[0]
    _expand_one(kind, alt, cid, origin, choices, pools, out_grants, out_choices)


def _add_fixed(kind: str, key: str, g: Grants):
    target = _SET_FIELD.get(kind)
    if kind == 'skillToolLanguage':
        target = 'skills' if key in SKILLS else 'tools'
    if target:
        getattr(g, target).add(_norm(key))


_TOOL_CATEGORIES = {'musical instrument': 'anyMusicalInstrument', 'gaming set': 'anyGamingSet',
                    "artisan's tools": 'anyArtisansTool'}


def _expand_one(kind, alt, cid, origin, choices, pools, g: Grants, out_choices):
    if isinstance(alt, str):
        _add_fixed(kind, alt, g)
        return
    n = 0
    for key, val in alt.items():
        if key == 'choose':
            specs = val if isinstance(val, list) else [val]
            for spec in specs:
                frm = spec.get('from') or []
                opts = []
                for o in frm:
                    if isinstance(o, str) and o.startswith('any'):
                        opts += pools.named(o, kind)
                    elif isinstance(o, str) and _norm(o) in _TOOL_CATEGORIES:
                        # "a musical instrument or a gaming set": the player
                        # picks a specific one from the category.
                        opts += pools.named(_TOOL_CATEGORIES[_norm(o)], kind)
                    else:
                        opts.append(_norm(o) if isinstance(o, str) else o)
                count = int(spec.get('count', 1))
                sub = f'{cid}:c{n}'
                n += 1
                q = {}
                if 'fromFilter' in spec:
                    q = {'filter': spec['fromFilter']}
                out_choices.append(Choice(id=sub, kind=_choice_kind(kind), label=f'{origin}: choose {count} {_KIND_LABEL.get(kind, kind)}{"s" if count > 1 else ""}',
                                          count=count, options=sorted(dict.fromkeys(opts)), query=q, origin=origin))
                for p in _picked(choices, sub)[:count]:
                    _add_fixed(kind, str(p), g)
        elif key.startswith('any') or (key in ('any',) and isinstance(val, int)):
            count = int(val) if isinstance(val, int) else 1
            sub = f'{cid}:{key}'
            out_choices.append(Choice(id=sub, kind=_choice_kind(kind), label=f'{origin}: choose {count} {_KIND_LABEL.get(kind, kind)}{"s" if count > 1 else ""}',
                                      count=count, options=pools.named(key, kind), origin=origin,
                                      query={'pool': key}))
            for p in _picked(choices, sub)[:count]:
                _add_fixed(kind, str(p), g)
        elif val is True:
            if kind == 'armor' or kind == 'weapon':
                getattr(g, _SET_FIELD[kind]).add(_norm(key))
            elif key == 'other':
                continue   # "one other language of your choice" is modelled by anyStandard in data
            else:
                _add_fixed(kind, key, g)


def _choice_kind(kind: str) -> str:
    return {'skillToolLanguage': 'skill_tool_language'}.get(kind, kind)


def describe_alt(alt) -> str:
    if isinstance(alt, str):
        return alt
    parts = []
    for k, v in alt.items():
        if k == 'choose':
            for spec in (v if isinstance(v, list) else [v]):
                if 'weighted' in spec:
                    w = spec['weighted']
                    parts.append('+' + '/+'.join(str(x) for x in w.get('weights', [])) + ' among ' +
                                 ', '.join(a.upper() for a in w.get('from', [])))
                else:
                    parts.append(f'choose {spec.get("count", 1)} from ' + ', '.join(str(x).split('|')[0] for x in spec.get('from', [])))
        elif v is True:
            parts.append(k.split('|')[0])
        elif isinstance(v, int):
            parts.append(f'{k.upper()} +{v}' if k in ABILITIES else f'{k} x{v}')
    return '; '.join(parts) or 'none'


def expand_ability(value, cid: str, origin: str, choices: dict, g: Grants, out_choices: list) -> None:
    """Expand an ``ability`` field (fixed bonuses / choose / weighted)."""
    if not value:
        return
    alts = value if isinstance(value, list) else [value]
    if len(alts) > 1:
        out_choices.append(Choice(id=f'{cid}#alt', kind='alternative', label=f'{origin}: ability score increase option',
                                  options=[{'value': i, 'label': describe_alt(a)} for i, a in enumerate(alts)],
                                  origin=origin))
        sel = _picked(choices, f'{cid}#alt')
        try:
            idx = int(sel[0]) if sel else None
        except (TypeError, ValueError):
            idx = None
        if idx is None or not (0 <= idx < len(alts)):
            return
        alt, cid = alts[idx], f'{cid}#{idx}'
    else:
        alt = alts[0]
    if not isinstance(alt, dict):
        return
    cap = int(alt.get('max', 20))
    for k, v in alt.items():
        if k in ABILITIES and isinstance(v, int):
            g.abilities[k] = g.abilities.get(k, 0) + v
            if cap > 20:
                g.ability_max[k] = max(g.ability_max.get(k, 20), cap)
        elif k == 'choose':
            spec = v
            if 'weighted' in spec:
                w = spec['weighted']
                frm = list(w.get('from') or ABILITIES)
                weights = tuple(int(x) for x in w.get('weights') or [1])
                out_choices.append(Choice(id=cid, kind='ability', label=f'{origin}: ability score increases',
                                          count=len(weights), options=frm, weights=weights, origin=origin,
                                          max_score=cap))
                picked = choices.get(cid)
                if isinstance(picked, dict) and _valid_weighted(picked, frm, weights):
                    for a, amt in picked.items():
                        g.abilities[a] = g.abilities.get(a, 0) + int(amt)
                        if cap > 20:
                            g.ability_max[a] = max(g.ability_max.get(a, 20), cap)
            else:
                frm = list(spec.get('from') or ABILITIES)
                count = int(spec.get('count', 1))
                amount = int(spec.get('amount', 1))
                out_choices.append(Choice(id=cid, kind='ability', label=f'{origin}: +{amount} to {count} abilit{"ies" if count > 1 else "y"}',
                                          count=count, options=frm, amount=amount, origin=origin,
                                          weights=tuple([amount] * count), max_score=cap))
                picked = choices.get(cid)
                if isinstance(picked, dict) and _valid_weighted(picked, frm, tuple([amount] * count)):
                    for a, amt in picked.items():
                        g.abilities[a] = g.abilities.get(a, 0) + int(amt)
                        if cap > 20:
                            g.ability_max[a] = max(g.ability_max.get(a, 20), cap)


def _valid_weighted(picked: dict, frm, weights) -> bool:
    if not picked or any(a not in frm for a in picked):
        return False
    try:
        got = sorted((int(v) for v in picked.values()), reverse=True)
    except (TypeError, ValueError):
        return False
    return got == sorted(weights, reverse=True)


def validate_choice(choice: Choice, value) -> str | None:
    """Return an error message for an invalid selection, else None."""
    if value is None or value == [] or value == {}:
        return None if not choice.required else f'{choice.label}: not chosen'
    if choice.kind == 'ability':
        if not isinstance(value, dict):
            return f'{choice.label}: expected ability -> bonus'
        if not _valid_weighted(value, choice.options, choice.weights):
            return f'{choice.label}: invalid ability selection'
        return None
    if choice.kind == 'asi':
        if not isinstance(value, dict) or not (value.get('feat') or isinstance(value.get('asi'), dict)):
            return f'{choice.label}: choose an ability score increase or a feat'
        return None
    if choice.kind == 'alternative':
        vals = value if isinstance(value, list) else [value]
        ok = {o['value'] for o in choice.options}
        try:
            return None if int(vals[0]) in ok else f'{choice.label}: invalid option'
        except (TypeError, ValueError):
            return f'{choice.label}: invalid option'
    vals = value if isinstance(value, list) else [value]
    if len(vals) > choice.count:
        return f'{choice.label}: pick at most {choice.count}'
    if len(set(map(str, vals))) != len(vals):
        return f'{choice.label}: duplicate picks'
    if choice.options:
        allowed = {str(o['value']) if isinstance(o, dict) else str(o) for o in choice.options}
        bad = [v for v in vals if str(v) not in allowed]
        if bad:
            return f'{choice.label}: {bad[0]!r} is not an option'
    return None


def is_complete(choice: Choice, value) -> bool:
    if not choice.required:
        return True
    if value is None:
        return False
    if choice.kind == 'ability':
        return isinstance(value, dict) and _valid_weighted(value, choice.options, choice.weights)
    if choice.kind == 'alternative':
        return value not in (None, [], '')
    if choice.kind == 'asi':
        return isinstance(value, dict) and bool(value.get('feat') or value.get('asi'))
    vals = value if isinstance(value, list) else [value]
    if choice.options:
        return len(vals) >= min(choice.count, len(choice.options))
    return len(vals) >= choice.count
