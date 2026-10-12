// Pure viewport math. World units are scene pixels; screen units are CSS pixels
// of the map host. screen = world * scale + offset.

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface Viewport {
  x: number; // screen-space offset of world origin
  y: number;
  scale: number;
}

export interface ZoomLimits {
  min: number;
  max: number;
}

export const DEFAULT_ZOOM_LIMITS: ZoomLimits = { min: 0.1, max: 4 };

export function clampScale(scale: number, limits: ZoomLimits = DEFAULT_ZOOM_LIMITS): number {
  if (!Number.isFinite(scale)) return limits.min;
  return Math.min(limits.max, Math.max(limits.min, scale));
}

export function worldToScreen(v: Viewport, p: Point): Point {
  return { x: p.x * v.scale + v.x, y: p.y * v.scale + v.y };
}

export function screenToWorld(v: Viewport, p: Point): Point {
  return { x: (p.x - v.x) / v.scale, y: (p.y - v.y) / v.scale };
}

// Zoom by `factor` keeping the world point under `anchor` (screen) fixed, so a
// wheel zoom happens where the pointer is rather than at the corner.
export function zoomAt(
  v: Viewport,
  anchor: Point,
  factor: number,
  limits: ZoomLimits = DEFAULT_ZOOM_LIMITS,
): Viewport {
  const scale = clampScale(v.scale * factor, limits);
  const world = screenToWorld(v, anchor);
  return { scale, x: anchor.x - world.x * scale, y: anchor.y - world.y * scale };
}

export function panBy(v: Viewport, dx: number, dy: number): Viewport {
  return { ...v, x: v.x + dx, y: v.y + dy };
}

// Fit the whole scene inside the screen with `padding` CSS px on every side,
// centred. Used on first load and by the table, which always shows the scene.
export function fitToScreen(
  scene: Size,
  screen: Size,
  padding = 0,
  limits: ZoomLimits = DEFAULT_ZOOM_LIMITS,
): Viewport {
  const availW = Math.max(1, screen.width - padding * 2);
  const availH = Math.max(1, screen.height - padding * 2);
  const scale = clampScale(
    Math.min(availW / Math.max(1, scene.width), availH / Math.max(1, scene.height)),
    limits,
  );
  return {
    scale,
    x: (screen.width - scene.width * scale) / 2,
    y: (screen.height - scene.height * scale) / 2,
  };
}
