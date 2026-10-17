import { describe, expect, it } from 'vitest';
import { cellAt, chunk, floodRegion, isRevealed, type FillWall, type FogGrid } from './fogFill';

// A 6 x 4 grid of 64 px cells, nothing revealed.
const grid = (bits?: Uint8Array): FogGrid => ({ cols: 6, rows: 4, gridSize: 64, bits: bits ?? new Uint8Array(3) });
// A wall straight down the grid line between columns 2 and 3, the full height.
const divider = (doorState: FillWall['doorState'] = 'none'): FillWall => ({ x1: 192, y1: 0, x2: 192, y2: 256, doorState });
const sorted = (cells: Array<[number, number]>) => cells.map((c) => c.join(',')).sort();

describe('floodRegion', () => {
  it('with no walls, reveals the whole grid', () => {
    expect(floodRegion(grid(), [0, 0], [], 'reveal')).toHaveLength(24);
  });

  it('stops at a wall drawn exactly on a grid line (centre-to-centre test)', () => {
    const left = floodRegion(grid(), [0, 0], [divider()], 'reveal');
    expect(left).toHaveLength(12);
    expect(left.every(([c]) => c <= 2)).toBe(true);
  });

  it('closed, locked and secret doors block; an open door lets the flood through', () => {
    for (const state of ['closed', 'locked', 'secret'] as const) {
      expect(floodRegion(grid(), [0, 0], [divider(state)], 'reveal'), state).toHaveLength(12);
    }
    expect(floodRegion(grid(), [0, 0], [divider('open')], 'reveal')).toHaveLength(24);
  });

  it('a gap in the wall lets the flood through it', () => {
    const shortWall: FillWall = { x1: 192, y1: 0, x2: 192, y2: 192, doorState: 'none' }; // rows 0-2 only
    expect(floodRegion(grid(), [0, 0], [shortWall], 'reveal')).toHaveLength(24);
  });

  it('returns only cells whose state changes, so its undo is the exact opposite', () => {
    const bits = new Uint8Array(3);
    bits[0] = 0b00000011; // cells (0,0) and (1,0) already revealed
    const g = grid(bits);
    const reveal = floodRegion(g, [0, 0], [divider()], 'reveal');
    expect(sorted(reveal)).not.toContain('0,0');
    expect(reveal).toHaveLength(10);
    expect(sorted(floodRegion(g, [0, 0], [divider()], 'hide'))).toEqual(['0,0', '1,0']);
  });
});

describe('fog grid helpers', () => {
  it('reads bits row-major, least significant first, and maps points to cells', () => {
    const bits = new Uint8Array(3);
    bits[1] = 0b00000100; // index 10 = (4, 1)
    expect(isRevealed(grid(bits), 4, 1)).toBe(true);
    expect(isRevealed(grid(bits), 3, 1)).toBe(false);
    expect(cellAt(grid(), 130, 70)).toEqual([2, 1]);
    expect(cellAt(grid(), -1, 0)).toBeNull();
    expect(cellAt(grid(), 384, 0)).toBeNull();
  });

  it('splits large regions into commands within the contract cap', () => {
    expect(chunk(Array.from({ length: 45000 }, (_, i) => i)).map((c) => c.length)).toEqual([20000, 20000, 5000]);
  });
});
