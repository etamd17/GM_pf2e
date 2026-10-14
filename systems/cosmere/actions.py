"""Structured Cosmere actions and pure, revision-ready action transactions.

Foundry event scripts are deliberately ignored.  Only declarative fields are
normalized; unresolved tests, damage rolls, targets, Opportunities, and
Complications remain explicit receipt data for the table to resolve.
"""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import os
import re
from collections.abc import Mapping

from .rules.schema import RulesValidationError, entity_identifier, require


_COST_TYPES = {"act": "action", "action": "action", "rea": "reaction",
               "reaction": "reaction", "fre": "free", "free": "free",
               "none": "free", None: "free"}
_RESOURCES = {"foc": "focus", "focus": "focus", "inv": "investiture",
              "investiture": "investiture", "hea": "health", "health": "health"}


class ActionTransactionError(RulesValidationError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def _slug(value):
    value = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return value or "action"


def _description(value):
    if isinstance(value, Mapping):
        for key in ("short", "chat", "value"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key]
        return ""
    return value if isinstance(value, str) else ""


def _cost(raw):
    raw = raw if isinstance(raw, Mapping) else {}
    kind = _COST_TYPES.get(raw.get("type"), str(raw.get("type") or "free"))
    value = raw.get("value")
    if kind == "reaction":
        value = 1
    elif kind == "free":
        value = 0
    else:
        try:
            value = max(0, int(value if value is not None else 1))
        except (TypeError, ValueError):
            value = 1
    return {"type": kind, "value": value}


def _consume(values):
    result = []
    for row in values if isinstance(values, list) else []:
        if not isinstance(row, Mapping):
            continue
        value = row.get("value") if isinstance(row.get("value"), Mapping) else {}
        try:
            raw_amount = row.get("amount") if "amount" in row else value.get(
                "min", value.get("actual", 0))
            amount = max(0, int(raw_amount or 0))
        except (TypeError, ValueError):
            amount = 0
        resource = _RESOURCES.get(row.get("resource"), str(row.get("resource") or ""))
        result.append({"type": str(row.get("type") or "resource"),
                       "resource": resource, "amount": amount,
                       "minimum": (row.get("minimum") if "minimum" in row
                                   else value.get("min")),
                       "maximum": (row.get("maximum") if "maximum" in row
                                   else value.get("max"))})
    return result


def normalize_action_item(item, actor_id=None):
    if not isinstance(item, Mapping):
        raise RulesValidationError("invalid action item")
    raw = deepcopy(dict(item))
    system = raw.get("system") if isinstance(raw.get("system"), Mapping) else {}
    activation = system.get("activation") if isinstance(system.get("activation"), Mapping) else {}
    damage = system.get("damage") if isinstance(system.get("damage"), Mapping) else {}
    target = system.get("target") if isinstance(system.get("target"), Mapping) else {}
    item_id = str(raw.get("_id") or system.get("uuid") or _slug(raw.get("name")))
    action_id = str(system.get("id") or item_id or _slug(raw.get("name")))
    cost = _cost(activation.get("cost"))
    use_limit = deepcopy(activation.get("uses")) if isinstance(activation.get("uses"), Mapping) else None
    test = {
        "type": str(activation.get("type") or "utility"),
        "skill": activation.get("skill"),
        "attribute": activation.get("attribute"),
        "modifier": str(activation.get("modifierFormula") or ""),
    }
    normalized_damage = {
        "formula": damage.get("formula"), "type": damage.get("type"),
        "skill": damage.get("skill"), "attribute": damage.get("attribute"),
        "graze_formula": damage.get("grazeOverrideFormula"),
    }
    result = {
        "id": action_id,
        "source_id": item_id,
        "item_id": item_id,
        "actor_id": actor_id,
        "name": str(raw.get("name") or action_id),
        "kind": "weapon" if raw.get("type") == "weapon" else "action",
        "activation": {
            "type": str(activation.get("type") or "utility"),
            "cost": cost,
            "consume": _consume(activation.get("consume")),
            "flavor": str(activation.get("flavor") or ""),
        },
        "test": test,
        "damage": normalized_damage,
        "target": deepcopy(dict(target)),
        "plot_die": bool(activation.get("plotDie", False)),
        "opportunity": deepcopy(activation.get("opportunity")),
        "complication": deepcopy(activation.get("complication")),
        "use_limit": use_limit,
        "description": _description(system.get("description")),
        "allow_multiple": bool(system.get("allowMultiple", False)),
        "permissions": deepcopy(system.get("permissions"))
                       if isinstance(system.get("permissions"), Mapping) else {},
    }
    # Explicit handbook exceptions, encoded as rules data rather than inferred
    # by parsing descriptions or executing Foundry macros.
    if action_id == "regenerate":
        result["permissions"]["while_unconscious"] = True
    return result


@lru_cache(maxsize=1)
def _source_actions():
    directory = os.path.join(os.path.dirname(__file__), "content")
    by_id = {}
    # Handbook data is the more specific source and therefore loaded first.
    for filename in ("handbook-actions.json", "actions.json"):
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            documents = json.load(handle)
        for document in documents:
            if not isinstance(document, Mapping) or document.get("type") not in {"action", "weapon"}:
                continue
            row = normalize_action_item(document)
            for identity in (row["id"], row["source_id"], _slug(row["name"])):
                by_id.setdefault(identity, row)
    return by_id


def action_by_id(action_id, embedded=()):
    identity = str(action_id or "")
    for item in embedded or ():
        try:
            row = normalize_action_item(item)
        except RulesValidationError:
            continue
        if identity in {row["id"], row["source_id"], _slug(row["name"])}:
            return row
    row = _source_actions().get(identity)
    return deepcopy(row) if row is not None else None


def _resource_value(state, resource):
    resources = state.get("resources") if isinstance(state.get("resources"), Mapping) else {}
    if resource in state:
        return state.get(resource), "top"
    if resource in resources:
        return resources.get(resource), "nested"
    return 0, "top"


def _set_resource(state, resource, value, location):
    if location == "nested":
        resources = dict(state.get("resources") or {})
        resources[resource] = value
        state["resources"] = resources
    else:
        state[resource] = value


def action_receipt(state, request_id):
    receipts = state.get("action_receipts")
    if not isinstance(receipts, list):
        return None
    return next((deepcopy(row) for row in receipts
                 if isinstance(row, Mapping) and row.get("request_id") == request_id), None)


def apply_lifecycle_transition(
        runtime_state, trigger, *, next_scope_id=None, boundary_id=None,
        actor_id=None):
    """Apply one authoritative scene, encounter, or contribution boundary."""
    if not isinstance(runtime_state, Mapping):
        raise ActionTransactionError("invalid_state", "Runtime state is invalid.")
    if trigger not in {"scene_start", "scene_end", "encounter_end",
                       "contribution_end"}:
        raise ActionTransactionError("invalid_lifecycle", "Lifecycle trigger is invalid.")
    if trigger == "contribution_end" and (
            not isinstance(actor_id, str) or not actor_id):
        raise ActionTransactionError(
            "invalid_lifecycle", "Contribution lifecycle requires an actor identity.")
    from .effects import advance_effects, normalize_effects, project_conditions

    state = deepcopy(dict(runtime_state))
    boundaries = list(state.get("lifecycle_boundaries") or [])
    if boundary_id is not None:
        boundary_id = str(boundary_id)
        if boundary_id in boundaries:
            return state, []
    effects = normalize_effects(state.get("effects") or [])
    event = {"trigger": trigger}
    if trigger == "contribution_end":
        event["actor_id"] = actor_id
    if trigger == "scene_start":
        active, expired = effects, []
    else:
        active, expired = advance_effects(effects, event)
    state["effects"] = active
    state["conditions"] = project_conditions(active)
    if isinstance(state.get("mistborn"), Mapping):
        from .mistborn_runtime import reconcile_mistborn_effect_mirrors
        state = reconcile_mistborn_effect_mirrors(state)

    scope_key = ({"scene_start": "scene", "scene_end": "scene",
                  "encounter_end": "encounter"}.get(trigger))
    if scope_key is not None:
        scope = dict(state.get(scope_key) or {})
        scope["id"] = next_scope_id
        scope["uses"] = {}
        if trigger == "scene_end":
            scope.pop("type", None)
        state[scope_key] = scope
    # A narrative boundary also terminates the current combat turn. Clearing
    # this ledger prevents a previous scene's Recover entry from blocking the
    # first turn in the next scene without restoring any spent pips.
    if trigger != "contribution_end":
        turn = dict(state.get("turn") or {})
        turn["named_actions_used"] = []
        state["turn"] = turn
    if boundary_id is not None:
        boundaries.append(boundary_id)
        state["lifecycle_boundaries"] = boundaries
    return state, expired


def apply_action_transaction(runtime_state, action, *, request_id):
    """Apply declarative costs/limits and return ``(new_state, receipt)``.

    Tests and damage remain unresolved structured data.  The caller wraps this
    pure transform in the character revision compare-and-swap boundary.
    """
    try:
        entity_identifier(request_id, "action request ID")
    except RulesValidationError:
        raise ActionTransactionError("invalid_request_id", "Action request ID is invalid.") from None
    if not isinstance(runtime_state, Mapping) or not isinstance(action, Mapping):
        raise ActionTransactionError("invalid_action", "Action data is invalid.")
    original = deepcopy(dict(runtime_state))
    duplicate = action_receipt(original, request_id)
    if duplicate is not None:
        raise ActionTransactionError("request_replayed", "Action request ID was already used.", 409)
    state = deepcopy(original)
    row = deepcopy(dict(action))
    action_id = str(row.get("id") or "")
    require(bool(action_id), "action identity is required")

    from .combat import turn_permissions
    turn = dict(state.get("turn") or {})
    conditions = state.get("conditions") if isinstance(state.get("conditions"), Mapping) else {}
    choice = str(turn.get("choice") or state.get("speed_choice") or "slow")
    permissions = turn_permissions(choice, conditions)
    allowed_while_unconscious = bool((row.get("permissions") or {}).get("while_unconscious"))
    if conditions.get("unconscious") and not allowed_while_unconscious:
        raise ActionTransactionError("action_blocked", "An Unconscious actor cannot take actions.", 409)

    used = list(turn.get("named_actions_used") or [])
    named_key = _slug(row.get("name") or action_id)
    if named_key in used and not row.get("allow_multiple"):
        raise ActionTransactionError("named_action_used", "A named action can normally be used only once per turn.", 409)

    scene = dict(state.get("scene") or {})
    scene_uses = dict(scene.get("uses") or {})
    encounter = dict(state.get("encounter") or {})
    encounter_uses = dict(encounter.get("uses") or {})
    use_limit = row.get("use_limit") if isinstance(row.get("use_limit"), Mapping) else {}
    per_scene = action_id == "recover" or use_limit.get("recharge") == "per_scene"
    per_encounter = use_limit.get("recharge") == "per_encounter"
    if per_scene and int(scene_uses.get(action_id, 0) or 0) >= int(use_limit.get("max", 1) or 1):
        raise ActionTransactionError("scene_use_spent", "Recover and other per-scene actions can be used only once per scene.", 409)
    if (per_encounter and
            int(encounter_uses.get(action_id, 0) or 0) >=
            int(use_limit.get("max", 1) or 1)):
        raise ActionTransactionError(
            "encounter_use_spent",
            "This action can be used only once per encounter.", 409)

    cost = ((row.get("activation") or {}).get("cost") or {})
    cost_type = cost.get("type", "free")
    try:
        cost_value = max(0, int(cost.get("value", 0) or 0))
    except (TypeError, ValueError):
        raise ActionTransactionError("invalid_action", "Action cost is invalid.") from None
    costs = {}
    if cost_type == "action":
        available = min(int(turn.get("actions_remaining", permissions["actions"]) or 0),
                        permissions["actions"])
        if available < cost_value:
            raise ActionTransactionError("insufficient_actions", "The actor does not have enough actions.", 409)
        turn["actions_remaining"] = available - cost_value
        costs["actions"] = cost_value
    elif cost_type == "reaction":
        available = bool(turn.get("reaction_available", True)) and permissions["reaction"]
        if not available:
            raise ActionTransactionError("reaction_unavailable", "The actor cannot use a reaction.", 409)
        turn["reaction_available"] = False
        costs["reaction"] = 1

    for consumption in (row.get("activation") or {}).get("consume", []):
        if not isinstance(consumption, Mapping) or consumption.get("type") != "resource":
            continue
        resource = str(consumption.get("resource") or "")
        amount = max(0, int(consumption.get("amount", 0) or 0))
        current, location = _resource_value(state, resource)
        try:
            current = int(current or 0)
        except (TypeError, ValueError):
            current = 0
        if current < amount:
            raise ActionTransactionError("insufficient_resource",
                                         f"The actor does not have enough {resource}.", 409)
        _set_resource(state, resource, current - amount, location)
        costs[resource] = costs.get(resource, 0) + amount

    if not row.get("allow_multiple"):
        used.append(named_key)
    turn["named_actions_used"] = used
    state["turn"] = turn
    if per_scene:
        scene_uses[action_id] = int(scene_uses.get(action_id, 0) or 0) + 1
        scene["uses"] = scene_uses
        state["scene"] = scene
    if per_encounter:
        encounter_uses[action_id] = int(
            encounter_uses.get(action_id, 0) or 0) + 1
        encounter["uses"] = encounter_uses
        state["encounter"] = encounter

    receipt = {
        "request_id": request_id,
        "action_id": action_id,
        "source_id": row.get("source_id"),
        "item_id": row.get("item_id"),
        "name": row.get("name"),
        "costs": costs,
        "test": deepcopy(row.get("test") or {}),
        "damage": deepcopy(row.get("damage") or {}),
        "target": deepcopy(row.get("target") or {}),
        "plot_die": bool(row.get("plot_die")),
        "opportunity": deepcopy(row.get("opportunity")),
        "complication": deepcopy(row.get("complication")),
        "use_limit": deepcopy(row.get("use_limit")),
        "resolved": False,
    }
    receipts = list(state.get("action_receipts") or [])
    receipts.append(deepcopy(receipt))
    state["action_receipts"] = receipts[-100:]
    return state, receipt
