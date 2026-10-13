"""Mistborn Era 1/Era 2 rules profile behind the shared Cosmere boundary."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

from .mistborn_build import MistbornBuild
from .rules.schema import RulesValidationError, require, to_dict


def _mapping(value):
    return dict(value) if isinstance(value, Mapping) else {}


def normalize_mistborn_runtime_state(state, target_id, build, prior_build=None):
    """Normalize only resources accessible to the current immutable build.

    ``prior_build`` is intentionally accepted even when a particular transition
    needs only current access.  That context prevents future package rules from
    guessing whether a missing pool is new, inaccessible, or legacy data.
    """
    from .effects import normalize_runtime_state

    require(isinstance(target_id, str) and bool(target_id),
            "Runtime effect normalization requires an actor identity.")
    build = build if isinstance(build, MistbornBuild) else MistbornBuild(build)
    if prior_build is not None and not isinstance(prior_build, MistbornBuild):
        prior_build = MistbornBuild(prior_build)
    result = normalize_runtime_state(deepcopy(_mapping(state)), target_id)
    from .mistborn_runtime import normalize_mistborn_runtime

    raw_mistborn = _mapping(result.get("mistborn"))
    raw_allomancy = _mapping(raw_mistborn.get("allomancy"))
    # One pre-profile prototype used this name.  Canonical runtime follows the
    # Handbook term and the shared Mistborn service's ``depleted_metals`` key.
    if "depleted_metals" not in raw_allomancy and "depleted_power_ids" in raw_allomancy:
        raw_allomancy["depleted_metals"] = raw_allomancy.pop("depleted_power_ids")
    raw_mistborn["allomancy"] = raw_allomancy

    accessible_allomancy = (build.accessible_metals("allomancy")
                            if build.has_allomancy() else ())
    accessible_feruchemy = (build.accessible_metals("feruchemy")
                            if build.has_feruchemy() else ())
    prior_allomancy = (prior_build.accessible_metals("allomancy")
                       if prior_build is not None and prior_build.has_allomancy() else ())
    prior_feruchemy = (prior_build.accessible_metals("feruchemy")
                       if prior_build is not None and prior_build.has_feruchemy() else ())
    attributes = {
        "awareness": build.attributes["awa"],
        "presence": build.attributes["pre"],
    }
    normalized = normalize_mistborn_runtime(
        raw_mistborn, attributes=attributes,
        allomancy_rank=build.allomancy_rank, feruchemy_rank=build.feruchemy_rank,
        era=build.era, accessible_allomantic_metals=accessible_allomancy,
        accessible_feruchemical_metals=accessible_feruchemy,
        prior_accessible_allomantic_metals=prior_allomancy,
        prior_accessible_feruchemical_metals=prior_feruchemy)
    if not build.has_allomancy():
        normalized["allomancy"].update(current_investiture=0, max_investiture=0,
                                        vial_inventory={}, rare_metal_access={})
    result["mistborn"] = normalized
    return result


class MistbornRulesProfile:
    """Mechanical profile for an exact Scadrial era scope."""

    def __init__(self, binding, registry, *, homebrew=None, spoiler_scope="player",
                 scope=None):
        from .rules.profiles import ReferenceRulesProfile
        self._reference = ReferenceRulesProfile(
            binding, registry, homebrew=homebrew, spoiler_scope=spoiler_scope,
            scope=scope)
        self.binding = binding
        self.registry = registry
        self.packages = self._reference.packages
        self.homebrew = homebrew or {}
        self.spoiler_scope = spoiler_scope
        self.scope = scope
        self._path_rules = {}
        self._talent_rules = {}
        for pack in self.packages:
            for record in pack.records:
                if (record.eras and scope is not None
                        and scope.era not in record.eras):
                    continue
                parameters = dict(record.parameters)
                mechanics = _mapping(parameters.get("data")) or parameters
                rule = {**to_dict(mechanics), "id": record.id,
                        "eras": tuple(record.eras),
                        "prerequisites": tuple(record.prerequisites)}
                if record.kind == "path":
                    path_id = mechanics.get("path") or mechanics.get("path_id")
                    if isinstance(path_id, str):
                        self._path_rules[record.id] = {
                            **rule, "path": path_id.lower()}
                elif record.kind == "talent":
                    self._talent_rules[record.id] = rule

    def catalog(self):
        result = self._reference.catalog()
        result.update(automation={"build": "calculated", "runtime": "tracked",
                                  "encounter_budget": "calculated"},
                      eligible=True, blocked_reasons=[])
        return result

    def create_build(self, document, *, trusted_goal_entitlements=(),
                     trusted_advancement_bonus_grants=None):
        data = dict(document or {})
        if self.scope is not None:
            require(data.get("origin_world", self.scope.world) == self.scope.world,
                    "Mistborn build world does not match its actor rules scope.")
            require(data.get("era", self.scope.era) == self.scope.era,
                    "Mistborn build era does not match its actor rules scope.")
            data.setdefault("origin_world", self.scope.world)
            data.setdefault("era", self.scope.era)
        return MistbornBuild(
            data, path_rules=self._path_rules, talent_rules=self._talent_rules,
            trusted_goal_entitlements=trusted_goal_entitlements,
            trusted_advancement_bonus_grants=trusted_advancement_bonus_grants)

    def builder_context(self, build, homebrew=None):
        build = build if isinstance(build, MistbornBuild) else self.create_build(build)
        return {"build": build.to_dict(), "catalog": self.catalog(),
                "paths": sorted(self._path_rules), "era": build.era}

    def level_grants(self, level, build=None):
        from .stormlight_build import level_grants
        attributes = getattr(build, "attributes", {}) if build is not None else {}
        return level_grants(level, attributes.get("str", 0))

    def validate_build(self, choices, existing=None, transition=None):
        build = choices if isinstance(choices, MistbornBuild) else self.create_build(choices)
        prior = (existing if isinstance(existing, MistbornBuild)
                 else self.create_build(existing) if isinstance(existing, Mapping) else None)
        return build.validate() + build.validate_transition(prior, transition)

    def project_actor(self, document, runtime_state=None):
        build = document if isinstance(document, MistbornBuild) else self.create_build(document)
        actor = build.to_actor_doc()
        if isinstance(runtime_state, Mapping):
            from .mistborn_runtime import project_active_effect_modifiers
            actor["active_effect_modifiers"] = project_active_effect_modifiers(
                runtime_state.get("effects") or [])
        return actor

    def normalize_runtime_state(self, state, target_id=None, *, build=None,
                                prior_build=None):
        require(build is not None, "Mistborn runtime normalization requires build context.")
        return normalize_mistborn_runtime_state(state, target_id, build, prior_build)

    def apply_action(self, state, action):
        from .mistborn_runtime import (apply_allomancy_operation,
                                       apply_feruchemy_operation)
        require(isinstance(state, Mapping) and isinstance(action, Mapping),
                "Invalid Mistborn action state.")
        result = deepcopy(dict(state))
        container = deepcopy(_mapping(result.get("mistborn")))
        art = action.get("art")
        operation = action.get("operation")
        require(isinstance(operation, Mapping),
                "Mistborn action requires a normalized operation.")
        if art == "allomancy":
            container[art] = apply_allomancy_operation(
                _mapping(container.get(art)), operation)
        elif art == "feruchemy":
            container[art] = apply_feruchemy_operation(
                _mapping(container.get(art)), operation)
        else:
            raise RulesValidationError("Unsupported Mistborn profile action.")
        result["mistborn"] = container
        return result

    def encounter_budget(self, actors, adversaries):
        from .mistborn_encounters import encounter_budget_for_party
        return encounter_budget_for_party(actors, adversaries)

    def tracker_block(self, actor):
        from .mistborn_runtime import project_active_effect_modifiers
        system = actor.get("system", actor) if isinstance(actor, Mapping) else {}
        return {"origin_world": "scadrial", "era": self.scope.era if self.scope else None,
                "resources": deepcopy(system.get("resources", {})),
                "mistborn": deepcopy(system.get("mistborn", {})),
                "active_effect_modifiers": project_active_effect_modifiers(
                    system.get("effects") or [])}
