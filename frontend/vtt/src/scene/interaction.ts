import { tokenRadius, type RenderScene } from './renderModel';

// Pure map geometry for GM input: what is under a point, and where a dropped
// token lands. No Pixi, so it is tested directly.

export interface Point {
  x: number;
  y: number;
}

export type Hit = { kind: 'token' | 'light' | 'wall'; id: string };

// Screen-pixel tolerances, converted to scene units by the current zoom.
export const LIGHT_HANDLE_PX = 12;
export const WALL_PICK_PX = 8;

export function distanceToSegment(p: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length2 = dx * dx + dy * dy;
  const t = length2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

// Topmost first: tokens (last drawn wins), then light handles, then walls.
// Only entities with ids can be picked; the table projection's walls never
// reach a surface that picks.
export function hitTest(scene: RenderScene, p: Point, scale: number): Hit | null {
  for (let i = scene.tokens.length - 1; i >= 0; i -= 1) {
    const t = scene.tokens[i];
    if (t && Math.hypot(p.x - t.x, p.y - t.y) <= tokenRadius(t.size, scene.gridSize)) return { kind: 'token', id: t.id };
  }
  const s = scale > 0 ? scale : 1;
  for (const l of scene.lights) {
    if (Math.hypot(p.x - l.x, p.y - l.y) <= LIGHT_HANDLE_PX / s) return { kind: 'light', id: l.id };
  }
  let best: Hit | null = null;
  let bestDistance = WALL_PICK_PX / s;
  for (const w of scene.walls) {
    if (!w.id) continue;
    const d = distanceToSegment(p, { x: w.x1, y: w.y1 }, { x: w.x2, y: w.y2 });
    if (d <= bestDistance) {
      best = { kind: 'wall', id: w.id };
      bestDistance = d;
    }
  }
  return best;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

// A token's centre snapped so it fills whole grid squares, kept on the map.
// Odd sizes centre in a square, even sizes on a grid intersection.
export function snapTokenCentre(p: Point, size: number, scene: RenderScene): Point {
  const g = scene.gridSize;
  const half = (size * g) / 2;
  const snap = (v: number) => Math.round((v - half) / g) * g + half;
  return {
    x: clamp(snap(p.x), Math.min(half, scene.width), Math.max(scene.width - half, 0)),
    y: clamp(snap(p.y), Math.min(half, scene.height), Math.max(scene.height - half, 0)),
  };
}

// Wall ends snap to grid intersections, so walls meet cleanly.
export function snapToGrid(p: Point, scene: RenderScene): Point {
  const g = scene.gridSize;
  return {
    x: clamp(Math.round(p.x / g) * g, 0, scene.width),
    y: clamp(Math.round(p.y / g) * g, 0, scene.height),
  };
}
