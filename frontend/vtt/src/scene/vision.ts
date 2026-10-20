// Dynamic lighting and vision (E-6), ported from /map (static/js/map.js:
// visibilityPolygon, drawVisionOverlay). What a party token, or a light, can
// see is a polygon cast against the segments that block sight; the table
// draws darkness everywhere outside the union of those polygons.
//
// Two rules carried over from /map's audit:
// - a light reveals out to its DIM radius, twice its (bright) radius: a
//   creature sees by dim light, it just sees worse;
// - nothing animated enters the mask's cache key, so the mask is recomputed
//   only when something that casts or blocks sight changes (visionSignature).

import type { Point } from './interaction';
import type { RenderScene } from './renderModel';

export interface Segment {
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
}

export interface VisionSource {
  readonly x: number;
  readonly y: number;
  readonly radius: number; // scene px; 0 sees nothing
}

const RAY_COUNT = 160;
const ENDPOINT_NUDGE = 0.0002; // radians either side of a wall end, so rays slip past corners

function rayHit(ox: number, oy: number, angle: number, s: Segment, max: number): number | null {
  const dx = Math.cos(angle);
  const dy = Math.sin(angle);
  const sx = s.x2 - s.x1;
  const sy = s.y2 - s.y1;
  const denominator = dx * sy - dy * sx;
  if (Math.abs(denominator) < 1e-8) return null;
  const qx = s.x1 - ox;
  const qy = s.y1 - oy;
  const t = (qx * sy - qy * sx) / denominator;
  const u = (qx * dy - qy * dx) / denominator;
  return t >= 0 && t <= max && u >= 0 && u <= 1 ? t : null;
}

// Only segments that can intersect the source's circle matter.
function nearby(source: VisionSource, segments: readonly Segment[]): Segment[] {
  const r = source.radius;
  return segments.filter((s) =>
    Math.max(s.x1, s.x2) >= source.x - r && Math.min(s.x1, s.x2) <= source.x + r
    && Math.max(s.y1, s.y2) >= source.y - r && Math.min(s.y1, s.y2) <= source.y + r);
}

// The polygon a source sees: rays at a fixed spacing plus either side of every
// nearby wall end, each stopped at the first segment it meets or the radius.
export function visibilityPolygon(source: VisionSource, segments: readonly Segment[]): Point[] {
  if (!(source.radius > 0)) return [];
  const walls = nearby(source, segments);
  const angles: number[] = [];
  for (let i = 0; i < RAY_COUNT; i += 1) angles.push((Math.PI * 2 * i) / RAY_COUNT - Math.PI);
  for (const w of walls) {
    for (const [ex, ey] of [[w.x1, w.y1], [w.x2, w.y2]] as const) {
      const a = Math.atan2(ey - source.y, ex - source.x);
      angles.push(a - ENDPOINT_NUDGE, a, a + ENDPOINT_NUDGE);
    }
  }
  angles.sort((a, b) => a - b);
  return angles.map((angle) => {
    let distance = source.radius;
    for (const w of walls) {
      const hit = rayHit(source.x, source.y, angle, w, distance);
      if (hit !== null && hit < distance) distance = hit;
    }
    return { x: source.x + Math.cos(angle) * distance, y: source.y + Math.sin(angle) * distance };
  });
}

// True when fog hides this point from the table: fog on, and the cell holding
// it (clamped into the grid) not revealed. The same rule as the server's
// vtt.fog.FogView.hides, which drops such a token or light from the table.
function fogHides(scene: RenderScene, x: number, y: number): boolean {
  const fog = scene.fog;
  if (!fog || !fog.enabled) return false;
  const col = Math.min(fog.cols - 1, Math.max(0, Math.floor(x / scene.gridSize)));
  const row = Math.min(fog.rows - 1, Math.max(0, Math.floor(y / scene.gridSize)));
  const index = row * fog.cols + col;
  return (((fog.bits[index >> 3] ?? 0) >> (index & 7)) & 1) === 0;
}

// Every source the table sees by: party tokens (their vision radius) and the
// scene's lights out to their dim radius. Exactly the sources the table's
// projection would carry (review 106), so the GM's preview matches the TV: a
// hidden token (hidden on the map, or its actor hidden in the tracker; the GM
// adapter marks both) and a hidden light are the GM's own, and a token or
// light whose centre fog hides never reaches the table.
export function visionSources(scene: RenderScene): VisionSource[] {
  const sources: VisionSource[] = [];
  for (const t of scene.tokens) {
    if (typeof t.vision === 'number' && t.vision > 0 && !t.hidden && !fogHides(scene, t.x, t.y)) {
      sources.push({ x: t.x, y: t.y, radius: t.vision });
    }
  }
  for (const l of scene.lights) {
    if (!l.hidden && l.radius > 0 && !fogHides(scene, l.x, l.y)) sources.push({ x: l.x, y: l.y, radius: l.radius * 2 });
  }
  return sources;
}

// What the mask depends on, and nothing else (no viewport, no animation).
export function visionSignature(scene: RenderScene): string {
  const parts: (string | number)[] = [scene.width, scene.height];
  for (const s of visionSources(scene)) parts.push('s', s.x, s.y, s.radius);
  for (const o of scene.occluders ?? []) parts.push('w', o.x1, o.y1, o.x2, o.y2);
  return parts.join('|');
}

// The scene is dark wherever nothing lights it or no party token sees it.
// True when the point is lit (inside some source's polygon); for tests and
// the probe.
export function pointVisible(scene: RenderScene, p: Point): boolean {
  const occluders = scene.occluders ?? [];
  return visionSources(scene).some((source) => {
    if (Math.hypot(p.x - source.x, p.y - source.y) > source.radius) return false;
    const angle = Math.atan2(p.y - source.y, p.x - source.x);
    const distance = Math.hypot(p.x - source.x, p.y - source.y);
    return !nearby(source, occluders).some((w) => {
      const hit = rayHit(source.x, source.y, angle, w, distance);
      return hit !== null && hit < distance - 1e-6;
    });
  });
}
