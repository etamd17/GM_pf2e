"""Revisioned conversation and endeavor scene state, separate from tactical maps."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
import uuid

from systems.cosmere.rules.compiler import canonical_json
from systems.cosmere.rules.profiles import normalize_binding
from systems.cosmere.rules.schema import to_dict


SCENE_TYPES = {"conversation", "endeavor"}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_IDENTITY = re.compile(r"^(?:[0-9a-f]{32}|[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+)$")


class NarrativeSceneValidationError(ValueError):
    pass


class NarrativeSceneError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code = code
        self.status = status


def _copy(value):
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise NarrativeSceneValidationError("narrative scene must be normalized JSON") from None


def _binding(value):
    try:
        return to_dict(normalize_binding({"rules_binding": value}))
    except Exception as exc:
        raise NarrativeSceneValidationError("invalid narrative scene binding") from exc


def _request(value):
    if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
        raise NarrativeSceneError("invalid_request_id", "Invalid narrative scene request ID.")
    return value


def _participants(value):
    if not isinstance(value, list) or len(value) > 100:
        raise NarrativeSceneValidationError("invalid narrative scene participants")
    result = []
    seen = set()
    for row in value:
        if not isinstance(row, Mapping):
            raise NarrativeSceneValidationError("invalid narrative scene participant")
        actor_id = row.get("actor_id")
        if not isinstance(actor_id, str) or _IDENTITY.fullmatch(actor_id) is None or actor_id in seen:
            raise NarrativeSceneValidationError("invalid narrative scene participant identity")
        seen.add(actor_id)
        role = row.get("role", "participant")
        if not isinstance(role, str) or not role.strip() or len(role) > 80:
            raise NarrativeSceneValidationError("invalid narrative scene participant role")
        result.append({"actor_id": actor_id, "role": role.strip()})
    return result


def _tracks(value):
    if not isinstance(value, Mapping) or not value or len(value) > 20:
        raise NarrativeSceneValidationError("invalid narrative scene tracks")
    result = {}
    for key, row in value.items():
        if not isinstance(key, str) or not key or not isinstance(row, Mapping):
            raise NarrativeSceneValidationError("invalid narrative scene track")
        current, target = row.get("value"), row.get("target")
        if (type(current) is not int or type(target) is not int
                or target <= 0 or current < 0 or current > target):
            raise NarrativeSceneValidationError("invalid narrative scene track values")
        result[key] = {"value": current, "target": target}
    return dict(sorted(result.items()))


def normalize_scene(payload, *, scene_type, binding, scene_id, revision=0,
                    status="active"):
    if scene_type not in SCENE_TYPES:
        raise NarrativeSceneValidationError("invalid narrative scene type")
    if not isinstance(scene_id, str) or _IDENTITY.fullmatch(scene_id) is None:
        raise NarrativeSceneValidationError("invalid narrative scene identity")
    if type(revision) is not int or revision < 0:
        raise NarrativeSceneValidationError("invalid narrative scene revision")
    if status not in {"active", "closed"}:
        raise NarrativeSceneValidationError("invalid narrative scene status")
    rules_binding = _binding(binding)
    world = payload.get("world", rules_binding["default_world"])
    allowed_worlds = tuple(rules_binding.get("allowed_worlds") or
                           (rules_binding["default_world"],))
    if world not in ("cosmere", *allowed_worlds):
        raise NarrativeSceneValidationError("narrative scene world is outside the binding")
    era = payload.get("era")
    allowed_eras = tuple(rules_binding["allowed_eras"])
    if world == "scadrial":
        if era is None and len(allowed_eras) == 1:
            era = allowed_eras[0]
        if era not in allowed_eras:
            raise NarrativeSceneValidationError(
                "narrative scene era is outside the Scadrial binding")
    elif era is not None:
        raise NarrativeSceneValidationError(
            "only a Scadrial narrative scene may select an era")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
        raise NarrativeSceneValidationError("invalid narrative scene name")
    return {
        "id": scene_id, "revision": revision, "type": scene_type,
        "name": name.strip(), "status": status,
        "rules_binding": rules_binding,
        "binding_hash": hashlib.sha256(canonical_json(rules_binding)).hexdigest(),
        "world": world, "era": era,
        "participants": _participants(payload.get("participants", [])),
        "tracks": _tracks(payload.get("tracks")),
    }


def normalize_scene_store(value, *, binding):
    value = value if isinstance(value, Mapping) else {}
    revision = value.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise NarrativeSceneError("invalid_revision", "Invalid narrative scene store revision.")
    rows, receipts = value.get("scenes", []), value.get("receipts", [])
    if not isinstance(rows, list) or not isinstance(receipts, list):
        raise NarrativeSceneError("invalid_state", "Invalid narrative scene store.")
    normalized = []
    expected = _binding(binding)
    for row in rows:
        if not isinstance(row, Mapping) or row.get("rules_binding") != expected:
            raise NarrativeSceneValidationError("narrative scene binding changed")
        normalized.append(normalize_scene(
            row, scene_type=row.get("type"), binding=expected,
            scene_id=row.get("id"), revision=row.get("revision", 0),
            status=row.get("status", "active")))
    ids = [row["id"] for row in normalized]
    if len(ids) != len(set(ids)):
        raise NarrativeSceneError("duplicate_identity", "Duplicate narrative scene identity.")
    return {"schema_version": 1, "revision": revision,
            "scenes": normalized, "receipts": _copy(receipts[-100:])}


def _prior(state, request_id):
    return next((row for row in reversed(state["receipts"])
                 if row.get("request_id") == request_id), None)


def _receipt(state, request_id, operation, scene_id, result, payload_hash=None):
    row = {"request_id": request_id, "operation": operation,
           "scene_id": scene_id, "result": deepcopy(result)}
    if payload_hash is not None:
        row["payload_hash"] = payload_hash
    state["receipts"].append(row)
    state["receipts"] = state["receipts"][-100:]


def create_scene(state, scene_type, payload, *, binding, expected_store_revision,
                 request_id, id_factory=None):
    request_id = _request(request_id)
    state = normalize_scene_store(state, binding=binding)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    prior = _prior(state, request_id)
    if prior is not None:
        if (prior.get("operation") != "create" or prior.get("payload_hash") != digest
                or prior.get("result", {}).get("scene", {}).get("type") != scene_type):
            raise NarrativeSceneError("request_id_conflict",
                                      "Narrative scene request ID belongs to another create.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    if state["revision"] != expected_store_revision:
        raise NarrativeSceneError("stale_revision", "stale narrative scene store revision", 409)
    scene_id = (id_factory or (lambda: uuid.uuid4().hex))()
    scene = normalize_scene(payload, scene_type=scene_type, binding=binding,
                            scene_id=scene_id)
    state["scenes"].append(scene)
    state["revision"] += 1
    result = {"scene": deepcopy(scene), "revision": state["revision"],
              "replayed": False}
    _receipt(state, request_id, "create", scene_id, result, digest)
    return state, result


def update_scene(state, scene_id, payload, *, binding, expected_revision,
                 request_id):
    request_id = _request(request_id)
    state = normalize_scene_store(state, binding=binding)
    digest = hashlib.sha256(canonical_json(payload)).hexdigest()
    prior = _prior(state, request_id)
    if prior is not None:
        if (prior.get("operation") != "update" or prior.get("scene_id") != scene_id
                or prior.get("payload_hash") != digest):
            raise NarrativeSceneError("request_id_conflict",
                                      "Narrative scene request ID belongs to another update.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    index = next((index for index, row in enumerate(state["scenes"])
                  if row["id"] == scene_id), None)
    if index is None:
        raise NarrativeSceneError("not_found", "Narrative scene not found.", 404)
    current = state["scenes"][index]
    if current["revision"] != expected_revision:
        raise NarrativeSceneError("stale_revision", "stale narrative scene revision", 409)
    if current["status"] != "active":
        raise NarrativeSceneError("scene_closed", "Narrative scene is closed.", 409)
    scene = normalize_scene(payload, scene_type=current["type"], binding=binding,
                            scene_id=scene_id, revision=expected_revision + 1)
    state["scenes"][index] = scene
    state["revision"] += 1
    result = {"scene": deepcopy(scene), "revision": state["revision"],
              "replayed": False}
    _receipt(state, request_id, "update", scene_id, result, digest)
    return state, result


def close_scene(state, scene_id, *, expected_revision, request_id):
    request_id = _request(request_id)
    raw_rows = state.get("scenes", []) if isinstance(state, Mapping) else []
    first = next((row for row in raw_rows if isinstance(row, Mapping)), None)
    scene_binding = first.get("rules_binding") if first else to_dict(normalize_binding())
    state = normalize_scene_store(state, binding=scene_binding)
    prior = _prior(state, request_id)
    if prior is not None:
        if prior.get("operation") != "close" or prior.get("scene_id") != scene_id:
            raise NarrativeSceneError("request_id_conflict",
                                      "Narrative scene request ID belongs to another close.", 409)
        result = deepcopy(prior["result"])
        result["replayed"] = True
        return state, result
    index = next((index for index, row in enumerate(state["scenes"])
                  if row["id"] == scene_id), None)
    if index is None:
        raise NarrativeSceneError("not_found", "Narrative scene not found.", 404)
    current = state["scenes"][index]
    if current["revision"] != expected_revision:
        raise NarrativeSceneError("stale_revision", "stale narrative scene revision", 409)
    if current["status"] != "active":
        raise NarrativeSceneError("scene_closed", "Narrative scene is closed.", 409)
    changed = deepcopy(current)
    changed["revision"] += 1
    changed["status"] = "closed"
    state["scenes"][index] = changed
    state["revision"] += 1
    result = {"scene": deepcopy(changed), "revision": state["revision"],
              "lifecycle": {"trigger": "scene_end", "scene_id": scene_id,
                            "scene_type": current["type"]},
              "replayed": False}
    _receipt(state, request_id, "close", scene_id, result)
    return state, result
