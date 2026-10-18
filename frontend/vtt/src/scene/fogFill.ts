// Revealing a room (parity P1, handoff 060). One click floods from the clicked
// cell across the grid, four neighbours at a time, and stops where a step
// would cross a blocking wall: a plain wall or a closed, locked or secret door.
// An open door lets the flood through. This is /map's rule, including its
// lesson: a step is tested from cell CENTRE to cell centre, because testing
// along the cell edge misses every wall drawn exactly on a grid line.
//
// The result is only the cells whose state would change (revealing a hidden
// cell, or hiding a revealed one), so the command is minimal and its undo is
// exactly the opposite mode on exactly those cells.

export interface FogGrid {
  cols: number;
  rows: number;
  gridSize: number;
  bits: Uint8Array; // row-major, least significant bit first
}

export interface FillWall {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  doorState: 'none' | 'open' | 'closed' | 'locked' | 'secret';
}

export type Cell = [number, number];

export function isRevealed(grid: FogGrid, col: number, row: number): boolean {
  const index = row * grid.cols + col;
  return ((grid.bits[index >> 3] ?? 0) >> (index & 7)) & 1 ? true : false;
}

export function cellAt(grid: Pick<FogGrid, 'cols' | 'rows' | 'gridSize'>, x: number, y: number): Cell | null {
  const col = Math.floor(x / grid.gridSize);
  const row = Math.floor(y / grid.gridSize);
  if (col < 0 || row < 0 || col >= grid.cols || row >= grid.rows) return null;
  return [col, row];
}

const blocks = (w: FillWall) => w.doorState !== 'open';

// Proper segment intersection (touching counts as crossing, so a wall that
// ends exactly on a centre line still closes the gap).
function crosses(ax: number, ay: number, bx: number, by: number, w: FillWall): boolean {
  const d = (px: number, py: number, qx: number, qy: number, rx: number, ry: number) =>
    (qx - px) * (ry - py) - (qy - py) * (rx - px);
  const d1 = d(w.x1, w.y1, w.x2, w.y2, ax, ay);
  const d2 = d(w.x1, w.y1, w.x2, w.y2, bx, by);
  const d3 = d(ax, ay, bx, by, w.x1, w.y1);
  const d4 = d(ax, ay, bx, by, w.x2, w.y2);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return true;
  const on = (px: number, py: number, qx: number, qy: number, rx: number, ry: number) =>
    Math.min(px, qx) <= rx && rx <= Math.max(px, qx) && Math.min(py, qy) <= ry && ry <= Math.max(py, qy);
  return (
    (d1 === 0 && on(w.x1, w.y1, w.x2, w.y2, ax, ay)) ||
    (d2 === 0 && on(w.x1, w.y1, w.x2, w.y2, bx, by)) ||
    (d3 === 0 && on(ax, ay, bx, by, w.x1, w.y1)) ||
    (d4 === 0 && on(ax, ay, bx, by, w.x2, w.y2))
  );
}

// Walls bucketed by the cells their bounding box touches, so each step only
// tests the walls near it (a scene can have hundreds of walls and a quarter of
// a million cells).
function indexWalls(grid: Pick<FogGrid, 'cols' | 'rows' | 'gridSize'>, walls: readonly FillWall[]): Map<number, FillWall[]> {
  const buckets = new Map<number, FillWall[]>();
  const g = grid.gridSize;
  for (const w of walls) {
    if (!blocks(w)) continue;
    const c0 = Math.max(0, Math.floor(Math.min(w.x1, w.x2) / g) - 1);
    const c1 = Math.min(grid.cols - 1, Math.floor(Math.max(w.x1, w.x2) / g) + 1);
    const r0 = Math.max(0, Math.floor(Math.min(w.y1, w.y2) / g) - 1);
    const r1 = Math.min(grid.rows - 1, Math.floor(Math.max(w.y1, w.y2) / g) + 1);
    for (let r = r0; r <= r1; r += 1) {
      for (let c = c0; c <= c1; c += 1) {
        const key = r * grid.cols + c;
        const list = buckets.get(key);
        if (list) list.push(w);
        else buckets.set(key, [w]);
      }
    }
  }
  return buckets;
}

// The connected region around `start`, bounded by blocking walls and the grid
// edge, as the cells whose state `mode` would change.
export function floodRegion(grid: FogGrid, start: Cell, walls: readonly FillWall[], mode: 'reveal' | 'hide'): Cell[] {
  return regionCells(grid, start, walls).filter(([c, r]) => isRevealed(grid, c, r) !== (mode === 'reveal'));
}

// Every cell of the connected region around `start` (fog reveal and terrain
// paint both use it; each keeps only the cells it would change).
export function regionCells(grid: Pick<FogGrid, 'cols' | 'rows' | 'gridSize'>, start: Cell, walls: readonly FillWall[]): Cell[] {
  const buckets = indexWalls(grid, walls);
  const g = grid.gridSize;
  const centre = (c: number) => c * g + g / 2;
  const seen = new Uint8Array(grid.cols * grid.rows);
  const queue: number[] = [start[1] * grid.cols + start[0]];
  seen[queue[0] as number] = 1;
  const changed: Cell[] = [];
  for (let head = 0; head < queue.length; head += 1) {
    const index = queue[head] as number;
    const col = index % grid.cols;
    const row = (index - col) / grid.cols;
    changed.push([col, row]);
    const near = buckets.get(index) ?? [];
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]] as const) {
      const nc = col + dc;
      const nr = row + dr;
      if (nc < 0 || nr < 0 || nc >= grid.cols || nr >= grid.rows) continue;
      const next = nr * grid.cols + nc;
      if (seen[next]) continue;
      const ax = centre(col), ay = centre(row), bx = centre(nc), by = centre(nr);
      if (near.some((w) => crosses(ax, ay, bx, by, w))) continue;
      seen[next] = 1;
      queue.push(next);
    }
  }
  return changed;
}

// The kind (0-4) of one cell of a 4-bit terrain grid (parity P2).
export function terrainKindAt(cells: Uint8Array, cols: number, col: number, row: number): number {
  const index = row * cols + col;
  const byte = cells[index >> 1] ?? 0;
  return index & 1 ? byte >> 4 : byte & 0x0f;
}

// A command carries at most this many cells (the contract's cap).
export const FOG_CELLS_PER_COMMAND = 20000;

export function chunk<T>(items: readonly T[], size = FOG_CELLS_PER_COMMAND): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}
