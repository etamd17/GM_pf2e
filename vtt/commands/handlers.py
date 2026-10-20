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

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vtt import fog
from vtt import terrain as terrain_rules
from vtt.projection import serializers as S
from vtt.storage.models import VttEntity, VttFog, VttScene, VttTablePointer, VttTerrain

ENTITY_DATA = {
    "token": ("x", "y", "size", "label", "color", "artAssetId", "actorId", "locked", "controllerIds", "vision"),
    "light": ("x", "y", "radius", "color"),
    "wall": ("x1", "y1", "x2", "y2", "doorState"),
    "template": ("shape", "x", "y", "aimX", "aimY", "size", "color"),
}
HIDEABLE = ("token", "light", "template")
MAX_TEMPLATES = 512          # per scene (parity P2)
SCENE_COLUMNS = {"name": "name", "width": "width", "height": "height", "gridSize": "grid_size",
                 "showGrid": "show_grid", "backgroundAssetId": "background_asset_id",
                 "published": "published", "lighting": "lighting_enabled", "defaultVision": "default_vision"}


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


def _check_assets(ctx: Context) -> None:
    """A scene background or token art must be an asset this campaign holds
    (M6). Another campaign's asset, or none at all, is the same refusal."""
    from vtt.assets.service import exists
    for field in ("backgroundAssetId", "artAssetId"):
        asset = ctx.payload.get(field)
        if asset is not None and not exists(ctx.session, ctx.campaign_id, asset):
            raise Rejection("rule-violation")


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
    _check_assets(ctx)
    p = ctx.payload
    ctx.session.add(VttScene(campaign_id=ctx.campaign_id, id=ctx.scene_id, name=p["name"], width=p["width"],
                             height=p["height"], grid_size=p["gridSize"], show_grid=p["showGrid"],
                             background_asset_id=p["backgroundAssetId"], published=False,
                             lighting_enabled=bool(p.get("lighting", False)),
                             default_vision=float(p.get("defaultVision", 0))))
    ctx.session.flush()
    return {"kind": "scene", "sceneId": ctx.scene_id}


def scene_update(ctx: Context) -> dict[str, Any]:
    scene = _lock_scene(ctx)
    _check_version(ctx, "scene", scene.id, S.gm_scene(ctx.key, scene))
    geometry = {"width": scene.width, "height": scene.height, "gridSize": scene.grid_size}
    if any(field in ctx.payload and ctx.payload[field] != value for field, value in geometry.items()):
        # A reveal names grid cells; new geometry would make it mean other
        # cells. Refused while anything is revealed: the GM resets fog first,
        # so nothing they revealed is ever silently discarded (handoff 060).
        _row, view = fog.load(ctx.session, scene, for_update=True)
        if view is not None and view.any_revealed():
            raise Rejection("rule-violation")
        # Painted terrain names cells too (parity P2): the same refusal.
        _trow, painted = terrain_rules.load(ctx.session, scene, for_update=True)
        if painted is not None and painted.any_painted():
            raise Rejection("rule-violation")
        # Nor may new geometry silently turn fog off: a grid too large for fog
        # has no fog, which would show the table everything it covered
        # (handoff 062). The GM turns fog off first, deliberately.
        width = ctx.payload.get("width", scene.width)
        height = ctx.payload.get("height", scene.height)
        grid_size = ctx.payload.get("gridSize", scene.grid_size)
        if view is not None and view.enabled and not fog.available(*fog.grid_of(width, height, grid_size)):
            raise Rejection("rule-violation")
    if ctx.payload.get("published") is False:
        pointer = ctx.session.get(VttTablePointer, ctx.campaign_id, with_for_update=True)
        if pointer is not None and pointer.scene_id == scene.id:
            # Unpublishing the scene the table is showing would leave the table
            # pointing at a hidden scene; clear the table first.
            raise Rejection("rule-violation")
    _check_assets(ctx)
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


# --- fog (parity-1) ---------------------------------------------------------------------------

def _fog_event(ctx: Context) -> dict[str, Any]:
    """Fog changes what the table may see: name the fog and every token and
    light of the scene, so delivery's per-key diff adds or removes exactly the
    ones whose visibility changed."""
    keys = ctx.session.execute(
        select(VttEntity.kind, VttEntity.id).where(
            VttEntity.campaign_id == ctx.campaign_id, VttEntity.scene_id == ctx.scene_id,
            VttEntity.kind.in_(("token", "light", "template"))).order_by(VttEntity.kind, VttEntity.id)
    ).all()
    # Terrain too: the table's copy is masked by fog (parity P2).
    return {"kind": "entities", "sceneId": ctx.scene_id,
            "changed": [["fog", "fog"], ["terrain", "terrain"]] + [[k, i] for k, i in keys]}


def _fog(ctx: Context, *, versioned: bool):
    scene = _lock_scene(ctx)
    row, view = fog.load(ctx.session, scene, for_update=True)
    if view is None:
        raise Rejection("rule-violation")      # the scene's grid is too large for fog
    if versioned:
        _check_version(ctx, "fog", "fog", S.fog_entity(ctx.key, ctx.campaign_id, "gm", view))
    return scene, row, view


def _write_fog(ctx: Context, row: VttFog | None, view, *, enabled: bool, bits: bytes) -> None:
    if row is None:
        ctx.session.add(VttFog(campaign_id=ctx.campaign_id, scene_id=ctx.scene_id, enabled=enabled,
                               cols=view.cols, rows=view.rows, revealed=bits))
    else:
        row.enabled, row.cols, row.rows, row.revealed = enabled, view.cols, view.rows, bits
    ctx.session.flush()


def fog_update(ctx: Context) -> dict[str, Any]:
    _scene, row, view = _fog(ctx, versioned=True)
    _write_fog(ctx, row, view, enabled=bool(ctx.payload["enabled"]), bits=view.bits)
    return _fog_event(ctx)


def fog_reveal(ctx: Context) -> dict[str, Any]:
    # No version precondition: a reveal is a set operation, so concurrent
    # reveals compose and a retry changes nothing.
    _scene, row, view = _fog(ctx, versioned=False)
    try:
        bits = fog.with_cells(view, ctx.payload["cells"], ctx.payload["mode"] == "reveal")
    except ValueError:
        raise Rejection("validation-failed") from None
    _write_fog(ctx, row, view, enabled=view.enabled, bits=bits)
    return _fog_event(ctx)


def fog_reset(ctx: Context) -> dict[str, Any]:
    _scene, row, view = _fog(ctx, versioned=True)
    _write_fog(ctx, row, view, enabled=view.enabled, bits=fog.empty_bits(view.cols, view.rows))
    return _fog_event(ctx)


# --- terrain (parity-2) -------------------------------------------------------------------------

def _terrain(ctx: Context, *, versioned: bool):
    scene = _lock_scene(ctx)
    row, view = terrain_rules.load(ctx.session, scene, for_update=True)
    if view is None:
        raise Rejection("rule-violation")      # the scene's grid is too large for terrain
    if versioned:
        _check_version(ctx, "terrain", "terrain", S.terrain_entity(ctx.key, ctx.campaign_id, "gm", view))
    return row, view


def _write_terrain(ctx: Context, row: VttTerrain | None, view, cells: bytes) -> dict[str, Any]:
    if row is None:
        ctx.session.add(VttTerrain(campaign_id=ctx.campaign_id, scene_id=ctx.scene_id, cols=view.cols,
                                   rows=view.rows, cells=cells))
    else:
        row.cols, row.rows, row.cells = view.cols, view.rows, cells
    ctx.session.flush()
    return {"kind": "entities", "sceneId": ctx.scene_id, "changed": [["terrain", "terrain"]]}


def terrain_paint(ctx: Context) -> dict[str, Any]:
    # A set operation with a kind per cell: no precondition, and its inverse is
    # the same command with each cell's previous kind.
    row, view = _terrain(ctx, versioned=False)
    try:
        cells = terrain_rules.with_cells(view, ctx.payload["cells"])
    except ValueError:
        raise Rejection("validation-failed") from None
    return _write_terrain(ctx, row, view, cells)


def terrain_reset(ctx: Context) -> dict[str, Any]:
    row, view = _terrain(ctx, versioned=True)
    return _write_terrain(ctx, row, view, terrain_rules.empty_cells(view.cols, view.rows))


# --- entities --------------------------------------------------------------------------------

def _create(kind: str) -> Callable[[Context], dict[str, Any]]:
    def handler(ctx: Context) -> dict[str, Any]:
        _lock_scene(ctx)
        p = ctx.payload
        if _lock_entity(ctx, kind, p["id"]) is not None:
            raise Rejection("conflict")
        _check_assets(ctx)
        if kind == "template":
            count = ctx.session.execute(
                select(func.count()).select_from(VttEntity).where(
                    VttEntity.campaign_id == ctx.campaign_id, VttEntity.scene_id == ctx.scene_id,
                    VttEntity.kind == "template")).scalar_one()
            if count >= MAX_TEMPLATES:
                raise Rejection("rule-violation")
        # Required fields are guaranteed by the schema; optional ones (a token's
        # vision, E-6) are stored only when sent.
        data = {name: p[name] for name in ENTITY_DATA[kind] if name in p}
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
        _check_assets(ctx)
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
    # Fog changes what the table shows: the same capability as publishing.
    "fog.update": Policy(_caps("scene.publish"), fog_update),
    "fog.reveal": Policy(_caps("scene.publish"), fog_reveal),
    "fog.reset": Policy(_caps("scene.publish"), fog_reset),
    # Area templates and terrain are prepared like tokens (parity-2).
    "template.create": Policy(_caps("scene.prepare"), _create("template")),
    "template.update": Policy(_caps("scene.prepare"), _update("template")),
    "terrain.paint": Policy(_caps("scene.prepare"), terrain_paint),
    "terrain.reset": Policy(_caps("scene.prepare"), terrain_reset),
}
