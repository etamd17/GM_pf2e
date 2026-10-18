"""Portable standalone Cosmere character JSON without server authority."""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json

from .documents import CHARACTER_SCHEMA_VERSION, normalize_character_document
from .rules.profiles import normalize_binding
from .rules.schema import CosmereRulesBinding, RulesValidationError, from_dict, require, to_dict


FORMAT = "cosmere-character"
FORMAT_VERSION = 1
MAX_PORTABLE_BYTES = 2 * 1024 * 1024
PORTABLE_ACTOR_ID = "cosmere.actor.portable"
AUTHORITY_FIELDS = frozenset({
    "id", "campaign_id", "owner_user_id", "owner_user_ids", "editor_user_ids",
    "viewer_user_ids", "target_id", "base_fingerprint",
})
NESTED_AUTHORITY_FIELDS = AUTHORITY_FIELDS - {"id", "target_id"}


def _strip_authority_tree(value, *, source_actor_id=None, target_actor_id=None):
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if key in NESTED_AUTHORITY_FIELDS:
                continue
            if key == "target_id":
                # Runtime effects commonly target their owning actor. Preserve
                # that rules state without exporting a server identity; foreign
                # actor targets are intentionally not portable.
                if item == source_actor_id and target_actor_id is not None:
                    result[key] = target_actor_id
                continue
            result[key] = _strip_authority_tree(
                item, source_actor_id=source_actor_id,
                target_actor_id=target_actor_id)
        return result
    if isinstance(value, (list, tuple)):
        return [_strip_authority_tree(item, source_actor_id=source_actor_id,
                                      target_actor_id=target_actor_id)
                for item in value]
    return deepcopy(value)


def _strip_authority(value, *, source_actor_id=None, target_actor_id=None):
    result = _strip_authority_tree(
        value, source_actor_id=source_actor_id, target_actor_id=target_actor_id)
    result.pop("id", None)
    result.pop("target_id", None)
    if isinstance(result.get("build"), dict):
        result["build"].pop("id", None)
        result["build"].pop("target_id", None)
    return result


def export_character(document, *, profile=None, homebrew=None):
    canonical = normalize_character_document(
        document, profile=profile, homebrew=homebrew)
    character = _strip_authority(
        canonical, source_actor_id=canonical["id"],
        target_actor_id=PORTABLE_ACTOR_ID)
    character.pop("play_state", None)
    for event in character.get("advancement_history", []):
        if isinstance(event, dict):
            event["actor_id"] = PORTABLE_ACTOR_ID
    payload = {"format": FORMAT, "format_version": FORMAT_VERSION,
               "character": character}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    require(len(encoded) <= MAX_PORTABLE_BYTES, "portable character exceeds size limit")
    return payload


def import_character(payload, *, actor_id, expected_binding, profile=None,
                     homebrew=None):
    require(isinstance(payload, Mapping), "portable character must be an object")
    require(payload.get("format") == FORMAT and payload.get("format_version") == FORMAT_VERSION,
            "unsupported portable character format")
    raw = payload.get("character")
    require(isinstance(raw, Mapping), "portable character payload is missing")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    require(len(encoded) <= MAX_PORTABLE_BYTES, "portable character exceeds size limit")
    expected = (expected_binding if isinstance(expected_binding, CosmereRulesBinding)
                else from_dict(CosmereRulesBinding, expected_binding))
    expected = normalize_binding({"rules_binding": to_dict(expected)})
    result = _strip_authority(
        raw, source_actor_id=PORTABLE_ACTOR_ID, target_actor_id=actor_id)
    result.update(id=actor_id, system="cosmere", owner_user_id=None,
                  editor_user_ids=[], viewer_user_ids=[])
    require(result.get("schema_version") == CHARACTER_SCHEMA_VERSION,
            "unsupported Cosmere character schema version")
    for event in result.get("advancement_history", []):
        require(isinstance(event, dict), "invalid advancement history")
        require(event.get("actor_id") == PORTABLE_ACTOR_ID,
                "portable advancement history contains server authority")
        event["actor_id"] = actor_id
    # normalize_character_document recomputes derived state and validates every
    # event and package pin; imported derived/authority fields are never trusted.
    return normalize_character_document(
        result, actor_id=actor_id, binding=expected,
        profile=profile, homebrew=homebrew)
