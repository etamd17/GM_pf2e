"""Painted terrain for the VTT (parity P2, handoff 065).

Cosmetic, as on /map: lava, water, poison and blood painted on grid cells.
Nothing reads it for rules. Stored and sent as a 4-bit kind per cell, two
cells per byte, low nibble first, over the same grid as fog
(``vtt.fog.grid_of``) and under the same 262,144-cell cap.

The table's copy is masked by fog: while fog is on, every cell that is not
revealed is sent as 0, so terrain inside a hidden room never reaches the
display.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from vtt import fog
from vtt.storage.models import VttTerrain

KINDS = {0: "none", 1: "lava", 2: "water", 3: "poison", 4: "blood"}
MAX_KIND = 4


def empty_cells(cols: int, rows: int) -> bytes:
    return bytes(-(-cols * rows // 2))


@dataclass(frozen=True)
class TerrainView:
    cols: int
    rows: int
    cells: bytes

    def kind_at(self, col: int, row: int) -> int:
        index = row * self.cols + col
        byte = self.cells[index >> 1]
        return byte >> 4 if index & 1 else byte & 0x0F

    def any_painted(self) -> bool:
        return any(self.cells)

    @property
    def encoded(self) -> str:
        return fog.encode(self.cells)

    def masked(self, fog_view) -> TerrainView:
        """The table's copy: cells under fog read as 0."""
        if fog_view is None or not fog_view.enabled:
            return self
        out = bytearray(self.cells)
        for index in range(self.cols * self.rows):
            col, row = index % self.cols, index // self.cols
            if not fog_view.revealed(col, row):
                out[index >> 1] &= 0x0F if index & 1 else 0xF0
        return TerrainView(self.cols, self.rows, bytes(out))


def view_of(scene, row: VttTerrain | None) -> TerrainView | None:
    cols, rows = fog.grid_of(scene.width, scene.height, scene.grid_size)
    if not fog.available(cols, rows):
        return None
    if row is None or (row.cols, row.rows) != (cols, rows):
        return TerrainView(cols, rows, empty_cells(cols, rows))
    return TerrainView(cols, rows, bytes(row.cells))


def load(session: Session, scene, *, for_update: bool = False) -> tuple[VttTerrain | None, TerrainView | None]:
    query = select(VttTerrain).where(VttTerrain.campaign_id == scene.campaign_id, VttTerrain.scene_id == scene.id)
    if for_update:
        query = query.with_for_update()
    row = session.execute(query).scalar_one_or_none()
    return row, view_of(scene, row)


def with_cells(view: TerrainView, cells) -> bytes:
    """The cells with each ``(col, row, kind)`` set. Raises ValueError for a
    cell outside the grid or a kind above MAX_KIND."""
    out = bytearray(view.cells)
    for col, row, kind in cells:
        if not (0 <= col < view.cols and 0 <= row < view.rows) or not (0 <= kind <= MAX_KIND):
            raise ValueError("terrain cell outside the grid or of an unknown kind")
        index = row * view.cols + col
        if index & 1:
            out[index >> 1] = (out[index >> 1] & 0x0F) | (kind << 4)
        else:
            out[index >> 1] = (out[index >> 1] & 0xF0) | kind
    return bytes(out)
