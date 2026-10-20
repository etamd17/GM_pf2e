"""Import legacy ``/map`` scenes into the VTT (M5, handoff 046).

Two steps, so a dry run is the same code as the real thing:

- ``plan_scene(legacy)`` maps one ``core/scenes.py`` scene to VTT rows and
  reports, in plain words, everything it clamped or could not bring across.
  Pure: no database, no files.
- ``apply_plan(db, campaign_id, plan, ...)`` writes one plan in ONE transaction:
  scene, entities, optionally the table pointer, each change recorded in the
  stream and outbox so a live GM or table sees it arrive. The scene's GM
  snapshot must fit the frame limit (the M2 write-time rule); if it does not,
  the transaction rolls back and nothing of that scene is written.

Ids are ``legacy_<legacy id>``, or ``legacyh_<sha1>`` when a legacy id is not
a valid VTT ``Id``; the two forms cannot meet. Every row an import writes gets
a provenance row naming its exact source (``vtt_imported_scenes`` /
``vtt_imported_entities``, which cascade away with the row), and an existing
destination row is updated only when its provenance names the same source: a
proven re-import. Any other row already holding that id (a native scene or
entity, or a different source mapping to the same id) refuses the whole scene,
leaving rows, table pointer and outbox untouched (handoff 048). A re-run
creates and updates; it never deletes, because the GM may have edited or added
to an imported scene since.

Lock order matches commands: the campaign stream row is locked first, before
any destination row is read or written.

Backgrounds and token art uploaded to /map (M6) are read from its asset files,
checked with the asset store's own image rules, stored by content hash before
the scene is written, and referenced by their asset ids. Content-addressed ids
make a re-import of the same image the same asset, so assets need no
provenance rows: identical bytes are the same asset by definition.

The legacy store is only read. Nothing in it is changed.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from core.persistence.database import Database
from vtt.commands.handlers import MAX_TEMPLATES
from vtt.live import actor_id
from vtt.commands.service import FRAME_HEADROOM, GM_ENTITY_CAP, TABLE_ENTITY_CAP, gm_snapshot_bytes
from vtt.protocol.boundary import MAX_JSON_BYTES
from vtt.storage.models import VttEntity, VttImportedEntity, VttImportedScene, VttScene, VttTablePointer
from vtt.storage.stream import advance, lock_stream

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
PREFIX = "legacy_"
HASHED_PREFIX = "legacyh_"
ART_URL = re.compile(r"^/api/scenes/([^/?#]+)/tokens/([^/?#]+)/image(?:[?#].*)?$")
ART_EXTENSIONS = (".png", ".jpg", ".webp")
COORD_MAX = 16384.0
LABEL_MAX = 80
NAME_MAX = 120
CONTROL = re.compile("[\u0000-\u001f\u007f-\u009f‪-‮⁦-⁩\ud800-\udfff]")


@dataclass
class Plan:
    legacy_id: str
    scene_id: str
    name: str
    scene: dict[str, Any]
    entities: list[tuple[str, str, dict[str, Any], bool]] = field(default_factory=list)  # kind, id, data, hidden
    sources: dict[tuple[str, str], str] = field(default_factory=dict)  # (kind, id) -> provenance source
    duplicates: list[str] = field(default_factory=list)  # mapped ids planned more than once
    dropped: list[str] = field(default_factory=list)
    clamped: list[str] = field(default_factory=list)
    # Legacy image files that could supply a background or token art (M6).
    # plan_scene only names them; resolve_assets reads and checks them.
    background_files: list[str] = field(default_factory=list)
    art_files: dict[str, list[str]] = field(default_factory=dict)  # token id -> candidate files
    art_unsupported: int = 0          # token images that are not uploaded /map art
    uploads: list[bytes] = field(default_factory=list)  # checked images to store before writing
    # Fog (parity P1): on/off and the revealed cells, or None when there is none
    # to bring across.
    fog: dict[str, Any] | None = None
    # Terrain (parity P2): (col, row, kind) cells, or None when none to bring.
    terrain: dict[str, Any] | None = None


def vtt_id(legacy_id: Any) -> str:
    raw = str(legacy_id or "")
    candidate = PREFIX + raw
    if raw and ID_RE.fullmatch(candidate):
        return candidate
    return HASHED_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def scene_source(legacy_id: str) -> str:
    return f"legacy-map:{legacy_id}"


def entity_source(legacy_scene_id: str, kind: str, legacy_id: Any) -> str:
    # The raw legacy id, never the mapped one: two sources that map to the same
    # VTT id must not prove each other.
    return f"legacy-map:{legacy_scene_id}:{kind}:{'' if legacy_id is None else legacy_id}"


def _add(plan: Plan, kind: str, legacy_id: Any, eid: str, data: dict[str, Any], hidden: bool) -> None:
    if (kind, eid) in plan.sources:
        plan.duplicates.append(f"{kind} {eid}")
        return
    plan.sources[(kind, eid)] = entity_source(plan.legacy_id, kind, legacy_id)
    plan.entities.append((kind, eid, data, hidden))


def _clean_text(value: Any, limit: int) -> str:
    return CONTROL.sub("", str(value or ""))[:limit]


def _color(value: Any, default: int) -> int:
    text = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{6}", text):
        return int(text, 16)
    if re.fullmatch(r"[0-9a-fA-F]{3}", text):
        return int("".join(c * 2 for c in text), 16)
    return default


def _number(value: Any, low: float, high: float, default: float, what: str, plan: Plan) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        plan.clamped.append(f"{what}: not a number, used {default:g}")
        return default
    if number != number or number in (float("inf"), float("-inf")):
        plan.clamped.append(f"{what}: not a finite number, used {default:g}")
        return default
    if number < low or number > high:
        clamped = max(low, min(high, number))
        plan.clamped.append(f"{what}: {number:g} is outside {low:g}-{high:g}, used {clamped:g}")
        return clamped
    return number


def _count(items: Any) -> int:
    return len(items) if isinstance(items, list) else 0


def _plan_fog(plan: Plan, legacy: dict[str, Any], grid: dict[str, Any]) -> None:
    """/map fog: ``enabled`` and ``revealed_cells`` ("col,row" in grid cells)
    map onto the VTT fog when the legacy grid starts at the corner, the VTT
    grid starts there too; the old brush log (``operations``) is reported."""
    from vtt import fog as fog_rules
    legacy_fog = legacy.get("fog") or {}
    if not isinstance(legacy_fog, dict):
        return
    if _count(legacy_fog.get("operations")):
        plan.dropped.append(f"{_count(legacy_fog.get('operations'))} old fog brush stroke(s)")
    cells_text = legacy_fog.get("revealed_cells") if isinstance(legacy_fog.get("revealed_cells"), list) else []
    enabled = bool(legacy_fog.get("enabled"))
    if not enabled and not cells_text:
        # Fog off and nothing revealed is still the source's fog: on a re-import
        # it replaces whatever an earlier import wrote (handoff 062). A new scene
        # needs no row for it ("replace_only").
        plan.fog = {"enabled": False, "cells": [], "replace_only": True}
        return
    if grid.get("offset_x") or grid.get("offset_y"):
        plan.dropped.append("fog and revealed areas (the legacy grid is offset)")
        return
    cols, rows = fog_rules.grid_of(plan.scene["width"], plan.scene["height"], plan.scene["grid_size"])
    if not fog_rules.available(cols, rows):
        plan.dropped.append("fog and revealed areas (the scene's grid is too large for fog)")
        return
    cells, outside = [], 0
    for text in cells_text:
        try:
            col, row = (int(part) for part in str(text).split(","))
        except ValueError:
            outside += 1
            continue
        if 0 <= col < cols and 0 <= row < rows:
            cells.append((col, row))
        else:
            outside += 1
    if outside:
        plan.dropped.append(f"{outside} revealed fog cell(s) outside the scene")
    plan.fog = {"enabled": enabled, "cells": cells}


def plan_scene(legacy: dict[str, Any]) -> Plan:
    legacy_id = str(legacy.get("id") or "")
    plan = Plan(legacy_id=legacy_id, scene_id=vtt_id(legacy_id),
                name=_clean_text(legacy.get("name") or "Imported scene", NAME_MAX) or "Imported scene", scene={})
    grid = legacy.get("grid") or {}
    plan.scene = {
        "name": plan.name,
        "width": _number(legacy.get("width", 2048), 1, COORD_MAX, 2048, "scene width", plan),
        "height": _number(legacy.get("height", 2048), 1, COORD_MAX, 2048, "scene height", plan),
        "grid_size": _number(grid.get("size", 64), 8, 1024, 64, "grid size", plan),
        "show_grid": bool(grid.get("visible", True)),
        "background_asset_id": None,
    }
    if grid.get("offset_x") or grid.get("offset_y"):
        plan.dropped.append("grid offset (the VTT grid starts at the corner)")

    background = legacy.get("background")
    if background:
        filename = (background or {}).get("filename") if isinstance(background, dict) else None
        plan.background_files = [os.path.basename(str(filename))] if filename else []
        if not plan.background_files:
            plan.dropped.append("background image (no stored file)")

    # --- what has no VTT equivalent yet ------------------------------------------------------
    _plan_fog(plan, legacy, grid)
    _plan_terrain(plan, legacy, grid)
    settings = legacy.get("settings") or {}
    # Dynamic lighting and vision (E-6): /map's switch and its default vision
    # radius (700 px when unset, as /map defaults it). A token's own radius is
    # kept below only when it differs from that default; 0 or unset in /map
    # means the default too.
    plan.scene["lighting_enabled"] = bool(settings.get("dynamic_lighting"))
    plan.scene["default_vision"] = _number(settings.get("default_vision", 700), 0, COORD_MAX, 700,
                                           "default vision", plan)

    # --- tokens --------------------------------------------------------------------------------
    linked = nameplates = 0
    for token in legacy.get("tokens") or []:
        if not isinstance(token, dict):
            continue
        tid = vtt_id(token.get("id"))
        name = _clean_text(token.get("name"), LABEL_MAX)
        label = f"token {name or tid}"
        data = {
            "x": _number(token.get("x", 0), 0, COORD_MAX, 0, f"{label} x", plan),
            "y": _number(token.get("y", 0), 0, COORD_MAX, 0, f"{label} y", plan),
            "size": _number(token.get("size", 1), 0.25, 10, 1, f"{label} size", plan),
            "label": name,
            "color": _color(token.get("color"), 0xB88A44),
            "artAssetId": None,
            # The tracker link (parity P3), with /map's precedence: the
            # combatant first, then the party character.
            "actorId": (actor_id("cmb", token.get("combatant_id")) if token.get("combatant_id")
                        else actor_id("chr", token.get("character_id"))),
            "locked": bool(token.get("locked", False)),
            "controllerIds": [],
        }
        try:
            radius = float(token.get("vision_radius") or 0)
        except (TypeError, ValueError):
            radius = 0
            plan.dropped.append(f"{label} vision radius (not a number)")
        if radius > 0 and radius != plan.scene["default_vision"]:
            data["vision"] = _number(radius, 0, COORD_MAX, 0, f"{label} vision", plan)
        image = token.get("image")
        if image:
            # Only art uploaded through /map is a file this import can read
            # (app.py api_scene_token_image stores it as
            # <scene>_token_<token><ext>); any other URL is reported.
            uploaded = ART_URL.match(str(image))
            if uploaded and uploaded.group(1) == legacy_id and uploaded.group(2) == str(token.get("id")):
                plan.art_files[tid] = [f"{legacy_id}_token_{token.get('id')}{ext}" for ext in ART_EXTENSIONS]
            else:
                plan.art_unsupported += 1
        linked += bool(token.get("combatant_id") or token.get("character_id")) and data["actorId"] is None
        nameplates += token.get("show_nameplate", True) is False
        _add(plan, "token", token.get("id"), tid, data, token.get("visible_to_players", True) is False)
    if nameplates:
        plan.dropped.append(f"hidden nameplate on {nameplates} token(s) (the VTT shows every label)")
    if plan.art_unsupported:
        plan.dropped.append(f"token art on {plan.art_unsupported} token(s) (not an image uploaded to /map)")
    if linked:
        plan.dropped.append(f"tracker links (live HP and conditions) on {linked} token(s) (an id the VTT cannot hold)")

    # --- walls and doors -----------------------------------------------------------------------
    for wall in legacy.get("walls") or []:
        if not isinstance(wall, dict):
            continue
        wid = vtt_id(wall.get("id"))
        what = f"wall {wid}"
        data = {k: _number(wall.get(k, 0), 0, COORD_MAX, 0, f"{what} {k}", plan) for k in ("x1", "y1", "x2", "y2")}
        if (data["x1"], data["y1"]) == (data["x2"], data["y2"]):
            plan.dropped.append(f"{what}: zero length")
            continue
        if wall.get("kind") == "door":
            data["doorState"] = "secret" if wall.get("secret") else ("open" if wall.get("open") else "closed")
        else:
            data["doorState"] = "none"
        _add(plan, "wall", wall.get("id"), wid, data, False)

    # --- lights --------------------------------------------------------------------------------
    intensities = 0
    for light in legacy.get("lights") or []:
        if not isinstance(light, dict):
            continue
        lid = vtt_id(light.get("id"))
        what = f"light {lid}"
        data = {
            "x": _number(light.get("x", 0), 0, COORD_MAX, 0, f"{what} x", plan),
            "y": _number(light.get("y", 0), 0, COORD_MAX, 0, f"{what} y", plan),
            "radius": _number(light.get("radius", 350), 0, COORD_MAX, 350, f"{what} radius", plan),
            "color": _color(light.get("color"), 0xFFD580),
        }
        try:
            intensities += "intensity" in light and float(light.get("intensity") or 0.75) != 0.75
        except (TypeError, ValueError):
            intensities += 1
        _add(plan, "light", light.get("id"), lid, data, light.get("visible_to_players", True) is False)
    if intensities:
        plan.dropped.append(f"light intensity on {intensities} light(s)")
    _plan_templates(plan, legacy)
    return plan


TEMPLATE_SHAPES = ("burst", "emanation", "cone", "line")
TEMPLATE_COLOR = 0x75C5EB                     # /map's one template colour
TERRAIN_KIND = {"lava": 1, "water": 2, "poison": 3, "blood": 4}


def _plan_templates(plan: Plan, legacy: dict[str, Any]) -> None:
    """/map templates (pixel radius, anchor x1/y1, aim x2/y2) become VTT
    templates sized in squares. The emanation's token link is not kept, and a
    line wider than one square is drawn one square wide; both are reported."""
    grid_size = plan.scene["grid_size"]
    linked = wide = skipped = 0
    for tpl in legacy.get("templates") or []:
        if not isinstance(tpl, dict) or tpl.get("kind") not in TEMPLATE_SHAPES:
            skipped += 1
            continue
        tid = vtt_id(tpl.get("id"))
        what = f"template {tid}"
        x = _number(tpl.get("x1", 0), 0, COORD_MAX, 0, f"{what} x", plan)
        y = _number(tpl.get("y1", 0), 0, COORD_MAX, 0, f"{what} y", plan)
        try:
            squares = float(tpl.get("radius", grid_size)) / grid_size
        except (TypeError, ValueError):
            squares = 1
        size = int(max(1, min(60, round(squares))))
        data = {
            "shape": tpl["kind"], "x": x, "y": y,
            "aimX": _number(tpl.get("x2", x), 0, COORD_MAX, x, f"{what} aim x", plan),
            "aimY": _number(tpl.get("y2", y), 0, COORD_MAX, y, f"{what} aim y", plan),
            "size": size, "color": TEMPLATE_COLOR,
        }
        linked += bool(tpl.get("source_token_id"))
        try:
            wide += tpl["kind"] == "line" and float(tpl.get("width", grid_size)) > grid_size * 1.5
        except (TypeError, ValueError):
            pass
        _add(plan, "template", tpl.get("id"), tid, data, tpl.get("visible_to_players", True) is False)
    if linked:
        plan.dropped.append(f"emanation link to its token on {linked} template(s) (the area stays where it was)")
    if wide:
        plan.dropped.append(f"line width on {wide} template(s) (VTT lines are one square wide)")
    if skipped:
        plan.dropped.append(f"{skipped} template(s) of an unknown shape")


def _plan_terrain(plan: Plan, legacy: dict[str, Any], grid: dict[str, Any]) -> None:
    """/map terrain layers ({kind, cells: ["col,row"]}) become the VTT terrain
    grid when the grid starts at the corner. An empty source replaces an
    earlier import's terrain, as fog does."""
    from vtt import fog as fog_rules
    layers = legacy.get("terrain")
    if not isinstance(layers, list):
        return
    if not layers:
        plan.terrain = {"cells": [], "replace_only": True}
        return
    if grid.get("offset_x") or grid.get("offset_y"):
        plan.dropped.append("terrain (the legacy grid is offset)")
        return
    cols, rows = fog_rules.grid_of(plan.scene["width"], plan.scene["height"], plan.scene["grid_size"])
    if not fog_rules.available(cols, rows):
        plan.dropped.append("terrain (the scene's grid is too large for terrain)")
        return
    cells: dict[tuple[int, int], int] = {}
    outside = unknown = 0
    for layer in layers:
        if not isinstance(layer, dict) or layer.get("kind") not in TERRAIN_KIND:
            unknown += 1
            continue
        for text in layer.get("cells") or []:
            try:
                col, row = (int(part) for part in str(text).split(","))
            except ValueError:
                outside += 1
                continue
            if 0 <= col < cols and 0 <= row < rows:
                cells[(col, row)] = TERRAIN_KIND[layer["kind"]]
            else:
                outside += 1
    if outside:
        plan.dropped.append(f"{outside} terrain cell(s) outside the scene")
    if unknown:
        plan.dropped.append(f"{unknown} terrain layer(s) of an unknown kind")
    plan.terrain = {"cells": [(c, r, k) for (c, r), k in sorted(cells.items())],
                    "replace_only": not cells}


class Refused(Exception):
    """The plan cannot be written as a whole: an id collides, or the scene would not fit."""


def _check_destination(session: Any, campaign_id: str, plan: Plan) -> tuple[VttScene | None, dict]:
    """Raise Refused unless every destination id is free or provably written by
    an earlier import of the same source. Returns the existing scene and
    entities. Read only; ``apply_plan`` calls it under the stream lock."""
    if plan.duplicates:
        raise Refused(f"{plan.name}: more than one legacy item maps to {', '.join(plan.duplicates[:5])}")
    scene = session.get(VttScene, (campaign_id, plan.scene_id))
    if scene is not None:
        owner = session.get(VttImportedScene, (campaign_id, plan.scene_id))
        if owner is None or owner.source != scene_source(plan.legacy_id):
            raise Refused(f"{plan.name}: scene id {plan.scene_id} is already used by a scene "
                          "this import did not create")
    existing = {(row.kind, row.id): row for row in session.execute(
        select(VttEntity).where(VttEntity.campaign_id == campaign_id, VttEntity.scene_id == plan.scene_id)
    ).scalars()}
    owners = {(row.kind, row.id): row.source for row in session.execute(
        select(VttImportedEntity).where(VttImportedEntity.campaign_id == campaign_id,
                                        VttImportedEntity.scene_id == plan.scene_id)
    ).scalars()}
    for kind, eid, _data, _hidden in plan.entities:
        if (kind, eid) in existing and owners.get((kind, eid)) != plan.sources[(kind, eid)]:
            raise Refused(f"{plan.name}: {kind} id {eid} is already used by something "
                          "this import did not create")
    # The per-scene template cap that template.create enforces. An import never
    # deletes, so the scene ends with every template it has now (GM-added ones
    # included) plus each planned one it does not already hold; a re-import
    # that replaces its own templates counts them once.
    templates = {eid for kind, eid in existing if kind == "template"}
    templates.update(eid for kind, eid, _data, _hidden in plan.entities if kind == "template")
    if len(templates) > MAX_TEMPLATES:
        raise Refused(f"{plan.name}: {len(templates)} templates; a scene holds at most {MAX_TEMPLATES}")
    return scene, existing


def apply_plan(db: Database, campaign_id: str, plan: Plan, *, on_table: bool = False) -> dict[str, int]:
    """Write one scene in one transaction. Returns counts; raises Refused (and
    writes nothing) when an id is held by a row this import cannot prove it
    wrote from the same source, or the scene would break the frame limit or
    entity caps."""
    counts = {"created": 0, "updated": 0}
    # The transaction rolls back on any exception, Refused included.
    with db.transaction() as session:
        # The stream lock first, exactly as a command takes it, so an import and
        # a scene edit never wait on each other's locks in opposite order; it
        # also serializes two imports of one scene before either reads it.
        lock_stream(session, campaign_id)
        source = scene_source(plan.legacy_id)
        scene, existing = _check_destination(session, campaign_id, plan)

        fields = dict(plan.scene)
        if on_table:
            fields["published"] = True
        if scene is None:
            session.add(VttScene(campaign_id=campaign_id, id=plan.scene_id, **fields))
            session.flush()
            session.add(VttImportedScene(campaign_id=campaign_id, scene_id=plan.scene_id, source=source))
        else:
            for name, value in fields.items():
                setattr(scene, name, value)
        session.flush()
        advance(session, campaign_id, {"kind": "scene", "sceneId": plan.scene_id})
        changed = []
        for kind, eid, data, hidden in plan.entities:
            row = existing.get((kind, eid))
            if row is None:
                session.add(VttEntity(campaign_id=campaign_id, scene_id=plan.scene_id, kind=kind, id=eid,
                                      data=data, hidden=hidden))
                session.flush()
                session.add(VttImportedEntity(campaign_id=campaign_id, scene_id=plan.scene_id, kind=kind,
                                              id=eid, source=plan.sources[(kind, eid)]))
                counts["created"] += 1
            else:
                row.data, row.hidden = data, hidden
                counts["updated"] += 1
            changed.append([kind, eid])
        session.flush()
        if plan.terrain is not None and _write_terrain(session, campaign_id, plan):
            changed.append(["terrain", "terrain"])
        if plan.fog is not None and _write_fog(session, campaign_id, plan):
            # Written before sizing (its bitset counts too). Fog decides what the
            # table sees, so every token and light of the scene is rechecked,
            # including ones the GM added since the last import.
            known = {(k, i) for k, i in changed}
            changed.append(["fog", "fog"])
            for kind, eid in session.execute(
                select(VttEntity.kind, VttEntity.id).where(
                    VttEntity.campaign_id == campaign_id, VttEntity.scene_id == plan.scene_id,
                    VttEntity.kind.in_(("token", "light")))).all():
                if (kind, eid) not in known:
                    changed.append([kind, eid])
        size, gm_count, table_count = gm_snapshot_bytes(session, campaign_id, plan.scene_id)
        if size + FRAME_HEADROOM > MAX_JSON_BYTES or gm_count > GM_ENTITY_CAP or table_count > TABLE_ENTITY_CAP:
            raise Refused(f"{plan.name}: {gm_count} entities, {size} bytes; over the frame limit or entity caps")
        if changed:
            advance(session, campaign_id, {"kind": "entities", "sceneId": plan.scene_id, "changed": changed})
        if on_table:
            pointer = session.get(VttTablePointer, campaign_id)
            if pointer is None:
                session.add(VttTablePointer(campaign_id=campaign_id, scene_id=plan.scene_id))
            else:
                pointer.scene_id = plan.scene_id
            session.flush()
            advance(session, campaign_id, {"kind": "table-pointer"})
    return counts


def _store_uploads(db: Database, backend, campaign_id: str, plan: Plan) -> None:
    from vtt.assets.backends import AssetBackendError
    from vtt.assets.images import BadImage
    from vtt.assets.service import store
    if backend is None:
        raise Refused(f"{plan.name}: images to import but no asset store is configured")
    try:
        for data in plan.uploads:
            store(db, backend, campaign_id, "legacy-import", data)
    except AssetBackendError as exc:
        raise Refused(f"{plan.name}: the asset store is unavailable, try again") from exc
    except BadImage as exc:  # already checked once; refuse rather than reference a missing asset
        raise Refused(f"{plan.name}: an image could not be stored ({exc})") from exc


def resolve_assets(plan: Plan, read_asset) -> None:
    """Read and check the legacy images a plan names, and point the scene and
    tokens at the asset ids they will have. ``read_asset(filename)`` returns
    the file's bytes or None; without a reader every image is reported as not
    brought across. Checked bytes are queued in ``plan.uploads``."""
    from vtt.assets.images import BadImage, inspect
    from vtt.assets.service import asset_id

    def first_good(candidates: list[str]) -> tuple[bytes | None, str | None]:
        problem = "file missing"
        for name in candidates:
            data = read_asset(name) if read_asset else None
            if data is None:
                continue
            try:
                inspect(data)
            except BadImage as bad:
                problem = str(bad)
                continue
            return data, None
        return None, problem if read_asset else "no asset reader"

    if plan.background_files:
        data, problem = first_good(plan.background_files)
        if data is None:
            plan.dropped.append(f"background image ({problem})")
        else:
            plan.scene["background_asset_id"] = asset_id(hashlib.sha256(data).hexdigest())
            plan.uploads.append(data)
    missing = 0
    by_id = {eid: data for kind, eid, data, _hidden in plan.entities if kind == "token"}
    for tid, candidates in plan.art_files.items():
        data, _problem = first_good(candidates)
        if data is None or tid not in by_id:
            missing += 1
            continue
        by_id[tid]["artAssetId"] = asset_id(hashlib.sha256(data).hexdigest())
        plan.uploads.append(data)
    if missing:
        plan.dropped.append(f"token art on {missing} token(s) (file missing or unreadable)")


def _write_terrain(session, campaign_id: str, plan: Plan) -> bool:
    """The scene's terrain as /map had it; an empty source removes an earlier
    import's terrain (no row reads as nothing painted)."""
    from vtt import fog as fog_rules
    from vtt import terrain as terrain_rules
    from vtt.storage.models import VttTerrain
    row = session.get(VttTerrain, (campaign_id, plan.scene_id))
    if plan.terrain.get("replace_only"):
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True
    cols, rows = fog_rules.grid_of(plan.scene["width"], plan.scene["height"], plan.scene["grid_size"])
    view = terrain_rules.TerrainView(cols, rows, terrain_rules.empty_cells(cols, rows))
    cells = terrain_rules.with_cells(view, plan.terrain["cells"])
    if row is None:
        session.add(VttTerrain(campaign_id=campaign_id, scene_id=plan.scene_id, cols=cols, rows=rows, cells=cells))
    else:
        row.cols, row.rows, row.cells = cols, rows, cells
    session.flush()
    return True


def _write_fog(session, campaign_id: str, plan: Plan) -> bool:
    """The scene's fog as /map had it (a re-import replaces it: the legacy fog
    is the source, like every other imported field). Returns whether anything
    was written: an off-and-empty source fog writes nothing for a scene that
    has no fog row yet."""
    from vtt import fog as fog_rules
    from vtt.storage.models import VttFog
    row = session.get(VttFog, (campaign_id, plan.scene_id))
    if plan.fog.get("replace_only"):
        # No row means fog off and nothing revealed: exactly the source's fog,
        # and it holds on any grid (a row cannot exist for one too large).
        if row is None:
            return False
        session.delete(row)
        session.flush()
        return True
    cols, rows = fog_rules.grid_of(plan.scene["width"], plan.scene["height"], plan.scene["grid_size"])
    view = fog_rules.FogView(False, cols, rows, fog_rules.empty_bits(cols, rows), plan.scene["grid_size"])
    bits = fog_rules.with_cells(view, plan.fog["cells"], True)
    if row is None:
        session.add(VttFog(campaign_id=campaign_id, scene_id=plan.scene_id, enabled=plan.fog["enabled"],
                           cols=cols, rows=rows, revealed=bits))
    else:
        row.enabled, row.cols, row.rows, row.revealed = plan.fog["enabled"], cols, rows, bits
    session.flush()
    return True


def import_campaign(db: Database, campaign_id: str, legacy_scenes: list[dict[str, Any]],
                    legacy_table_scene_id: str | None, *, apply: bool,
                    read_asset=None, asset_backend=None) -> dict[str, Any]:
    """Plan (and with ``apply``, write) every legacy scene of a campaign. One
    transaction per scene: a refused scene leaves the others imported. The
    legacy table scene becomes the VTT table scene.

    ``read_asset(campaign_id, filename)`` reads a /map image file and
    ``asset_backend`` stores it (M6). An image is stored before its scene is
    written; a scene that is then refused leaves an unreferenced asset, which
    is harmless (content-addressed, and no row points at it)."""
    report: list[dict[str, Any]] = []
    for legacy in legacy_scenes:
        if not isinstance(legacy, dict) or legacy.get("campaign_id") not in (None, campaign_id):
            continue
        plan = plan_scene(legacy)
        reader = (lambda name, _cid=campaign_id: read_asset(_cid, name)) if read_asset else None
        resolve_assets(plan, reader)
        kinds: dict[str, int] = {}
        for kind, *_ in plan.entities:
            kinds[kind] = kinds.get(kind, 0) + 1
        entry: dict[str, Any] = {
            "legacyId": plan.legacy_id, "sceneId": plan.scene_id, "name": plan.name, "entities": kinds,
            "dropped": plan.dropped, "clamped": plan.clamped,
            "onTable": bool(legacy_table_scene_id) and legacy.get("id") == legacy_table_scene_id,
            "status": "planned",
        }
        if plan.uploads:
            entry["images"] = len(plan.uploads)
        try:
            if apply:
                if plan.uploads:
                    _store_uploads(db, asset_backend, campaign_id, plan)
                counts = apply_plan(db, campaign_id, plan, on_table=entry["onTable"])
                entry["status"] = "imported"
                entry.update(counts)
            else:
                # The dry run reports a collision too (it is re-checked under
                # the lock when applied).
                with db.transaction() as session:
                    _check_destination(session, campaign_id, plan)
        except Refused as refusal:
            entry["status"] = "refused"
            entry["reason"] = str(refusal)
        report.append(entry)
    return {"campaignId": campaign_id, "applied": apply, "scenes": report}
