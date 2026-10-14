"""Canonical Cosmere effect instances and compatibility condition projections.

``runtime_state.effects`` is authoritative.  ``conditions`` is rebuilt as a
detached legacy projection for the existing sheets and tracker.  The module is
pure so profile normalization, HTTP mutations, encounter restore, and tests all
use one definition of stacking and expiry.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from collections.abc import Mapping, Sequence

from .rules.schema import EffectInstance, RulesValidationError, from_dict, to_dict


_ATTRIBUTES = {
    "str": "strength", "spd": "speed", "int": "intellect", "wil": "willpower",
    "awa": "awareness", "pre": "presence",
}


def _effect(value):
    if isinstance(value, EffectInstance):
        return value
    if not isinstance(value, Mapping):
        raise RulesValidationError("invalid effect instance")
    raw = deepcopy(dict(value))
    # Task-1 records shipped before the full runtime lifecycle fields existed.
    # Fill their explicit compatibility defaults at the normalization boundary.
    raw.setdefault("selected_attribute", None)
    raw.setdefault("stacking_identity", "effect.legacy.generic")
    raw.setdefault("duration_unit", "permanent")
    raw.setdefault("remaining_duration", None)
    raw.setdefault("trigger", "manual")
    raw.setdefault("refresh_policy", "independent")
    raw.setdefault("expiry_state", {})
    raw.setdefault("expired", False)
    selected = raw.get("selected_attribute")
    if selected in _ATTRIBUTES:
        raw["selected_attribute"] = _ATTRIBUTES[selected]
    return from_dict(EffectInstance, raw)


def normalize_effects(values):
    if values is None:
        return []
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise RulesValidationError("effects must be a list")
    result, identities = [], set()
    for value in values:
        row = to_dict(_effect(value))
        if row["id"] in identities:
            raise RulesValidationError("duplicate effect instance ID")
        identities.add(row["id"])
        if not row.get("expired"):
            result.append(row)
    return result


def _legacy_id(target_id, condition):
    digest = sha256(f"{target_id}:{condition}".encode("utf-8")).hexdigest()[:20]
    return f"effect.legacy.h{digest}"


def effect_target_id(value):
    """Return a schema-safe stable identity for legacy tracker instance IDs."""
    try:
        from .rules.schema import entity_identifier
        entity_identifier(value, "effect target ID")
        return value
    except RulesValidationError:
        digest = sha256(str(value or "unknown").encode("utf-8")).hexdigest()[:20]
        return f"combatant.runtime.h{digest}"


def _legacy_effect(target_id, condition, value):
    magnitude = int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 1
    magnitude = max(1, magnitude)
    return {
        "id": _legacy_id(target_id, condition),
        "source_id": f"condition.legacy.{condition.replace('_', '-')}",
        "target_id": target_id,
        "parameters": {"condition": condition, "magnitude": magnitude,
                       "legacy_projection": True},
        "selected_attribute": None,
        "stacking_identity": f"condition.{condition.replace('_', '-')}",
        "duration_unit": "permanent",
        "remaining_duration": None,
        "trigger": "manual",
        "refresh_policy": "replace",
        "expiry_state": {},
        "expired": False,
    }


def effects_from_conditions(conditions, target_id):
    target_id = effect_target_id(target_id)
    if not isinstance(conditions, Mapping):
        return []
    rows = []
    for condition, value in sorted(conditions.items()):
        if value and value != 0:
            rows.append(to_dict(_effect(_legacy_effect(target_id, str(condition), value))))
    return rows


def condition_effect(target_id, condition, *, magnitude=1, selected_attribute=None,
                     source_id=None, duration_unit="permanent",
                     remaining_duration=None, trigger="manual",
                     refresh_policy="independent", expiry_state=None,
                     instance_id=None):
    target_id = effect_target_id(target_id)
    condition = str(condition or "").strip().lower()
    if not condition:
        raise RulesValidationError("missing effect condition")
    identity = instance_id or f"effect.runtime.h{sha256(os_urandom()).hexdigest()[:20]}"
    return to_dict(_effect({
        "id": identity,
        "source_id": source_id or f"condition.manual.{condition.replace('_', '-')}",
        "target_id": target_id,
        "parameters": {"condition": condition, "magnitude": max(1, int(magnitude))},
        "selected_attribute": selected_attribute,
        "stacking_identity": f"condition.{condition.replace('_', '-')}" +
                             (f".{selected_attribute}" if selected_attribute else ""),
        "duration_unit": duration_unit,
        "remaining_duration": remaining_duration,
        "trigger": trigger,
        "refresh_policy": refresh_policy,
        "expiry_state": expiry_state or {},
        "expired": False,
    }))


def os_urandom():
    """Small seam kept separate so tests can supply explicit instance IDs."""
    import os
    return os.urandom(32)


def project_conditions(effects):
    """Return a fresh lossy projection for legacy boolean/value consumers."""
    result = {}
    for row in normalize_effects(effects):
        parameters = row.get("parameters") or {}
        condition = parameters.get("condition")
        if not isinstance(condition, str) or not condition:
            continue
        magnitude = parameters.get("magnitude", 1)
        try:
            magnitude = max(1, int(magnitude))
        except (TypeError, ValueError):
            magnitude = 1
        # Valued conditions add independent instances.  Boolean conditions only
        # need a truthy compatibility chip.
        if condition in {"afflicted", "enhanced", "exhausted"}:
            result[condition] = int(result.get(condition, 0) or 0) + magnitude
        else:
            result[condition] = True
    return deepcopy(result)


def normalize_runtime_state(state, target_id):
    if not isinstance(state, Mapping):
        raise RulesValidationError("runtime state must be an object")
    result = deepcopy(dict(state))
    raw_effects = result.get("effects")
    if raw_effects is None:
        effects = effects_from_conditions(result.get("conditions", {}), target_id)
    else:
        effects = normalize_effects(raw_effects)
    result["effects"] = effects
    result["conditions"] = project_conditions(effects)
    return result


def replace_condition_projection(effects, conditions, target_id):
    """Replace condition-bearing effects while preserving unrelated effects."""
    retained = [row for row in normalize_effects(effects)
                if not isinstance(row.get("parameters"), Mapping)
                or not row["parameters"].get("condition")]
    return retained + effects_from_conditions(conditions, target_id)


def reconcile_condition_projection(effects, conditions, target_id):
    """Honor a legacy projection edit without collapsing unchanged instances."""
    rows = normalize_effects(effects)
    desired = {str(key): value for key, value in (conditions or {}).items()
               if value and value != 0}
    current = project_conditions(rows)
    condition_names = set(current) | set(desired)
    for condition in sorted(condition_names):
        before = current.get(condition, 0)
        after = desired.get(condition, 0)
        before_n = int(before) if not isinstance(before, bool) else (1 if before else 0)
        after_n = int(after) if not isinstance(after, bool) else (1 if after else 0)
        if condition not in {"afflicted", "enhanced", "exhausted"}:
            before_n, after_n = int(bool(before_n)), int(bool(after_n))
        if after_n == before_n:
            continue
        indices = [index for index, row in enumerate(rows)
                   if (row.get("parameters") or {}).get("condition") == condition]
        if after_n < before_n:
            remove = before_n - after_n
            for index in reversed(indices):
                magnitude = int((rows[index].get("parameters") or {}).get("magnitude", 1) or 1)
                if remove >= magnitude:
                    remove -= magnitude
                    rows.pop(index)
                else:
                    parameters = dict(rows[index]["parameters"])
                    parameters["magnitude"] = magnitude - remove
                    rows[index]["parameters"] = parameters
                    remove = 0
                if remove <= 0:
                    break
        else:
            rows.append(condition_effect(
                target_id, condition, magnitude=after_n - before_n,
                refresh_policy="independent" if condition in {
                    "afflicted", "enhanced", "exhausted"} else "replace"))
    return rows


def migrate_condition_expiry(effects, conditions, condition_expiry, target_id):
    """Translate legacy condition-level turn timers into EffectInstances.

    Historical encounter autosaves kept a separate ``condition_expiry`` map
    and decremented it at the end of the affected combatant's turn. Canonical
    effects own both the condition and its clock, so every matching instance is
    given the equivalent turn duration. A missing legacy projection is created
    deterministically before the obsolete map is discarded by the caller.
    """
    target_id = effect_target_id(target_id)
    rows = normalize_effects(effects)
    if not isinstance(condition_expiry, Mapping):
        return rows
    projected = conditions if isinstance(conditions, Mapping) else {}
    for raw_condition, raw_remaining in sorted(condition_expiry.items()):
        condition = str(raw_condition or "").strip().lower()
        if not condition or not projected.get(raw_condition):
            continue
        try:
            remaining = int(raw_remaining)
        except (TypeError, ValueError):
            continue
        matching = [row for row in rows
                    if (row.get("parameters") or {}).get("condition") == condition]
        if not matching:
            legacy_value = projected.get(raw_condition, True)
            rows.append(to_dict(_effect(
                _legacy_effect(target_id, condition, legacy_value))))
            matching = [rows[-1]]
        if remaining <= 0:
            rows = [row for row in rows if row not in matching]
            continue
        for row in matching:
            row["duration_unit"] = "turn"
            row["remaining_duration"] = remaining
            row["trigger"] = "turn_end"
            row["expiry_state"] = {"actor_id": target_id}
    return normalize_effects(rows)


def apply_effect(effects, incoming):
    rows = normalize_effects(effects)
    fresh = to_dict(_effect(incoming))
    if any(row["id"] == fresh["id"] for row in rows):
        raise RulesValidationError("duplicate effect instance ID")
    policy = fresh["refresh_policy"]
    matching = [index for index, row in enumerate(rows)
                if row["stacking_identity"] == fresh["stacking_identity"]]
    if policy == "independent" or not matching:
        return rows + [fresh]
    if policy == "replace":
        first = matching[0]
        return [row for index, row in enumerate(rows) if index not in matching[:1]][:first] + [fresh] + [
            row for index, row in enumerate(rows) if index > first and index not in matching]
    index = matching[0]
    current = deepcopy(rows[index])
    if policy == "refresh":
        keep_id = current["id"]
        current.update(deepcopy(fresh))
        current["id"] = keep_id
    elif policy == "extend":
        old = current.get("remaining_duration")
        added = fresh.get("remaining_duration")
        if old is None or added is None:
            raise RulesValidationError("only finite effects can be extended")
        current["remaining_duration"] = int(old) + int(added)
    rows[index] = current
    return rows


def _event_matches(row, event):
    if row["trigger"] != event.get("trigger"):
        return False
    state = row.get("expiry_state") or {}
    actor_id = state.get("actor_id")
    if actor_id is not None and actor_id != event.get("actor_id"):
        return False
    phase = state.get("phase")
    if phase is not None and phase != event.get("phase"):
        return False
    not_before_round = state.get("not_before_round")
    if not_before_round is not None and int(event.get("round", -1)) < int(not_before_round):
        return False
    not_before_turn = state.get("not_before_turn")
    if not_before_turn is not None and int(event.get("turn", -1)) < int(not_before_turn):
        return False
    if row["trigger"] == "manual" and event.get("duration_unit") != row["duration_unit"]:
        return False
    return True


def advance_effects(effects, event):
    """Advance one explicit timing event and return ``(active, expired)``.

    Round effects are normally authored with ``phase_start`` plus the phase and
    ``not_before_round`` at which the same phase next occurs.  Turn effects use
    a target actor and absolute turn serial, keeping "end of next turn"
    distinct from a round boundary.  Conversation/endeavor contributions use
    ``contribution_end``.
    """
    if not isinstance(event, Mapping):
        raise RulesValidationError("invalid effect expiry event")
    active, expired = [], []
    for original in normalize_effects(effects):
        row = deepcopy(original)
        if not _event_matches(row, event) or row["duration_unit"] == "permanent":
            active.append(row)
            continue
        state = dict(row.get("expiry_state") or {})
        boundary_id = event.get("boundary_id")
        if (boundary_id is not None
                and state.get("last_applied_boundary_id") == boundary_id):
            active.append(row)
            continue
        decrement = event.get("elapsed", 1) if row["duration_unit"] in {"minute", "hour"} else 1
        try:
            decrement = max(0, int(decrement))
            remaining = int(row["remaining_duration"]) - decrement
        except (TypeError, ValueError):
            raise RulesValidationError("invalid effect duration") from None
        if remaining <= 0:
            row["remaining_duration"] = 0
            row["expired"] = True
            if boundary_id is not None:
                state["last_applied_boundary_id"] = str(boundary_id)
            state["expired_by"] = str(event.get("trigger"))
            row["expiry_state"] = state
            expired.append(row)
        else:
            row["remaining_duration"] = remaining
            if boundary_id is not None:
                state["last_applied_boundary_id"] = str(boundary_id)
                row["expiry_state"] = state
            active.append(row)
    return active, expired
