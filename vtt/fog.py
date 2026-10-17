"""Fog of war for the VTT (parity P1, handoff 060).

Fog is cell-based, like /map: the scene's grid is ``cols`` x ``rows`` cells
(``ceil(width / gridSize)`` by ``ceil(height / gridSize)``), and the GM reveals
or hides cells. Unlike /map it is applied ON THE SERVER: when fog is on, the
table projection leaves out every token and light whose centre cell is not
revealed, so a hidden creature never reaches the display (or, later, a player).

The bitset is row-major, least significant bit first: cell ``(col, row)`` is
bit ``index % 8`` of byte ``index // 8`` with ``index = row * cols + col``. A
scene whose grid has more than ``MAX_CELLS`` cells cannot use fog.
"""

from __future__ import annotations

import base64
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from vtt.storage.models import VttFog

MAX_CELLS = 262144


def grid_of(width: float, height: float, grid_size: float) -> tuple[int, int]:
    return max(1, math.ceil(width / grid_size)), max(1, math.ceil(height / grid_size))


def available(cols: int, rows: int) -> bool:
    return cols * rows <= MAX_CELLS


def empty_bits(cols: int, rows: int) -> bytes:
    return bytes(-(-cols * rows // 8))


def encode(bits: bytes) -> str:
    return base64.urlsafe_b64encode(bits).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class FogView:
    """A scene's fog as the projections need it."""

    enabled: bool
    cols: int
    rows: int
    bits: bytes
    grid_size: float

    def revealed(self, col: int, row: int) -> bool:
        index = row * self.cols + col
        return bool(self.bits[index >> 3] >> (index & 7) & 1)

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        col = min(self.cols - 1, max(0, int(x // self.grid_size)))
        row = min(self.rows - 1, max(0, int(y // self.grid_size)))
        return col, row

    def hides(self, x: float, y: float) -> bool:
        """True when fog is on and the cell holding this point is not revealed."""
        return self.enabled and not self.revealed(*self.cell_of(x, y))

    def any_revealed(self) -> bool:
        return any(self.bits)

    @property
    def encoded(self) -> str:
        return encode(self.bits)


def view_of(scene, row: VttFog | None) -> FogView | None:
    """The scene's fog, or None when its grid is too large for fog. A missing
    row is fog off with nothing revealed; a row written for another grid reads
    as nothing revealed (it keeps its on/off)."""
    cols, rows = grid_of(scene.width, scene.height, scene.grid_size)
    if not available(cols, rows):
        return None
    if row is None:
        return FogView(False, cols, rows, empty_bits(cols, rows), scene.grid_size)
    bits = row.revealed if (row.cols, row.rows) == (cols, rows) else empty_bits(cols, rows)
    return FogView(bool(row.enabled), cols, rows, bytes(bits), scene.grid_size)


def load(session: Session, scene, *, for_update: bool = False) -> tuple[VttFog | None, FogView | None]:
    query = select(VttFog).where(VttFog.campaign_id == scene.campaign_id, VttFog.scene_id == scene.id)
    if for_update:
        query = query.with_for_update()
    row = session.execute(query).scalar_one_or_none()
    return row, view_of(scene, row)


def with_cells(view: FogView, cells, reveal: bool) -> bytes:
    """The bitset with these cells revealed (or hidden). Cells must be in range;
    raises ValueError otherwise."""
    bits = bytearray(view.bits)
    for col, row in cells:
        if not (0 <= col < view.cols and 0 <= row < view.rows):
            raise ValueError("cell outside the scene's grid")
        index = row * view.cols + col
        if reveal:
            bits[index >> 3] |= 1 << (index & 7)
        else:
            bits[index >> 3] &= ~(1 << (index & 7)) & 0xFF
    return bytes(bits)
