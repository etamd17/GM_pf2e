"""Canonical VTT writes. Each one commits its change together with a stream
position and outbox row (``stream.advance``) in one transaction, so delivery
can never see a change without its event or an event without its change.

M1 uses these to seed state and to prove the delivery boundary; M2's command
layer (receipts, the campaign lock under contention, acks) is built on them.
After a write commits, call ``Hub.notify(campaign_id)``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from core.persistence.database import Database

from .models import VttEntity, VttScene, VttTablePointer
from .stream import advance


def create_scene(db: Database, campaign_id: str, scene_id: str, *, name: str, width: float = 2048,
                 height: float = 2048, grid_size: float = 64, show_grid: bool = True,
                 background_asset_id: str | None = None, published: bool = False) -> int:
    with db.transaction() as session:
        session.add(VttScene(id=scene_id, campaign_id=campaign_id, name=name, width=width, height=height,
                             grid_size=grid_size, show_grid=show_grid,
                             background_asset_id=background_asset_id, published=published))
        session.flush()
        return advance(session, campaign_id, {"kind": "scene", "sceneId": scene_id})


def update_scene(db: Database, campaign_id: str, scene_id: str, **fields: Any) -> int:
    allowed = {"name", "width", "height", "grid_size", "show_grid", "background_asset_id", "published"}
    if set(fields) - allowed:
        raise ValueError("unknown scene field")
    with db.transaction() as session:
        scene = session.get(VttScene, (campaign_id, scene_id))
        if scene is None or scene.campaign_id != campaign_id:
            raise LookupError("scene not found")
        for name, value in fields.items():
            setattr(scene, name, value)
        return advance(session, campaign_id, {"kind": "scene", "sceneId": scene_id})


def set_table_pointer(db: Database, campaign_id: str, scene_id: str | None) -> int:
    with db.transaction() as session:
        pointer = session.get(VttTablePointer, campaign_id)
        if pointer is None:
            session.add(VttTablePointer(campaign_id=campaign_id, scene_id=scene_id))
        else:
            pointer.scene_id = scene_id
        session.flush()
        return advance(session, campaign_id, {"kind": "table-pointer"})


def upsert_entity(db: Database, campaign_id: str, scene_id: str, kind: str, entity_id: str,
                  data: dict[str, Any], *, hidden: bool = False) -> int:
    with db.transaction() as session:
        row = session.get(VttEntity, (campaign_id, scene_id, kind, entity_id))
        if row is None:
            session.add(VttEntity(campaign_id=campaign_id, scene_id=scene_id, kind=kind, id=entity_id,
                                  data=dict(data), hidden=hidden))
        else:
            if row.campaign_id != campaign_id:
                raise LookupError("entity not found")
            row.data = dict(data)
            row.hidden = hidden
        session.flush()
        return advance(session, campaign_id,
                       {"kind": "entities", "sceneId": scene_id, "changed": [[kind, entity_id]]})


def delete_entity(db: Database, campaign_id: str, scene_id: str, kind: str, entity_id: str) -> int:
    with db.transaction() as session:
        row = session.execute(
            select(VttEntity).where(VttEntity.campaign_id == campaign_id, VttEntity.scene_id == scene_id,
                                    VttEntity.kind == kind, VttEntity.id == entity_id)
        ).scalar_one_or_none()
        if row is None:
            raise LookupError("entity not found")
        session.delete(row)
        session.flush()
        return advance(session, campaign_id,
                       {"kind": "entities", "sceneId": scene_id, "changed": [[kind, entity_id]]})
