"""Command handlers and the fail-closed policy table (VTT-ADR-0002 sections 2 and 7).

A handler runs inside the command transaction after the campaign stream lock
and the receipt check. It locks its own rows in the documented order (the
scene, then entities by ``(kind, id)``), compares expected versions with the
current GM tokens, applies the change and returns the outbox event. A
deterministic refusal raises ``Rejection``; the service rolls the change back
and records the receipt.

Every type in the contract's ``commandVariants`` must have a policy here, and
nothing else may: ``POLICIES`` is checked against the registry at start-up and
by a test (acceptance criterion 2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vtt.projection import serializers as S
from vtt.storage.models import VttEntity, VttScene, VttTablePointer

ENTITY_DATA = {
    "token": ("x", "y", "size", "label", "color", "artAssetId", "actorId", "locked", "controllerIds"),
    "light": ("x", "y", "radius", "color"),
    "wall": ("x1", "y1", "x2", "y2", "doorState"),
}
HIDEABLE = ("token", "light")
SCENE_COLUMNS = {"name": "name", "width": "width", "height": "height", "gridSize": "grid_size",
                 "showGrid": "show_grid", "backgroundAssetId": "background_asset_id",
                 "published": "published"}


class Rejection(Exception):
    """A deterministic, receipted refusal: stale, conflict, validation-failed or rule-violation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class Context:
    session: Session
    key: S.TokenKey
    campaign_id: str
    scene_id: str
    payload: dict[str, Any]
    expected: dict[tuple[str, str], str]


@dataclass(frozen=True)
class Policy:
    capability: Callable[[dict[str, Any]], set[str]]
    handler: Callable[[Context], dict[str, Any]]
    sizes_scene: bool = True       # run the write-time projection-size rule


# --- locking and preconditions ---------------------------------------------------------------

def _lock_scene(ctx: Context) -> VttScene:
    scene = ctx.session.execute(
        select(VttScene).where(VttScene.campaign_id == ctx.campaign_id, VttScene.id == ctx.scene_id)
        .with_for_update()
    ).scalar_one_or_none()
    if scene is None:
        # Missing, or another campaign's: indistinguishable (section 8).
        raise Rejection("validation-failed")
    return scene


def _lock_entity(ctx: Context, kind: str, entity_id: str) -> VttEntity | None:
    return ctx.session.execute(
        select(VttEntity).where(VttEntity.campaign_id == ctx.campaign_id, VttEntity.scene_id == ctx.scene_id,
                                VttEntity.kind == kind, VttEntity.id == entity_id)
        .with_for_update()
    ).scalar_one_or_none()


def _check_version(ctx: Context, kind: str, entity_id: str, projected: dict[str, Any]) -> None:
    if ctx.expected.get((kind, entity_id)) != projected["version"]:
        raise Rejection("stale")


def _existing_entity(ctx: Context, kind: str) -> VttEntity:
    _lock_scene(ctx)
    row = _lock_entity(ctx, kind, ctx.payload["id"])
    if row is None:
        raise Rejection("validation-failed")
    _check_version(ctx, kind, row.id, S.gm_entity(ctx.key, row))
    return row


def _entities_event(ctx: Context, kind: str, entity_id: str) -> dict[str, Any]:
    return {"kind": "entities", "sceneId": ctx.scene_id, "changed": [[kind, entity_id]]}


def _check_wall(data: dict[str, Any]) -> None:
    if (data["x1"], data["y1"]) == (data["x2"], data["y2"]):
        raise Rejection("validation-failed")


# --- scenes ----------------------------------------------------------------------------------

def scene_create(ctx: Context) -> dict[str, Any]:
    existing = ctx.session.execute(
        select(VttScene).where(VttScene.campaign_id == ctx.campaign_id, VttScene.id == ctx.scene_id)
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        raise Rejection("conflict")
    p = ctx.payload
    ctx.session.add(VttScene(campaign_id=ctx.campaign_id, id=ctx.scene_id, name=p["name"], width=p["width"],
                             height=p["height"], grid_size=p["gridSize"], show_grid=p["showGrid"],
                             background_asset_id=p["backgroundAssetId"], published=False))
    ctx.session.flush()
    return {"kind": "scene", "sceneId": ctx.scene_id}


def scene_update(ctx: Context) -> dict[str, Any]:
    scene = _lock_scene(ctx)
    _check_version(ctx, "scene", scene.id, S.gm_scene(ctx.key, scene))
    if ctx.payload.get("published") is False:
        pointer = ctx.session.get(VttTablePointer, ctx.campaign_id, with_for_update=True)
        if pointer is not None and pointer.scene_id == scene.id:
            # Unpublishing the scene the table is showing would leave the table
            # pointing at a hidden scene; clear the table first.
            raise Rejection("rule-violation")
    for field, value in ctx.payload.items():
        setattr(scene, SCENE_COLUMNS[field], value)
    ctx.session.flush()
    return {"kind": "scene", "sceneId": scene.id}


def table_show(ctx: Context) -> dict[str, Any]:
    scene = _lock_scene(ctx)
    _check_version(ctx, "scene", scene.id, S.gm_scene(ctx.key, scene))
    scene.published = True
    pointer = ctx.session.get(VttTablePointer, ctx.campaign_id, with_for_update=True)
    if pointer is None:
        ctx.session.add(VttTablePointer(campaign_id=ctx.campaign_id, scene_id=scene.id))
    else:
        pointer.scene_id = scene.id
    ctx.session.flush()
    # One row: the pointer moved (tables resync) and this scene's row changed.
    return {"kind": "table-pointer", "sceneId": scene.id, "scene": True}


def table_clear(ctx: Context) -> dict[str, Any]:
    scene = _lock_scene(ctx)
    _check_version(ctx, "scene", scene.id, S.gm_scene(ctx.key, scene))
    pointer = ctx.session.get(VttTablePointer, ctx.campaign_id, with_for_update=True)
    if pointer is None or pointer.scene_id != scene.id:
        raise Rejection("conflict")     # the table is not showing this scene
    pointer.scene_id = None
    ctx.session.flush()
    return {"kind": "table-pointer", "sceneId": scene.id}


# --- entities --------------------------------------------------------------------------------

def _create(kind: str) -> Callable[[Context], dict[str, Any]]:
    def handler(ctx: Context) -> dict[str, Any]:
        _lock_scene(ctx)
        p = ctx.payload
        if _lock_entity(ctx, kind, p["id"]) is not None:
            raise Rejection("conflict")
        data = {name: p[name] for name in ENTITY_DATA[kind]}
        if kind == "wall":
            _check_wall(data)
        ctx.session.add(VttEntity(campaign_id=ctx.campaign_id, scene_id=ctx.scene_id, kind=kind, id=p["id"],
                                  data=data, hidden=bool(p.get("hidden", False)) if kind in HIDEABLE else False))
        ctx.session.flush()
        return _entities_event(ctx, kind, p["id"])
    return handler


def _update(kind: str, fields: tuple[str, ...] | None = None) -> Callable[[Context], dict[str, Any]]:
    allowed = fields or ENTITY_DATA[kind]

    def handler(ctx: Context) -> dict[str, Any]:
        row = _existing_entity(ctx, kind)
        data = dict(row.data or {})
        for name in allowed:
            if name in ctx.payload:
                data[name] = ctx.payload[name]
        if kind == "wall":
            _check_wall(data)
        row.data = data
        if kind in HIDEABLE and "hidden" in ctx.payload and fields is None:
            row.hidden = bool(ctx.payload["hidden"])
        ctx.session.flush()
        return _entities_event(ctx, kind, row.id)
    return handler


def entity_delete(ctx: Context) -> dict[str, Any]:
    kind = ctx.payload["kind"]
    row = _existing_entity(ctx, kind)
    ctx.session.delete(row)
    ctx.session.flush()
    return _entities_event(ctx, kind, row.id)


# --- policy ----------------------------------------------------------------------------------

def _caps(*names: str) -> Callable[[dict[str, Any]], set[str]]:
    return lambda _payload: set(names)


def _scene_update_caps(payload: dict[str, Any]) -> set[str]:
    caps = {"scene.prepare"} if set(payload) - {"published"} else set()
    if "published" in payload:
        caps.add("scene.publish")
    return caps or {"scene.prepare"}


POLICIES: dict[str, Policy] = {
    "scene.create": Policy(_caps("scene.prepare"), scene_create),
    "scene.update": Policy(_scene_update_caps, scene_update),
    "table.show": Policy(_caps("scene.publish"), table_show),
    "table.clear": Policy(_caps("scene.publish"), table_clear, sizes_scene=False),
    "token.create": Policy(_caps("scene.prepare"), _create("token")),
    "token.update": Policy(_caps("scene.prepare"), _update("token")),
    "token.move": Policy(_caps("token.move"), _update("token", ("x", "y"))),
    "light.create": Policy(_caps("scene.prepare"), _create("light")),
    "light.update": Policy(_caps("scene.prepare"), _update("light")),
    "wall.create": Policy(_caps("scene.prepare"), _create("wall")),
    "wall.update": Policy(_caps("scene.prepare"), _update("wall")),
    "entity.delete": Policy(_caps("scene.prepare"), entity_delete, sizes_scene=False),
}
