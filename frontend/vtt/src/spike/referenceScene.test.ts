import { describe, expect, it } from 'vitest';
import { REFERENCE, buildReferenceScene, moveToken } from './referenceScene';

describe('reference scene', () => {
  it('matches the charter reference counts and size', () => {
    const s = buildReferenceScene();
    expect(s.width).toBe(REFERENCE.width);
    expect(s.height).toBe(REFERENCE.height);
    expect(s.tokens).toHaveLength(100);
    expect(s.walls).toHaveLength(500);
    expect(s.lights).toHaveLength(20);
  });

  it('is deterministic for a seed and differs across seeds', () => {
    expect(buildReferenceScene({ seed: 7 })).toEqual(buildReferenceScene({ seed: 7 }));
    expect(buildReferenceScene({ seed: 7 })).not.toEqual(buildReferenceScene({ seed: 8 }));
  });

  it('keeps every token and wall inside the scene', () => {
    const s = buildReferenceScene();
    for (const t of s.tokens) {
      const r = (t.size * s.gridSize) / 2;
      expect(t.x - r).toBeGreaterThanOrEqual(0);
      expect(t.y - r).toBeGreaterThanOrEqual(0);
      expect(t.x + r).toBeLessThanOrEqual(s.width);
      expect(t.y + r).toBeLessThanOrEqual(s.height);
    }
    for (const w of s.walls) {
      for (const v of [w.x1, w.x2]) expect(v).toBeGreaterThanOrEqual(0);
      for (const v of [w.x1, w.x2]) expect(v).toBeLessThanOrEqual(s.width);
      for (const v of [w.y1, w.y2]) expect(v).toBeLessThanOrEqual(s.height);
    }
  });

  it('moveToken replaces only the moved token, sharing every other object', () => {
    const s = buildReferenceScene();
    const next = moveToken(s, 't3', 64, 0);
    expect(next.walls).toBe(s.walls);
    expect(next.lights).toBe(s.lights);
    next.tokens.forEach((t, i) => {
      if (t.id === 't3') {
        expect(t).not.toBe(s.tokens[i]);
        expect(t.x).toBe((s.tokens[i]?.x ?? 0) + 64);
      } else {
        expect(t).toBe(s.tokens[i]);
      }
    });
  });
});
