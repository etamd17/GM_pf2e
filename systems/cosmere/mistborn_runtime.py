"""Pure, server-authoritative Mistborn Metallic Arts runtime primitives.

This module deliberately owns no persistence, revisions, HTTP behavior, or UI
state.  It accepts already-authorized build context, returns detached values,
and rejects any operation outside its closed vocabulary.  A later service
adapter can therefore place these transforms inside the shared character CAS
boundary without duplicating rules.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import re

from .combat import TURN_PHASES
from .effects import apply_effect, normalize_effects
from .rules.schema import (
    EffectInstance,
    RulesValidationError,
    entity_identifier,
    from_dict,
    to_dict,
)


class MistbornRuntimeError(ValueError):
    """A stable domain error suitable for translation by an HTTP/CAS adapter."""

    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


VALID_ERAS = frozenset({"era-1", "era-2"})
METALS = frozenset({
    "aluminum", "atium", "bendalloy", "brass", "bronze", "cadmium",
    "chromium", "copper", "duralumin", "electrum", "gold", "iron",
    "nicrosil", "pewter", "steel", "tin", "zinc",
})
ERA_METALS = {
    # Era 1 includes the late-era discoveries represented by the Handbook's
    # Era 1 pack.  Path validation decides which of these a character unlocks.
    "era-1": frozenset({
        "aluminum", "atium", "brass", "bronze", "copper", "duralumin",
        "electrum", "gold", "iron", "pewter", "steel", "tin", "zinc",
    }),
    "era-2": frozenset(METALS - {"atium"}),
}
RARE_VIAL_METALS = frozenset({
    "aluminum", "atium", "bendalloy", "cadmium", "chromium", "duralumin",
    "electrum", "gold", "nicrosil",
})

# Literal Mistborn Handbook table.  Rank 6+ retains the actual rank as its
# spend limit while die and range use the final row.
METALLIC_ART_RANK_TABLE = (
    {"rank": 0, "limit": 1, "die": None, "range_ft": 10},
    {"rank": 1, "limit": 1, "die": "d4", "range_ft": 20},
    {"rank": 2, "limit": 2, "die": "d6", "range_ft": 40},
    {"rank": 3, "limit": 3, "die": "d8", "range_ft": 80},
    {"rank": 4, "limit": 4, "die": "d10", "range_ft": 160},
    {"rank": 5, "limit": 5, "die": "d12", "range_ft": 320},
    {"rank": "6+", "limit": "rank", "die": "d20", "range_ft": 640},
)

ALLOMANCY_OPERATION_KINDS = frozenset({
    "scene_start", "spend", "deplete", "drink_vial", "restore",
})
FERUCHEMY_OPERATION_KINDS = frozenset({
    "store_start", "store_maintain", "store_stop",
    "tap_start", "tap_maintain", "tap_stop", "restore",
})
AUTOMATION_DISPOSITIONS = frozenset({
    "calculated", "tracked", "reference", "gm_adjudicated",
})
SCENE_TYPES = frozenset({"combat", "conversation", "endeavor", "freeplay"})
DURATION_KINDS = frozenset({
    "rank_rounds", "one_round", "until_turn_start", "until_turn_end",
    "scene", "encounter", "day_sustenance", "permanent",
})

_RUNTIME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _error(code, message, status=422):
    raise MistbornRuntimeError(code, message, status)


def _integer(value, label, *, minimum=0, code="invalid_resource"):
    if type(value) is not int or value < minimum:
        _error(code, f"{label} must be an integer of at least {minimum}.")
    return value


def _rank(rank):
    return _integer(rank, "Metallic Art rank", minimum=0, code="invalid_rank")


def metallic_art_scaling(rank):
    """Return a detached literal scaling row for a character skill rank."""
    rank = _rank(rank)
    if rank < 6:
        return deepcopy(METALLIC_ART_RANK_TABLE[rank])
    return {"rank": rank, "limit": rank, "die": "d20", "range_ft": 640}


def governing_attribute(art):
    if art == "allomancy":
        return "willpower"
    if art == "feruchemy":
        return "intellect"
    _error("invalid_art", "Unknown Metallic Art.")


def _attribute(attributes, name):
    if not isinstance(attributes, Mapping):
        _error("invalid_attributes", "Attributes must be an object.")
    return _integer(attributes.get(name), name, code="invalid_attributes")


def allomancy_maximum(attributes):
    return 2 + max(_attribute(attributes, "awareness"),
                   _attribute(attributes, "presence"))


def feruchemy_maximum(rank):
    return 2 + _rank(rank)


def _era(era):
    if not isinstance(era, str) or era not in VALID_ERAS:
        _error("invalid_era", "Mistborn era must be era-1 or era-2.")
    return era


def _metal(metal, *, era=None):
    if not isinstance(metal, str) or metal not in METALS:
        _error("invalid_metal", "Unknown Mistborn metal.")
    if era is not None and metal not in ERA_METALS[_era(era)]:
        _error("metal_wrong_era", f"{metal} is unavailable in {era}.")
    return metal


def _metals(values, *, era, label):
    if not isinstance(values, (list, tuple, set, frozenset)):
        _error("invalid_metal_access", f"{label} must be a list of metals.")
    result = []
    for value in values:
        metal = _metal(value, era=era)
        if metal in result:
            _error("invalid_metal_access", f"{label} contains a duplicate metal.")
        result.append(metal)
    return tuple(sorted(result))


def _json_copy(value, label):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        _error("invalid_state", f"{label} must contain normalized JSON.")


def _amount(value, *, maximum=None):
    value = _integer(value, "amount", minimum=1)
    if maximum is not None and value > maximum:
        _error("limit_exceeded", "The amount exceeds the Metallic Art limit.", 409)
    return value


def _vials(value, *, era):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _error("invalid_vial_inventory", "Vial inventory must be an object.")
    result = {}
    for vial_id, raw in sorted(value.items()):
        if not isinstance(vial_id, str) or _RUNTIME_ID.fullmatch(vial_id) is None:
            _error("invalid_vial_inventory", "Vial inventory has an invalid identity.")
        if not isinstance(raw, Mapping):
            _error("invalid_vial_inventory", "Vial inventory entry must be an object.")
        extra = set(raw) - {"count", "metals"}
        if extra:
            _error("invalid_vial_inventory", "Vial inventory has unknown fields.")
        count = _integer(raw.get("count", 0), "vial count")
        metals = _metals(raw.get("metals", ()), era=era, label="vial metals")
        if not metals:
            _error("invalid_vial_inventory", "A vial must identify at least one metal.")
        result[vial_id] = {"count": count, "metals": list(metals)}
    return result


def _rare_access(value, *, era):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _error("invalid_rare_access", "Rare metal access must be an object.")
    result = {}
    for raw_metal, allowed in sorted(value.items()):
        metal = _metal(raw_metal, era=era)
        if metal not in RARE_VIAL_METALS or type(allowed) is not bool:
            _error("invalid_rare_access", "Rare metal access is invalid.")
        result[metal] = allowed
    return result


def _residual(value, accessible):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        _error("invalid_state", "Residual Investiture must be an object.")
    result = {}
    for raw_metal, amount in sorted(value.items()):
        metal = _metal(raw_metal)
        amount = _integer(amount, "residual Investiture")
        if metal in accessible and amount:
            result[metal] = amount
    return result


def _filter_effects(values, accessible, *, field):
    try:
        rows = normalize_effects(values or [])
    except RulesValidationError as exc:
        _error("invalid_effect", f"Invalid {field}: {exc}")
    result = []
    for row in rows:
        metal = (row.get("parameters") or {}).get("metal")
        if metal is None or metal in accessible:
            result.append(row)
    return result


def normalize_allomancy_state(
        value, *, attributes, rank, era, accessible_metals,
        prior_accessible_metals=()):
    """Normalize Allomancy from authoritative build and transition context."""
    if not isinstance(value, Mapping):
        _error("invalid_state", "Allomancy state must be an object.")
    era = _era(era)
    rank = _rank(rank)
    accessible = _metals(accessible_metals, era=era,
                         label="accessible Allomantic metals")
    prior = _metals(prior_accessible_metals, era=era,
                    label="prior accessible Allomantic metals")
    # Computing removed explicitly makes transition cleanup intentional; the
    # authoritative current set still cleans malformed stale state as well.
    removed = set(prior) - set(accessible)
    maximum = allomancy_maximum(attributes)
    current = value["current_investiture"] if "current_investiture" in value else maximum
    current = _integer(current, "current Investiture")
    current = min(current, maximum)

    raw_depleted = value.get("depleted_metals", [])
    if not isinstance(raw_depleted, (list, tuple, set, frozenset)):
        _error("invalid_state", "Depleted metals must be a list.")
    depleted = set()
    for raw_metal in raw_depleted:
        metal = _metal(raw_metal)
        if metal in accessible and metal not in removed:
            depleted.add(metal)

    active = _filter_effects(value.get("active_burns", []), set(accessible),
                             field="active Allomantic effects")
    return {
        "governing_attribute": "willpower",
        "rank": rank,
        "era": era,
        "accessible_metals": list(accessible),
        "current_investiture": current,
        "max_investiture": maximum,
        "depleted_metals": sorted(depleted),
        "residual_by_power": _residual(value.get("residual_by_power"), set(accessible)),
        "vial_inventory": _vials(value.get("vial_inventory"), era=era),
        "rare_metal_access": _rare_access(value.get("rare_metal_access"), era=era),
        "active_burns": active,
    }


def _operation(operation, kinds, allowed_fields):
    if not isinstance(operation, Mapping):
        _error("invalid_operation", "Mistborn operation must be an object.")
    kind = operation.get("kind")
    if not isinstance(kind, str) or kind not in kinds:
        _error("invalid_operation", "Unknown Mistborn operation.")
    extras = set(operation) - allowed_fields[kind]
    if extras:
        _error("invalid_operation_fields", "Mistborn operation has unknown fields.")
    return kind


_ALLOMANCY_FIELDS = {
    "scene_start": {"kind", "surprised"},
    "spend": {"kind", "metal", "amount"},
    "deplete": {"kind", "metal"},
    "drink_vial": {"kind", "vial_id"},
    "restore": {"kind", "amount"},
}


def _available_metal(state, raw_metal):
    metal = _metal(raw_metal, era=state.get("era"))
    if metal not in state.get("accessible_metals", ()):
        _error("metal_unavailable", "The actor cannot access that metal.", 409)
    return metal


def apply_allomancy_cost(state, raw_metal, *, mode="selected", amount=None):
    """Apply one server-derived catalog cost after authorizing its metal.

    ``mode`` is compiled from the immutable RuleRecord; it is never accepted
    from an action request.  This keeps exceptional zero/all-remaining costs
    out of the client operation vocabulary while sharing the same availability,
    Depleted, rare-access, and rank-limit checks as ordinary spending.
    """
    if not isinstance(state, Mapping):
        _error("invalid_state", "Allomancy state must be an object.")
    if mode not in {"selected", "none", "all_remaining"}:
        _error("invalid_operation", "Unknown Allomantic resource cost.")
    result = deepcopy(dict(state))
    metal = _available_metal(result, raw_metal)
    if (metal in RARE_VIAL_METALS
            and (result.get("rare_metal_access") or {}).get(metal) is not True):
        _error("rare_metal_unavailable",
               "The actor lacks access to that rare metal.", 409)
    if metal in set(result.get("depleted_metals", ())):
        _error("metal_depleted", "That Allomantic power is Depleted.", 409)
    current = _integer(result.get("current_investiture"), "current Investiture")
    if mode == "none":
        if current < 1:
            _error("insufficient_investiture", "Not enough Investiture.", 409)
        cost = 0
    elif mode == "all_remaining":
        if current < 1:
            _error("insufficient_investiture", "Not enough Investiture.", 409)
        cost = current
    else:
        limit = metallic_art_scaling(result.get("rank"))["limit"]
        cost = _amount(amount, maximum=limit)
        if current < cost:
            _error("insufficient_investiture", "Not enough Investiture.", 409)
    result["current_investiture"] = current - cost
    return result


def apply_allomancy_operation(state, operation):
    """Apply one deterministic operation to normalized Allomancy state."""
    if not isinstance(state, Mapping):
        _error("invalid_state", "Allomancy state must be an object.")
    kind = _operation(operation, ALLOMANCY_OPERATION_KINDS,
                      _ALLOMANCY_FIELDS)
    result = deepcopy(dict(state))
    maximum = _integer(result.get("max_investiture"), "maximum Investiture")
    current = _integer(result.get("current_investiture"), "current Investiture")

    if kind == "scene_start":
        surprised = operation.get("surprised", False)
        if type(surprised) is not bool:
            _error("invalid_operation", "Surprised must be true or false.")
        result["current_investiture"] = min(maximum, 1) if surprised else maximum
        return result

    if kind == "restore":
        if "amount" not in operation:
            result["current_investiture"] = maximum
        else:
            amount = _amount(operation["amount"])
            result["current_investiture"] = min(maximum, current + amount)
        return result

    if kind == "drink_vial":
        vial_id = operation.get("vial_id")
        inventory = result.get("vial_inventory")
        if not isinstance(vial_id, str) or not isinstance(inventory, Mapping) \
                or vial_id not in inventory:
            _error("vial_unavailable", "That vial is unavailable.", 409)
        vial = inventory[vial_id]
        if int(vial.get("count", 0) or 0) <= 0:
            _error("vial_empty", "That vial has no remaining uses.", 409)
        accessible = set(result.get("accessible_metals", ()))
        contained = accessible & set(vial.get("metals", ()))
        allowed = set()
        blocked_rare = set()
        rare_access = result.get("rare_metal_access") or {}
        for metal in contained:
            if metal in RARE_VIAL_METALS and rare_access.get(metal) is not True:
                blocked_rare.add(metal)
            else:
                allowed.add(metal)
        if not allowed:
            if blocked_rare:
                _error("rare_metal_unavailable",
                       "The actor lacks access to the vial's rare metal.", 409)
            _error("vial_no_usable_metal",
                   "The vial contains no accessible Allomantic metal.", 409)
        copied_inventory = deepcopy(dict(inventory))
        copied_inventory[vial_id] = deepcopy(dict(vial))
        copied_inventory[vial_id]["count"] -= 1
        result["vial_inventory"] = copied_inventory
        result["current_investiture"] = maximum
        # Drinking replaces the actor's currently available flakes: contained
        # usable powers refresh, while other accessible powers become Depleted.
        result["depleted_metals"] = sorted(accessible - allowed)
        return result

    metal = _available_metal(result, operation.get("metal"))
    depleted = set(result.get("depleted_metals", ()))
    if kind == "deplete":
        depleted.add(metal)
        result["depleted_metals"] = sorted(depleted)
        return result

    return apply_allomancy_cost(
        result, metal, mode="selected", amount=operation.get("amount"))


def _linked_metadata(value, *, maximum, truncate=False):
    if value is None:
        return []
    if not isinstance(value, list):
        _error("invalid_linked_metadata", "Linked charge metadata must be a list.")
    rows = []
    identities = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            _error("invalid_linked_metadata", "Linked charge metadata is invalid.")
        row = _json_copy(dict(raw), "linked charge metadata")
        identity = row.get("id")
        if not isinstance(identity, str) or _RUNTIME_ID.fullmatch(identity) is None \
                or identity in identities:
            _error("invalid_linked_metadata", "Linked charge identity is invalid.")
        identities.add(identity)
        rows.append(row)
    if len(rows) > maximum:
        if truncate:
            return rows[:maximum]
        _error("invalid_linked_metadata",
               "Linked charge metadata exceeds the available charges.")
    return rows


def _feruchemy_pool(raw, *, rank, metal, accessible):
    raw = raw if isinstance(raw, Mapping) else {}
    maximum = feruchemy_maximum(rank)
    current = raw.get("charge_current", 0)
    if type(current) is not int:
        _error("invalid_resource", "Feruchemical charges must be an integer.")
    current = max(0, min(maximum, current))
    mode = raw.get("mode", "idle")
    if not isinstance(mode, str) or mode not in {"idle", "storing", "tapping"}:
        _error("invalid_mode", "Feruchemical mode is invalid.")
    effects = _filter_effects(raw.get("active_effects", []), accessible,
                              field=f"active {metal} effects")
    return {
        "charge_current": current,
        "charge_max": maximum,
        "mode": mode,
        "active_effects": effects,
        "linked_charge_metadata": _linked_metadata(
            raw.get("linked_charge_metadata"), maximum=current, truncate=True),
    }


def normalize_feruchemy_state(
        value, *, rank, era, accessible_metals,
        prior_accessible_metals=()):
    """Normalize independent metalmind pools and remove stale path resources."""
    if not isinstance(value, Mapping):
        _error("invalid_state", "Feruchemy state must be an object.")
    era = _era(era)
    rank = _rank(rank)
    accessible = _metals(accessible_metals, era=era,
                         label="accessible Feruchemical metals")
    prior = _metals(prior_accessible_metals, era=era,
                    label="prior accessible Feruchemical metals")
    removed = set(prior) - set(accessible)
    raw_pools = value.get("by_metal", {})
    if not isinstance(raw_pools, Mapping):
        _error("invalid_state", "Feruchemy pools must be an object.")
    for raw_metal in raw_pools:
        _metal(raw_metal)
    pools = {}
    for metal in accessible:
        if metal in removed:
            continue
        pools[metal] = _feruchemy_pool(
            raw_pools.get(metal), rank=rank, metal=metal,
            accessible=set(accessible))
    return {
        "governing_attribute": "intellect",
        "rank": rank,
        "era": era,
        "accessible_metals": list(accessible),
        "by_metal": pools,
    }


_FERUCHEMY_FIELDS = {
    "store_start": {"kind", "metal", "effect"},
    "store_maintain": {"kind", "metal", "amount", "linked_metadata"},
    "store_stop": {"kind", "metal"},
    "tap_start": {"kind", "metal", "amount", "linked_id", "effect"},
    "tap_maintain": {"kind", "metal", "amount", "linked_id"},
    "tap_stop": {"kind", "metal"},
    "restore": {"kind", "metal", "amount"},
}


def _feruchemy_metal(state, raw_metal):
    metal = _available_metal(state, raw_metal)
    pools = state.get("by_metal")
    if not isinstance(pools, Mapping) or metal not in pools:
        _error("metal_unavailable", "That metalmind is unavailable.", 409)
    return metal


def _remove_mode_effects(effects, mode):
    return [row for row in effects
            if (row.get("parameters") or {}).get("feruchemy_mode") != mode]


def _operation_effect(raw, *, metal, mode):
    if raw is None:
        return None
    try:
        row = to_dict(from_dict(EffectInstance, raw))
    except (RulesValidationError, TypeError) as exc:
        _error("invalid_effect", f"Invalid Feruchemical effect: {exc}")
    parameters = row.get("parameters") or {}
    if parameters.get("metal") != metal or parameters.get("feruchemy_mode") != mode:
        _error("invalid_effect", "Feruchemical effect does not match its operation.")
    return row


def _apply_mode_effect(effects, effect):
    if effect is None:
        return normalize_effects(effects)
    try:
        return apply_effect(effects, effect)
    except RulesValidationError as exc:
        _error("effect_conflict", f"Feruchemical effect conflicts with active state: {exc}", 409)


def _spend_linked(pool, linked_id, amount):
    metadata = deepcopy(pool.get("linked_charge_metadata") or [])
    if linked_id is not None:
        if amount != 1:
            _error("invalid_linked_metadata",
                   "A linked charge operation spends exactly one charge.")
        index = next((i for i, row in enumerate(metadata)
                      if row.get("id") == linked_id), None)
        if index is None:
            _error("linked_charge_unavailable", "That linked charge is unavailable.", 409)
        metadata.pop(index)
    elif int(pool.get("charge_current", 0)) - len(metadata) < amount:
        _error("linked_charge_required",
               "The remaining charges require an explicit linked identity.", 409)
    pool["linked_charge_metadata"] = metadata


def apply_feruchemy_operation(state, operation):
    """Apply one Store/Tap/restore operation to normalized Feruchemy state."""
    if not isinstance(state, Mapping):
        _error("invalid_state", "Feruchemy state must be an object.")
    kind = _operation(operation, FERUCHEMY_OPERATION_KINDS,
                      _FERUCHEMY_FIELDS)
    result = deepcopy(dict(state))
    metal = _feruchemy_metal(result, operation.get("metal"))
    result["by_metal"] = deepcopy(dict(result["by_metal"]))
    pool = deepcopy(dict(result["by_metal"][metal]))
    effects = list(pool.get("active_effects") or [])
    limit = metallic_art_scaling(result.get("rank"))["limit"]

    if kind == "restore":
        amount = _amount(operation.get("amount"))
        pool["charge_current"] = min(pool["charge_max"],
                                     pool["charge_current"] + amount)
    elif kind == "store_start":
        effects = _remove_mode_effects(effects, "tapping")
        effect = _operation_effect(operation.get("effect"), metal=metal,
                                   mode="storing")
        effects = _apply_mode_effect(effects, effect)
        pool["mode"] = "storing"
        pool["active_effects"] = effects
    elif kind == "store_maintain":
        if pool.get("mode") != "storing":
            _error("wrong_mode", "The metalmind is not currently storing.", 409)
        amount = _amount(operation.get("amount"), maximum=limit)
        credited = min(amount, pool["charge_max"] - pool["charge_current"])
        pool["charge_current"] += credited
        incoming = operation.get("linked_metadata", [])
        incoming = _linked_metadata(incoming, maximum=credited)
        if len(incoming) > credited:
            _error("invalid_linked_metadata",
                   "More linked records were supplied than charges stored.")
        existing = list(pool.get("linked_charge_metadata") or [])
        duplicate = {row["id"] for row in existing} & {row["id"] for row in incoming}
        if duplicate:
            _error("invalid_linked_metadata", "Linked charge identity already exists.")
        pool["linked_charge_metadata"] = existing + incoming
    elif kind == "store_stop":
        if pool.get("mode") != "storing":
            _error("wrong_mode", "The metalmind is not currently storing.", 409)
        effects = _remove_mode_effects(effects, "storing")
        pool["mode"] = "idle"
        pool["active_effects"] = effects
    elif kind in {"tap_start", "tap_maintain"}:
        if kind == "tap_maintain" and pool.get("mode") != "tapping":
            _error("wrong_mode", "The metalmind is not currently tapping.", 409)
        amount = _amount(operation.get("amount"), maximum=limit)
        if pool["charge_current"] < amount:
            _error("insufficient_charges", "Not enough metalmind charges.", 409)
        _spend_linked(pool, operation.get("linked_id"), amount)
        pool["charge_current"] -= amount
        if kind == "tap_start":
            effects = _remove_mode_effects(effects, "storing")
            effect = _operation_effect(operation.get("effect"), metal=metal,
                                       mode="tapping")
            effects = _apply_mode_effect(effects, effect)
            pool["active_effects"] = effects
            pool["mode"] = "tapping"
    elif kind == "tap_stop":
        if pool.get("mode") != "tapping":
            _error("wrong_mode", "The metalmind is not currently tapping.", 409)
        effects = _remove_mode_effects(effects, "tapping")
        pool["mode"] = "idle"
        pool["active_effects"] = effects

    result["by_metal"][metal] = pool
    return result


def normalize_mistborn_runtime(
        value, *, attributes, allomancy_rank, feruchemy_rank, era,
        accessible_allomantic_metals, accessible_feruchemical_metals,
        prior_accessible_allomantic_metals=(),
        prior_accessible_feruchemical_metals=()):
    """Normalize both Arts using current and prior authoritative build context."""
    if not isinstance(value, Mapping):
        _error("invalid_state", "Mistborn runtime state must be an object.")
    return {
        "allomancy": normalize_allomancy_state(
            value.get("allomancy", {}), attributes=attributes,
            rank=allomancy_rank, era=era,
            accessible_metals=accessible_allomantic_metals,
            prior_accessible_metals=prior_accessible_allomantic_metals),
        "feruchemy": normalize_feruchemy_state(
            value.get("feruchemy", {}), rank=feruchemy_rank, era=era,
            accessible_metals=accessible_feruchemical_metals,
            prior_accessible_metals=prior_accessible_feruchemical_metals),
    }


def reconcile_mistborn_effect_mirrors(runtime_state):
    """Project canonical active effects into their resource-specific mirrors."""
    if not isinstance(runtime_state, Mapping):
        _error("invalid_state", "Mistborn runtime state must be an object.")
    result = deepcopy(dict(runtime_state))
    mistborn = result.get("mistborn")
    if not isinstance(mistborn, Mapping):
        return result
    mistborn = deepcopy(dict(mistborn))
    canonical = {
        row["id"]: row for row in normalize_effects(result.get("effects") or [])
    }

    def project(values):
        return [deepcopy(canonical[row["id"]])
                for row in normalize_effects(values or [])
                if row["id"] in canonical]

    allomancy = mistborn.get("allomancy")
    if isinstance(allomancy, Mapping):
        allomancy = deepcopy(dict(allomancy))
        allomancy["active_burns"] = project(allomancy.get("active_burns"))
        mistborn["allomancy"] = allomancy

    feruchemy = mistborn.get("feruchemy")
    if isinstance(feruchemy, Mapping):
        feruchemy = deepcopy(dict(feruchemy))
        pools = feruchemy.get("by_metal")
        if isinstance(pools, Mapping):
            projected_pools = {}
            for metal, raw_pool in pools.items():
                pool = deepcopy(dict(raw_pool)) if isinstance(raw_pool, Mapping) else {}
                pool["active_effects"] = project(pool.get("active_effects"))
                projected_pools[metal] = pool
            feruchemy["by_metal"] = projected_pools
        mistborn["feruchemy"] = feruchemy

    result["mistborn"] = mistborn
    return result


def project_active_effect_modifiers(effects):
    """Flatten typed active effect operations for roll/tracker consumers."""
    projected = []
    for effect in normalize_effects(effects or []):
        raw_operations = (effect.get("parameters") or {}).get(
            "applied_effects", [])
        if not isinstance(raw_operations, list):
            _error("invalid_effect", "Applied effect operations must be a list.")
        for raw in raw_operations:
            if not isinstance(raw, Mapping):
                _error("invalid_effect", "Applied effect operation is invalid.")
            row = deepcopy(dict(raw))
            row.update({
                "effect_id": effect["id"],
                "source_id": effect["source_id"],
                "target_id": effect["target_id"],
                "stacking_identity": row.get(
                    "stacking_identity", effect["stacking_identity"]),
                "duration_unit": effect["duration_unit"],
                "remaining_duration": effect["remaining_duration"],
                "trigger": effect["trigger"],
                "expiry_state": deepcopy(effect.get("expiry_state") or {}),
            })
            projected.append(row)
    return projected


def materialize_active_effect(
        *, instance_id, source_id, target_id, art, metal, scene_type,
        duration, rank, automation_disposition="calculated", contextual=False,
        parameters=None, selected_attribute=None, refresh_policy="replace",
        actor_id=None, phase=None, round_number=None):
    """Compile a scene-aware duration into the shared EffectInstance contract.

    The function only materializes timing and provenance.  It never invents a
    mechanical outcome for a contextual effect; such an effect must retain a
    non-calculated disposition for downstream GM handling.
    """
    if not isinstance(scene_type, str) or scene_type not in SCENE_TYPES:
        _error("invalid_scene_type", "Unknown Mistborn scene type.")
    if not isinstance(duration, str) or duration not in DURATION_KINDS:
        _error("invalid_duration", "Unknown Mistborn duration.")
    rank = _rank(rank)
    _metal(metal)
    if not isinstance(art, str) or art not in {"allomancy", "feruchemy"}:
        _error("invalid_art", "Unknown Metallic Art.")
    if (not isinstance(automation_disposition, str)
            or automation_disposition not in AUTOMATION_DISPOSITIONS):
        _error("invalid_automation", "Unknown automation disposition.")
    if type(contextual) is not bool:
        _error("invalid_automation", "Contextual flag must be true or false.")
    if contextual and automation_disposition == "calculated":
        _error("invalid_automation",
               "Contextual Mistborn effects cannot be calculated automatically.")

    if duration in {"rank_rounds", "one_round"}:
        rounds = max(1, rank) if duration == "rank_rounds" else 1
        if scene_type == "combat":
            if (not isinstance(phase, str) or phase not in TURN_PHASES
                    or type(round_number) is not int or round_number < 1):
                _error(
                    "missing_timing_context",
                    "Combat round durations require the current phase and round.")
            unit, remaining, trigger = "round", rounds, "phase_start"
        elif scene_type in {"conversation", "endeavor"}:
            try:
                entity_identifier(actor_id, "contributing actor ID")
            except RulesValidationError:
                _error(
                    "missing_timing_context",
                    "Contribution durations require the contributing actor identity.")
            unit, remaining, trigger = "contribution", rounds, "contribution_end"
        else:
            unit, remaining, trigger = "minute", rounds * 10, "manual"
    elif duration == "until_turn_start":
        unit, remaining, trigger = "turn", 1, "turn_start"
    elif duration == "until_turn_end":
        unit, remaining, trigger = "turn", 1, "turn_end"
    elif duration == "scene":
        unit, remaining, trigger = "scene", 1, "scene_end"
    elif duration == "encounter":
        unit, remaining, trigger = "encounter", 1, "encounter_end"
    elif duration == "day_sustenance":
        unit, remaining, trigger = "hour", 24, "manual"
    else:
        unit, remaining, trigger = "permanent", None, "manual"

    expiry_state = {}
    if trigger == "phase_start":
        expiry_state.update({"phase": phase,
                             "not_before_round": round_number + 1})
    if trigger == "contribution_end":
        expiry_state["actor_id"] = actor_id
    if trigger in {"turn_start", "turn_end"}:
        expiry_state["actor_id"] = actor_id or target_id
    if phase is not None and trigger != "phase_start":
        expiry_state["phase"] = phase
    effect_parameters = _json_copy(dict(parameters or {}), "effect parameters")
    effect_parameters.update({
        "art": art,
        "metal": metal,
        "scene_type": scene_type,
        "automation_disposition": automation_disposition,
        "contextual": contextual,
    })
    raw = {
        "id": instance_id,
        "source_id": source_id,
        "target_id": target_id,
        "parameters": effect_parameters,
        "selected_attribute": selected_attribute,
        "stacking_identity": f"mistborn.{art}.{metal}",
        "duration_unit": unit,
        "remaining_duration": remaining,
        "trigger": trigger,
        "refresh_policy": refresh_policy,
        "expiry_state": expiry_state,
        "expired": False,
    }
    try:
        return to_dict(from_dict(EffectInstance, raw))
    except RulesValidationError as exc:
        _error("invalid_effect", f"Invalid Mistborn effect: {exc}")
