"""Rulebook-accurate Cosmere character builder + leveler engine.

Encodes the Stormlight core rulebook's creation and advancement rules
(Ch.1, verified against the 392-page core rulebook):

  Attributes  12-point buy at creation, each 0-3; +1 at L3/6/9/12/15/18; hard cap 5.
  Health      10+STR at L1, then per-tier gains +5/+4/+3/+2/+1, STR re-added at L6/11/16.
  Focus       2+WIL.    Investiture  2+max(AWA,PRE) once Radiant, else 0.
  Defenses    Physical 10+STR+SPD, Cognitive 10+INT+WIL, Spiritual 10+AWA+PRE.
  Skills      4 ranks +1 from the starting path at L1, +2/level through L20;
              L21+ = +1 rank OR +1 talent.  Max rank by tier 2/3/4/5/5.
              Skill mod = ranks + governing attribute.
  Expertises  2 from culture + Intellect more.
  Talents     1 path key talent at L1 + ancestry bonus talent at each tier start
              (L1/6/11/16/21); +1/level through L20; L21+ = +1 talent OR +1 rank.

Rules are GUIDED, not enforced: ``validate()`` reports every violation as a
human-readable string so the UI can warn, but a GM may override (the engine
never raises on a "wrong" build).
"""
from __future__ import annotations

from systems.cosmere import SKILL_ATTR, SKILL_NAMES, SURGE_SKILLS, PATHS
from systems.cosmere import radiant as _radiant
from systems.cosmere import origins as _origins
from systems.cosmere.actor import cosmere_max_health, tier_of
from systems.cosmere.items import Inventory

ATTR_KEYS = ('str', 'spd', 'int', 'wil', 'awa', 'pre')

# --- creation / advancement constants (rulebook Ch.1 Character Advancement) --
CREATION_ATTR_POINTS = 12
CREATION_ATTR_MAX = 3
ATTR_HARD_CAP = 5
ATTR_INCREASE_LEVELS = (3, 6, 9, 12, 15, 18)
TIER_START_LEVELS = (1, 6, 11, 16, 21)        # ancestry bonus talent here
MAX_SKILL_RANK_BY_TIER = {1: 2, 2: 3, 3: 4, 4: 5, 5: 5}
CREATION_FREE_SKILL_RANKS = 4
PATH_SKILL_RANK = 1                            # +1 in the starting path's skill


# --- advancement budget functions ------------------------------------------
def attribute_points(level: int) -> int:
    """Total attribute *score* points available by `level` (12 + the L3/6/9/.. bumps)."""
    return CREATION_ATTR_POINTS + sum(1 for L in ATTR_INCREASE_LEVELS if L <= level)


def max_skill_rank(level: int) -> int:
    return MAX_SKILL_RANK_BY_TIER[tier_of(level)]


def free_skill_ranks(level: int, epic_skill_choices: int = 0) -> int:
    """Player-distributed skill ranks by `level` (4 at L1, +2/level through L20,
    then +1 per L21+ level the player spent on a skill rather than a talent)."""
    lv = max(1, min(level, 20))
    return CREATION_FREE_SKILL_RANKS + 2 * (lv - 1) + max(0, epic_skill_choices)


def total_skill_ranks(level: int, epic_skill_choices: int = 0) -> int:
    return free_skill_ranks(level, epic_skill_choices) + PATH_SKILL_RANK


def base_talents(level: int, epic_talent_choices: int = 0) -> int:
    """Path/level talents by `level` (1 at L1, +1/level through L20, then L21+ choices)."""
    return max(1, min(level, 20)) + max(0, epic_talent_choices)


def ancestry_bonus_talents(level: int) -> int:
    return sum(1 for L in TIER_START_LEVELS if L <= level)


def total_talents(level: int, epic_talent_choices: int = 0) -> int:
    return base_talents(level, epic_talent_choices) + ancestry_bonus_talents(level)


def expertises_total(intellect: int) -> int:
    return 2 + max(0, int(intellect))


def health_gain_at(level: int, strength: int) -> int:
    """Health gained when reaching `level` (the advancement-table row)."""
    if level <= 1:
        return cosmere_max_health(1, strength)
    return cosmere_max_health(level, strength) - cosmere_max_health(level - 1, strength)


def level_grants(level: int, strength: int = 0) -> dict:
    """What advancing TO `level` grants — the advancement-table row, for the UI."""
    return {
        'level': level,
        'tier': tier_of(level),
        'attribute_point': level in ATTR_INCREASE_LEVELS,
        'health': health_gain_at(level, strength),
        'skill_ranks': 2 if level <= 20 else 'choice',
        'talent': (level <= 20) or 'choice',
        'ancestry_bonus_talent': level in TIER_START_LEVELS,
        'max_skill_rank': max_skill_rank(level),
    }


class CosmereBuild:
    """An editable Cosmere character build (creation + leveling)."""

    def __init__(self, data=None):
        d = dict(data or {})
        self.name = d.get('name') or 'New Hero'
        self.level = max(1, int(d.get('level', 1) or 1))
        self.ancestry = d.get('ancestry') or 'Human'
        self.culture = d.get('culture') or ''
        self.path = (d.get('path') or '').lower()
        self.attributes = {k: int((d.get('attributes') or {}).get(k, 0) or 0) for k in ATTR_KEYS}
        self.skills = {c: int(v) for c, v in (d.get('skills') or {}).items()
                       if c in SKILL_ATTR and int(v or 0) > 0}
        self.path_skill = d.get('path_skill')
        self.expertises = [e for e in (d.get('expertises') or []) if e]
        self.talents = [t for t in (d.get('talents') or []) if t]   # [{id, name}]
        # Radiant / Surgebinding (Ch.5): an order grants Investiture + Stormlight
        # actions; swearing its First Ideal unlocks its two surge skills.
        self.radiant_order = (d.get('radiant_order') or '').lower()
        self.ideals_sworn = max(0, min(_radiant.IDEAL_COUNT, int(d.get('ideals_sworn', 0) or 0)))
        self.spren_name = d.get('spren_name', '')
        self.ideal_words = list(d.get('ideal_words') or [])
        self.is_radiant = bool(self.radiant_order) or bool(d.get('is_radiant'))
        self.inventory = Inventory(d.get('inventory'))
        self.epic_choices = list(d.get('epic_choices') or [])       # per L21+ level: 'skill' | 'talent'
        self.goals = d.get('goals', '')
        self.purpose = d.get('purpose', '')
        self.obstacle = d.get('obstacle', '')
        self.appearance = d.get('appearance', '')
        self.notes = d.get('notes', '')

    # -- budgets ------------------------------------------------------------
    @property
    def tier(self) -> int:
        return tier_of(self.level)

    @property
    def epic_skill_choices(self) -> int:
        return self.epic_choices.count('skill')

    @property
    def epic_talent_choices(self) -> int:
        return self.epic_choices.count('talent')

    def attr_points_spent(self) -> int:
        return sum(self.attributes.values())

    def attr_points_available(self) -> int:
        return attribute_points(self.level)

    def skill_ranks_spent(self) -> int:
        return sum(self.skills.values())

    def skill_ranks_available(self) -> int:
        # Swearing the First Ideal grants a free rank in each of the order's
        # two surge skills (beyond the normal advancement budget).
        return total_skill_ranks(self.level, self.epic_skill_choices) + len(self.surges_unlocked())

    # -- radiant / surgebinding --------------------------------------------
    @property
    def first_ideal_sworn(self) -> bool:
        return self.ideals_sworn >= 1

    def order(self):
        return _radiant.order(self.radiant_order)

    def surge_codes(self) -> tuple:
        o = self.order()
        return tuple(o['surges']) if o else ()

    def surges_unlocked(self) -> tuple:
        """The order's two surge skills — available once the First Ideal is sworn."""
        return self.surge_codes() if (self.radiant_order and self.first_ideal_sworn) else ()

    # -- heroic path grants (key talent + starting skill) ------------------
    def path_start_skill(self):
        return _origins.path_start_skill(self.path)

    def path_key_talent(self):
        return _origins.path_key_talent(self.path)

    def has_key_talent(self) -> bool:
        kt = self.path_key_talent()
        if not kt:
            return False
        return any(isinstance(t, dict) and (t.get('id') == kt['id'] or t.get('name') == kt['name'])
                   for t in self.talents)

    def talents_available(self) -> int:
        return total_talents(self.level, self.epic_talent_choices)

    def expertises_available(self) -> int:
        return expertises_total(self.attributes['int'])

    # -- derived statistics (rulebook formulas) -----------------------------
    def defenses(self) -> dict:
        a = self.attributes
        return {'phy': 10 + a['str'] + a['spd'],
                'cog': 10 + a['int'] + a['wil'],
                'spi': 10 + a['awa'] + a['pre']}

    def health_max(self) -> int:
        return cosmere_max_health(self.level, self.attributes['str'])

    def focus_max(self) -> int:
        return 2 + self.attributes['wil']

    def investiture_max(self) -> int:
        a = self.attributes
        return (2 + max(a['awa'], a['pre'])) if self.is_radiant else 0

    def skill_mods(self) -> dict:
        return {c: self.skills.get(c, 0) + self.attributes[SKILL_ATTR[c]] for c in SKILL_ATTR}

    def deflect_value(self) -> int:
        return self.inventory.deflect_value()

    # -- validation (guided; never raises) ----------------------------------
    def validate(self) -> list:
        issues = []
        sp, av = self.attr_points_spent(), self.attr_points_available()
        if sp != av:
            issues.append(f"Attributes: {sp} of {av} points spent.")
        if self.level == 1 and any(v > CREATION_ATTR_MAX for v in self.attributes.values()):
            issues.append(f"Attributes: max {CREATION_ATTR_MAX} per attribute at character creation.")
        if any(v > ATTR_HARD_CAP for v in self.attributes.values()):
            issues.append(f"Attributes: hard cap is {ATTR_HARD_CAP}.")
        if any(v < 0 for v in self.attributes.values()):
            issues.append("Attributes cannot be negative.")

        msr = max_skill_rank(self.level)
        over = [c for c, v in self.skills.items() if v > msr]
        if over:
            issues.append(f"Skills above the tier-{self.tier} max rank of {msr}: {', '.join(sorted(over))}.")
        ssp, ssa = self.skill_ranks_spent(), self.skill_ranks_available()
        if ssp > ssa:
            issues.append(f"Skills: {ssp} of {ssa} ranks spent.")
        unlocked = set(self.surges_unlocked())
        stray_surge = [c for c in self.skills if c in SURGE_SKILLS and c not in unlocked]
        if stray_surge:
            issues.append("Surge skills require swearing your order's First Ideal: "
                          + ', '.join(_radiant.surge_name(c) for c in sorted(stray_surge)) + '.')
        if self.radiant_order and self.radiant_order not in _radiant.RADIANT_ORDERS:
            issues.append("Unknown Radiant order.")
        if self.radiant_order and self.level < _radiant.RADIANT_MIN_LEVEL:
            issues.append("Becoming Radiant (a First Ideal) requires level %d+." % _radiant.RADIANT_MIN_LEVEL)

        exp_av = self.expertises_available()
        if len(self.expertises) > exp_av:
            issues.append(f"Expertises: {len(self.expertises)} chosen of {exp_av} available.")

        t_av = self.talents_available()
        if len(self.talents) > t_av:
            issues.append(f"Talents: {len(self.talents)} chosen of {t_av} available.")

        if self.path not in PATHS:
            issues.append("Choose a heroic path.")
        else:
            ks = self.path_start_skill()
            if ks and self.skills.get(ks, 0) < 1:
                issues.append("Your path's starting skill (%s) should have at least 1 rank." % SKILL_NAMES.get(ks, ks))
            if not self.has_key_talent():
                kt = self.path_key_talent()
                if kt:
                    issues.append("Add your path's key talent: %s." % kt['name'])
        return issues

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            'name': self.name, 'level': self.level, 'ancestry': self.ancestry,
            'culture': self.culture, 'path': self.path,
            'attributes': dict(self.attributes), 'skills': dict(self.skills),
            'path_skill': self.path_skill, 'expertises': list(self.expertises),
            'talents': list(self.talents), 'is_radiant': self.is_radiant,
            'radiant_order': self.radiant_order, 'ideals_sworn': self.ideals_sworn,
            'spren_name': self.spren_name, 'ideal_words': list(self.ideal_words),
            'inventory': self.inventory.to_list(), 'epic_choices': list(self.epic_choices),
            'goals': self.goals, 'purpose': self.purpose, 'obstacle': self.obstacle,
            'appearance': self.appearance, 'notes': self.notes,
        }

    def to_actor_doc(self) -> dict:
        """A Foundry-shaped character doc that CosmereActor renders (the bridge
        from a build to a live sheet/tracker actor). All stats are left to
        CosmereActor to compute from attributes+level (matching this engine)."""
        a = self.attributes
        unlocked_surges = set(self.surges_unlocked())
        skills = {}
        for c, attr in SKILL_ATTR.items():
            skills[c] = {
                'attribute': attr,
                'rank': self.skills.get(c, 0),
                'mod': {'override': None, 'useOverride': False, 'bonus': 0},
                'unlocked': (c not in SURGE_SKILLS) or (c in unlocked_surges),
            }
        inv_max = ({'override': self.investiture_max(), 'useOverride': True, 'bonus': 0}
                   if self.is_radiant else {'override': None, 'useOverride': False, 'bonus': 0})
        system = {
            'level': {'value': self.level},
            'tier': self.tier,
            'role': 'hero',
            'size': 'medium',
            'attributes': {k: {'value': a[k], 'bonus': 0} for k in ATTR_KEYS},
            'defenses': {d: {'bonus': 0, 'override': None, 'useOverride': False}
                         for d in ('phy', 'cog', 'spi')},
            'resources': {
                'hea': {'value': None, 'max': {'override': None, 'useOverride': False, 'bonus': 0}},
                'foc': {'value': None, 'max': {'override': None, 'useOverride': False, 'bonus': 0}},
                'inv': {'value': None, 'max': inv_max},
            },
            'skills': skills,
            'deflect': self.inventory.deflect_block(),
            'expertises': list(self.expertises),
            'ancestry': self.ancestry, 'culture': self.culture, 'path': self.path,
            'radiant_order': self.radiant_order, 'spren': self.spren_name,
            'ideals_sworn': self.ideals_sworn,
            'cosmere_build': self.to_dict(),     # stashed so the build can be re-edited / leveled
        }
        items = self.inventory.foundry_weapon_items()
        if self.is_radiant:
            # A First Ideal talent grants the three Stormlight actions (Ch.5).
            for act in _radiant.STORMLIGHT_ACTIONS:
                items.append({'type': 'action', 'name': act['name'],
                              'system': {'description': {'value': act['description']}}})
        return {'name': self.name, 'type': 'character', 'system': system, 'items': items}

    @classmethod
    def from_actor_doc(cls, doc) -> 'CosmereBuild':
        """Recover a build from a stored actor doc (reads the stashed build)."""
        sys = (doc or {}).get('system', {}) if isinstance(doc, dict) else {}
        stashed = sys.get('cosmere_build') if isinstance(sys.get('cosmere_build'), dict) else None
        return cls(stashed or {})
