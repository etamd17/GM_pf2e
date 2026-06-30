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
from systems.cosmere import talents as _talents
from systems.cosmere import homebrew as _homebrew
from systems.cosmere import infected as _infected
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


# ── Attribute-derived lookup tables (rulebook Ch.3). Each maps an attribute
#    score to a sheet value. Recovery Die lives in combat.py (combat.recovery_die).
def _band(score: int) -> int:
    """The shared attribute band index: 0 -> 0, 1-2 -> 1, 3-4 -> 2, 5-6 -> 3,
    7-8 -> 4, 9+ -> 5."""
    s = max(0, int(score or 0))
    return 0 if s == 0 else min(5, (s + 1) // 2)


def movement_rate(speed: int) -> int:
    """Movement in feet per action, from Speed (Ch.3)."""
    return (20, 25, 30, 40, 60, 80)[_band(speed)]


def senses_range(awareness: int):
    """Range (ft) you sense clearly when obscured, from Awareness (Ch.3). At 9+
    you are unaffected by obscured senses -> returns the string 'Unaffected'."""
    b = _band(awareness)
    return 'Unaffected' if b >= 5 else (5, 10, 20, 50, 100)[b]


def lifting_capacity(strength: int) -> int:
    """Max lift in pounds, from Strength (Ch.3)."""
    return (100, 200, 500, 1000, 5000, 10000)[_band(strength)]


def carrying_capacity(strength: int) -> int:
    """Max carry in pounds (half of lifting), from Strength (Ch.3)."""
    return (50, 100, 250, 500, 2500, 5000)[_band(strength)]


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

    def __init__(self, data=None, homebrew=None):
        d = dict(data or {})
        # Per-campaign homebrew store ({type: [entry]}); its structured stat
        # bonuses are applied to derived stats and its paths/orders are honored.
        self._homebrew = homebrew or {}
        # Homebrew surges that are real skills ({code: {name, attribute}}).
        self._hb_surges = _homebrew.surge_skills(self._homebrew)
        self.name = d.get('name') or 'New Hero'
        self.level = max(1, int(d.get('level', 1) or 1))
        self.ancestry = d.get('ancestry') or 'Human'
        self.culture = d.get('culture') or ''
        self.path = (d.get('path') or '').lower()
        self.singer_form = (d.get('singer_form') or '').lower()   # Singer ancestry only
        self.attributes = {k: int((d.get('attributes') or {}).get(k, 0) or 0) for k in ATTR_KEYS}
        self.skills = {c: int(v) for c, v in (d.get('skills') or {}).items()
                       if c in self.eff_skill_attr() and int(v or 0) > 0}
        self.path_skill = d.get('path_skill')
        self.expertises = [e for e in (d.get('expertises') or []) if e]
        self.talents = [t for t in (d.get('talents') or []) if t]   # [{id, name}]
        # Radiant / Surgebinding (Ch.5): an order grants Investiture + Stormlight
        # actions; swearing its First Ideal unlocks its two surge skills.
        self.radiant_order = (d.get('radiant_order') or '').lower()
        self.radiant_variant = (d.get('radiant_variant') or '').lower()   # order sub-path (canon/nale/enlightened)
        self.ideals_sworn = max(0, min(_radiant.IDEAL_COUNT, int(d.get('ideals_sworn', 0) or 0)))
        self.spren_name = d.get('spren_name', '')
        self.ideal_words = list(d.get('ideal_words') or [])
        # Milestones (0-3) toward speaking the NEXT Ideal (Ch.5: the GM marks
        # three, then the Words can be spoken). Per-character progress, not RAW math.
        self.ideal_progress = max(0, min(3, int(d.get('ideal_progress', 0) or 0)))
        self.is_radiant = bool(self.radiant_order) or bool(d.get('is_radiant'))
        self.inventory = Inventory(d.get('inventory'))
        # Custom / homebrew weapons that don't match a catalog item (e.g. an
        # imported "Soravar's Gauntlet"): each {name, damage, type, attack} becomes
        # a Strike with its explicit attack bonus. Lets the PDF importer keep
        # one-off creations instead of dropping unmatched weapons.
        self.custom_weapons = [dict(w) for w in (d.get('custom_weapons') or []) if isinstance(w, dict) and w.get('name')]
        self.fabrials = [str(x) for x in (d.get('fabrials') or [])]   # equipped Fabrial device ids (Ch.7)
        self.epic_choices = list(d.get('epic_choices') or [])       # per L21+ level: 'skill' | 'talent'
        self.goals = d.get('goals', '')
        self.purpose = d.get('purpose', '')
        self.obstacle = d.get('obstacle', '')
        self.appearance = d.get('appearance', '')
        self.notes = d.get('notes', '')
        # Narrative fields the rulebook character sheet records (Ch.1). Connections
        # (NPC/faction bonds) is the one the sheet explicitly tracks; the rest are
        # the recommended story prompts. All free-form text.
        self.connections = d.get('connections', '')
        self.occupation = d.get('occupation', '')
        self.relationships = d.get('relationships', '')
        self.loyalties = d.get('loyalties', '')
        self.personality = d.get('personality', '')
        # Structured stat bonuses from the homebrew this character has selected.
        self.homebrew_bonuses, self.homebrew_sources, self.homebrew_dangling = \
            _homebrew.resolve_bonuses(d, self._homebrew)
        # Explicit additive stat bonuses carried by the build dict itself. Used by
        # the PDF importer to reproduce a sheet's AUTHORITATIVE totals (e.g. a +5
        # health from the Hardy talent, or armor Deflect) that the build can't
        # derive from talents-by-name. Keys match _hb(): 'health', 'deflect',
        # 'focus', 'investiture', 'def:phy|cog|spi', 'skill:<code>'. Merged
        # additively so they flow through every derived stat via _hb() (and thus
        # to_actor_doc), at every render site, with no per-site patching.
        self.stat_bonuses = {}
        for _k, _v in (d.get('stat_bonuses') or {}).items():
            try:
                iv = int(_v)
            except (TypeError, ValueError):
                continue
            self.stat_bonuses[_k] = iv
            self.homebrew_bonuses[_k] = int(self.homebrew_bonuses.get(_k, 0) or 0) + iv
        # Infected Arts (a homebrew Invested-disease system): a selection of art
        # ids whose disease COSTS apply as structured stat effects -- ``_inf_add``
        # sums into derived stats via _hb(), ``_inf_set`` overrides them (e.g.
        # STR->9, max Focus->0). The granted abilities ride on to_actor_doc.
        self.infected_arts = [a for a in (d.get('infected_arts') or []) if a]
        self._inf_add, self._inf_set, self._inf_records = _infected.resolve(self.infected_arts)

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

    def next_ideal(self):
        """The Ideal number (1-4) the character is working toward, or None when
        all reachable Ideals are sworn (the Fifth is unreachable in play)."""
        return self.ideals_sworn + 1 if self.ideals_sworn < 4 else None

    def next_ideal_level_ok(self) -> bool:
        """Is the character high enough level to swear the next Ideal? First needs
        L2+ (becoming Radiant); the Fourth needs L13+ (Ch.5)."""
        n = self.next_ideal()
        if n is None:
            return False
        if n == 1:
            return self.level >= _radiant.RADIANT_MIN_LEVEL
        if n >= 4:
            return self.level >= _radiant.FOURTH_IDEAL_LEVEL
        return True

    def can_speak_next_ideal(self) -> bool:
        """The next Ideal can be sworn once its three milestones are marked and
        the level gate is met."""
        return self.next_ideal() is not None and self.ideal_progress >= 3 and self.next_ideal_level_ok()

    def ideal_states(self) -> list:
        """Per-Ideal view for the sheet/builder: [{n, sworn, words, suggested}]
        for Ideals 1-4 (5th is unreachable)."""
        out = []
        for n in range(1, 5):
            words = self.ideal_words[n - 1] if n - 1 < len(self.ideal_words) else ''
            out.append({
                'n': n,
                'sworn': self.ideals_sworn >= n,
                'words': words,
                'suggested': _radiant.ideal_text(self.radiant_order, n),
                'personal': self.radiant_order in _radiant.IDEAL_PERSONAL,
            })
        return out

    def derived_stats(self) -> dict:
        """Attribute-derived sheet values (Ch.3): movement, senses, lifting/
        carrying capacity, and the recovery die size. Uses effective attributes
        (so Singer-form bonuses apply)."""
        import systems.cosmere.combat as _cc
        a = self.eff_attributes()
        return {
            'movement': movement_rate(a['spd']),
            'senses': senses_range(a['awa']),
            'lifting': lifting_capacity(a['str']),
            'carrying': carrying_capacity(a['str']),
            'recovery_die': 'd%d' % _cc.recovery_die(a['wil']),
        }

    def _hb(self, key) -> int:
        """An additive structured bonus for a derived-stat target (0 if none) --
        from selected homebrew AND the additive effects of Infected Arts."""
        return int(self.homebrew_bonuses.get(key, 0) or 0) + int(self._inf_add.get(key, 0) or 0)

    def order(self):
        # Variant-adjusted (e.g. Canon Dustbringers lose Division) so surge_codes()
        # — and everything downstream — reflects the chosen sub-path.
        return (_radiant.order_with_variant(self.radiant_order, self.radiant_variant)
                or _homebrew.radiant_order(self._homebrew, self.radiant_order))

    def surge_codes(self) -> tuple:
        o = self.order()
        return tuple(o['surges']) if o else ()

    def surges_unlocked(self) -> tuple:
        """The order's two surge skills — available once the First Ideal is sworn."""
        return self.surge_codes() if (self.radiant_order and self.first_ideal_sworn) else ()

    # -- effective skill tables (canon + homebrew surge skills) -------------
    def eff_skill_attr(self) -> dict:
        """skill code -> governing attribute, INCLUDING homebrew surge skills."""
        return {**SKILL_ATTR, **{c: s['attribute'] for c, s in self._hb_surges.items()}}

    def eff_skill_names(self) -> dict:
        return {**SKILL_NAMES, **{c: s['name'] for c, s in self._hb_surges.items()}}

    def eff_surge_skills(self) -> set:
        """The Surge skills (locked until the order's First Ideal) — canon + homebrew."""
        return set(SURGE_SKILLS) | set(self._hb_surges)

    def eff_surge_names(self) -> dict:
        """{code: {'name'}} for surge display — canon SURGES + homebrew surges."""
        out = {c: {'name': v['name']} for c, v in _radiant.SURGES.items()}
        out.update({c: {'name': s['name']} for c, s in self._hb_surges.items()})
        return out

    # -- heroic path grants (key talent + starting skill) ------------------
    def path_start_skill(self):
        s = _origins.path_start_skill(self.path)
        if s:
            return s
        hb = _homebrew.heroic_path(self._homebrew, self.path)
        return (hb.get('start_skill') or None) if hb else None

    def path_key_talent(self):
        kt = _origins.path_key_talent(self.path)
        if kt:
            return kt
        hb = _homebrew.heroic_path(self._homebrew, self.path)
        if hb and hb.get('key_talent'):
            return {'id': 'hb:%s-key' % hb['slug'], 'name': hb['key_talent']}
        return None

    def has_key_talent(self) -> bool:
        kt = self.path_key_talent()
        if not kt:
            return False
        return any(isinstance(t, dict) and (t.get('id') == kt['id'] or t.get('name') == kt['name'])
                   for t in self.talents)

    def talents_available(self) -> int:
        # Singers gain an EXTRA talent at creation: their ancestry grants Change
        # Form PLUS one connected starting-forms talent (Ch.1 "Choose an Ancestry
        # Talent", SL:1620-1622) -- two L1 ancestry talents where every other
        # ancestry gets one. That extra talent persists, so the budget is +1 at
        # every level for a Singer.
        return total_talents(self.level, self.epic_talent_choices) + (1 if self.is_singer else 0)

    def expertises_available(self) -> int:
        return expertises_total(self.attributes['int'])

    # -- singer ancestry / forms -------------------------------------------
    @property
    def is_singer(self) -> bool:
        return (self.ancestry or '').lower() == 'singer'

    def form(self):
        return _origins.singer_form(self.singer_form) if self.is_singer else None

    def eff_attributes(self) -> dict:
        """Attributes including the active Singer form's bonuses (a form may
        raise stats above the normal maximum) and any homebrew attribute bonuses
        (which then cascade into defenses / health / skills, like a real boost)."""
        a = dict(self.attributes)
        f = self.form()
        if f:
            for k, v in (f.get('attrs') or {}).items():
                a[k] = a.get(k, 0) + v
        for k in ATTR_KEYS:
            a[k] = a.get(k, 0) + self._hb('attr:%s' % k)   # form + homebrew + Infected adds
        # Infected Arts may OVERRIDE an attribute outright (superhuman, above the
        # normal cap) -- e.g. Hypercoagulable sets STR 9, Coagulopathy sets SPD 9.
        for k in ATTR_KEYS:
            if ('attr:%s' % k) in self._inf_set:
                a[k] = self._inf_set['attr:%s' % k]
        return a

    # -- derived statistics (rulebook formulas; use in-form attributes) -----
    def defenses(self) -> dict:
        a = self.eff_attributes()
        return {'phy': 10 + a['str'] + a['spd'] + self._hb('def:phy'),
                'cog': 10 + a['int'] + a['wil'] + self._hb('def:cog'),
                'spi': 10 + a['awa'] + a['pre'] + self._hb('def:spi')}

    def health_max(self) -> int:
        return cosmere_max_health(self.level, self.eff_attributes()['str']) + self._hb('health')

    def focus_max(self) -> int:
        f = self.form()
        # An Infected Art may zero out Focus (Chronic Pain) -- a 'set' override
        # wins over the computed pool; never below 0.
        if 'focus' in self._inf_set:
            return max(0, self._inf_set['focus'])
        return max(0, 2 + self.eff_attributes()['wil'] + (f['focus'] if f else 0) + self._hb('focus'))

    def investiture_max(self) -> int:
        a = self.eff_attributes()
        base = (2 + max(a['awa'], a['pre'])) if self.is_radiant else 0
        return base + self._hb('investiture')

    def skill_mods(self) -> dict:
        a = self.eff_attributes()
        return {c: self.skills.get(c, 0) + a[attr] + self._hb('skill:%s' % c)
                for c, attr in self.eff_skill_attr().items()}

    def deflect_value(self) -> int:
        f = self.form()
        return self.inventory.deflect_value() + (f['deflect'] if f else 0) + self._hb('deflect')

    def _deflect_block(self) -> dict:
        v = self.deflect_value()
        return {'natural': 0, 'bonus': 0, 'override': v, 'useOverride': bool(v),
                'source': 'armor', 'types': {'impact': True, 'keen': True, 'energy': True,
                                             'spirit': False, 'vital': False, 'heal': False}}

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
        stray_surge = [c for c in self.skills if c in self.eff_surge_skills() and c not in unlocked]
        if stray_surge:
            issues.append("Surge skills require swearing your order's First Ideal: "
                          + ', '.join(_radiant.surge_name(c) for c in sorted(stray_surge)) + '.')
        if self.radiant_order and self.radiant_order not in _radiant.RADIANT_ORDERS:
            issues.append("Unknown Radiant order.")
        if self.radiant_variant and self.radiant_variant not in _radiant.variants(self.radiant_order):
            issues.append("That order variant doesn't belong to this order.")
        if self.radiant_order and self.level < _radiant.RADIANT_MIN_LEVEL:
            issues.append("Becoming Radiant (a First Ideal) requires level %d+." % _radiant.RADIANT_MIN_LEVEL)
        if self.ideals_sworn >= 4 and self.level < _radiant.FOURTH_IDEAL_LEVEL:
            issues.append("The Fourth Ideal can't be sworn before level %d." % _radiant.FOURTH_IDEAL_LEVEL)

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

        # Singer ancestry must take Change Form (its key talent).
        if self.is_singer and not any(isinstance(t, dict) and t.get('id') == _origins.SINGER_CHANGE_FORM['id']
                                      for t in self.talents):
            issues.append("Add the Singer key talent: Change Form.")

        # Talent prerequisites — now hard-enforced (see hard_violations); listed
        # here too so the full guidance view still mentions them.
        issues.extend(self.unmet_prereqs())
        return issues

    def unmet_prereqs(self) -> list:
        """Taken talents whose CONCRETE prerequisites (a predecessor talent, a
        skill rank, or an attribute floor) aren't met — one human-readable string
        each. Narrative gates (goals, Ideals, level) are NOT machine-checked.
        Uses effective attributes so a Singer form's bonus counts."""
        taken = [t.get('name', '') for t in self.talents if isinstance(t, dict)]
        out = []
        # Radiant talents are gated by Ideal (and sometimes level); enforce those
        # at save the same way the visual tree locks them client-side.
        import systems.cosmere.radiant_talents as _rt
        _gates = _rt.talent_gates()
        _ORD = ['', 'First', 'Second', 'Third', 'Fourth', 'Fifth']
        for t in self.talents:
            if not (isinstance(t, dict) and t.get('id')):
                continue
            tid, tname = t['id'], t.get('name', 'A talent')
            if str(tid).startswith('radiant:'):
                g = _gates.get((t.get('name') or '').lower())
                if g:
                    if g['ideal'] > self.ideals_sworn:
                        ord_name = _ORD[g['ideal']] if g['ideal'] < len(_ORD) else str(g['ideal'])
                        out.append("%s needs the %s Ideal." % (tname, ord_name))
                    if g['level'] > self.level:
                        out.append("%s needs level %d." % (tname, g['level']))
                continue
            miss = _talents.unmet(tid, taken, self.skills, self.eff_attributes())
            if miss:
                out.append("%s needs %s." % (tname, ' + '.join(miss)))
        return out

    @property
    def is_valid(self) -> bool:
        return not self.validate()

    def hard_violations(self) -> list:
        """The subset of validate() that represents OVER-application of the rules:
        more attribute points / skill ranks / talents / expertises than the build
        is entitled to, a value above its hard cap, or a negative score. These are
        the "you literally can't have this much" breaks -- they BLOCK a player's
        save (a GM may override). Unmet talent PREREQUISITES are also blocked here
        (the rulebook requires a predecessor talent before a deeper one). Under-
        spending, a missing key talent, and other "incomplete" guidance are NOT
        included: those are warnings, not illegal characters."""
        hard = []
        sp, av = self.attr_points_spent(), self.attr_points_available()
        if sp > av:
            hard.append(f"Attributes: {sp} of {av} points spent (over budget by {sp - av}).")
        if self.level == 1 and any(v > CREATION_ATTR_MAX for v in self.attributes.values()):
            hard.append(f"Attributes: the max is {CREATION_ATTR_MAX} per attribute at character creation.")
        if any(v > ATTR_HARD_CAP for v in self.attributes.values()):
            hard.append(f"Attributes: the hard cap is {ATTR_HARD_CAP}.")
        if any(v < 0 for v in self.attributes.values()):
            hard.append("Attributes cannot be negative.")
        msr = max_skill_rank(self.level)
        over = [c for c, v in self.skills.items() if v > msr]
        if over:
            names = ', '.join(SKILL_NAMES.get(c, c) for c in sorted(over))
            hard.append(f"Skills above your tier-{self.tier} max rank of {msr}: {names}.")
        ssp, ssa = self.skill_ranks_spent(), self.skill_ranks_available()
        if ssp > ssa:
            hard.append(f"Skills: {ssp} of {ssa} ranks spent (over budget by {ssp - ssa}).")
        exp_av = self.expertises_available()
        if len(self.expertises) > exp_av:
            hard.append(f"Expertises: {len(self.expertises)} chosen of {exp_av} available.")
        t_av = self.talents_available()
        if len(self.talents) > t_av:
            hard.append(f"Talents: {len(self.talents)} chosen of {t_av} available.")
        hard.extend(self.unmet_prereqs())     # a talent without its prerequisite is illegal
        return hard

    # -- serialization ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            'name': self.name, 'level': self.level, 'ancestry': self.ancestry,
            'culture': self.culture, 'path': self.path, 'singer_form': self.singer_form,
            'attributes': dict(self.attributes), 'skills': dict(self.skills),
            'path_skill': self.path_skill, 'expertises': list(self.expertises),
            'talents': list(self.talents), 'is_radiant': self.is_radiant,
            'radiant_order': self.radiant_order, 'radiant_variant': self.radiant_variant,
            'ideals_sworn': self.ideals_sworn,
            'ideal_progress': self.ideal_progress,
            'spren_name': self.spren_name, 'ideal_words': list(self.ideal_words),
            'inventory': self.inventory.to_list(), 'epic_choices': list(self.epic_choices),
            'fabrials': list(self.fabrials),
            'infected_arts': list(self.infected_arts),
            'goals': self.goals, 'purpose': self.purpose, 'obstacle': self.obstacle,
            'appearance': self.appearance, 'notes': self.notes,
            'connections': self.connections, 'occupation': self.occupation,
            'relationships': self.relationships, 'loyalties': self.loyalties,
            'personality': self.personality,
            'stat_bonuses': dict(self.stat_bonuses),
            'custom_weapons': list(self.custom_weapons),
        }

    def infected_records(self) -> list:
        """The selected Infected Art records (disease cost + granted abilities)."""
        return list(self._inf_records)

    def to_actor_doc(self) -> dict:
        """A Foundry-shaped character doc that CosmereActor renders (the bridge
        from a build to a live sheet/tracker actor). All stats are left to
        CosmereActor to compute from attributes+level (matching this engine)."""
        a = self.eff_attributes()                    # in-form attributes (Singer)
        form_focus = (self.form() or {}).get('focus', 0)
        unlocked_surges = set(self.surges_unlocked())
        surge_set = self.eff_surge_skills()
        skills = {}
        for c, attr in self.eff_skill_attr().items():     # canon + homebrew surge skills
            skills[c] = {
                'attribute': attr,
                'rank': self.skills.get(c, 0),
                # Homebrew skill bonuses ride the Foundry mod.bonus that CosmereActor adds.
                'mod': {'override': None, 'useOverride': False, 'bonus': self._hb('skill:%s' % c)},
                'unlocked': (c not in surge_set) or (c in unlocked_surges),
            }
        inv_max = ({'override': self.investiture_max(), 'useOverride': True, 'bonus': 0}
                   if self.is_radiant else {'override': None, 'useOverride': False, 'bonus': 0})
        # An Infected Art that ZEROES Focus (Chronic Pain) overrides the pool;
        # otherwise Focus is the form + additive bonuses CosmereActor sums.
        foc_max = ({'override': max(0, self._inf_set['focus']), 'useOverride': True, 'bonus': 0}
                   if 'focus' in self._inf_set
                   else {'override': None, 'useOverride': False, 'bonus': form_focus + self._hb('focus')})
        system = {
            'level': {'value': self.level},
            'tier': self.tier,
            'role': 'hero',
            'size': 'medium',
            # Attribute homebrew is already folded into `a` (eff_attributes); the
            # direct stat bonuses ride the Foundry bonus/override fields below so
            # CosmereActor reproduces the same numbers as this engine.
            'attributes': {k: {'value': a[k], 'bonus': 0} for k in ATTR_KEYS},
            'defenses': {d: {'bonus': self._hb('def:%s' % d), 'override': None, 'useOverride': False}
                         for d in ('phy', 'cog', 'spi')},
            'resources': {
                'hea': {'value': None, 'max': {'override': None, 'useOverride': False, 'bonus': self._hb('health')}},
                'foc': {'value': None, 'max': foc_max},
                'inv': {'value': None, 'max': inv_max},
            },
            'skills': skills,
            'deflect': self._deflect_block(),
            'expertises': list(self.expertises),
            'ancestry': self.ancestry, 'culture': self.culture, 'path': self.path,
            'singer_form': self.singer_form,
            'radiant_order': self.radiant_order, 'spren': self.spren_name,
            'ideals_sworn': self.ideals_sworn,
            'infected_arts': self._inf_records,  # disease cost + granted abilities, for the sheet
            'cosmere_build': self.to_dict(),     # stashed so the build can be re-edited / leveled
        }
        items = self.inventory.foundry_weapon_items()
        # Infected Art abilities are surfaced in their own grouped sheet panel
        # (driven by ``system.infected_arts`` / build.infected_records()), so they
        # are NOT also pushed into the generic action list -- that would duplicate
        # them. Their disease COSTS already apply to the stats above.
        # Equipped homebrew weapons -> Strikes (canon weapons already flow through
        # the Inventory; homebrew items are invisible to it).
        items += _homebrew.weapon_docs(self.to_dict(), self._homebrew)
        # Custom one-off weapons (e.g. imported homebrew that matches no catalog
        # id) -> Strikes carrying their explicit attack bonus.
        for w in self.custom_weapons:
            try:
                atk = int(w.get('attack') or 0)
            except (TypeError, ValueError):
                atk = 0
            items.append({'type': 'weapon', 'name': str(w.get('name') or 'Weapon')[:60],
                          'system': {'damage': {'formula': str(w.get('damage') or ''),
                                                'type': str(w.get('type') or '')},
                                     # distinct key: `attack` is a reserved schema
                                     # field (range/type); CosmereActor reads this.
                                     'custom_attack_bonus': atk}})
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
