import { describe, expect, it } from 'vitest';
import {
  clampScale,
  FIT_ZOOM_LIMITS,
  fitToScreen,
  interactiveZoomLimits,
  panBy,
  screenToWorld,
  worldToScreen,
  zoomAt,
  type Viewport,
} from './viewport';

describe('viewport', () => {
  it('round-trips world and screen coordinates', () => {
    const v: Viewport = { x: 30, y: -12, scale: 0.37 };
    const p = { x: 1234.5, y: 87 };
    const back = screenToWorld(v, worldToScreen(v, p));
    expect(back.x).toBeCloseTo(p.x, 9);
    expect(back.y).toBeCloseTo(p.y, 9);
  });

  it('keeps the anchored world point fixed when zooming', () => {
    const v: Viewport = { x: 100, y: 50, scale: 1 };
    const anchor = { x: 400, y: 300 };
    const before = screenToWorld(v, anchor);
    const next = zoomAt(v, anchor, 2);
    const after = screenToWorld(next, anchor);
    expect(next.scale).toBe(2);
    expect(after.x).toBeCloseTo(before.x, 9);
    expect(after.y).toBeCloseTo(before.y, 9);
  });

  it('clamps zoom to limits and survives non-finite input', () => {
    expect(clampScale(100)).toBe(4);
    expect(clampScale(0.001)).toBe(0.1);
    expect(clampScale(Number.NaN)).toBe(0.1);
    expect(zoomAt({ x: 0, y: 0, scale: 4 }, { x: 0, y: 0 }, 10).scale).toBe(4);
  });

  it('pans by screen pixels', () => {
    expect(panBy({ x: 1, y: 2, scale: 3 }, 10, -5)).toEqual({ x: 11, y: -3, scale: 3 });
  });

  it('fits and centres the reference scene in a 16:9 screen', () => {
    const v = fitToScreen({ width: 4096, height: 4096 }, { width: 1920, height: 1080 });
    expect(v.scale).toBeCloseTo(1080 / 4096, 9);
    const topLeft = worldToScreen(v, { x: 0, y: 0 });
    const bottomRight = worldToScreen(v, { x: 4096, y: 4096 });
    expect(topLeft.y).toBeCloseTo(0, 9);
    expect(bottomRight.y).toBeCloseTo(1080, 9);
    expect(topLeft.x + bottomRight.x).toBeCloseTo(1920, 9); // horizontally centred
  });

  it('respects padding when fitting', () => {
    const v = fitToScreen({ width: 1000, height: 1000 }, { width: 1048, height: 1048 }, 24);
    expect(v.scale).toBe(1);
    expect(v.x).toBe(24);
    expect(v.y).toBe(24);
  });

  // VTT-20261012-012 finding 3: the first zoom-out after Fit must never zoom in.
  it.each([
    ['GM, 24 px padding', 24],
    ['table, no padding', 0],
  ])('zoom-out from a fitted oversized scene never increases scale (%s)', (_label, padding) => {
    const scene = { width: 10000, height: 10000 };
    const screen = { width: 1000, height: 1000 };
    const fitted = fitToScreen(scene, screen, padding, FIT_ZOOM_LIMITS);
    const limits = interactiveZoomLimits(scene, screen, padding);
    const centre = { x: 500, y: 500 };
    let v = fitted;
    for (let i = 0; i < 5; i += 1) {
      const next = zoomAt(v, centre, 1 / 1.2, limits);
      expect(next.scale).toBeLessThanOrEqual(v.scale);
      v = next;
    }
    expect(limits.min).toBeLessThanOrEqual(fitted.scale);
  });

  it('keeps the default floor for scenes that fit comfortably', () => {
    const limits = interactiveZoomLimits({ width: 1000, height: 1000 }, { width: 1920, height: 1080 }, 24);
    expect(limits.min).toBe(0.1);
  });
});
