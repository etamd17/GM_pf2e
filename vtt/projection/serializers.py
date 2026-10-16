"""Audience serializers (VTT-ADR-0002 section 2).

There is one serializer per audience and they share no output path. The table
serializer reads only what the table may see and builds its own objects; it is
never a GM projection with fields deleted afterwards. Removed and newly hidden
entities are indistinguishable to the table: both simply stop appearing.

Version tokens (section 4) are opaque, unguessable and scoped to campaign,
audience, entity and the audience-visible fields. They are an HMAC of exactly
those fields, so a change confined to fields an audience cannot see does not
change that audience's token, and equal tokens mean an unchanged projection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

GM_CAPABILITIES = ["scene.prepare", "scene.publish", "token.control", "token.move", "campaign.manage"]


@dataclass(frozen=True)
class TokenKey:
    """Server secret for version tokens, derived once from the app secret."""

    secret: bytes

    @classmethod
    def derive(cls, app_secret: str | bytes) -> "TokenKey":
        raw = app_secret.encode("utf-8") if isinstance(app_secret, str) else app_secret
        return cls(hmac.new(raw, b"vtt-version-token-v1", hashlib.sha256).digest())

    def token(self, campaign_id: str, audience: str, kind: str, entity_id: str, fields: dict[str, Any]) -> str:
        body = json.dumps([campaign_id, audience, kind, entity_id, fields], sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        digest = hmac.new(self.secret, body.encode("utf-8"), hashlib.sha256).digest()[:18]
        return "v" + base64.urlsafe_b64encode(digest).decode("ascii")  # 25 chars, [A-Za-z0-9_-]


def _with_version(key: TokenKey, campaign_id: str, audience: str, kind: str, entity_id: str,
                  fields: dict[str, Any]) -> dict[str, Any]:
    entity = {"kind": kind, "id": entity_id, "version": key.token(campaign_id, audience, kind, entity_id, fields)}
    entity.update(fields)
    return entity


# --- GM ------------------------------------------------------------------------------------

def gm_scene(key: TokenKey, scene) -> dict[str, Any]:
    fields = {
        "name": scene.name, "width": scene.width, "height": scene.height, "gridSize": scene.grid_size,
        "showGrid": bool(scene.show_grid), "backgroundAssetId": scene.background_asset_id,
        "published": bool(scene.published),
    }
    return _with_version(key, scene.campaign_id, "gm", "scene", scene.id, fields)


def gm_entity(key: TokenKey, row) -> dict[str, Any]:
    d = row.data or {}
    if row.kind == "token":
        fields = {
            "x": d["x"], "y": d["y"], "size": d.get("size", 1), "label": d.get("label", ""),
            "color": d.get("color", 0), "artAssetId": d.get("artAssetId"), "actorId": d.get("actorId"),
            "hidden": bool(row.hidden), "locked": bool(d.get("locked", False)),
            "controllerIds": list(d.get("controllerIds", [])),
        }
    elif row.kind == "light":
        fields = {"x": d["x"], "y": d["y"], "radius": d["radius"], "color": d.get("color", 0),
                  "hidden": bool(row.hidden)}
    elif row.kind == "wall":
        fields = {"x1": d["x1"], "y1": d["y1"], "x2": d["x2"], "y2": d["y2"],
                  "doorState": d.get("doorState", "none")}
    else:
        raise ValueError("unknown entity kind")
    return _with_version(key, row.campaign_id, "gm", row.kind, row.id, fields)


def gm_entities(key: TokenKey, scene, rows) -> list[dict[str, Any]]:
    out = [gm_scene(key, scene)] if scene is not None else []
    out += [gm_entity(key, row) for row in rows]
    return out


# --- table -----------------------------------------------------------------------------------
# Reads only the published scene's own fields, and non-hidden tokens and lights.
# Walls, doors, hidden/locked/controller data and the published flag never enter.

def table_scene(key: TokenKey, scene) -> dict[str, Any]:
    fields = {
        "name": scene.name, "width": scene.width, "height": scene.height, "gridSize": scene.grid_size,
        "showGrid": bool(scene.show_grid), "backgroundAssetId": scene.background_asset_id,
    }
    return _with_version(key, scene.campaign_id, "table", "scene", scene.id, fields)


def table_entity(key: TokenKey, row) -> dict[str, Any] | None:
    """The table's view of one canonical entity, or None when the table may not see it."""
    if row.hidden or row.kind not in ("token", "light"):
        return None
    d = row.data or {}
    if row.kind == "token":
        fields = {
            "x": d["x"], "y": d["y"], "size": d.get("size", 1), "label": d.get("label", ""),
            "color": d.get("color", 0), "artAssetId": d.get("artAssetId"), "actorId": d.get("actorId"),
        }
    else:
        fields = {"x": d["x"], "y": d["y"], "radius": d["radius"], "color": d.get("color", 0)}
    return _with_version(key, row.campaign_id, "table", row.kind, row.id, fields)


def table_entities(key: TokenKey, scene, rows) -> list[dict[str, Any]]:
    if scene is None or not scene.published:
        return []
    out = [table_scene(key, scene)]
    for row in rows:
        projected = table_entity(key, row)
        if projected is not None:
            out.append(projected)
    return out


# --- frames ----------------------------------------------------------------------------------

def snapshot_frame(audience: str, subscription_id: str, campaign_id: str, scene_id: str | None,
                   principal: dict[str, Any], entities: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": 1, "frame": "snapshot", "audience": audience, "subscriptionId": subscription_id,
        "seq": 0, "campaignId": campaign_id, "sceneId": scene_id, "principal": principal,
        "capabilities": list(GM_CAPABILITIES) if audience == "gm" else [], "entities": entities,
    }


def patch_frame(subscription_id: str, seq: int, upserted: list[dict[str, Any]],
                removed: list[dict[str, str]], origin_command_id: str | None = None) -> dict[str, Any]:
    frame = {"schemaVersion": 1, "frame": "patch", "subscriptionId": subscription_id, "seq": seq,
             "upserted": upserted, "removed": removed}
    if origin_command_id is not None:
        frame["originCommandId"] = origin_command_id
    return frame
