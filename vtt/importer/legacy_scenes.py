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

The legacy store is only read. Nothing in it is changed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from core.persistence.database import Database
from vtt.commands.service import FRAME_HEADROOM, GM_ENTITY_CAP, TABLE_ENTITY_CAP, gm_snapshot_bytes
from vtt.protocol.boundary import MAX_JSON_BYTES
from vtt.storage.models import VttEntity, VttImportedEntity, VttImportedScene, VttScene, VttTablePointer
from vtt.storage.stream import advance, lock_stream

ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
PREFIX = "legacy_"
HASHED_PREFIX = "legacyh_"
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

    # --- what has no VTT equivalent yet ------------------------------------------------------
    if legacy.get("background"):
        plan.dropped.append("background image (needs the asset store, M6)")
    fog = legacy.get("fog") or {}
    if fog.get("enabled") or _count(fog.get("operations")) or _count(fog.get("revealed_cells")):
        plan.dropped.append("fog and revealed areas")
    if _count(legacy.get("templates")):
        plan.dropped.append(f"{_count(legacy.get('templates'))} template(s)")
    if _count(legacy.get("terrain")):
        plan.dropped.append(f"{_count(legacy.get('terrain'))} terrain area(s)")
    settings = legacy.get("settings") or {}
    if settings.get("dynamic_lighting"):
        plan.dropped.append("dynamic lighting and vision")

    # --- tokens --------------------------------------------------------------------------------
    art = linked = vision = nameplates = 0
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
            "actorId": None,
            "locked": bool(token.get("locked", False)),
            "controllerIds": [],
        }
        art += bool(token.get("image"))
        linked += bool(token.get("combatant_id") or token.get("character_id"))
        try:
            vision += float(token.get("vision_radius") or 0) > 0
        except (TypeError, ValueError):
            vision += 1
        nameplates += token.get("show_nameplate", True) is False
        _add(plan, "token", token.get("id"), tid, data, token.get("visible_to_players", True) is False)
    if vision:
        plan.dropped.append(f"vision radius on {vision} token(s) (the VTT has no vision yet)")
    if nameplates:
        plan.dropped.append(f"hidden nameplate on {nameplates} token(s) (the VTT shows every label)")
    if art:
        plan.dropped.append(f"token art on {art} token(s)")
    if linked:
        plan.dropped.append(f"tracker links (live HP and conditions) on {linked} token(s)")

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
    return plan


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


def import_campaign(db: Database, campaign_id: str, legacy_scenes: list[dict[str, Any]],
                    legacy_table_scene_id: str | None, *, apply: bool) -> dict[str, Any]:
    """Plan (and with ``apply``, write) every legacy scene of a campaign. One
    transaction per scene: a refused scene leaves the others imported. The
    legacy table scene becomes the VTT table scene."""
    report: list[dict[str, Any]] = []
    for legacy in legacy_scenes:
        if not isinstance(legacy, dict) or legacy.get("campaign_id") not in (None, campaign_id):
            continue
        plan = plan_scene(legacy)
        kinds: dict[str, int] = {}
        for kind, *_ in plan.entities:
            kinds[kind] = kinds.get(kind, 0) + 1
        entry: dict[str, Any] = {
            "legacyId": plan.legacy_id, "sceneId": plan.scene_id, "name": plan.name, "entities": kinds,
            "dropped": plan.dropped, "clamped": plan.clamped,
            "onTable": bool(legacy_table_scene_id) and legacy.get("id") == legacy_table_scene_id,
            "status": "planned",
        }
        try:
            if apply:
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
