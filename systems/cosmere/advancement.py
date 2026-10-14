"""Pure, revisioned Cosmere advancement preview/commit/revert transactions."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import json

from core.character_workflows.privacy import preserve_owner_private_build_fields

from .documents import (CharacterRevisionError, apply_revisioned_mutation,
                        binding_hash, normalize_character_document)
from .rules.profiles import resolve_profile
from .rules.schema import (AdvancementEvent, RulesValidationError, from_dict,
                           require, to_dict)


class AdvancementError(RulesValidationError):
    def __init__(self, code, message, status=422, details=()):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = tuple(details)


def _fail(code, message, status=422, details=()):
    raise AdvancementError(code, message, status, details)


def _request_id(request):
    value = request.get("request_id")
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        _fail("invalid_request_id", "A stable advancement request ID is required.")
    return value


def _expected_revision(request):
    value = request.get("expected_revision")
    if type(value) is not int or value < 0:
        _fail("invalid_revision", "Expected revision must be a nonnegative integer.")
    return value


def _history_ids(document):
    return {event.get("id") for event in document.get("advancement_history", [])
            if isinstance(event, Mapping)}


def _check_request(current, request):
    request_id = _request_id(request)
    if request_id in _history_ids(current):
        _fail("replay_detected", "This advancement request was already applied.", 409)
    expected = _expected_revision(request)
    if current["revision"] != expected:
        _fail("stale_revision", "The character changed. Reload before advancing.", 409)
    return request_id, expected


def _count(values):
    return Counter(json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) for value in values)


def _subset(before, after):
    return not (_count(before) - _count(after))


def _reward_map(goal):
    raw = goal.get("rewards", goal.get("grant", goal.get("grants", {})))
    if isinstance(raw, str):
        raw = {raw: 1}
    if not isinstance(raw, Mapping):
        _fail("invalid_goal_reward", "A completed goal has an invalid reward.")
    aliases = {
        "attribute": "attribute_points", "attribute_point": "attribute_points",
        "attribute_points": "attribute_points", "skill": "skill_ranks",
        "skill_rank": "skill_ranks", "skill_ranks": "skill_ranks",
        "talent": "talents", "talents": "talents",
        "expertise": "expertises", "expertises": "expertises",
    }
    result = {key: 0 for key in set(aliases.values())}
    for key, value in raw.items():
        if key not in aliases or type(value) is not int or value < 0:
            _fail("invalid_goal_reward", "A completed goal has an invalid reward.")
        result[aliases[key]] += value
    return result


def _goal_entitlements(goal):
    """Derive narrow Mistborn unlock authority from persisted goal semantics.

    Reward payloads are deliberately ignored.  A client can select a completed
    goal ID, but cannot name the entitlement that goal creates.
    """
    goal_type = str(goal.get("type") or "").lower().replace("_", "-")

    def power(value, expected_art=None):
        parts = str(value or "").lower().replace("_", "-").split(".")
        if len(parts) != 4 or parts[:2] != ["mistborn", "power"]:
            _fail("invalid_goal_entitlement", "A Mistborn goal has an invalid power target.")
        art, metal = parts[2:]
        if art not in {"allomancy", "feruchemy"} or not metal or (
                expected_art is not None and art != expected_art):
            _fail("invalid_goal_entitlement", "A Mistborn goal has an invalid power target.")
        return f"mistborn.entitlement.power.{art}.{metal}.unlock"

    if goal_type == "mistborn.goal.train-power":
        return (power(goal.get("power_id"), "allomancy"),)
    if goal_type == "mistborn.goal.train-power-pair":
        power_ids = goal.get("power_ids")
        if not isinstance(power_ids, (list, tuple)) or len(power_ids) != 2:
            _fail("invalid_goal_entitlement", "A paired power goal needs two targets.")
        result = tuple(power(value, "allomancy") for value in power_ids)
        if len(set(result)) != 2:
            _fail("invalid_goal_entitlement", "A paired power goal needs two distinct targets.")
        return result
    if goal_type == "mistborn.goal.construct-metalmind":
        return (power(goal.get("power_id"), "feruchemy"),)
    if goal_type == "mistborn.reward.pure-lerasium":
        return ("mistborn.entitlement.reward.pure-lerasium",)
    return ()


def _selected_goals(current, requested_ids):
    if not isinstance(requested_ids, (list, tuple)) or any(
            not isinstance(item, str) for item in requested_ids):
        _fail("invalid_goal_selection", "Goal selections must be a list of goal IDs.")
    if len(set(requested_ids)) != len(requested_ids):
        _fail("invalid_goal_selection", "A goal can only be selected once.")
    goals = current.get("runtime_state", {}).get("goals", [])
    if not isinstance(goals, list):
        _fail("invalid_goal_state", "Character goal state is invalid.")
    by_id = {goal.get("id"): goal for goal in goals
             if isinstance(goal, Mapping) and isinstance(goal.get("id"), str)}
    totals = {"attribute_points": 0, "skill_ranks": 0, "talents": 0, "expertises": 0}
    selected = []
    for goal_id in requested_ids:
        goal = by_id.get(goal_id)
        if not goal or goal.get("completed") is not True or goal.get("consumed_by"):
            _fail("goal_unavailable", f"Completed goal {goal_id!r} is unavailable.")
        reward = _reward_map(goal)
        for key, value in reward.items():
            totals[key] += value
        selected.append((goal_id, reward, _goal_entitlements(goal)))
    return selected, totals


def _new_surges(before, after):
    previous = set(getattr(before, "surges_unlocked", lambda: ())())
    current = set(getattr(after, "surges_unlocked", lambda: ())())
    return tuple(sorted(current - previous))


def _exact_grants(before, after, grants, goal_rewards):
    expected = {
        "attribute_points": int(bool(grants.get("attribute_point"))) + goal_rewards["attribute_points"],
        "skill_ranks": (int(grants.get("skill_ranks") or 0)
                         if grants.get("skill_ranks") != "choice" else 0),
        "talents": int(bool(grants.get("talent"))
                       if grants.get("talent") != "choice" else 0),
        "expertises": goal_rewards["expertises"],
    }
    if grants.get("ancestry_bonus_talent"):
        expected["talents"] += 1

    prior_epic = list(getattr(before, "epic_choices", []))
    next_epic = list(getattr(after, "epic_choices", []))
    if after.level > 20:
        if (len(next_epic) != len(prior_epic) + 1 or next_epic[:-1] != prior_epic
                or next_epic[-1] not in {"skill", "talent"}):
            _fail("missing_required_grants",
                  "Epic advancement requires exactly one skill-or-talent choice.")
        expected["skill_ranks"] += int(next_epic[-1] == "skill")
        expected["talents"] += int(next_epic[-1] == "talent")
    elif next_epic != prior_epic:
        _fail("invalid_advancement_change", "Epic choices cannot change before level 21.")
    expected["skill_ranks"] += goal_rewards["skill_ranks"]
    expected["talents"] += goal_rewards["talents"]

    before_attrs, after_attrs = before.attributes, after.attributes
    if any(after_attrs.get(key, 0) < before_attrs.get(key, 0) for key in before_attrs):
        _fail("invalid_advancement_change", "Advancement cannot lower an attribute.")
    before_skills, after_skills = before.skills, after.skills
    if any(after_skills.get(key, 0) < value for key, value in before_skills.items()):
        _fail("invalid_advancement_change", "Advancement cannot remove skill ranks.")
    if not _subset(before.talents, after.talents):
        _fail("invalid_advancement_change", "Advancement cannot replace existing talents.")
    if not _subset(before.expertises, after.expertises):
        _fail("invalid_advancement_change", "Advancement cannot remove existing expertises.")

    new_surges = _new_surges(before, after)
    for code in new_surges:
        if after_skills.get(code, 0) - before_skills.get(code, 0) != 1:
            _fail("missing_required_grants",
                  "Each newly unlocked Surge requires its free skill rank.")
    expected["skill_ranks"] += len(new_surges)
    # Raising Intellect grants another expertise through the profile's budget.
    expected["expertises"] += max(0, after.attributes.get("int", 0)
                                  - before.attributes.get("int", 0))
    actual = {
        "attribute_points": sum(after_attrs.values()) - sum(before_attrs.values()),
        "skill_ranks": sum(after_skills.values()) - sum(before_skills.values()),
        "talents": len(after.talents) - len(before.talents),
        "expertises": len(after.expertises) - len(before.expertises),
    }
    if actual != expected:
        detail = ", ".join(f"{key}: selected {actual[key]}, required {expected[key]}"
                           for key in sorted(expected) if actual[key] != expected[key])
        _fail("missing_required_grants", "The level's required grants are incomplete: " + detail,
              details=(actual, expected))
    return expected, new_surges


def _issues(profile, before, after, transition=None):
    previous = set(profile.validate_build(before))
    proposed = list(profile.validate_build(after, before, transition))
    hard = list(getattr(after, "hard_violations", lambda: proposed)())
    failures = list(dict.fromkeys(hard + [issue for issue in proposed if issue not in previous]))
    if failures:
        _fail("profile_validation_failed", failures[0], details=failures)


def _profile_build(profile, payload, goal_entitlements=(),
                   advancement_bonus_grants=None):
    if isinstance(payload, Mapping) and payload.get("origin_world") == "scadrial":
        return profile.create_build(
            payload, trusted_goal_entitlements=goal_entitlements,
            trusted_advancement_bonus_grants=advancement_bonus_grants)
    return profile.create_build(payload)


def _json_operations(before, after, root):
    operations = []
    keys = sorted(set(before) | set(after))
    for key in keys:
        before_exists, after_exists = key in before, key in after
        old, new = before.get(key), after.get(key)
        if before_exists == after_exists and old == new:
            continue
        operation = {"path": [root, key], "before_exists": before_exists,
                     "after_exists": after_exists}
        if before_exists:
            operation["before"] = deepcopy(old)
        if after_exists:
            operation["after"] = deepcopy(new)
        operations.append(operation)
    return operations


def _mark_goals(runtime, selected, event_id):
    result = deepcopy(runtime)
    ids = {goal_id for goal_id, _reward, _entitlements in selected}
    if not ids:
        return result
    for goal in result.get("goals", []):
        if isinstance(goal, dict) and goal.get("id") in ids:
            goal["consumed_by"] = event_id
    return result


def _snapshot(document, profile=None):
    return {"level": document["build"]["level"], "derived": deepcopy(document["derived"]),
            "binding_hash": binding_hash(
                (profile or resolve_profile(document)).binding)}


def _prepare(document, request, *, profile=None, homebrew=None):
    try:
        current = normalize_character_document(
            document, profile=profile, homebrew=homebrew)
        request_id, expected_revision = _check_request(current, request)
        target = request.get("target_level")
        if type(target) is not int or target != current["build"]["level"] + 1:
            _fail("invalid_level_transition", "Advancement must change exactly one level.")
        raw_build = request.get("build")
        if not isinstance(raw_build, Mapping):
            _fail("invalid_build", "Advancement requires the complete proposed build.")
        supplied = deepcopy(dict(raw_build))
        supplied["level"] = target
        supplied.setdefault("rules_binding", deepcopy(current["rules_binding"]))
        if supplied.get("rules_binding") != current["rules_binding"]:
            _fail("binding_mismatch", "Proposed build rules binding does not match the character package.")
        supplied["cosmere_theme"] = current["cosmere_theme"]
        active_profile = profile or resolve_profile(current, homebrew=homebrew)
        before = _profile_build(
            active_profile, current["build"],
            current["build"].get("goal_entitlements", ()),
            current["build"].get("advancement_bonus_grants", {}))
        selected_goals, reward_totals = _selected_goals(current, request.get("goal_ids", []))
        goal_entitlements = list(dict.fromkeys(
            entitlement for _goal_id, _reward, entitlements in selected_goals
            for entitlement in entitlements))
        bonus_grants = dict(getattr(before, "advancement_bonus_grants", {}) or {})
        for key, value in reward_totals.items():
            bonus_grants[key] = int(bonus_grants.get(key, 0) or 0) + value
        supplied["advancement_bonus_grants"] = bonus_grants
        prior_entitlements = list(getattr(before, "goal_entitlements", ()) or ())
        supplied["goal_entitlements"] = list(dict.fromkeys(
            prior_entitlements + goal_entitlements))
        # Free-form owner notes are mechanically inert server-owned state.  An
        # advancement submission may neither replace nor erase them.
        supplied = preserve_owner_private_build_fields(
            current, {"build": supplied})["build"]
        after = _profile_build(
            active_profile, supplied, supplied.get("goal_entitlements", ()),
            bonus_grants)
        if after.level != target:
            _fail("invalid_level_transition", "Advancement must change exactly one level.")
        for field in ("path", "ancestry", "culture"):
            if getattr(after, field, None) != getattr(before, field, None):
                _fail("path_locked", f"Advancement cannot change the character's {field}.")
        grants = active_profile.level_grants(target, after)
        exact, new_surges = _exact_grants(before, after, grants, reward_totals)
        _issues(active_profile, before, after,
                {"goal_entitlements": goal_entitlements})
        # Serialize through the active profile/facade so callers cannot retain
        # unknown or malformed fields from their request.
        proposed_build = after.to_dict()
        proposed_build["rules_binding"] = deepcopy(current["rules_binding"])
        proposed_build["cosmere_theme"] = current["cosmere_theme"]
        proposed_build = preserve_owner_private_build_fields(
            current, {"build": proposed_build})["build"]
        proposed_runtime = _mark_goals(current["runtime_state"], selected_goals, request_id)
        candidate = deepcopy(current)
        candidate.update(build=proposed_build, runtime_state=proposed_runtime,
                         play_state=deepcopy(proposed_runtime))
        candidate = normalize_character_document(
            candidate, binding=current["rules_binding"],
            profile=active_profile, homebrew=homebrew,
            prior_build=current["build"],
            trusted_goal_entitlements=getattr(after, "goal_entitlements", ()),
            trusted_advancement_bonus_grants=getattr(
                after, "advancement_bonus_grants", {}))
        operations = _json_operations(current["build"], candidate["build"], "build")
        operations += _json_operations(current["runtime_state"], candidate["runtime_state"],
                                       "runtime_state")
        return current, candidate, request_id, expected_revision, selected_goals, {
            "level_grants": deepcopy(dict(grants)), "required": exact,
            "new_surges": list(new_surges), "operations": operations,
            "selected_grants": deepcopy(request.get("selected_grants", {})),
            "goal_rewards": {goal_id: deepcopy(reward)
                             for goal_id, reward, _entitlements in selected_goals},
            "goal_entitlements": deepcopy(goal_entitlements),
        }
    except AdvancementError:
        raise
    except CharacterRevisionError as error:
        raise AdvancementError(error.code, str(error), error.status) from None
    except RulesValidationError as error:
        raise AdvancementError("rules_validation_failed", str(error), 422) from None
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        raise AdvancementError("invalid_advancement", "Advancement data is invalid.", 422) from None


def preview_advancement(document, request, *, profile=None, homebrew=None):
    """Validate without mutation and return authoritative eligibility metadata."""
    current, candidate, request_id, _expected, selected_goals, choices = _prepare(
        document, request, profile=profile, homebrew=homebrew)
    return {"eligible": True, "blocked_reasons": [], "request_id": request_id,
            "prior_revision": current["revision"], "resulting_revision": current["revision"] + 1,
            "from_level": current["build"]["level"], "to_level": candidate["build"]["level"],
            "rules_binding": deepcopy(current["rules_binding"]),
            "derived": deepcopy(candidate["derived"]),
            "selected_goal_ids": [goal_id for goal_id, _reward, _entitlements in selected_goals],
            "grants": choices["level_grants"], "required": choices["required"]}


def commit_advancement(document, request, *, profile=None, homebrew=None):
    """Return a new document containing one auditable advancement event."""
    current, candidate, request_id, expected, selected_goals, choices = _prepare(
        document, request, profile=profile, homebrew=homebrew)
    active_profile = profile or resolve_profile(current, homebrew=homebrew)

    def transform(_current):
        result = deepcopy(candidate)
        event = AdvancementEvent(
            id=request_id, actor_id=current["id"], action="advance",
            prior_revision=current["revision"], revision=current["revision"] + 1,
            from_level=current["build"]["level"], to_level=candidate["build"]["level"],
            package_hash=binding_hash(active_profile.binding), choices=choices,
            consumed_goal_ids=tuple(goal_id for goal_id, _reward, _entitlements in selected_goals),
            prior_snapshot=_snapshot(current, active_profile),
            resulting_snapshot=_snapshot(candidate, active_profile),
            audit_metadata={"request_id": request_id},
            package_pins=(active_profile.binding.core_package,
                          *active_profile.binding.enabled_packs),
        )
        result["advancement_history"] = deepcopy(current["advancement_history"]) + [to_dict(event)]
        return result

    try:
        return apply_revisioned_mutation(current, expected_revision=expected,
                                         transform=transform,
                                         binding=current["rules_binding"],
                                         profile=active_profile, homebrew=homebrew)
    except CharacterRevisionError as error:
        raise AdvancementError(error.code, str(error), error.status) from None


def _path_value(document, path):
    cursor = document
    for key in path:
        if not isinstance(cursor, Mapping) or key not in cursor:
            return False, None
        cursor = cursor[key]
    return True, cursor


def _set_path(document, path, exists, value=None):
    cursor = document
    for key in path[:-1]:
        cursor = cursor.setdefault(key, {})
    if exists:
        cursor[path[-1]] = deepcopy(value)
    else:
        cursor.pop(path[-1], None)


def _inverse(document, operations):
    result = deepcopy(document)
    for operation in reversed(list(operations)):
        operation = to_dict(operation)
        path = list(operation.get("path") or [])
        if len(path) < 2 or any(not isinstance(key, (str, int)) for key in path):
            _fail("invalid_advancement_history", "The latest advancement cannot be reversed safely.", 409)
        exists, value = _path_value(result, path)
        if exists != bool(operation.get("after_exists")) or (
                exists and value != operation.get("after")):
            _fail("revert_conflict", "The advanced choices changed and cannot be safely reverted.", 409)
        _set_path(result, path, bool(operation.get("before_exists")), operation.get("before"))
    return result


def revert_advancement(document, request, *, profile=None, homebrew=None):
    """Apply the latest advance event's inverse operations and append a revert."""
    try:
        current = normalize_character_document(
            document, profile=profile, homebrew=homebrew)
        active_profile = profile or resolve_profile(current, homebrew=homebrew)
        request_id, expected = _check_request(current, request)
        history = current.get("advancement_history", [])
        if not history or history[-1].get("action") != "advance":
            _fail("nothing_to_revert", "Only the latest unreverted advancement can be reverted.", 409)
        advanced = from_dict(AdvancementEvent, history[-1])
        if advanced.package_hash != binding_hash(active_profile.binding):
            _fail("binding_mismatch", "The advancement package no longer matches the character.", 409)
        operations = advanced.choices.get("operations", ())
        candidate = _inverse(current, operations)
        candidate["play_state"] = deepcopy(candidate.get("runtime_state", {}))
        candidate = normalize_character_document(
            candidate, binding=current["rules_binding"],
            profile=active_profile, homebrew=homebrew,
            prior_build=current["build"],
            trusted_goal_entitlements=candidate.get("build", {}).get(
                "goal_entitlements", ()),
            trusted_advancement_bonus_grants=candidate.get("build", {}).get(
                "advancement_bonus_grants", {}))

        def transform(_current):
            result = deepcopy(candidate)
            inverse_operations = []
            for frozen_operation in reversed(list(operations)):
                operation = to_dict(frozen_operation)
                inverse = {**operation,
                           "before_exists": operation.get("after_exists"),
                           "after_exists": operation.get("before_exists")}
                inverse.pop("before", None)
                inverse.pop("after", None)
                if operation.get("after_exists"):
                    inverse["before"] = deepcopy(operation.get("after"))
                if operation.get("before_exists"):
                    inverse["after"] = deepcopy(operation.get("before"))
                inverse_operations.append(inverse)
            inverse_choices = {"operations": inverse_operations}
            event = AdvancementEvent(
                id=request_id, actor_id=current["id"], action="revert",
                prior_revision=current["revision"], revision=current["revision"] + 1,
                from_level=current["build"]["level"], to_level=candidate["build"]["level"],
                package_hash=advanced.package_hash, choices=inverse_choices,
                consumed_goal_ids=(), prior_snapshot=_snapshot(current, active_profile),
                resulting_snapshot=_snapshot(candidate, active_profile),
                audit_metadata={"request_id": request_id}, reverts_event_id=advanced.id,
                package_pins=advanced.package_pins,
            )
            result["advancement_history"] = deepcopy(history) + [to_dict(event)]
            return result

        return apply_revisioned_mutation(current, expected_revision=expected,
                                         transform=transform,
                                         binding=current["rules_binding"],
                                         profile=active_profile, homebrew=homebrew)
    except AdvancementError:
        raise
    except CharacterRevisionError as error:
        raise AdvancementError(error.code, str(error), error.status) from None
    except RulesValidationError as error:
        raise AdvancementError("invalid_advancement_history", str(error), 409) from None
