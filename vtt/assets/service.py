"""The asset store's rules (M6, handoff 053).

- ``store`` checks the bytes (``images.inspect``), writes them to the backend
  under ``<campaign>/<sha256>``, then records the campaign's row. Bytes first:
  a failure after the write leaves an unreferenced blob, never a row whose
  bytes are missing. The same bytes again are the same asset (idempotent).
- ``exists`` is the command rule: a scene or token may only reference an asset
  this campaign holds.
- ``table_may_read`` is the table's read rule. A paired display may read only
  an asset the current table projection shows: the published table scene's
  background, or the art of a token in it that is not hidden and (parity P1)
  not under fog. The same image on a visible token, or as the background,
  stays readable even if a covered token also uses it. A content-hash id
  is not a secret (anyone holding the image can compute it), so a hidden
  token's art must be refused by rule, not by obscurity.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.persistence.database import Database
from vtt.assets.backends import AssetBackend, key_for
from vtt.assets.images import inspect
from vtt.storage.models import VttAsset, VttEntity, VttScene, VttTablePointer

PREFIX = "asset_"


def asset_id(sha256: str) -> str:
    return PREFIX + sha256[:40]


def describe(row: VttAsset) -> dict[str, Any]:
    return {"id": row.id, "mediaType": row.media_type, "byteSize": row.byte_size,
            "width": row.width, "height": row.height}


def store(db: Database, backend: AssetBackend, campaign_id: str, user_id: str, data: bytes) -> tuple[dict, bool]:
    """Returns (description, created). Raises images.BadImage for a refused file
    and backends.AssetBackendError when the backend cannot be written."""
    info = inspect(data)
    sha = hashlib.sha256(data).hexdigest()
    new_id = asset_id(sha)
    with db.session() as session:
        found = session.get(VttAsset, (campaign_id, new_id))
        if found is not None:
            return describe(found), False
    backend.put(key_for(campaign_id, sha), data, info.media_type)
    with db.transaction() as session:
        found = session.get(VttAsset, (campaign_id, new_id), with_for_update=True)
        if found is not None:                       # a concurrent upload of the same bytes won
            return describe(found), False
        row = VttAsset(campaign_id=campaign_id, id=new_id, sha256=sha, media_type=info.media_type,
                       byte_size=len(data), width=info.width, height=info.height, created_by=user_id)
        session.add(row)
        session.flush()
        return describe(row), True


def exists(session: Session, campaign_id: str, asset: str) -> bool:
    return session.get(VttAsset, (campaign_id, asset)) is not None


def read(session: Session, backend: AssetBackend, campaign_id: str, asset: str) -> tuple[VttAsset, bytes] | None:
    row = session.get(VttAsset, (campaign_id, asset))
    if row is None:
        return None
    data = backend.get(key_for(campaign_id, row.sha256))
    return (row, data) if data is not None else None


def table_may_read(session: Session, campaign_id: str, asset: str) -> bool:
    pointer = session.get(VttTablePointer, campaign_id)
    if pointer is None or pointer.scene_id is None:
        return False
    scene = session.get(VttScene, (campaign_id, pointer.scene_id))
    if scene is None or not scene.published:
        return False
    if scene.background_asset_id == asset:
        return True
    from vtt import fog, live
    fog_view = fog.load(session, scene)[1]
    actors = None
    tokens = session.execute(
        select(VttEntity).where(VttEntity.campaign_id == campaign_id, VttEntity.scene_id == scene.id,
                                VttEntity.kind == "token", VttEntity.hidden.is_(False))
    ).scalars()
    for row in tokens:
        data = row.data or {}
        if data.get("artAssetId") != asset:
            continue
        if fog_view is not None and fog_view.hides(data.get("x", 0), data.get("y", 0)):
            continue
        # The same rules as the table projection, parity P3 included: a token
        # whose tracker actor is hidden is not on the table, so neither is its art.
        if data.get("actorId"):
            if actors is None:
                actors = live.load(session, campaign_id)
            if (actors.get(data["actorId"]) or {}).get("hidden"):
                continue
        return True
    return False
