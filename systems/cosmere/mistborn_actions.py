"""Server-owned Mistborn action compilation for the shared character CAS.

Clients select an exact catalog action and provide only the variable values that
the operation needs.  Art, metal, era, actor scope, mechanics, and package
identity are derived from the persisted character plus its immutable binding.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from hashlib import sha256
from math import ceil
import re

from .actions import ActionTransactionError, apply_action_transaction
from .mistborn_runtime import (MistbornRuntimeError, apply_allomancy_cost,
                               materialize_active_effect,
                               metallic_art_scaling)
from .rules.schema import EffectInstance, RulesValidationError, from_dict, to_dict


_METALLIC_ACTION = re.compile(
    r"^mistborn\.(allomancy|feruchemy)\.([a-z0-9-]+)\.action\.([a-z0-9-]+)$")
_REQUEST_FIELDS = frozenset({"expected_revision", "request_id", "operation"})
_ALLOMANCY_FIELDS = frozenset({"kind", "amount"})
_VIAL_FIELDS = frozenset({"kind", "vial_id"})
_FERUCHEMY_FIELDS = frozenset({"kind", "amount", "linked_id"})
_DURATION_KINDS = {
    "1_round": "one_round",
    "allomancy_rank_rounds": "rank_rounds",
    "feruchemy_rank_rounds": "rank_rounds",
    "1_scene": "scene",
    "1_scene_per_charge": "scene",
    "instant_with_1_day_sustenance": "day_sustenance",
}
_COSTS = {
    "one": {"type": "action", "value": 1},
    "two": {"type": "action", "value": 2},
    "reaction": {"type": "reaction", "value": 1},
    "special": {"type": "free", "value": 0},
    None: {"type": "free", "value": 0},
}


class MistbornActionError(ActionTransactionError):
    """Stable API error for catalog resolution and operation compilation."""


def is_mistborn_actor(document):
    """Return whether persisted mechanical scope, not theme, is Scadrian."""
    if not isinstance(document, Mapping):
        return False
    build = document.get("build") if isinstance(document.get("build"), Mapping) else {}
    return document.get("origin_world", build.get("origin_world")) == "scadrial"


def _error(code, message, status=422):
    raise MistbornActionError(code, message, status)


def _catalog_action(profile, action_id):
    catalog = profile.catalog()
    actions = (catalog.get("catalogs") or {}).get("action") or []
    if isinstance(actions, Mapping):
        actions = actions.values()
    record = next((deepcopy(dict(row)) for row in actions
                   if isinstance(row, Mapping) and row.get("id") == action_id), None)
    if record is None:
        _error("action_not_found", "Unknown Mistborn action.", 404)
    return record


def _trusted_build(profile, document):
    raw = document.get("build") if isinstance(document.get("build"), Mapping) else document
    raw = dict(raw or {})
    # This function is called only with a server-loaded character document.
    # Reusing its normalized entitlement ledger is therefore not client trust.
    trusted = tuple(raw.get("goal_entitlements") or ())
    trusted_bonus = raw.get("advancement_bonus_grants") or {}
    try:
        return profile.create_build(
            raw,
            trusted_goal_entitlements=trusted,
            trusted_advancement_bonus_grants=trusted_bonus,
        )
    except TypeError:
        return profile.create_build(raw)


def _operation(payload, *, art, action_name, metal):
    raw = payload.get("operation")
    if not isinstance(raw, Mapping):
        _error("invalid_operation", "Mistborn action requires an operation object.")
    operation = deepcopy(dict(raw))

    if art == "allomancy":
        allowed = _ALLOMANCY_FIELDS
        expected = {"spend"}
    elif art == "vial":
        allowed = _VIAL_FIELDS
        expected = {"drink_vial"}
    elif action_name.startswith("store-"):
        allowed = _FERUCHEMY_FIELDS
        expected = {"store_start", "store_maintain", "store_stop"}
    elif action_name.startswith("tap-"):
        allowed = _FERUCHEMY_FIELDS
        expected = {"tap_start", "tap_maintain", "tap_stop"}
    else:
        _error("action_not_available",
               "This Mistborn action has no deterministic actor operation.", 409)

    if set(operation) - allowed:
        _error("mistborn_action_spoof",
               "Mistborn operation includes server-owned or unknown fields.")
    if operation.get("kind") not in expected:
        _error("invalid_operation",
               "The selected operation does not belong to this catalog action.")
    if metal is not None:
        operation["metal"] = metal
    return operation


def _package_pins(binding):
    return [to_dict(binding.core_package),
            *[to_dict(pin) for pin in binding.enabled_packs]]


def _package_identity(value):
    value = value if isinstance(value, Mapping) else {}
    return (value.get("id"), value.get("version"), value.get("package_hash"))


def _server_context(document, runtime_state, timing=None):
    """Return action context derived only from persisted/live server state."""
    timing = dict(timing or {})
    runtime = runtime_state if isinstance(runtime_state, Mapping) else {}
    scene = runtime.get("scene") if isinstance(runtime.get("scene"), Mapping) else {}
    actor_id = document.get("id")
    if not isinstance(actor_id, str) or not actor_id:
        _error("invalid_actor", "Mistborn actions require a persisted actor identity.")
    scene_type = timing.get("scene_type", scene.get("type", "freeplay"))
    return {
        "actor_id": actor_id,
        "scene_type": scene_type,
        "phase": timing.get("phase"),
        "round_number": timing.get("round_number"),
    }


def _allomancy_resource_cost(mechanics, operation):
    """Compile immutable resource syntax into one closed cost policy."""
    resources = set(mechanics.get("resources") or [])
    if "cost:0" in resources:
        return {"mode": "none", "minimum_current": 1, "amount": 0}
    if ({"investiture:all_remaining", "investiture:1_then_set_current_to_0"}
            & resources):
        return {"mode": "all_remaining", "minimum_current": 1}
    if any(value == "investiture:1_on_success" for value in resources):
        _error(
            "resolution_context_required",
            "This action spends Investiture only after a resolved success.", 409)
    fixed = next((int(value.split(":", 1)[1]) for value in resources
                  if re.fullmatch(r"investiture:[1-9][0-9]*", value)), None)
    amount = fixed if fixed is not None else operation.get("amount")
    return {"mode": "selected", "amount": amount}


_NUMERIC_EFFECT_STATS = {
    "strength": "strength",
    "speed": "speed",
    "intellect": "intellect",
    "willpower": "willpower",
    "awareness": "awareness",
    "spiritual_defense": "spiritual_defense",
    "count_size": "size_category",
    "reaction_and_free_focus_cost": "reaction_and_free_focus_cost",
    "opportunity_range": "opportunity_range",
    "complication_range": "complication_range",
    "exhausted_penalty": "exhausted_penalty",
}


def _typed_effect(record_id, actor_id, *, kind, stat, amount=None,
                  dice=None, value=None):
    row = {
        "kind": kind,
        "target": "self",
        "stat": stat,
        "source_id": record_id,
        "target_id": actor_id,
        "stacking_identity": f"{record_id}.{stat.replace('_', '-')}",
    }
    if amount is not None:
        row["amount"] = amount
    if dice is not None:
        row["dice"] = dice
    if value is not None:
        row["value"] = value
    return row


def _compile_effect_execution(record, mechanics, operation, *, actor_id, rank):
    """Translate supported deterministic ops and expose all unresolved ones."""
    record_id = record["id"]
    spend = operation.get("amount")
    applied, unresolved = [], []
    raw_effects = mechanics.get("effect_ops") or []
    if isinstance(raw_effects, str) or isinstance(raw_effects, Mapping):
        raw_effects = [raw_effects]
    for raw in raw_effects:
        if not isinstance(raw, str):
            unresolved.append(deepcopy(raw))
            continue
        enhanced = re.fullmatch(
            r"enhanced_(speed|strength|awareness)=ceil\(spend/2\)", raw)
        numeric = re.fullmatch(r"([a-z0-9_]+):([+-][0-9]+)", raw)
        if enhanced and type(spend) is int and spend >= 1:
            stat = enhanced.group(1)
            applied.append(_typed_effect(
                record_id, actor_id, kind="modifier", stat=stat,
                amount=ceil(spend / 2)))
        elif numeric and numeric.group(1) in _NUMERIC_EFFECT_STATS:
            applied.append(_typed_effect(
                record_id, actor_id, kind="modifier",
                stat=_NUMERIC_EFFECT_STATS[numeric.group(1)],
                amount=int(numeric.group(2))))
        elif raw == "ignore_exhausted_test_penalty":
            applied.append(_typed_effect(
                record_id, actor_id, kind="penalty_override",
                stat="exhausted_test_penalty", amount=0))
        elif raw == "fed_and_hydrated_for_1_day":
            applied.append(_typed_effect(
                record_id, actor_id, kind="status", stat="sustenance",
                value={"fed": True, "hydrated": True}))
        elif raw == "nonattack_strength_tests:advantage_1":
            applied.append(_typed_effect(
                record_id, actor_id, kind="advantage",
                stat="nonattack_strength_tests", dice=1))
        elif raw in {"all_senses_supernatural", "no_breathing_required",
                     "free_move:once_per_turn"}:
            applied.append(_typed_effect(
                record_id, actor_id, kind="capability", stat=raw,
                value=True))
        else:
            unresolved.append(raw)
    declared = record.get("automation")
    disposition = declared
    if declared == "calculated" and unresolved:
        disposition = "tracked"
    return {
        "disposition": disposition,
        "declared_disposition": declared,
        "applied_effects": applied,
        "unresolved_effects": unresolved,
        "rank": rank,
    }


def _effect_for_action(*, record, mechanics, document, runtime_state, timing,
                       art, metal, rank, operation, request_id, execution):
    duration = _DURATION_KINDS.get(mechanics.get("duration"))
    if duration is None or metal is None:
        return None
    operation_kind = operation.get("kind")
    if art == "feruchemy" and operation_kind in {"store_stop", "tap_stop"}:
        return None
    context = _server_context(document, runtime_state, timing)
    effect_parameters = {
        "action_id": record["id"],
        "effect_ops": deepcopy(mechanics.get("effect_ops") or []),
        "applied_effects": deepcopy(execution["applied_effects"]),
        "unresolved_effects": deepcopy(execution["unresolved_effects"]),
    }
    if art == "feruchemy":
        effect_parameters["feruchemy_mode"] = (
            "storing" if operation_kind.startswith("store_") else "tapping")
    digest = sha256(
        f"{context['actor_id']}\0{record['id']}\0{request_id}".encode("utf-8")
    ).hexdigest()[:20]
    effect = materialize_active_effect(
        instance_id=f"effect.mistborn.h{digest}",
        source_id=record["id"],
        target_id=context["actor_id"],
        art=art,
        metal=metal,
        scene_type=context["scene_type"],
        duration=duration,
        rank=rank,
        automation_disposition=execution["disposition"],
        contextual=execution["disposition"] != "calculated",
        parameters=effect_parameters,
        refresh_policy="refresh",
        actor_id=context["actor_id"],
        phase=context["phase"],
        round_number=context["round_number"],
    )
    if mechanics.get("duration") == "1_scene_per_charge":
        charge_count = operation.get("amount")
        limit = metallic_art_scaling(rank)["limit"]
        if (type(charge_count) is not int or charge_count < 1
                or charge_count > limit):
            _error("invalid_charge_count",
                   "Scene duration requires a charge count within the Metallic Art limit.")
        effect["remaining_duration"] = charge_count
        parameters = dict(effect["parameters"])
        parameters["charge_count"] = charge_count
        effect["parameters"] = parameters
        try:
            effect = to_dict(from_dict(EffectInstance, effect))
        except RulesValidationError as error:
            _error("invalid_effect", f"Invalid Mistborn effect: {error}")
    return effect


def compile_mistborn_action(profile, document, action_id, payload, *,
                            runtime_state=None, timing=None,
                            materialize_effect=True):
    """Compile a client selection into an actor-scoped, package-bound command."""
    if not isinstance(payload, Mapping):
        _error("invalid_action", "Mistborn action data must be an object.")
    if set(payload) - _REQUEST_FIELDS:
        _error("mistborn_action_spoof",
               "Mistborn action includes server-owned or unknown fields.")
    if not is_mistborn_actor(document):
        _error("action_not_available", "This actor does not use Mistborn rules.", 409)

    record = _catalog_action(profile, str(action_id or ""))
    build = _trusted_build(profile, document)
    match = _METALLIC_ACTION.fullmatch(str(action_id or ""))
    if action_id == "mistborn.action.drink-vial":
        if not build.has_allomancy():
            _error("art_unavailable", "The actor cannot use Allomancy.", 409)
        art, metal, action_name = "vial", None, "drink-vial"
        profile_art = "allomancy"
    elif match is not None:
        art, metal, action_name = match.groups()
        profile_art = art
        has_art = build.has_allomancy() if art == "allomancy" else build.has_feruchemy()
        if not has_art:
            _error("art_unavailable", f"The actor cannot use {art.title()}.", 409)
        if metal not in build.accessible_metals(art):
            _error("metal_unavailable", "The actor cannot access that metal.", 409)
    else:
        # Adversary, crafting, and reference actions may share the same catalog
        # kind, but they are not actor Metallic Art commands.
        _error("action_not_available",
               "This Mistborn action is not available to a player actor.", 409)

    operation = _operation(payload, art=art, action_name=action_name, metal=metal)
    mechanics = deepcopy((record.get("parameters") or {}).get("data") or {})
    if metal is not None and mechanics.get("metal") != metal:
        _error("invalid_catalog_action",
               "Mistborn action identity does not match its catalog metal.")
    raw_cost = mechanics.get("action_cost")
    if raw_cost not in _COSTS:
        _error("unsupported_action_cost",
               "The catalog action has an unsupported action cost.")
    package = deepcopy(record.get("package") or {})
    binding = to_dict(profile.binding)
    pins = _package_pins(profile.binding)
    if _package_identity(package) not in {
            _package_identity(pin) for pin in pins}:
        _error("invalid_catalog_action",
               "Mistborn action is not owned by an exactly pinned package.")
    rank = (build.allomancy_rank if profile_art == "allomancy"
            else build.feruchemy_rank)
    context = _server_context(document, runtime_state, timing)
    resource_cost = (_allomancy_resource_cost(mechanics, operation)
                     if art == "allomancy" else None)
    profile_operation = deepcopy(operation)
    if resource_cost is not None and resource_cost["mode"] == "selected":
        profile_operation["amount"] = resource_cost.get("amount")
    if profile_operation.get("kind") == "store_start":
        # The selected amount controls the catalog's per-charge duration. The
        # runtime Store start operation only enters the mode; later maintenance
        # boundaries credit charges.
        profile_operation.pop("amount", None)
    execution = _compile_effect_execution(
        record, mechanics, operation, actor_id=context["actor_id"], rank=rank)
    try:
        effect = (_effect_for_action(
            record=record, mechanics=mechanics, document=document,
            runtime_state=runtime_state, timing=timing, art=profile_art,
            metal=metal, rank=rank, operation=operation,
            request_id=payload.get("request_id"), execution=execution,
        ) if materialize_effect else None)
    except MistbornRuntimeError as error:
        raise MistbornActionError(error.code, str(error), error.status) from None
    if (effect is not None and profile_art == "feruchemy"
            and operation.get("kind") in {"store_start", "tap_start"}):
        profile_operation["effect"] = deepcopy(effect)
    receipt_metadata = {
        "rules_binding": binding,
        "package": package,
        "package_pins": pins,
        "automation": record.get("automation"),
        "audit_status": record.get("audit_status"),
        "citations": deepcopy(record.get("citations") or []),
        "mechanics": mechanics,
        "mistborn": {
            "actor_id": context["actor_id"],
            "art": profile_art,
            "metal": metal,
            "rank": rank,
            "era": build.era,
            "scene_type": context["scene_type"],
            "operation": deepcopy(operation),
            "resource_cost": deepcopy(resource_cost),
            "execution": deepcopy(execution),
            "effect": deepcopy(effect),
        },
    }
    # Stopping a metalmind is free (user ruling 2026-10-28): it spends no
    # action and neither needs nor uses the action's once-per-turn slot, so a
    # store or tap can end in the same turn it began.
    stopping = art == "feruchemy" and operation.get("kind") in {"store_stop", "tap_stop"}
    action = {
        "id": record["id"],
        "source_id": record["id"],
        "item_id": None,
        "name": record.get("name") or record["id"],
        "kind": "action",
        "activation": {
            "type": "utility",
            "cost": ({"type": "free", "value": 0} if stopping
                     else deepcopy(_COSTS[raw_cost])),
            "consume": [],
        },
        "test": {"requirements": deepcopy(mechanics.get("tests") or [])},
        "damage": {},
        "target": {"range": mechanics.get("range")},
        "plot_die": False,
        "opportunity": None,
        "complication": None,
        "use_limit": None,
        "allow_multiple": stopping,
        "permissions": {},
    }
    return {
        "action": action,
        "profile_action": {"origin_world": "scadrial",
                           "era": build.era,
                           "art": profile_art,
                           "operation": profile_operation},
        "effect": effect,
        "resource_cost": resource_cost,
        "effect_stop": (f"mistborn.{profile_art}.{metal}"
                        if metal is not None and operation.get("kind") in {
                            "store_stop", "tap_stop"} else None),
        "receipt": receipt_metadata,
    }


def apply_mistborn_action_transaction(runtime_state, command, *, profile,
                                       request_id):
    """Apply action economy and its Metallic Art resource change atomically."""
    try:
        state, receipt = apply_action_transaction(
            runtime_state, command["action"], request_id=request_id)
        profile_action = command["profile_action"]
        resource_cost = command.get("resource_cost")
        if (profile_action.get("art") == "allomancy"
                and isinstance(resource_cost, Mapping)
                and resource_cost.get("mode") in {"none", "all_remaining"}):
            state = deepcopy(dict(state))
            mistborn = deepcopy(dict(state.get("mistborn") or {}))
            allomancy = apply_allomancy_cost(
                mistborn.get("allomancy") or {},
                profile_action["operation"].get("metal"),
                mode=resource_cost["mode"])
            mistborn["allomancy"] = allomancy
            state["mistborn"] = mistborn
        else:
            state = profile.apply_action(state, profile_action)
        from .effects import apply_effect, normalize_effects, project_conditions
        if command.get("effect") is not None:
            profile_action = command["profile_action"]
            operation = profile_action["operation"]
            art = profile_action["art"]
            if art == "allomancy":
                mistborn = deepcopy(dict(state.get("mistborn") or {}))
                art_state = deepcopy(dict(mistborn.get("allomancy") or {}))
                art_state["active_burns"] = apply_effect(
                    art_state.get("active_burns") or [], command["effect"])
                mistborn["allomancy"] = art_state
                state["mistborn"] = mistborn
            elif (art == "feruchemy"
                  and operation.get("kind") in {
                      "store_maintain", "tap_maintain"}):
                mistborn = deepcopy(dict(state.get("mistborn") or {}))
                art_state = deepcopy(dict(mistborn.get("feruchemy") or {}))
                pools = deepcopy(dict(art_state.get("by_metal") or {}))
                metal = operation["metal"]
                pool = deepcopy(dict(pools.get(metal) or {}))
                pool["active_effects"] = apply_effect(
                    pool.get("active_effects") or [], command["effect"])
                pools[metal] = pool
                art_state["by_metal"] = pools
                mistborn["feruchemy"] = art_state
                state["mistborn"] = mistborn
            state["effects"] = apply_effect(
                state.get("effects") or [], command["effect"])
            state["conditions"] = project_conditions(state["effects"])
        elif command.get("effect_stop"):
            state["effects"] = [
                row for row in normalize_effects(state.get("effects") or [])
                if row.get("stacking_identity") != command["effect_stop"]
            ]
            state["conditions"] = project_conditions(state["effects"])
    except MistbornActionError:
        raise
    except MistbornRuntimeError as error:
        raise MistbornActionError(error.code, str(error), error.status) from None
    except RulesValidationError as error:
        raise MistbornActionError(
            getattr(error, "code", "invalid_mistborn_action"),
            str(error), getattr(error, "status", 422)) from None

    receipt_metadata = deepcopy(command["receipt"])
    if command.get("effect") is not None:
        stacking_identity = command["effect"].get("stacking_identity")
        canonical_effect = next((
            deepcopy(row) for row in state.get("effects") or []
            if row.get("stacking_identity") == stacking_identity
        ), None)
        receipt_metadata["mistborn"]["effect"] = canonical_effect
    receipt.update(receipt_metadata)
    receipts = list(state.get("action_receipts") or [])
    for index in range(len(receipts) - 1, -1, -1):
        row = receipts[index]
        if isinstance(row, Mapping) and row.get("request_id") == request_id:
            receipts[index] = deepcopy(receipt)
            break
    state["action_receipts"] = receipts
    return state, receipt
