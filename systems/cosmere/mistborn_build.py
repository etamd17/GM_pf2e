"""Package-bound Mistborn character build and deterministic metallic-art math."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import re

from .stormlight_build import (ATTR_HARD_CAP, ATTR_KEYS, CREATION_ATTR_MAX,
                               ancestry_bonus_talents, attribute_points,
                               base_talents, cosmere_max_health, expertises_total,
                               free_skill_ranks, max_skill_rank, tier_of)


HEROIC_PATHS = frozenset({
    "agent", "envoy", "hunter", "leader", "scholar", "warrior",
    "misting", "mistborn", "feruchemist", "ferring", "twinborn",
})
METALBORN_PATH_ERAS = {
    "misting": frozenset({"era-1", "era-2"}),
    "mistborn": frozenset({"era-1"}),
    "feruchemist": frozenset({"era-1"}),
    "ferring": frozenset({"era-2"}),
    "twinborn": frozenset({"era-2"}),
}
ALLOMANTIC_PATHS = frozenset({"misting", "mistborn", "twinborn"})
FERUCHEMICAL_PATHS = frozenset({"feruchemist", "ferring", "twinborn"})
METALBORN_ANCESTRIES = {
    "misting": frozenset({"human", "koloss-blooded"}),
    "mistborn": frozenset({"human"}),
    "feruchemist": frozenset({"human"}),
    "ferring": frozenset({"human", "koloss-blooded"}),
    "twinborn": frozenset({"human", "koloss-blooded"}),
}
PURE_LERASIUM_ENTITLEMENT = "mistborn.entitlement.reward.pure-lerasium"
POWER_STATES = frozenset({"nascent", "goal_active", "unlocked"})
POWER_STATE_ORDER = {"nascent": 0, "goal_active": 1, "unlocked": 2}
ORDINARY_HEROIC_PATHS = HEROIC_PATHS - frozenset(METALBORN_PATH_ERAS)
ATIUM_STARTS_UNLOCKED = "atium_starts_unlocked"
ADVANCEMENT_BONUS_KEYS = (
    "attribute_points", "skill_ranks", "talents", "expertises",
)

PATH_KEY_TO_PATH = {
    "mistborn.path.key.misting-snap": "misting",
    "mistborn.path.key.mistborn-snap": "mistborn",
    "mistborn.path.key.feruchemist-heritage": "feruchemist",
    "mistborn.path.key.ferring-heritage": "ferring",
    "mistborn.path.key.twinborn-heritage": "twinborn",
}
PATH_KEY_PREREQUISITES = {
    key: (("ancestry:human", "no_snap_or_heritage")
          if path in {"mistborn", "feruchemist"}
          else ("ancestry:human|koloss_blooded", "no_snap_or_heritage"))
    for key, path in PATH_KEY_TO_PATH.items()
}
PATH_START_SKILLS = {
    "misting": "allomancy",
    "mistborn": None,
    "feruchemist": None,
    "ferring": "feruchemy",
    "twinborn": "dis",
}
SKILL_ALIASES = {
    "agility": "agi", "athletics": "ath", "crafting": "cra",
    "deception": "dec", "deduction": "ded", "discipline": "dis",
    "heavy_weaponry": "hwp", "intimidation": "inm", "insight": "ins",
    "leadership": "lea", "light_weaponry": "lwp", "lore": "lor",
    "medicine": "med", "perception": "prc", "persuasion": "prs",
    "stealth": "stl", "survival": "sur", "thievery": "thv",
    "allomancy": "allomancy", "feruchemy": "feruchemy",
}

# These normalized source predicates describe when a selected talent can be
# activated, not whether it can be acquired. Listing them explicitly keeps the
# build validator fail-closed for genuinely unknown prerequisite syntax.
RUNTIME_ONLY_PREREQUISITES = frozenset({
    "after_rest", "ally_in_range_targeted_by_sensed_metal_projectile",
    "already_used_one_action_ability_testing_awareness_intellect_or_willpower",
    "already_used_one_action_ability_with_physical_test",
    "another_power_or_invested_ability", "at_least_two_other_powers_or_invested_abilities",
    "aware_of_blockable_invested_ability", "aware_of_reachable_invested_ability",
    "before_becoming_unconscious", "before_event_affected_by_store_or_tap_weight",
    "before_impact_or_keen_damage", "before_scene", "before_sensory_trigger",
    "braced_against_larger_object", "burning_pewter_or_tapping_strength",
    "choosing_unconscious_recovery", "currently_burning_pewter",
    "currently_burning_tin", "currently_storing_determination",
    "currently_storing_sense", "currently_storing_warmth", "currently_storing_weight",
    "currently_tapping_determination", "currently_tapping_fortune",
    "currently_tapping_mental_speed", "currently_tapping_physical_or_mental_speed",
    "currently_tapping_speed", "currently_tapping_strength", "currently_tapping_warmth",
    "currently_tapping_weight", "direct_touch_or_metal_melee_contact",
    "exhausted_would_remain_below_10", "free_hand", "gm_raises_stakes",
    "rest_with_at_least_1_hour_work", "self_afflicted_or_disoriented",
    "sensed_willing_ally_tests", "smaller_attacker_hits_self_with_melee",
    "successful_physical_skill_test_vs_target_in_reach", "target_currently_rioted",
    "target_currently_soothed", "target_focus=0", "target_is_ally",
    "target_non_enemy_npc", "target_willing_ally_or_restrained_enemy_no_more_than_one_size_larger",
    "touch_target", "unconscious_or_dead_no_more_than_1_minute",
})

BASE_METALS = frozenset({"iron", "steel", "tin", "pewter", "zinc", "brass",
                         "copper", "bronze", "gold"})
ERA_METALS = {
    "era-1": BASE_METALS | {"aluminum", "duralumin", "electrum", "atium"},
    "era-2": BASE_METALS | {"chromium", "nicrosil", "aluminum", "duralumin",
                            "cadmium", "bendalloy", "electrum"},
}

_RANK_SCALING = {
    0: {"limit": 1, "die": None, "range_ft": 10},
    1: {"limit": 1, "die": "d4", "range_ft": 20},
    2: {"limit": 2, "die": "d6", "range_ft": 40},
    3: {"limit": 3, "die": "d8", "range_ft": 80},
    4: {"limit": 4, "die": "d10", "range_ft": 160},
    5: {"limit": 5, "die": "d12", "range_ft": 320},
}


_POWER_TREE = re.compile(r"(allomancy|feruchemy)\.[a-z][a-z0-9_-]*")


def _rank(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def metallic_art_scaling(rank):
    """Return the literal Handbook rank row; ranks above six do not extrapolate."""
    rank = _rank(rank)
    if rank >= 6:
        return {"limit": rank, "die": "d20", "range_ft": 640}
    return dict(_RANK_SCALING[rank])


def allomancy_investiture_max(attributes):
    attributes = attributes if isinstance(attributes, Mapping) else {}
    awareness = attributes.get("awa", attributes.get("awareness", 0))
    presence = attributes.get("pre", attributes.get("presence", 0))
    return 2 + max(_rank(awareness), _rank(presence))


def feruchemy_charge_max(rank):
    return 2 + _rank(rank)


def _unique_strings(value):
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item.strip().lower() for item in value
                              if isinstance(item, str) and item.strip()))


def _power_map(value):
    if not isinstance(value, Mapping):
        return {}
    return {str(metal).strip().lower(): str(state).strip().lower()
            for metal, state in value.items() if str(metal).strip()}


def _metalborn_path(key, path_rules=None):
    token = str(key or "").strip().lower()
    if path_rules and token in path_rules:
        path = path_rules[token].get("path")
        return path if path in METALBORN_PATH_ERAS else None
    return PATH_KEY_TO_PATH.get(token)


def _reference_tokens(value):
    token = str(value or "").strip().lower().replace("-", "_")
    return {token, token.rsplit(".", 1)[-1]}


class MistbornBuild:
    """Normalized build choices for either supported Scadrian era."""

    def __init__(self, data=None, *, path_rules=None, talent_rules=None,
                 trusted_goal_entitlements=(),
                 trusted_advancement_bonus_grants=None):
        d = dict(data or {})
        self._input_issues = []
        self._path_rules = deepcopy(dict(path_rules or {}))
        if not self._path_rules:
            self._path_rules = {
                key: {"path": path, "eras": tuple(METALBORN_PATH_ERAS[path]),
                      "prerequisites": PATH_KEY_PREREQUISITES[key],
                      "effect_ops": ((ATIUM_STARTS_UNLOCKED,)
                                     if path in {"misting", "mistborn"} else ())}
                for key, path in PATH_KEY_TO_PATH.items()
            }
        self._catalog_enforced = talent_rules is not None
        self._talent_rules = deepcopy(dict(talent_rules or {}))
        self.name = str(d.get("name") or "New Hero")
        raw_level = d.get("level", 1)
        if type(raw_level) is not int or raw_level < 1:
            self._input_issues.append("Level must be a positive integer.")
            self.level = 1
        else:
            self.level = raw_level
        self.origin_world = "scadrial"
        self.era = str(d.get("era") or "").lower() or None
        self.ancestry = str(d.get("ancestry") or "Human")
        self.culture = str(d.get("culture") or "")
        self.starting_path = str(d.get("starting_path", d.get("path", "")) or "").lower()
        self.heroic_paths = _unique_strings(d.get("heroic_paths") or
                                             ([self.starting_path] if self.starting_path else []))
        self.metalborn_keys = _unique_strings(d.get("metalborn_keys") or [])
        legacy_metalborn = str(d.get("metalborn_path") or "").lower()
        if legacy_metalborn and not self.metalborn_keys:
            self.metalborn_keys = [f"mistborn.path.key.{legacy_metalborn}"]
        for key in self.metalborn_keys:
            path = _metalborn_path(key, self._path_rules)
            if path and path not in self.heroic_paths:
                self.heroic_paths.append(path)
        raw_attributes = d.get("attributes")
        if not isinstance(raw_attributes, Mapping):
            self._input_issues.append("Attributes must be an object of nonnegative integers.")
            raw_attributes = {}
        self.attributes = {}
        for key in ATTR_KEYS:
            value = raw_attributes.get(key, 0)
            if type(value) is not int or value < 0:
                self._input_issues.append(
                    f"Attribute {key} must be a nonnegative integer.")
                value = 0
            self.attributes[key] = value
        self.skills = {}
        raw_skills = d.get("skills") if isinstance(d.get("skills"), Mapping) else {}
        for raw_code, value in raw_skills.items():
            code = str(raw_code).strip().lower()
            if type(value) is not int or value < 0:
                self._input_issues.append(f"Skill {code or raw_code} rank must be a nonnegative integer.")
                continue
            if value > 0:
                self.skills[code] = value
        self.talents = [deepcopy(item) for item in (d.get("talents") or [])]
        self.expertises = [deepcopy(item) for item in (d.get("expertises") or [])]
        self.epic_choices = [item for item in (d.get("epic_choices") or [])
                             if item in {"skill", "talent"}]
        submitted_bonus = d.get("advancement_bonus_grants") or {}
        if not isinstance(submitted_bonus, Mapping):
            self._input_issues.append(
                "Advancement bonus grants must be a server-owned grant object.")
            submitted_bonus = {}
        normalized_submitted_bonus = {}
        for key in ADVANCEMENT_BONUS_KEYS:
            value = submitted_bonus.get(key, 0)
            if type(value) is not int or value < 0:
                self._input_issues.append(
                    f"Advancement bonus grant {key} must be a nonnegative integer.")
                value = 0
            normalized_submitted_bonus[key] = value
        authoritative_bonus = trusted_advancement_bonus_grants or {}
        if not isinstance(authoritative_bonus, Mapping):
            self._input_issues.append("Server advancement bonus grants are invalid.")
            authoritative_bonus = {}
        self.advancement_bonus_grants = {}
        for key in ADVANCEMENT_BONUS_KEYS:
            value = authoritative_bonus.get(key, 0)
            if type(value) is not int or value < 0:
                self._input_issues.append("Server advancement bonus grants are invalid.")
                value = 0
            self.advancement_bonus_grants[key] = value
        if normalized_submitted_bonus != self.advancement_bonus_grants:
            self._input_issues.append(
                "Advancement bonus grants are server-owned and cannot be supplied by a client.")
        self.allomancy_rank = self.skills.get("allomancy", 0)
        self.feruchemy_rank = self.skills.get("feruchemy", 0)
        for field, derived in (("allomancy_rank", self.allomancy_rank),
                               ("feruchemy_rank", self.feruchemy_rank)):
            if field in d and (type(d[field]) is not int or d[field] < 0
                               or d[field] != derived):
                label = field.replace("_", " ").capitalize()
                self._input_issues.append(
                    f"{label} is server-derived from the matching skill rank.")
        self.allomantic_powers = _power_map(d.get("allomantic_powers"))
        self.feruchemical_powers = _power_map(d.get("feruchemical_powers"))
        access = d.get("metal_access") if isinstance(d.get("metal_access"), Mapping) else {}
        self._submitted_metal_access = {
            "allomancy": _unique_strings(access.get("allomancy") or []),
            "feruchemy": _unique_strings(access.get("feruchemy") or []),
        }
        self.metal_access = {
            "allomancy": sorted(self.allomantic_powers),
            "feruchemy": sorted(self.feruchemical_powers),
        }
        submitted_entitlements = [value for value in _unique_strings(
            d.get("goal_entitlements") or []) if value.startswith("mistborn.entitlement.")]
        self.goal_entitlements = [value for value in _unique_strings(
            trusted_goal_entitlements or []) if value.startswith("mistborn.entitlement.")]
        if submitted_entitlements != self.goal_entitlements:
            self._input_issues.append(
                "Goal entitlements are server-owned and cannot be supplied by a client.")
        self.inventory = deepcopy(list(d.get("inventory") or []))
        self.currency = deepcopy(dict(d.get("currency") or {}))
        for field in ("goals", "purpose", "obstacle", "appearance", "notes",
                      "connections", "occupation", "relationships", "loyalties",
                      "personality"):
            setattr(self, field, deepcopy(d.get(field, "")))

    @property
    def path(self):
        return self.starting_path

    @property
    def tier(self):
        return tier_of(self.level)

    def governing_attribute(self, art):
        return {"allomancy": "wil", "feruchemy": "int"}.get(str(art).lower())

    def metalborn_paths(self):
        return tuple(path for path in (
            _metalborn_path(key, self._path_rules) for key in self.metalborn_keys)
                     if path)

    def _atium_starts_unlocked(self):
        if self.era != "era-1":
            return False
        for key in self.metalborn_keys:
            rule = self._path_rules.get(key) or {}
            eras = tuple(rule.get("eras") or ())
            if eras and self.era not in eras:
                continue
            effect_ops = rule.get("effect_ops") or ()
            if ATIUM_STARTS_UNLOCKED in effect_ops:
                return True
        return False

    def has_allomancy(self):
        return (self.allomancy_rank > 0 and
                (bool(set(self.metalborn_paths()) & ALLOMANTIC_PATHS)
                 or PURE_LERASIUM_ENTITLEMENT in self.goal_entitlements))

    def has_feruchemy(self):
        return (self.feruchemy_rank > 0 and
                bool(set(self.metalborn_paths()) & FERUCHEMICAL_PATHS))

    def accessible_metals(self, art):
        powers = self.allomantic_powers if art == "allomancy" else self.feruchemical_powers
        return tuple(sorted(powers))

    def investiture_max(self):
        return allomancy_investiture_max(self.attributes) if self.has_allomancy() else 0

    def feruchemy_charge_max(self, metal=None):
        if not self.has_feruchemy():
            return 0
        if metal is not None and str(metal).lower() not in self.accessible_metals("feruchemy"):
            return 0
        return feruchemy_charge_max(self.feruchemy_rank)

    def eff_attributes(self):
        return dict(self.attributes)

    def defenses(self):
        a = self.attributes
        return {"phy": 10 + a["str"] + a["spd"],
                "cog": 10 + a["int"] + a["wil"],
                "spi": 10 + a["awa"] + a["pre"]}

    def health_max(self):
        return cosmere_max_health(self.level, self.attributes["str"])

    def focus_max(self):
        return 2 + self.attributes["wil"]

    def deflect_value(self):
        return 0

    def derived_stats(self):
        return {"metallic_arts": {
            "allomancy": metallic_art_scaling(self.allomancy_rank),
            "feruchemy": metallic_art_scaling(self.feruchemy_rank),
        }}

    def attr_points_spent(self):
        return sum(self.attributes.values())

    def attr_points_available(self):
        return attribute_points(self.level) + self.advancement_bonus_grants["attribute_points"]

    def skill_ranks_spent(self):
        return sum(self.skills.values())

    def skill_ranks_available(self):
        key_grants = sum(
            int(path in ALLOMANTIC_PATHS) + int(path in FERUCHEMICAL_PATHS)
            for path in self.metalborn_paths())
        if self.starting_path in PATH_START_SKILLS:
            starting_grant = int(PATH_START_SKILLS[self.starting_path] is not None)
        else:
            # Every ordinary heroic path supplies one starting skill rank.
            starting_grant = int(bool(self.starting_path))
        return (free_skill_ranks(self.level, self.epic_choices.count("skill"))
                + key_grants + starting_grant
                + self.advancement_bonus_grants["skill_ranks"])

    def talents_available(self):
        return (base_talents(self.level, self.epic_choices.count("talent"))
                + ancestry_bonus_talents(self.level)
                + self.advancement_bonus_grants["talents"])

    def expertises_available(self):
        return expertises_total(self.attributes["int"]) + \
            self.advancement_bonus_grants["expertises"]

    def surges_unlocked(self):
        return ()

    def _talent_ids(self):
        return {item.get("id") for item in self.talents
                if isinstance(item, Mapping) and isinstance(item.get("id"), str)}

    def _has_talent_reference(self, reference):
        wanted = _reference_tokens(reference)
        return any(wanted & _reference_tokens(talent_id)
                   for talent_id in self._talent_ids())

    def _has_power_reference(self, reference):
        parts = str(reference or "").lower().replace("-", "_").split(".")
        if len(parts) != 2 or parts[0] not in {"allomancy", "feruchemy"}:
            return False
        powers = (self.allomantic_powers if parts[0] == "allomancy"
                  else self.feruchemical_powers)
        return powers.get(parts[1].replace("_", "-")) == "unlocked"

    def _prerequisite(self, expression):
        expression = str(expression or "").strip().lower()
        if expression in RUNTIME_ONLY_PREREQUISITES:
            return True
        if expression == "no_snap_or_heritage":
            return (len(self.metalborn_keys) <= 1
                    or PURE_LERASIUM_ENTITLEMENT in self.goal_entitlements)
        if expression == "allomantic_path_key":
            return bool(set(self.metalborn_paths()) & ALLOMANTIC_PATHS)
        if expression == "feruchemical_path_key":
            return bool(set(self.metalborn_paths()) & FERUCHEMICAL_PATHS)
        if expression == "relevant_path_key_or_metallic_splash":
            return (bool(set(self.metalborn_paths()) & ALLOMANTIC_PATHS)
                    or self._has_talent_reference("metallic_splash"))
        if expression in {"same_power_metal", "different_power_metals"}:
            allomantic = set(self.allomantic_powers)
            feruchemical = set(self.feruchemical_powers)
            same = bool(allomantic & feruchemical)
            return same if expression == "same_power_metal" else not same
        if expression.startswith("ancestry:"):
            choices = {value.replace("_", "-") for value in
                       expression.split(":", 1)[1].split("|")}
            return self.ancestry.strip().lower().replace("_", "-") in choices
        if expression.startswith("talent:"):
            return any(self._has_talent_reference(value)
                       for value in expression.split(":", 1)[1].split("|"))
        if expression.startswith("power:"):
            return any(self._has_power_reference(value)
                       for value in expression.split(":", 1)[1].split("|"))
        if expression.startswith("key:"):
            wanted = expression.split(":", 1)[1].replace("-", "_")
            return any(wanted in _reference_tokens(key) for key in self.metalborn_keys)

        import re
        options = expression.split("|")
        rank_checks = []
        for option in options:
            match = re.fullmatch(r"([a-z_]+)_rank>=(\d+)", option)
            if match:
                code = SKILL_ALIASES.get(match.group(1), match.group(1))
                rank_checks.append(self.skills.get(code, 0) >= int(match.group(2)))
                continue
            match = re.fullmatch(r"(strength|speed|intellect|willpower|awareness|presence|agility)>=(\d+)", option)
            if match:
                code = {"strength": "str", "speed": "spd", "intellect": "int",
                        "willpower": "wil", "awareness": "awa", "presence": "pre",
                        "agility": "agi"}[match.group(1)]
                value = (self.skills.get(code, 0) if code == "agi"
                         else self.attributes.get(code, 0))
                rank_checks.append(value >= int(match.group(2)))
                continue
            return None
        return any(rank_checks) if rank_checks else None

    def _record_issues(self):
        if not self._catalog_enforced:
            return []
        issues = []
        selected_ids = [item.get("id") for item in self.talents
                        if isinstance(item, Mapping)]
        if len(selected_ids) != len(self.talents) or any(
                not isinstance(talent_id, str) or not talent_id
                for talent_id in selected_ids):
            issues.append("Every selected talent needs an exact bound package record ID.")
        if len(set(selected_ids)) != len(selected_ids):
            issues.append("A talent cannot be selected more than once.")
        for talent_id in selected_ids:
            rule = self._talent_rules.get(talent_id) or self._path_rules.get(talent_id)
            if rule is None:
                issues.append(f"Talent {talent_id} is not available in the bound packages.")
                continue
            unsupported = []
            unmet = []
            for prerequisite in rule.get("prerequisites", ()):
                state = self._prerequisite(prerequisite)
                if state is None:
                    unsupported.append(prerequisite)
                elif not state:
                    unmet.append(prerequisite)
            if unsupported:
                issues.append(f"Talent {talent_id} has an unsupported prerequisite: "
                              + ", ".join(unsupported) + ".")
            elif unmet:
                issues.append(f"Talent {talent_id} prerequisites are not satisfied.")
            # Each metal's talents form that power's tree: most chain from a root
            # talent that requires the power, but a few list only a runtime
            # condition. A talent whose every tree is a power tree therefore needs
            # one of those powers, with the same meaning as a ``power:`` prerequisite.
            power_trees = [str(tree).strip().lower()
                           for tree in rule.get("tree_memberships") or ()]
            if power_trees and all(_POWER_TREE.fullmatch(tree) for tree in power_trees) \
                    and not any(self._has_power_reference(tree) for tree in power_trees):
                issues.append(f"Talent {talent_id} belongs to the talent tree of a power "
                              "this character does not have unlocked.")
        return issues

    def _path_issues(self):
        issues = []
        path_names = {rule.get("path") for rule in self._path_rules.values()}
        if not self.starting_path:
            issues.append("Choose exactly one immutable starting path.")
        elif self.starting_path not in HEROIC_PATHS and self.starting_path not in path_names:
            issues.append("Unknown starting path.")
        elif self.starting_path not in ORDINARY_HEROIC_PATHS:
            issues.append("The immutable starting path must be the character's ordinary heroic path.")
        if self.starting_path and self.starting_path not in self.heroic_paths:
            issues.append("The starting path must remain among the character's heroic paths.")
        unknown = [path for path in self.heroic_paths
                   if path not in HEROIC_PATHS and path not in path_names]
        if unknown:
            issues.append("Unknown heroic path: " + ", ".join(sorted(unknown)) + ".")
        ordinary_paths = set(self.heroic_paths) & ORDINARY_HEROIC_PATHS
        if len(ordinary_paths) != 1:
            issues.append("Choose exactly one ordinary heroic path.")
        paths = self.metalborn_paths()
        if len(paths) != len(self.metalborn_keys):
            issues.append("Unknown Metalborn path key.")
        if len(paths) > 1:
            issues.append("A character may have only one Metalborn path key.")
        ancestry = self.ancestry.strip().lower().replace("_", "-")
        for path in self.heroic_paths:
            if path not in METALBORN_PATH_ERAS:
                continue
            if self.era not in METALBORN_PATH_ERAS[path]:
                issues.append(f"The {path} Metalborn path is not available in this era.")
            if ancestry not in METALBORN_ANCESTRIES[path]:
                issues.append(f"The {path} Metalborn path ancestry prerequisite is not satisfied.")
            if path not in paths:
                issues.append(f"The {path} Metalborn path requires its exact bound path key.")
        for key in self.metalborn_keys:
            rule = self._path_rules.get(key)
            if rule is None:
                continue
            unsupported = []
            unmet = []
            for prerequisite in rule.get("prerequisites", ()):
                state = self._prerequisite(prerequisite)
                if state is None:
                    unsupported.append(prerequisite)
                elif not state:
                    unmet.append(prerequisite)
            if unsupported:
                issues.append(f"Metalborn path key {key} has an unsupported prerequisite: "
                              + ", ".join(unsupported) + ".")
            elif unmet:
                issues.append(f"Metalborn path key {key} prerequisites are not satisfied.")
        if "hemalurgy" in self.heroic_paths:
            issues.append("Hemalurgy is reward content, not a selectable heroic path.")
        return issues

    def _power_issues(self):
        issues = []
        valid_metals = ERA_METALS.get(self.era, frozenset())
        atium_starts_unlocked = self._atium_starts_unlocked()
        for art, powers in (("Allomancy", self.allomantic_powers),
                            ("Feruchemy", self.feruchemical_powers)):
            for metal, state in powers.items():
                if state not in POWER_STATES:
                    issues.append(f"{art} power {metal} has an invalid unlock state.")
                if metal not in valid_metals:
                    issues.append(f"{art} metal {metal} is not available in this era.")
                entitlement = f"mistborn.entitlement.power.{art.lower()}.{metal}.unlock"
                if state == "unlocked" and not (
                        art == "Allomancy" and metal == "atium"
                        and atium_starts_unlocked) and (
                        entitlement not in self.goal_entitlements):
                    issues.append(f"{art} power {metal} is unlocked without its server goal entitlement.")
        if ("atium" in self.allomantic_powers and atium_starts_unlocked
                and self.allomantic_powers["atium"] != "unlocked"):
            issues.append("Allomantic atium starts unlocked for this bound path grant.")

        paths = set(self.metalborn_paths())
        allomantic_count = len(self.allomantic_powers)
        feruchemical_count = len(self.feruchemical_powers)
        if "mistborn" in paths:
            if set(self.allomantic_powers) != set(valid_metals):
                issues.append("The Mistborn path grants every era-valid Allomantic power.")
        elif paths & {"misting", "twinborn"}:
            if allomantic_count != 1:
                issues.append("This Metalborn path grants exactly one Allomantic power.")
        elif allomantic_count:
            issues.append("Allomantic powers require an exact Allomantic path grant.")

        if "feruchemist" in paths:
            if set(self.feruchemical_powers) != set(valid_metals):
                issues.append("The Feruchemist path grants every era-valid Feruchemical power.")
        elif paths & {"ferring", "twinborn"}:
            if feruchemical_count != 1:
                issues.append("This Metalborn path grants exactly one Feruchemical power.")
        elif feruchemical_count:
            issues.append("Feruchemical powers require an exact Feruchemical path grant.")

        for art in ("allomancy", "feruchemy"):
            if self._submitted_metal_access[art] != self.metal_access[art]:
                issues.append(
                    f"{art.title()} metal access is server-derived from path-granted powers.")

        required_allomancy = sum(path in ALLOMANTIC_PATHS for path in paths)
        required_feruchemy = sum(path in FERUCHEMICAL_PATHS for path in paths)
        if self.starting_path == "misting":
            required_allomancy += 1
        if self.starting_path == "ferring":
            required_feruchemy += 1
        if self.allomancy_rank < required_allomancy:
            issues.append("The selected path grants required Allomancy skill ranks.")
        if self.feruchemy_rank < required_feruchemy:
            issues.append("The selected path grants required Feruchemy skill ranks.")
        if self.allomancy_rank and not self.has_allomancy():
            issues.append("Allomancy rank requires an Allomantic Metalborn path or reward.")
        if self.feruchemy_rank and not self.has_feruchemy():
            issues.append("Feruchemy rank requires a Feruchemical Metalborn path.")
        return issues

    def hard_violations(self):
        issues = list(self._input_issues) + self._path_issues() + self._power_issues()
        issues += self._record_issues()
        if self.attr_points_spent() > self.attr_points_available():
            issues.append("Attributes exceed the available advancement budget.")
        if self.level == 1 and any(value > CREATION_ATTR_MAX for value in self.attributes.values()):
            issues.append(f"Attributes: max {CREATION_ATTR_MAX} per attribute at character creation.")
        if any(value > ATTR_HARD_CAP for value in self.attributes.values()):
            issues.append(f"Attributes: hard cap is {ATTR_HARD_CAP}.")
        if any(value > max_skill_rank(self.level) for value in self.skills.values()):
            issues.append("A skill exceeds the tier rank limit.")
        if self.skill_ranks_spent() > self.skill_ranks_available():
            issues.append("Skills exceed the available advancement budget.")
        if len(self.talents) > self.talents_available():
            issues.append("Talents exceed the available advancement budget.")
        if len(self.expertises) > self.expertises_available():
            issues.append("Expertises exceed the available advancement budget.")
        return issues

    def validate(self):
        issues = self.hard_violations()
        if self.attr_points_spent() != self.attr_points_available():
            issues.append(f"Attributes: {self.attr_points_spent()} of "
                          f"{self.attr_points_available()} points spent.")
        return list(dict.fromkeys(issues))

    def validate_transition(self, existing, transition=None):
        issues = []
        if existing is not None:
            if self.starting_path != existing.starting_path:
                issues.append("The immutable starting path cannot change.")
            if self.origin_world != existing.origin_world or self.era != existing.era:
                issues.append("A character's rules world and era cannot change through advancement.")
            if not set(existing.heroic_paths) <= set(self.heroic_paths):
                issues.append("Advancement cannot remove an acquired heroic path.")
            new_ordinary_paths = ((set(self.heroic_paths) - set(existing.heroic_paths))
                                  & ORDINARY_HEROIC_PATHS)
            if new_ordinary_paths:
                issues.append(
                    "Advancement cannot add another ordinary heroic path by name.")
            if not set(existing.metalborn_keys) <= set(self.metalborn_keys):
                issues.append("Advancement cannot replace an acquired Metalborn path key.")
            entitlements = (set(existing.goal_entitlements)
                            | set((transition or {}).get("goal_entitlements") or ()))
            new_keys = set(self.metalborn_keys) - set(existing.metalborn_keys)
            acquired_key_talents = self._talent_ids() - existing._talent_ids()
            for key in sorted(new_keys):
                lerasium_grants_key = (
                    key == "mistborn.path.key.mistborn-snap"
                    and PURE_LERASIUM_ENTITLEMENT in entitlements
                )
                if key not in acquired_key_talents and not lerasium_grants_key:
                    issues.append(
                        f"Metalborn path key {key} requires its exact newly acquired path key talent or server reward entitlement.")
            if existing.metalborn_keys and new_keys and (
                    PURE_LERASIUM_ENTITLEMENT not in entitlements):
                issues.append("A second Metalborn path key requires its server reward entitlement.")
            for art, before, after in (
                    ("Allomantic", existing.allomantic_powers, self.allomantic_powers),
                    ("Feruchemical", existing.feruchemical_powers, self.feruchemical_powers)):
                if not set(before) <= set(after):
                    issues.append(f"Advancement cannot remove an acquired {art} power.")
            for art, before, after in (
                    ("allomancy", existing.allomantic_powers, self.allomantic_powers),
                    ("feruchemy", existing.feruchemical_powers, self.feruchemical_powers)):
                for metal, before_state in before.items():
                    after_state = after.get(metal)
                    if (before_state in POWER_STATE_ORDER
                            and after_state in POWER_STATE_ORDER
                            and POWER_STATE_ORDER[after_state]
                            < POWER_STATE_ORDER[before_state]):
                        issues.append(
                            f"Advancement cannot downgrade {art} {metal} from {before_state} to {after_state}.")
                for metal, state in after.items():
                    if state == "unlocked" and before.get(metal) != "unlocked":
                        required = f"mistborn.entitlement.power.{art}.{metal}.unlock"
                        if required not in entitlements:
                            issues.append(f"Unlocking {art} {metal} requires its completed goal entitlement.")
        return issues

    def to_dict(self):
        return {
            "name": self.name, "level": self.level, "origin_world": self.origin_world,
            "era": self.era, "ancestry": self.ancestry, "culture": self.culture,
            "starting_path": self.starting_path, "path": self.starting_path,
            "heroic_paths": list(self.heroic_paths), "metalborn_keys": list(self.metalborn_keys),
            "attributes": dict(self.attributes), "skills": dict(self.skills),
            "talents": deepcopy(self.talents), "expertises": deepcopy(self.expertises),
            "epic_choices": list(self.epic_choices),
            "advancement_bonus_grants": dict(self.advancement_bonus_grants),
            "allomancy_rank": self.allomancy_rank, "feruchemy_rank": self.feruchemy_rank,
            "allomantic_powers": dict(self.allomantic_powers),
            "feruchemical_powers": dict(self.feruchemical_powers),
            "metal_access": deepcopy(self.metal_access),
            "goal_entitlements": list(self.goal_entitlements),
            "inventory": deepcopy(self.inventory), "currency": deepcopy(self.currency),
            **{field: deepcopy(getattr(self, field)) for field in (
                "goals", "purpose", "obstacle", "appearance", "notes", "connections",
                "occupation", "relationships", "loyalties", "personality")},
        }

    def to_actor_doc(self):
        return {"name": self.name, "type": "character", "system": {
            "level": {"value": self.level}, "tier": self.tier, "role": "hero",
            "attributes": {key: {"value": value, "bonus": 0}
                           for key, value in self.attributes.items()},
            "defenses": {key: {"override": value, "useOverride": True, "bonus": 0}
                         for key, value in self.defenses().items()},
            "resources": {
                "hea": {"value": None, "max": {"override": self.health_max(),
                                                   "useOverride": True, "bonus": 0}},
                "foc": {"value": None, "max": {"override": self.focus_max(),
                                                   "useOverride": True, "bonus": 0}},
                "inv": {"value": None, "max": {"override": self.investiture_max(),
                                                   "useOverride": True, "bonus": 0}},
            },
            "deflect": {"override": 0, "useOverride": False, "bonus": 0,
                        "types": {}},
            "skills": {code: {"rank": rank, "attribute": "wil" if code == "allomancy"
                               else "int" if code == "feruchemy" else None}
                       for code, rank in self.skills.items()},
            "expertises": deepcopy(self.expertises), "ancestry": self.ancestry,
            "culture": self.culture, "path": self.starting_path,
            "origin_world": self.origin_world, "era": self.era,
            "cosmere_build": self.to_dict(),
        }, "items": []}
