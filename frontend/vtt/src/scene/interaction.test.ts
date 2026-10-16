import { describe, expect, it } from 'vitest';
import { EMPTY_SCENE, type RenderScene } from './renderModel';
import { distanceToSegment, hitTest, snapToGrid, snapTokenCentre } from './interaction';

const scene: RenderScene = {
  ...EMPTY_SCENE,
  width: 640,
  height: 640,
  gridSize: 64,
  tokens: [
    { id: 'under', x: 96, y: 96, size: 1, label: 'Under', color: 0 },
    { id: 'over', x: 110, y: 96, size: 1, label: 'Over', color: 0 },
  ],
  lights: [{ id: 'l1', x: 400, y: 400, radius: 128, color: 0 }],
  walls: [
    { id: 'w1', x1: 0, y1: 300, x2: 640, y2: 300, kind: 'wall' },
    { x1: 0, y1: 500, x2: 640, y2: 500, kind: 'wall' }, // no id: never picked
  ],
};

describe('hitTest', () => {
  it('picks the topmost token first', () => {
    expect(hitTest(scene, { x: 104, y: 96 }, 1)).toEqual({ kind: 'token', id: 'over' });
  });
  it('picks a light by its handle, scaled to the zoom', () => {
    expect(hitTest(scene, { x: 408, y: 400 }, 1)).toEqual({ kind: 'light', id: 'l1' });
    expect(hitTest(scene, { x: 420, y: 400 }, 1)).toBeNull();
    expect(hitTest(scene, { x: 420, y: 400 }, 0.5)).toEqual({ kind: 'light', id: 'l1' }); // zoomed out: wider
  });
  it('picks a wall near its line, and never a wall without an id', () => {
    expect(hitTest(scene, { x: 200, y: 305 }, 1)).toEqual({ kind: 'wall', id: 'w1' });
    expect(hitTest(scene, { x: 200, y: 320 }, 1)).toBeNull();
    expect(hitTest(scene, { x: 200, y: 500 }, 1)).toBeNull();
  });
  it('misses empty map', () => {
    expect(hitTest(scene, { x: 600, y: 50 }, 1)).toBeNull();
  });
});

describe('snapping', () => {
  it('centres an odd-sized token in a square and an even-sized one on an intersection', () => {
    expect(snapTokenCentre({ x: 100, y: 70 }, 1, scene)).toEqual({ x: 96, y: 96 });
    expect(snapTokenCentre({ x: 100, y: 70 }, 2, scene)).toEqual({ x: 128, y: 64 });
  });
  it('keeps a token on the map', () => {
    expect(snapTokenCentre({ x: -500, y: 9999 }, 1, scene)).toEqual({ x: 32, y: 608 });
  });
  it('snaps wall ends to intersections, on the map', () => {
    expect(snapToGrid({ x: 95, y: 33 }, scene)).toEqual({ x: 64, y: 64 });
    expect(snapToGrid({ x: 9999, y: -3 }, scene)).toEqual({ x: 640, y: 0 });
  });
  it('measures distance to a segment, ends included', () => {
    expect(distanceToSegment({ x: 5, y: 5 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
    expect(distanceToSegment({ x: 13, y: 4 }, { x: 0, y: 0 }, { x: 10, y: 0 })).toBe(5);
    expect(distanceToSegment({ x: 3, y: 4 }, { x: 0, y: 0 }, { x: 0, y: 0 })).toBe(5);
  });
});
