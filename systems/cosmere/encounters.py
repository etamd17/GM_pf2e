"""Profile-driven Cosmere encounter preview and durable staging contracts."""
from __future__ import annotations

from copy import deepcopy
from collections.abc import Mapping
import hashlib
import json
import re

from systems.cosmere.rules.compiler import canonical_json
from systems.cosmere.rules.schema import to_dict


_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FORBIDDEN = {"xp", "ac", "fortitude", "reflex", "will_save", "elite",
              "weak", "three_action", "three_actions"}


class EncounterValidationError(ValueError):
    pass


class EncounterStateError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def _binding_dict(binding):
    value = to_dict(binding)
    if isinstance(value, dict):
        return value
    return {
        "default_world": getattr(binding, "default_world", None),
        "allowed_worlds": list(getattr(binding, "allowed_worlds", ()) or ()),
        "allowed_eras": list(getattr(binding, "allowed_eras", ()) or ()),
    }


def binding_hash(binding):
    return hashlib.sha256(canonical_json(_binding_dict(binding))).hexdigest()


def _check_scope(row, *, world, era, allowed_eras):
    if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
        raise EncounterValidationError("encounter actors require stable identities")
    actor_world = row.get("world", world)
    if actor_world not in ("cosmere", world):
        raise EncounterValidationError("encounter actor world is outside the binding")
    actor_era = row.get("era")
    if actor_world == "scadrial" and actor_era != era:
        raise EncounterValidationError("encounter actor era is outside the binding")
    if actor_era is not None and (actor_era not in allowed_eras or actor_era != era):
        raise EncounterValidationError("encounter actor era is outside the binding")


def _reject_pf2e(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN:
                raise EncounterValidationError("PF2e encounter fields are not valid for Cosmere")
            _reject_pf2e(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_pf2e(child)


def preview_encounter(profile, participants, adversaries, *, world, era=None):
    if not isinstance(participants, list) or not isinstance(adversaries, list):
        raise EncounterValidationError("encounter participants and adversaries must be lists")
    binding = _binding_dict(profile.binding)
    allowed_worlds = set(binding.get("allowed_worlds") or ())
    if not allowed_worlds:
        allowed_worlds = {binding.get("default_world")}
        binding["allowed_worlds"] = sorted(allowed_worlds)
    if world not in allowed_worlds:
        raise EncounterValidationError("encounter world is outside the binding")
    allowed_eras = set(binding.get("allowed_eras") or [])
    if world != "scadrial" and era is not None:
        raise EncounterValidationError("encounter era requires Scadrial")
    if world == "scadrial" and era is not None and era not in allowed_eras:
        raise EncounterValidationError("encounter era is outside the binding")
    if world == "scadrial" and era is None and len(allowed_eras) == 1:
        era = next(iter(allowed_eras))
    if world == "scadrial" and era is None:
        raise EncounterValidationError("encounter era is required for Scadrial")
    for row in (*participants, *adversaries):
        _check_scope(row, world=world, era=era, allowed_eras=allowed_eras)
    budget = profile.encounter_budget(deepcopy(participants), deepcopy(adversaries))
    if not isinstance(budget, Mapping):
        raise EncounterValidationError("profile returned an invalid encounter budget")
    _reject_pf2e(budget)
    result = {
        "binding": binding, "binding_hash": binding_hash(profile.binding),
        "world": world, "era": era,
        "participants": deepcopy(participants),
        "adversaries": deepcopy(adversaries),
        "budget": deepcopy(dict(budget)),
    }
    _reject_pf2e(result)
    return result


def normalize_stage_state(value):
    value = value if isinstance(value, Mapping) else {}
    revision = value.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise EncounterStateError("invalid_revision", "Invalid encounter stage revision.")
    stage = value.get("stage")
    if stage is not None and not isinstance(stage, Mapping):
        raise EncounterStateError("invalid_state", "Invalid encounter stage state.")
    receipts = value.get("receipts", [])
    if not isinstance(receipts, list):
        raise EncounterStateError("invalid_state", "Invalid encounter stage state.")
    return {"schema_version": 1, "revision": revision,
            "stage": deepcopy(dict(stage)) if stage is not None else None,
            "receipts": deepcopy(receipts[-100:])}


def stage_encounter(state, preview, *, expected_revision, request_id):
    if not isinstance(request_id, str) or _REQUEST_ID.fullmatch(request_id) is None:
        raise EncounterStateError("invalid_request_id", "Invalid encounter request ID.")
    state = normalize_stage_state(state)
    digest = hashlib.sha256(canonical_json(preview)).hexdigest()
    prior = next((row for row in reversed(state["receipts"])
                  if row.get("request_id") == request_id), None)
    if prior is not None:
        if prior.get("operation") != "stage" or prior.get("preview_hash") != digest:
            raise EncounterStateError("request_id_conflict",
                                      "Encounter request ID belongs to a different stage.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    if type(expected_revision) is not int or expected_revision < 0:
        raise EncounterStateError("invalid_revision", "Invalid expected encounter revision.")
    if state["revision"] != expected_revision:
        raise EncounterStateError("stale_revision", "stale encounter stage revision", 409)
    _reject_pf2e(preview)
    stage_id = "stage." + hashlib.sha256(
        (request_id + "\0" + digest).encode("utf-8")).hexdigest()[:32]
    stage = {"id": stage_id, **deepcopy(preview)}
    state["stage"] = stage
    state["revision"] += 1
    result = {"stage": deepcopy(stage), "revision": state["revision"],
              "replayed": False}
    state["receipts"].append({"request_id": request_id, "operation": "stage",
                              "preview_hash": digest, "result": deepcopy(result)})
    state["receipts"] = state["receipts"][-100:]
    return state, result
