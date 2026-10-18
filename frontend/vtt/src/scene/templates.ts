// Area template geometry (parity P2, handoff 065), matching /map: Euclidean
// distance (no PF2e 5/10/5 diagonals, which /map does not do either), a cone
// 90 degrees wide around its aim, a line one square wide along its aim, and a
// burst or emanation a circle. Size is in squares; pixels are size * gridSize.

import type { Point } from './interaction';

export type TemplateShape = 'burst' | 'emanation' | 'cone' | 'line';

export interface TemplateGeometry {
  shape: TemplateShape;
  x: number;
  y: number;
  aimX: number;
  aimY: number;
  size: number;
}

const CONE_HALF_ANGLE = Math.PI / 4;
const ARC_STEPS = 24;

// The aim direction; an aim on the anchor itself points along +x, as /map does.
export function aimAngle(t: TemplateGeometry): number {
  const dx = t.aimX - t.x;
  const dy = t.aimY - t.y;
  return dx === 0 && dy === 0 ? 0 : Math.atan2(dy, dx);
}

export function templateContains(t: TemplateGeometry, gridSize: number, p: Point): boolean {
  const reach = t.size * gridSize;
  const dx = p.x - t.x;
  const dy = p.y - t.y;
  const distance = Math.hypot(dx, dy);
  if (t.shape === 'burst' || t.shape === 'emanation') return distance <= reach;
  const angle = aimAngle(t);
  if (t.shape === 'cone') {
    if (distance > reach) return false;
    if (distance === 0) return true;
    let diff = Math.atan2(dy, dx) - angle;
    while (diff > Math.PI) diff -= 2 * Math.PI;
    while (diff < -Math.PI) diff += 2 * Math.PI;
    return Math.abs(diff) <= CONE_HALF_ANGLE + 1e-9;
  }
  // line: along the aim, one square wide
  const along = dx * Math.cos(angle) + dy * Math.sin(angle);
  const across = -dx * Math.sin(angle) + dy * Math.cos(angle);
  return along >= 0 && along <= reach && Math.abs(across) <= gridSize / 2;
}

// What to draw: a circle, or a closed polygon (cone and line).
export type TemplateOutline = { kind: 'circle'; x: number; y: number; r: number } | { kind: 'polygon'; points: Point[] };

export function templateOutline(t: TemplateGeometry, gridSize: number): TemplateOutline {
  const reach = t.size * gridSize;
  if (t.shape === 'burst' || t.shape === 'emanation') return { kind: 'circle', x: t.x, y: t.y, r: reach };
  const angle = aimAngle(t);
  if (t.shape === 'cone') {
    const points: Point[] = [{ x: t.x, y: t.y }];
    for (let i = 0; i <= ARC_STEPS; i += 1) {
      const a = angle - CONE_HALF_ANGLE + (2 * CONE_HALF_ANGLE * i) / ARC_STEPS;
      points.push({ x: t.x + reach * Math.cos(a), y: t.y + reach * Math.sin(a) });
    }
    return { kind: 'polygon', points };
  }
  const half = gridSize / 2;
  const ux = Math.cos(angle);
  const uy = Math.sin(angle);
  const nx = -uy * half;
  const ny = ux * half;
  return {
    kind: 'polygon',
    points: [
      { x: t.x + nx, y: t.y + ny },
      { x: t.x + ux * reach + nx, y: t.y + uy * reach + ny },
      { x: t.x + ux * reach - nx, y: t.y + uy * reach - ny },
      { x: t.x - nx, y: t.y - ny },
    ],
  };
}
