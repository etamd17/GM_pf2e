import { describe, expect, it } from 'vitest';
import { EMPTY_SCENE, SCENE_LIMITS, gridLines, normalizeScene, tokenRadius } from './renderModel';

// VTT-20261012-010 finding 2: invalid geometry must never hang the renderer.

describe('gridLines', () => {
  it('produces the expected lines for a normal grid', () => {
    expect(gridLines(256, 64)).toEqual([0, 64, 128, 192, 256]);
  });

  it.each([0, -64, Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY, 1e-9, 0.5, 7])(
    'draws nothing for an unusable step %s',
    (step) => {
      expect(gridLines(4096, step)).toEqual([]);
    },
  );

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, SCENE_LIMITS.maxDimension + 1])(
    'draws nothing for an unusable length %s',
    (length) => {
      expect(gridLines(length, 64)).toEqual([]);
    },
  );

  it('is bounded at the worst valid input', () => {
    const lines = gridLines(SCENE_LIMITS.maxDimension, SCENE_LIMITS.minGrid);
    expect(lines.length).toBe(SCENE_LIMITS.maxDimension / SCENE_LIMITS.minGrid + 1);
  });
});

describe('normalizeScene', () => {
  it('returns a valid scene as the same object', () => {
    expect(normalizeScene(EMPTY_SCENE)).toBe(EMPTY_SCENE);
  });

  it.each([0, -64, Number.NaN, Number.POSITIVE_INFINITY, 0.001])(
    'turns grid %s into a gridless scene with a usable unit',
    (gridSize) => {
      const s = normalizeScene({ ...EMPTY_SCENE, gridSize });
      expect(s.showGrid).toBe(false);
      expect(s.gridSize).toBe(SCENE_LIMITS.defaultGrid);
    },
  );

  it('clamps non-finite and oversized dimensions', () => {
    const s = normalizeScene({ ...EMPTY_SCENE, width: Number.NaN, height: 1e9 });
    expect(s.width).toBe(1);
    expect(s.height).toBe(SCENE_LIMITS.maxDimension);
  });

  it('keeps inner arrays by identity so change detection still works', () => {
    const input = { ...EMPTY_SCENE, gridSize: 0 };
    const s = normalizeScene(input);
    expect(s.tokens).toBe(input.tokens);
    expect(s.walls).toBe(input.walls);
    expect(s.lights).toBe(input.lights);
  });

  it('respects an explicit gridless scene', () => {
    const s = normalizeScene({ ...EMPTY_SCENE, showGrid: false });
    expect(s.showGrid).toBe(false);
    expect(s.gridSize).toBe(64);
  });
});

describe('tokenRadius', () => {
  it('is half a square less the ring for a medium token', () => {
    expect(tokenRadius(1, 64)).toBe(29);
  });

  it.each([0, -1, Number.NaN, Number.POSITIVE_INFINITY, 1e6])('stays sane for size %s', (size) => {
    const r = tokenRadius(size, 64);
    expect(Number.isFinite(r)).toBe(true);
    expect(r).toBeGreaterThanOrEqual(2);
    expect(r).toBeLessThanOrEqual((SCENE_LIMITS.maxTokenSize * 64) / 2);
  });
});
