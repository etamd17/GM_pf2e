"""CosmereActor — the Cosmere RPG sibling of the PF2e ``Character``.

Reads a Foundry ``cosmere-rpg`` actor stat block (as ingested from the official
system packs, see ``tools/ingest_cosmere.py``) and derives the rulebook stats:
the three defenses, health / focus / investiture, deflect, and the skill mods.
It is self-contained (no dependency on the host Flask app), so the Cosmere
system binds it as its own actor factory at registration time.

Stat block shape (verified against cosmere-rpg v2.0.5 + the Stormlight core
rulebook, Ch.1/3/9/10):
  attributes: str/spd/int/wil/awa/pre, each {value, bonus}
  defenses:   phy/cog/spi, each {bonus, derived, override, useOverride}
  resources:  hea (health) / foc (focus) / inv (investiture),
              each {value, bonus, max:{derived, override, useOverride, bonus}}
  skills:     28 keys (18 basic + 10 Surge), each {attribute, rank, mod, unlocked}
  deflect:    {natural, bonus, override, useOverride, types:{impact,keen,energy,
              spirit,vital,heal -> bool}}

Where Foundry stores a value as a manual ``override`` (adversaries do this for
health/focus/deflect), we honor it; otherwise we compute from the rulebook.
"""
from __future__ import annotations

import re

# Defense = 10 + the two governing attributes (rulebook Ch.3).
_DEFENSE_ATTRS = {
    'phy': ('str', 'spd'),   # Physical
    'cog': ('int', 'wil'),   # Cognitive
    'spi': ('awa', 'pre'),   # Spiritual
}

# Per-level health gain by tier, and the levels at which STR is re-added (the
# first level of tiers 1-4). Rulebook Ch.1 advancement.
_TIER_HEALTH_GAIN = {1: 5, 2: 4, 3: 3, 4: 2, 5: 1}
_STR_READD_LEVELS = (6, 11, 16)   # L1 is folded into the base below


def tier_of(level: int) -> int:
    """Cosmere tier (1-5) for a character level (T1=1-5 ... T5=21+)."""
    return min(5, (max(1, int(level)) - 1) // 5 + 1)


def cosmere_max_health(level: int, strength: int) -> int:
    """Maximum Health for a character of the given level and STR (Ch.1).

    L1 = 10 + STR; each subsequent level adds its tier's gain (+5/+4/+3/+2/+1);
    STR is re-added at the first level of tiers 2-4 (L6/L11/L16). Adversaries
    don't use this — they carry an explicit ``max.override``.
    """
    level = max(1, int(level))
    strength = int(strength)
    total = 10 + strength                      # L1 (tier-1 STR folded in)
    for lv in range(2, level + 1):
        total += _TIER_HEALTH_GAIN[tier_of(lv)]
        if lv in _STR_READD_LEVELS:
            total += strength
    return total


def _i(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    """Plain text from a Foundry description (a ``{value: html}`` dict or str)."""
    if isinstance(value, dict):
        value = value.get('value', '')
    if not isinstance(value, str):
        return ''
    return re.sub(r'<[^>]+>', '', value).strip()


class CosmereActor:
    """A loaded Cosmere actor (adversary or, later, a player character)."""

    system = 'cosmere'

    def __init__(self, data, file_path: str = ''):
        self.file_path = file_path
        if not isinstance(data, dict):
            data = {}
        self._raw = data

        # The Foundry document may be the doc itself (an adversary) or nested
        # under `system_data` (a wrapped player-character envelope).
        doc = data
        if isinstance(data.get('system_data'), dict):
            doc = data['system_data']
        sys = doc.get('system', {}) if isinstance(doc, dict) else {}
        if not isinstance(sys, dict):
            sys = {}
        self._sys = sys

        self.name = (doc.get('name') if isinstance(doc, dict) else None) or data.get('name') or 'Unknown'
        self.type = (doc.get('type') if isinstance(doc, dict) else None) or 'character'
        self.is_pc = self.type == 'character'
        self.tier = sys.get('tier')
        self.role = sys.get('role')
        self.size = sys.get('size', 'medium')
        lvl = sys.get('level')
        self.level = _i(lvl.get('value'), 1) if isinstance(lvl, dict) else _i(lvl, 1)

        # --- attributes (6) ---
        self.attributes = {k: self._attr(k) for k in ('str', 'spd', 'int', 'wil', 'awa', 'pre')}

        # --- defenses (3): override, else 10 + governing pair (+bonus) ---
        self.defenses = {}
        dfn = sys.get('defenses', {})
        for key, (a, b) in _DEFENSE_ATTRS.items():
            node = dfn.get(key, {}) if isinstance(dfn, dict) else {}
            self.defenses[key] = self._resolve(
                node, 10 + self._attr(a) + self._attr(b)
            )

        # --- resources: health / focus / investiture ---
        res = sys.get('resources', {})
        self.is_radiant = bool(self._max_node(res, 'inv').get('value')) or \
            self._max_node(res, 'inv').get('override') not in (None, 0)
        self.health_max = self._resource_max(
            res, 'hea', cosmere_max_health(self.level, self._attr('str'))
        )
        self.health = _i(res.get('hea', {}).get('value'), self.health_max) if isinstance(res.get('hea'), dict) else self.health_max
        self.focus_max = self._resource_max(res, 'foc', 2 + self._attr('wil'))
        inv_default = (2 + max(self._attr('awa'), self._attr('pre'))) if self.is_radiant else 0
        self.investiture_max = self._resource_max(res, 'inv', inv_default)

        # --- deflect ---
        dfl = sys.get('deflect', {}) if isinstance(sys.get('deflect'), dict) else {}
        deflect_default = _i(dfl.get('natural')) + _i(dfl.get('bonus'))
        self.deflect = {
            'value': self._resolve(dfl, deflect_default),
            'types': dict(dfl.get('types', {})) if isinstance(dfl.get('types'), dict) else {},
        }

        # --- skills: mod = rank + governing attribute (+bonus), unless overridden ---
        self.skills = {}
        for key, node in (sys.get('skills', {}) or {}).items():
            if not isinstance(node, dict):
                continue
            rank = _i(node.get('rank'))
            attr = node.get('attribute')
            computed = rank + self._attr(attr)
            self.skills[key] = {
                'rank': rank,
                'attribute': attr,
                'mod': self._resolve(node.get('mod', {}), computed),
                'unlocked': bool(node.get('unlocked', True)),
            }

        # --- abilities from embedded items (adversary actions/weapons/traits) ---
        self.actions, self.strikes, self.traits = [], [], []
        items = doc.get('items', []) if isinstance(doc, dict) else []
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            itype, iname = it.get('type'), it.get('name', '')
            isys = it.get('system', {}) if isinstance(it.get('system'), dict) else {}
            if itype == 'action':
                self.actions.append({'name': iname, 'description': _text(isys.get('description'))})
            elif itype == 'weapon':
                dmg = isys.get('damage', {}) if isinstance(isys.get('damage'), dict) else {}
                skill = dmg.get('skill')
                # attack mod = the weapon-skill's mod (hwp/lwp); damage = dice + that mod on a hit.
                # `bonus` mirrors `mod` so a Cosmere Strike flows through the same
                # tracker/stat serializers as a PF2e one (which read `bonus`). Without
                # it, adding a weapon-bearing Cosmere adversary 500'd tracker_state.
                _smod = self.skills.get(skill, {}).get('mod', 0) if skill else 0
                self.strikes.append({'name': iname, 'damage': dmg.get('formula', ''), 'type': dmg.get('type', ''),
                                     'skill': skill, 'mod': _smod, 'bonus': _smod})
            elif itype == 'trait':
                self.traits.append(iname)

        # --- mutable combat state (used when this actor is a tracker combatant) ---
        self.instance_id = ''
        self.initiative = 0
        self.is_hazard = False
        self.visible_to_players = True
        self.delaying = False
        self.elite_weak = 0
        self.reaction_used = False
        self.actions_used = 0
        self.injuries = 0             # Cosmere death-spiral counter (Ch.9)
        self.speed_choice = 'slow'    # Cosmere: elect fast(2 actions, early) / slow(3, late) each round
        self.max_actions = 3          # reflects speed_choice (slow=3 / fast=2)
        self.conditions = {}          # Cosmere conditions {name: value|bool}
        self.condition_expiry = {}
        # Health drives the tracker HP bar. Expose PF2e-shaped aliases so the
        # existing tracker serializers/templates run UNMODIFIED; the true Cosmere
        # stats live in tracker_block()/to_summary() and the UI branches on
        # `system`. These aliases are non-crash fallbacks, not the source of truth.
        self.current_hp = self.health
        self.hp = self.health_max
        self.max_hp = self.health_max
        self.current_focus = self.focus_max
        self.speed = 25
        self.hero_points = 0
        self.persistent_damage = [] if self.is_pc else ''
        self.ac = self.defenses.get('phy', 10)
        self.base_ac = self.ac
        self.fort = self.defenses.get('phy', 10)
        self.ref = self.defenses.get('cog', 10)
        self.will = self.defenses.get('spi', 10)
        self.perception = self.skills.get('prc', {}).get('mod', 0)
        # Empty PF2e-shaped collections so both tracker render branches are safe.
        self.feats = []
        self.spell_casters = []
        self.spell_attack = 0
        self.spell_dc = 0
        self.immunities = []
        self.resistances = []
        self.weaknesses = []
        self.attacks = []

    # -- helpers ------------------------------------------------------------
    def _attr(self, key) -> int:
        a = self._sys.get('attributes', {}).get(key, {}) if key else {}
        if not isinstance(a, dict):
            return 0
        return _i(a.get('value')) + _i(a.get('bonus'))

    @staticmethod
    def _resolve(node, computed) -> int:
        """A manual override wins; otherwise the computed value (+ a flat bonus)."""
        if not isinstance(node, dict):
            return _i(computed)
        if node.get('useOverride') and node.get('override') is not None:
            return _i(node.get('override'))
        return _i(computed) + _i(node.get('bonus'))

    @staticmethod
    def _max_node(res, key) -> dict:
        node = res.get(key, {}) if isinstance(res, dict) else {}
        mx = node.get('max', {}) if isinstance(node, dict) else {}
        return mx if isinstance(mx, dict) else {}

    def _resource_max(self, res, key, computed) -> int:
        return self._resolve(self._max_node(res, key), computed)

    # -- serialization ------------------------------------------------------
    def tracker_block(self) -> dict:
        """Compact Cosmere stats for the system-aware combat tracker."""
        types = self.deflect.get('types') or {}
        return {
            'defenses': dict(self.defenses),
            'deflect': {
                'value': self.deflect.get('value', 0),
                'types': [t for t, on in types.items() if on],
            },
            'health': {'value': self.current_hp, 'max': self.health_max},
            'focus_max': self.focus_max,
            'investiture_max': self.investiture_max,
            'attributes': dict(self.attributes),
            'injuries': int(getattr(self, 'injuries', 0) or 0),
            'speed_choice': getattr(self, 'speed_choice', 'slow'),
            'phase': '%s_%s' % ('fast' if getattr(self, 'speed_choice', 'slow') == 'fast' else 'slow',
                                'pc' if self.is_pc else 'npc'),
            'tier': self.tier, 'role': self.role, 'size': self.size,
        }

    def to_summary(self) -> dict:
        """A flat, JSON-friendly snapshot for sheets / tests / the tracker."""
        return {
            'system': self.system,
            'name': self.name,
            'type': self.type,
            'tier': self.tier,
            'role': self.role,
            'size': self.size,
            'level': self.level,
            'attributes': dict(self.attributes),
            'defenses': dict(self.defenses),
            'health': {'value': self.health, 'max': self.health_max},
            'focus_max': self.focus_max,
            'investiture_max': self.investiture_max,
            'deflect': dict(self.deflect),
            'skills': {k: dict(v) for k, v in self.skills.items()},
        }
