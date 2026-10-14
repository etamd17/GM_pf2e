"""Canonical, package-pinned Cosmere character document boundary.

The browser-facing builder remains permissive. This module only normalizes
storage and concurrency authority; exact level transitions live in
``advancement``. ``runtime_state`` is canonical, while ``play_state`` is a
deliberate compatibility projection for the existing sheets and tracker.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from collections import Counter
from copy import deepcopy
import re

from .rules.profiles import (default_registry, normalize_binding, normalize_theme,
                             resolve_profile)
from .rules.schema import (AdvancementEvent, CosmereRulesBinding,
                           RulesValidationError, from_dict, require, to_dict)


CHARACTER_SCHEMA_VERSION = 2
_MISTBORN_ENTITLEMENT = re.compile(
    r"mistborn\.entitlement\.(?:reward\.pure-lerasium|power\.(?:allomancy|feruchemy)\.[a-z0-9-]+\.unlock)\Z")
_ADVANCEMENT_BONUS_KEYS = (
    "attribute_points", "skill_ranks", "talents", "expertises",
)


class CharacterRevisionError(RulesValidationError):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code = code
        self.status = status


def _nonnegative_integer(value, label):
    require(type(value) is int and value >= 0, f"invalid {label}")
    return value


def _actor_id(document, actor_id):
    value = actor_id or document.get("id")
    require(isinstance(value, str) and bool(value), "missing character identity")
    return value


def _binding(document, build, expected, *, registry=None):
    registry = registry or default_registry()
    expected_binding = None
    if expected is not None:
        expected_binding = (expected if isinstance(expected, CosmereRulesBinding)
                            else from_dict(CosmereRulesBinding, expected))
        expected_binding = normalize_binding({"rules_binding": to_dict(expected_binding)},
                                             registry=registry)
    source = document if "rules_binding" in document else build
    if "rules_binding" not in source and expected_binding is not None:
        source = {"rules_binding": to_dict(expected_binding)}
    actual = normalize_binding(source, registry=registry)
    require(expected_binding is None or actual == expected_binding,
            "character rules binding does not match the campaign binding")
    return actual


def binding_hash(binding):
    """Stable audit digest for the exact core and enabled package pins."""
    from hashlib import sha256
    from .rules.compiler import canonical_json
    return sha256(canonical_json(to_dict(binding))).hexdigest()


def _scope(binding, document, build=None):
    build = build if isinstance(build, Mapping) else {}
    allowed_worlds = set(binding.allowed_worlds)
    document_origin = document.get("origin_world")
    build_origin = build.get("origin_world")
    if document_origin and build_origin:
        require(document_origin == build_origin,
                "character and build origin worlds do not match")
    origin = document_origin or build_origin or binding.default_world
    require(origin in allowed_worlds, "origin world is not enabled by the rules binding")
    document_era = document.get("era")
    build_era = build.get("era")
    if document_era and build_era:
        require(document_era == build_era, "character and build eras do not match")
    era = document_era or build_era
    if era in (None, ""):
        era = None
    if origin == "scadrial":
        if era is None and len(binding.allowed_eras) == 1:
            era = binding.allowed_eras[0]
        require(isinstance(era, str) and era in binding.allowed_eras,
                "character era is not allowed by the rules binding")
    else:
        require(era is None, "character era requires a Scadrial origin world")
    return origin, era


def _derived(build, binding):
    result = {"level": int(build.level), "tier": int(build.tier),
              "binding_hash": binding_hash(binding)}
    calculations = {
        "defenses": "defenses", "health_max": "health_max",
        "focus_max": "focus_max", "investiture_max": "investiture_max",
        "deflect": "deflect_value", "attributes": "eff_attributes",
        "statistics": "derived_stats",
    }
    for key, method_name in calculations.items():
        method = getattr(build, method_name, None)
        if callable(method):
            result[key] = deepcopy(method())
    return result


def _history(raw, actor_id, revision):
    require(isinstance(raw, (list, tuple)), "invalid advancement history")
    result = []
    last_revision = -1
    for entry in raw:
        try:
            event = entry if isinstance(entry, AdvancementEvent) else from_dict(AdvancementEvent, entry)
        except RulesValidationError:
            raise RulesValidationError("invalid advancement history") from None
        require(event.actor_id == actor_id, "advancement history actor mismatch")
        require(event.revision > last_revision and event.revision <= revision,
                "invalid advancement history revision order")
        last_revision = event.revision
        result.append(to_dict(event))
    return result


def _history_goal_entitlements(history):
    """Replay the validated advancement ledger into current server authority."""
    counts = Counter()
    advances = {}
    reverted = set()
    for event in history:
        if event["action"] == "advance":
            values = event.get("choices", {}).get("goal_entitlements", [])
            require(isinstance(values, (list, tuple)),
                    "invalid advancement goal entitlements")
            normalized = []
            for value in values:
                require(isinstance(value, str)
                        and _MISTBORN_ENTITLEMENT.fullmatch(value) is not None,
                        "invalid advancement goal entitlement")
                normalized.append(value)
                counts[value] += 1
            advances[event["id"]] = tuple(normalized)
        elif event["action"] == "revert":
            target = event.get("reverts_event_id")
            require(target in advances and target not in reverted,
                    "invalid reverted advancement entitlement ledger")
            reverted.add(target)
            for value in advances[target]:
                counts[value] -= 1
                require(counts[value] >= 0,
                        "invalid reverted advancement entitlement ledger")
    return tuple(sorted(value for value, count in counts.items() if count > 0))


def _history_advancement_bonus_grants(history):
    """Replay goal rewards so client build payloads cannot mint budget."""
    totals = {key: 0 for key in _ADVANCEMENT_BONUS_KEYS}
    advances = {}
    reverted = set()
    for event in history:
        if event["action"] == "advance":
            rewards = event.get("choices", {}).get("goal_rewards", {})
            require(isinstance(rewards, Mapping),
                    "invalid advancement goal rewards")
            grants = {key: 0 for key in _ADVANCEMENT_BONUS_KEYS}
            for reward in rewards.values():
                require(isinstance(reward, Mapping),
                        "invalid advancement goal rewards")
                for key, value in reward.items():
                    require(key in grants and type(value) is int and value >= 0,
                            "invalid advancement goal rewards")
                    grants[key] += value
            advances[event["id"]] = grants
            for key, value in grants.items():
                totals[key] += value
        elif event["action"] == "revert":
            target = event.get("reverts_event_id")
            require(target in advances and target not in reverted,
                    "invalid reverted advancement reward ledger")
            reverted.add(target)
            for key, value in advances[target].items():
                totals[key] -= value
                require(totals[key] >= 0,
                        "invalid reverted advancement reward ledger")
    return totals


def with_legacy_play_state(document):
    """Project canonical runtime state to the historical field without aliasing."""
    result = deepcopy(dict(document))
    runtime = result.get("runtime_state")
    require(isinstance(runtime, Mapping), "invalid runtime state")
    result["runtime_state"] = deepcopy(dict(runtime))
    result["play_state"] = deepcopy(result["runtime_state"])
    return result


def normalize_character_document(document, *, actor_id=None, binding=None,
                                 cosmere_theme=None, prefer_legacy_runtime=False,
                                 profile=None, homebrew=None, prior_build=None,
                                 trusted_goal_entitlements=None,
                                 trusted_advancement_bonus_grants=None):
    """Return a detached v2 document and recompute all derived authority.

    Imported/supplied ``derived`` and nested build pins are never trusted.
    ``prefer_legacy_runtime`` is only for existing compatibility writers which
    explicitly edited ``play_state``; canonical services leave it false.
    """
    require(isinstance(document, Mapping), "character document must be an object")
    source = deepcopy(dict(document))
    require(source.get("system", "cosmere") == "cosmere", "not a Cosmere character")
    identity = _actor_id(source, actor_id)
    raw_build = source.get("build")
    require(isinstance(raw_build, Mapping), "invalid character build")
    registry = getattr(profile, "registry", None) or default_registry()
    selected_binding = _binding(source, raw_build, binding, registry=registry)
    origin, era = _scope(selected_binding, source, raw_build)
    revision = _nonnegative_integer(source.get("revision", 0), "character revision")
    history = _history(source.get("advancement_history", []), identity, revision)
    authoritative_entitlements = (
        _history_goal_entitlements(history)
        if trusted_goal_entitlements is None
        else tuple(trusted_goal_entitlements))
    authoritative_bonus_grants = (
        _history_advancement_bonus_grants(history)
        if trusted_advancement_bonus_grants is None
        else dict(trusted_advancement_bonus_grants))
    if cosmere_theme is not None:
        theme_source = {"cosmere_theme": cosmere_theme}
    elif "cosmere_theme" in source or "cosmere_world" in source:
        theme_source = source
    else:
        # Pre-v2 character files stored the cosmetic world switch inside the
        # build. Preserve it during default migration; it still never selects
        # mechanics because the binding above is resolved independently.
        theme_source = raw_build
    theme = normalize_theme(theme_source)

    selected_profile = profile or resolve_profile(
        {"rules_binding": to_dict(selected_binding), "origin_world": origin,
         "era": era}, registry=registry, homebrew=homebrew)
    require(getattr(selected_profile, "binding", None) == selected_binding,
            "profile rules binding does not match the character binding")

    # The façade makes the exact resolved profile serialize its own canonical
    # choices. This intentionally does not run advancement completeness checks.
    build_input = deepcopy(dict(raw_build))
    build_input["rules_binding"] = to_dict(selected_binding)
    build_input["cosmere_theme"] = theme
    build_input["origin_world"] = origin
    build_input["era"] = era
    if origin == "scadrial":
        submitted = build_input.get("goal_entitlements", [])
        require(isinstance(submitted, (list, tuple)) and all(
            isinstance(value, str) for value in submitted),
            "invalid goal entitlement ledger")
        require(tuple(sorted(set(submitted))) == tuple(sorted(set(authoritative_entitlements))),
                "unearned goal entitlement in character build")
        build = selected_profile.create_build(
            build_input,
            trusted_goal_entitlements=authoritative_entitlements,
            trusted_advancement_bonus_grants=authoritative_bonus_grants)
    else:
        build = selected_profile.create_build(build_input)
    if origin == "scadrial":
        hard_violations = getattr(build, "hard_violations", lambda: ())()
        require(not hard_violations, hard_violations[0] if hard_violations else
                "invalid Scadrian character build")
    normalized_build = build.to_dict()
    normalized_build["rules_binding"] = to_dict(selected_binding)
    normalized_build["cosmere_theme"] = theme
    # These fields are deliberately owner-private and mechanically inert. Keep
    # their presence/value stable through normalization; authorization layers
    # decide whether a submitted edit may supply them.
    for private_field in ("notes", "session_notes"):
        if private_field in raw_build:
            normalized_build[private_field] = deepcopy(raw_build[private_field])
        else:
            normalized_build.pop(private_field, None)

    if prefer_legacy_runtime and isinstance(source.get("play_state"), Mapping):
        raw_runtime = source["play_state"]
    elif isinstance(source.get("runtime_state"), Mapping):
        raw_runtime = source["runtime_state"]
    elif isinstance(source.get("play_state"), Mapping):
        raw_runtime = source["play_state"]
    else:
        raw_runtime = {}
    prior_context = None
    if prior_build is not None:
        if hasattr(prior_build, "to_dict"):
            prior_context = prior_build
        elif origin == "scadrial":
            prior_context = selected_profile.create_build(
                prior_build, trusted_goal_entitlements=(
                    prior_build.get("goal_entitlements", ())
                    if isinstance(prior_build, Mapping) else ()),
                trusted_advancement_bonus_grants=(
                    prior_build.get("advancement_bonus_grants", {})
                    if isinstance(prior_build, Mapping) else {}))
        else:
            prior_context = selected_profile.create_build(prior_build)
    runtime = selected_profile.normalize_runtime_state(
        deepcopy(dict(raw_runtime)), target_id=identity, build=build,
        prior_build=prior_context)
    require(isinstance(runtime, Mapping), "profile returned invalid runtime state")

    result = source
    result.update({
        "schema_version": CHARACTER_SCHEMA_VERSION,
        "revision": revision,
        "id": identity,
        "system": "cosmere",
        "name": normalized_build.get("name") or source.get("name") or "New Hero",
        "rules_binding": to_dict(selected_binding),
        "origin_world": origin,
        "era": era,
        "cosmere_theme": theme,
        "build": normalized_build,
        "derived": _derived(build, selected_binding),
        "runtime_state": deepcopy(dict(runtime)),
    })
    result["advancement_history"] = history
    return with_legacy_play_state(result)


def apply_revisioned_mutation(document, *, expected_revision: int,
                              transform: Callable[[dict], Mapping], binding=None,
                              profile=None, homebrew=None):
    """Pure compare-and-swap mutation used by advancement and later actions."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise CharacterRevisionError("invalid_revision",
                                     "Expected revision must be a nonnegative integer.", 422)
    current = normalize_character_document(
        document, binding=binding, profile=profile, homebrew=homebrew)
    if current["revision"] != expected_revision:
        raise CharacterRevisionError("stale_revision",
                                     "The character changed. Reload before trying again.", 409)
    candidate = transform(deepcopy(current))
    require(isinstance(candidate, Mapping), "character mutation returned invalid data")
    candidate = deepcopy(dict(candidate))
    candidate["revision"] = current["revision"] + 1
    candidate["id"] = current["id"]
    candidate["rules_binding"] = deepcopy(current["rules_binding"])
    candidate["play_state"] = deepcopy(candidate.get("runtime_state", {}))
    return normalize_character_document(
        candidate, actor_id=current["id"], binding=current["rules_binding"],
        profile=profile, homebrew=homebrew, prior_build=current["build"])
