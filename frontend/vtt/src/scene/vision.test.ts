import { describe, expect, it } from 'vitest';
import type { RenderScene } from './renderModel';
import { pointVisible, visibilityPolygon, visionSignature, visionSources } from './vision';

const scene = (extra: Partial<RenderScene> = {}): RenderScene => ({
  width: 1280, height: 960, gridSize: 64, showGrid: true, tokens: [], walls: [], lights: [], lighting: true,
  occluders: [], ...extra,
});

describe('the visibility polygon', () => {
  it('reaches its radius in the open and stops at a wall', () => {
    const open = visibilityPolygon({ x: 100, y: 100, radius: 50 }, []);
    expect(open.every((p) => Math.abs(Math.hypot(p.x - 100, p.y - 100) - 50) < 1e-6)).toBe(true);
    // A wall 20 px to the right, spanning well above and below.
    const walled = visibilityPolygon({ x: 100, y: 100, radius: 50 }, [{ x1: 120, y1: 0, x2: 120, y2: 200 }]);
    expect(Math.max(...walled.map((p) => p.x))).toBeLessThanOrEqual(120 + 1e-6);
  });

  it('sees nothing with no radius', () => {
    expect(visibilityPolygon({ x: 0, y: 0, radius: 0 }, [])).toEqual([]);
  });
});

describe('what the table sees', () => {
  const hero = { id: 'tok_a', x: 100, y: 100, size: 1, label: 'Valeros', color: 1, vision: 300 };

  it('is what party tokens and lights see, blocked by walls', () => {
    const s = scene({ tokens: [hero], occluders: [{ x1: 200, y1: 0, x2: 200, y2: 960 }] });
    expect(pointVisible(s, { x: 180, y: 100 })).toBe(true);
    expect(pointVisible(s, { x: 260, y: 100 })).toBe(false);       // behind the wall
    expect(pointVisible(s, { x: 100, y: 420 })).toBe(false);       // past its vision
  });

  it('counts a light out to twice its radius, and never a hidden one', () => {
    const torch = { id: 'l', x: 600, y: 600, radius: 64, color: 1 };
    expect(visionSources(scene({ lights: [torch] }))).toEqual([{ x: 600, y: 600, radius: 128 }]);
    expect(pointVisible(scene({ lights: [torch] }), { x: 700, y: 600 })).toBe(true);
    expect(visionSources(scene({ lights: [{ ...torch, hidden: true }] }))).toEqual([]);
  });

  it('ignores tokens that do not see for the table', () => {
    expect(visionSources(scene({ tokens: [{ ...hero, vision: undefined } as never, { ...hero, vision: 0 }] }))).toEqual([]);
  });
});

describe('the mask key', () => {
  it('changes with what casts or blocks sight, and nothing else', () => {
    const base = scene({ tokens: [{ id: 'a', x: 1, y: 1, size: 1, label: '', color: 1, vision: 10 }] });
    const key = visionSignature(base);
    expect(visionSignature({ ...base, showGrid: false })).toBe(key);
    expect(visionSignature({ ...base, tokens: [{ ...base.tokens[0]!, x: 2 }] })).not.toBe(key);
    expect(visionSignature({ ...base, occluders: [{ x1: 0, y1: 0, x2: 5, y2: 5 }] })).not.toBe(key);
  });
});

describe('the GM preview uses only what the table would see by (review 106)', () => {
  const pc = { id: 'pc', x: 100, y: 100, size: 1, label: 'PC', color: 1, vision: 256 };
  // A 10 x 8 fog grid (64 px cells) with only cell (1, 1) revealed.
  const bits = new Uint8Array(Math.ceil(80 / 8));
  const index = 1 * 10 + 1;
  bits[index >> 3] = (bits[index >> 3] ?? 0) | (1 << (index & 7));
  const fog = { enabled: true, cols: 10, rows: 8, bits, key: 'f1' };

  it('drops a hidden token (hidden, or its actor hidden in the tracker)', () => {
    expect(visionSources(scene({ tokens: [{ ...pc, hidden: true }] }))).toEqual([]);
  });

  it('drops a token or a light whose centre fog hides, as the table projection does', () => {
    const torch = { id: 'l', x: 500, y: 300, radius: 64, color: 1 };
    const fogged = scene({ fog, tokens: [pc, { ...pc, id: 'pc2', x: 400 }], lights: [torch, { ...torch, id: 'l2', x: 90, y: 90 }] });
    expect(visionSources(fogged)).toEqual([{ x: 100, y: 100, radius: 256 }, { x: 90, y: 90, radius: 128 }]);
    expect(visionSources({ ...fogged, fog: { ...fog, enabled: false } })).toHaveLength(4);
  });
});
