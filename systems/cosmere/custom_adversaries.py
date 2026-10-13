"""Rich, package-bound custom adversaries with legacy tracker adapters."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
import uuid

from systems.cosmere.actions import normalize_action_item
from systems.cosmere.rules.compiler import canonical_json
from systems.cosmere.rules.profiles import normalize_binding
from systems.cosmere.rules.schema import to_dict


ROLES = {"ordinary", "minion", "boss", "companion"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDENTITY = re.compile(r"^(?:[0-9a-f]{32}|[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+)$")


class AdversaryValidationError(ValueError):
    pass


class AdversaryStateError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def _copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise AdversaryValidationError("custom adversary must be normalized JSON") from None


def _binding(value):
    try:
        return to_dict(normalize_binding({"rules_binding": value}))
    except Exception as exc:
        raise AdversaryValidationError("invalid adversary rules binding") from exc


def _binding_hash(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _integer(value, label, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise AdversaryValidationError(f"invalid adversary {label}")
    return value


def _text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise AdversaryValidationError(f"invalid adversary {label}")
    value = value.strip()
    if re.search(r"<\s*/?\s*[A-Za-z][^>]*>", value):
        raise AdversaryValidationError(f"invalid adversary {label}")
    return value


def _resources(value):
    if not isinstance(value, Mapping) or "health" not in value:
        raise AdversaryValidationError("invalid adversary resources")
    result = {}
    for key, row in value.items():
        if not isinstance(key, str) or not key or not isinstance(row, Mapping):
            raise AdversaryValidationError("invalid adversary resource")
        maximum = _integer(row.get("max"), "resource maximum")
        current = _integer(row.get("current", maximum), "resource current")
        if current > maximum:
            raise AdversaryValidationError("invalid adversary resource current")
        result[key] = {"current": current, "max": maximum}
    for key in ("focus", "investiture"):
        result.setdefault(key, {"current": 0, "max": 0})
    return dict(sorted(result.items()))


def _defenses(value):
    if not isinstance(value, Mapping):
        raise AdversaryValidationError("invalid adversary defenses")
    result = {}
    for key in ("physical", "cognitive", "spiritual"):
        result[key] = _integer(value.get(key), f"defense {key}")
    return result


def _simple_list(value, label):
    if not isinstance(value, list) or len(value) > 100:
        raise AdversaryValidationError(f"invalid adversary {label}")
    result = []
    for row in value:
        if isinstance(row, str):
            result.append(_text(row, label, 100))
        elif isinstance(row, Mapping):
            copied = _copy(dict(row))
            if not isinstance(copied.get("name"), str) or not copied["name"].strip():
                raise AdversaryValidationError(f"invalid adversary {label}")
            result.append(copied)
        else:
            raise AdversaryValidationError(f"invalid adversary {label}")
    return result


def _action(value):
    if not isinstance(value, Mapping):
        raise AdversaryValidationError("invalid adversary action")
    if isinstance(value.get("system"), Mapping):
        try:
            value = normalize_action_item(value)
        except Exception as exc:
            raise AdversaryValidationError("invalid adversary action") from exc
    action = _copy(dict(value))
    for key in ("id", "name", "kind", "activation", "test", "damage", "target"):
        if key not in action:
            raise AdversaryValidationError("invalid adversary structured action")
    if action["kind"] not in {"action", "weapon"}:
        raise AdversaryValidationError("invalid adversary action kind")
    activation = action["activation"]
    if not isinstance(activation, dict) or not isinstance(activation.get("cost"), dict):
        raise AdversaryValidationError("invalid adversary action activation")
    _text(activation.get("type"), "action activation type", 80)
    cost = activation["cost"]
    if cost.get("type") not in {"action", "reaction", "free"}:
        raise AdversaryValidationError("invalid adversary action cost")
    _integer(cost.get("value"), "action cost")
    consume = activation.get("consume")
    if not isinstance(consume, list):
        raise AdversaryValidationError("invalid adversary action consumption")
    for row in consume:
        if not isinstance(row, Mapping) or row.get("type") != "resource":
            raise AdversaryValidationError("invalid adversary action consumption")
        if row.get("resource") not in {"health", "focus", "investiture"}:
            raise AdversaryValidationError("invalid adversary action resource")
        _integer(row.get("amount"), "action resource amount")
        for bound in ("minimum", "maximum"):
            if row.get(bound) is not None:
                _integer(row[bound], f"action resource {bound}")
    _text(str(action["id"]), "action ID", 160)
    _text(str(action["name"]), "action name", 160)
    test = action["test"]
    if not isinstance(test, Mapping):
        raise AdversaryValidationError("invalid adversary action test")
    _text(test.get("type"), "action test type", 80)
    for key in ("skill", "attribute"):
        if test.get(key) is not None:
            _text(test[key], f"action test {key}", 80)
    if not isinstance(test.get("modifier", ""), str):
        raise AdversaryValidationError("invalid adversary action test modifier")
    if (action.get("attack_modifier") is not None
            and type(action["attack_modifier"]) is not int):
        raise AdversaryValidationError("invalid adversary action attack modifier")
    damage = action["damage"]
    if not isinstance(damage, Mapping):
        raise AdversaryValidationError("invalid adversary action damage")
    for key in ("formula", "type", "skill", "attribute", "graze_formula"):
        if damage.get(key) is not None:
            _text(damage[key], f"action damage {key}", 160)
    if not isinstance(action["target"], Mapping):
        raise AdversaryValidationError("invalid adversary action target")
    if type(action.get("plot_die")) is not bool:
        raise AdversaryValidationError("invalid adversary action plot die")
    if type(action.get("allow_multiple")) is not bool:
        raise AdversaryValidationError("invalid adversary action multiple-target flag")
    if not isinstance(action.get("permissions"), Mapping):
        raise AdversaryValidationError("invalid adversary action permissions")
    use_limit = action.get("use_limit")
    if use_limit is not None:
        if not isinstance(use_limit, Mapping):
            raise AdversaryValidationError("invalid adversary action use limit")
        _integer(use_limit.get("max"), "action use limit", minimum=1)
        _text(use_limit.get("recharge"), "action recharge", 80)
    for candidate in (action.get("description"), action.get("opportunity"),
                      action.get("complication")):
        if candidate is not None and not isinstance(candidate, str):
            raise AdversaryValidationError("invalid adversary action prose")
        if isinstance(candidate, str) and re.search(r"<\s*/?\s*[A-Za-z][^>]*>", candidate):
            raise AdversaryValidationError("invalid adversary action prose")
    return action


def normalize_adversary(payload, *, binding, adversary_id=None, revision=0):
    if not isinstance(payload, Mapping):
        raise AdversaryValidationError("invalid custom adversary")
    rules_binding = _binding(binding)
    adversary_id = adversary_id or payload.get("id") or payload.get("_id")
    if not isinstance(adversary_id, str) or _IDENTITY.fullmatch(adversary_id) is None:
        raise AdversaryValidationError("invalid adversary identity")
    _integer(revision, "revision")
    role = str(payload.get("role") or "ordinary").strip().lower()
    if role not in ROLES:
        raise AdversaryValidationError("invalid adversary role")
    world = str(payload.get("world") or rules_binding["default_world"])
    allowed_worlds = tuple(rules_binding.get("allowed_worlds") or
                           (rules_binding["default_world"],))
    if world not in ("cosmere", *allowed_worlds):
        raise AdversaryValidationError("adversary world is outside the binding")
    era = payload.get("era")
    allowed_eras = tuple(rules_binding["allowed_eras"])
    if world == "scadrial":
        if era is None and len(allowed_eras) == 1:
            era = allowed_eras[0]
        if era not in allowed_eras:
            raise AdversaryValidationError(
                "adversary era is outside the Scadrial binding")
    elif era is not None:
        raise AdversaryValidationError(
            "only a Scadrial adversary may select an era")
    actions = payload.get("actions", [])
    if not isinstance(actions, list) or len(actions) > 100:
        raise AdversaryValidationError("invalid adversary actions")
    normalized_actions = [_action(row) for row in actions]
    action_ids = [row["id"] for row in normalized_actions]
    if len(set(action_ids)) != len(action_ids):
        raise AdversaryValidationError("duplicate adversary action ID")
    level = _integer(payload.get("level", 1), "level")
    from systems.cosmere.actor import tier_of
    tier = _integer(payload.get("tier", tier_of(max(1, level))), "tier", minimum=1)
    if tier > 5:
        raise AdversaryValidationError("invalid adversary tier")
    size = _text(payload.get("size", "medium"), "size", 40).lower()
    return {
        "id": adversary_id, "revision": revision,
        "rules_binding": rules_binding, "binding_hash": _binding_hash(rules_binding),
        "world": world, "era": era,
        "name": _text(payload.get("name"), "name", 160),
        "role": role, "level": level, "tier": tier, "size": size,
        "defenses": _defenses(payload.get("defenses")),
        "resources": _resources(payload.get("resources")),
        "deflect": _integer(payload.get("deflect", 0), "deflect"),
        "traits": _simple_list(payload.get("traits", []), "traits"),
        "equipment": _simple_list(payload.get("equipment", []), "equipment"),
        "actions": normalized_actions,
    }


def _from_actor_document(document, binding):
    from systems.cosmere.actor import CosmereActor, tier_of

    actor = CosmereActor(document)
    role = str(getattr(actor, "role", "") or "ordinary").strip().lower()
    if role not in ROLES:
        role = "ordinary"
    actions = []
    actor_actions = list(getattr(actor, "actions", []) or [])
    actor_strikes = list(getattr(actor, "strikes", []) or [])
    for row, action_kind in [*((row, "action") for row in actor_actions),
                             *((row, "weapon") for row in actor_strikes)]:
        action = deepcopy(row)
        action["kind"] = action_kind
        if action_kind == "weapon":
            action["damage"] = deepcopy(action.get("damage_record") or {})
            action["attack_modifier"] = int(action.get("mod", action.get("bonus", 0)) or 0)
        actions.append(action)
    raw_items = document.get("items") if isinstance(document, Mapping) else []
    raw_items = raw_items if isinstance(raw_items, list) else []
    traits = []
    equipment = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        item_type = item.get("type")
        if item_type in {"action", "weapon"}:
            continue
        if item_type == "trait":
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                traits.append(name.strip())
            continue
        if isinstance(item.get("name"), str) and item["name"].strip():
            equipment.append(_copy(dict(item)))
    resources = {
        "health": {"current": int(getattr(actor, "current_hp", actor.health_max) or 0),
                   "max": int(actor.health_max or 0)},
        "focus": {"current": int(getattr(actor, "current_focus", actor.focus_max) or 0),
                  "max": int(actor.focus_max or 0)},
        "investiture": {"current": int(getattr(actor, "current_investiture", actor.investiture_max) or 0),
                        "max": int(actor.investiture_max or 0)},
    }
    return normalize_adversary({
        "name": actor.name, "role": role, "level": int(actor.level or 1),
        "tier": int(actor.tier or tier_of(max(1, int(actor.level or 1)))),
        "size": str(actor.size or "medium"),
        "defenses": {"physical": int(actor.defenses.get("phy", 0)),
                     "cognitive": int(actor.defenses.get("cog", 0)),
                     "spiritual": int(actor.defenses.get("spi", 0))},
        "resources": resources, "deflect": int(actor.deflect.get("value", 0) or 0),
        "traits": traits, "equipment": equipment, "actions": actions,
    }, binding=binding, adversary_id=document.get("_id"), revision=0)


def normalize_store(value, *, binding):
    if isinstance(value, list):
        adversaries = [_from_actor_document(row, binding) for row in value
                       if isinstance(row, Mapping)]
        return {"schema_version": 1, "revision": 0,
                "adversaries": adversaries, "receipts": []}
    value = value if isinstance(value, Mapping) else {}
    revision = value.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise AdversaryStateError("invalid_revision", "Invalid adversary store revision.")
    rows = value.get("adversaries", [])
    receipts = value.get("receipts", [])
    if not isinstance(rows, list) or not isinstance(receipts, list):
        raise AdversaryStateError("invalid_state", "Invalid adversary store.")
    normalized = []
    for row in rows:
        if isinstance(row, Mapping) and "rules_binding" in row:
            row_binding = _binding(row["rules_binding"])
            if row_binding != _binding(binding):
                raise AdversaryValidationError("adversary rules binding changed")
            normalized.append(normalize_adversary(
                row, binding=row_binding, adversary_id=row.get("id"),
                revision=row.get("revision", 0)))
        elif isinstance(row, Mapping):
            normalized.append(_from_actor_document(row, binding))
    ids = [row["id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise AdversaryStateError("duplicate_identity", "Duplicate adversary identity.")
    return {"schema_version": 1, "revision": revision,
            "adversaries": normalized, "receipts": _copy(receipts[-100:])}


def _request(value):
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise AdversaryStateError("invalid_request_id", "Invalid adversary request ID.")
    return value


def _prior(state, request_id):
    return next((row for row in reversed(state["receipts"])
                 if row.get("request_id") == request_id), None)


def _record(state, request_id, operation, identity, result, payload_hash=None):
    row = {"request_id": request_id, "operation": operation,
           "adversary_id": identity, "result": deepcopy(result)}
    if payload_hash is not None:
        row["payload_hash"] = payload_hash
    state["receipts"].append(row)
    state["receipts"] = state["receipts"][-100:]


def create_adversary(state, payload, *, binding, expected_store_revision,
                     request_id, id_factory=None):
    request_id = _request(request_id)
    state = normalize_store(state, binding=binding)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    prior = _prior(state, request_id)
    if prior is not None:
        if prior.get("operation") != "create" or prior.get("payload_hash") != digest:
            raise AdversaryStateError("request_id_conflict",
                                      "Adversary request ID belongs to a different create.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    if state["revision"] != expected_store_revision:
        raise AdversaryStateError("stale_revision", "stale adversary store revision", 409)
    identity = (id_factory or (lambda: uuid.uuid4().hex))()
    adversary = normalize_adversary(payload, binding=binding,
                                    adversary_id=identity, revision=0)
    state["adversaries"].append(adversary)
    state["revision"] += 1
    result = {"adversary": deepcopy(adversary), "revision": state["revision"],
              "replayed": False}
    _record(state, request_id, "create", identity, result, digest)
    return state, result


def edit_adversary(state, adversary_id, payload, *, binding, expected_revision,
                   request_id):
    request_id = _request(request_id)
    state = normalize_store(state, binding=binding)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    prior = _prior(state, request_id)
    if prior is not None:
        if (prior.get("operation") != "edit" or prior.get("adversary_id") != adversary_id
                or prior.get("payload_hash") != digest):
            raise AdversaryStateError("request_id_conflict",
                                      "Adversary request ID belongs to a different edit.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    index = next((index for index, row in enumerate(state["adversaries"])
                  if row["id"] == adversary_id), None)
    if index is None:
        raise AdversaryStateError("not_found", "Custom adversary not found.", 404)
    current = state["adversaries"][index]
    if current["revision"] != expected_revision:
        raise AdversaryStateError("stale_revision", "stale adversary revision", 409)
    changed = normalize_adversary(payload, binding=binding,
                                  adversary_id=adversary_id,
                                  revision=expected_revision + 1)
    state["adversaries"][index] = changed
    state["revision"] += 1
    result = {"adversary": deepcopy(changed), "revision": state["revision"],
              "replayed": False}
    _record(state, request_id, "edit", adversary_id, result, digest)
    return state, result


def delete_adversary(state, adversary_id, *, expected_revision, request_id,
                     referenced_ids=()):
    request_id = _request(request_id)
    # The binding is recovered from the stored record only after basic shape
    # validation so callers cannot silently rebind a delete.
    if isinstance(state, Mapping) and state.get("adversaries"):
        first = state["adversaries"][0]
        store_binding = first.get("rules_binding") if isinstance(first, Mapping) else None
    else:
        store_binding = to_dict(normalize_binding())
    state = normalize_store(state, binding=store_binding)
    prior = _prior(state, request_id)
    if prior is not None:
        if prior.get("operation") != "delete" or prior.get("adversary_id") != adversary_id:
            raise AdversaryStateError("request_id_conflict",
                                      "Adversary request ID belongs to a different delete.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    index = next((index for index, row in enumerate(state["adversaries"])
                  if row["id"] == adversary_id), None)
    if index is None:
        raise AdversaryStateError("not_found", "Custom adversary not found.", 404)
    current = state["adversaries"][index]
    if current["revision"] != expected_revision:
        raise AdversaryStateError("stale_revision", "stale adversary revision", 409)
    if adversary_id in set(referenced_ids):
        raise AdversaryStateError("adversary_referenced",
                                  "Custom adversary is referenced by a staged encounter.", 409)
    del state["adversaries"][index]
    state["revision"] += 1
    result = {"id": adversary_id, "revision": state["revision"], "replayed": False}
    _record(state, request_id, "delete", adversary_id, result)
    return state, result


def _action_item(action):
    activation = action.get("activation") or {}
    cost = activation.get("cost") or {"type": "free", "value": 0}
    consume = []
    for row in activation.get("consume") or []:
        amount = int(row.get("amount", 0) or 0)
        minimum = row.get("minimum", amount)
        maximum = row.get("maximum", amount)
        consume.append({"type": row.get("type", "resource"),
                        "resource": row.get("resource"),
                        "amount": amount, "minimum": minimum, "maximum": maximum,
                        "value": {"min": minimum, "max": maximum,
                                  "actual": amount}})
    test = action.get("test") or {}
    damage = action.get("damage") or {}
    system = {
        "id": action["id"],
        "activation": {
            "type": test.get("type") or activation.get("type") or "utility",
            "skill": test.get("skill"), "attribute": test.get("attribute"),
            "modifierFormula": test.get("modifier", ""),
            "cost": deepcopy(cost), "consume": consume,
            "flavor": activation.get("flavor", ""),
            "plotDie": bool(action.get("plot_die")),
            "opportunity": deepcopy(action.get("opportunity")),
            "complication": deepcopy(action.get("complication")),
            "uses": deepcopy(action.get("use_limit")),
        },
        "damage": {"formula": damage.get("formula"), "type": damage.get("type"),
                   "skill": damage.get("skill"), "attribute": damage.get("attribute"),
                   "grazeOverrideFormula": damage.get("graze_formula")},
        "target": deepcopy(action.get("target") or {}),
        "description": {"short": action.get("description", "")},
        "allowMultiple": bool(action.get("allow_multiple")),
        "permissions": deepcopy(action.get("permissions") or {}),
    }
    if action.get("attack_modifier") is not None:
        system["custom_attack_bonus"] = int(action["attack_modifier"])
    return {"_id": str(action.get("item_id") or action.get("source_id") or action["id"]),
            "type": action.get("kind", "action"), "name": action["name"],
            "system": system}


def _compatibility_item(value, item_type):
    if isinstance(value, str):
        name = value
        identity = hashlib.sha256(
            (item_type + "\0" + name).encode("utf-8")).hexdigest()[:32]
        return {"_id": identity, "type": item_type, "name": name, "system": {}}
    item = _copy(dict(value))
    item.setdefault("type", item_type)
    item.setdefault("system", {})
    item.setdefault("_id", str(item.get("id") or hashlib.sha256(
        canonical_json(item)).hexdigest()[:32]))
    return item


def adversary_to_actor_document(adversary):
    adversary = normalize_adversary(
        adversary, binding=adversary.get("rules_binding"),
        adversary_id=adversary.get("id"), revision=adversary.get("revision", 0))
    resources = adversary["resources"]
    return {
        "_id": adversary["id"], "name": adversary["name"], "type": "adversary",
        "system": {
            "level": {"value": adversary["level"]}, "tier": adversary["tier"],
            "role": adversary["role"], "size": adversary["size"],
            "attributes": {key: {"value": 0}
                           for key in ("str", "spd", "int", "wil", "awa", "pre")},
            "defenses": {
                "phy": {"useOverride": True, "override": adversary["defenses"]["physical"]},
                "cog": {"useOverride": True, "override": adversary["defenses"]["cognitive"]},
                "spi": {"useOverride": True, "override": adversary["defenses"]["spiritual"]},
            },
            "resources": {
                "hea": {"value": resources["health"]["current"],
                        "max": {"useOverride": True, "override": resources["health"]["max"]}},
                "foc": {"value": resources["focus"]["current"],
                        "max": {"useOverride": True, "override": resources["focus"]["max"]}},
                "inv": {"value": resources["investiture"]["current"],
                        "max": {"useOverride": True, "override": resources["investiture"]["max"]}},
            },
            "deflect": {"natural": adversary["deflect"],
                        "types": {"impact": True, "keen": True, "energy": True,
                                  "spirit": False, "vital": False}},
            "skills": {},
        },
        "items": [
            *[_compatibility_item(row, "trait") for row in adversary["traits"]],
            *[_compatibility_item(row, "item") for row in adversary["equipment"]],
            *[_action_item(action) for action in adversary["actions"]],
        ],
    }
