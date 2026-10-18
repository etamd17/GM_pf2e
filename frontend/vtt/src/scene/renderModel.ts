// The renderer's own input model. This is NOT the wire contract: the shared
// v1 schemas are still being agreed (VTT-ADR-0002). Once they are accepted, an
// adapter maps a sanitized projection into these shapes, and the renderer
// never sees protocol fields.
//
// Objects are treated as immutable. SceneRuntime skips any token whose object
// identity has not changed, so producers must replace, never mutate.

export interface RenderToken {
  readonly id: string;
  readonly x: number; // centre, scene px
  readonly y: number;
  readonly size: number; // in grid squares (1 = medium)
  readonly label: string;
  readonly color: number; // 0xRRGGBB
  // Token art (M6): a same-origin media URL built by the adapter, drawn inside
  // the ring; the colour disc shows until it loads, and if it never does.
  readonly artUrl?: string;
  // GM only: drawn dimmed so the GM can tell what the table cannot see.
  readonly hidden?: boolean;
  // GM only: a command for it is still waiting for its correlated frame.
  readonly pending?: boolean;
  // Tracker state of the linked actor (parity P3). The table's projection
  // already decided what it may show: exact HP for a player character, only a
  // status for anything else. The GM sees exact HP for every actor.
  readonly live?: RenderLive;
}

export interface RenderLive {
  readonly hp: number | null;
  readonly maxHp: number | null;
  readonly status: 'Wounded' | 'Dead' | null;
  readonly conditions: readonly string[]; // display labels, e.g. "Frightened 2"
  readonly active: boolean; // whose turn it is
}

// Two live blocks that draw the same (tokens are rebuilt only when this changes).
export function sameLive(a: RenderLive | undefined, b: RenderLive | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.hp === b.hp &&
    a.maxHp === b.maxHp &&
    a.status === b.status &&
    a.active === b.active &&
    a.conditions.length === b.conditions.length &&
    a.conditions.every((c, i) => c === b.conditions[i])
  );
}

export interface RenderWall {
  readonly id?: string; // GM projection only; used for picking
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
  readonly kind: 'wall' | 'door';
  // GM only: which kind of door (walls.ts draws each differently).
  readonly doorState?: 'none' | 'open' | 'closed' | 'locked' | 'secret';
}

export interface RenderLight {
  readonly id: string;
  readonly x: number;
  readonly y: number;
  readonly radius: number; // scene px
  readonly color: number;
  readonly hidden?: boolean; // GM only
}

// Fog of war (parity P1): cols x rows grid cells, row-major bits, least
// significant first; a set bit is revealed. `key` changes exactly when the fog
// does (the projected version), so the drawn mask is cached on it.
export interface RenderFog {
  readonly enabled: boolean;
  readonly cols: number;
  readonly rows: number;
  readonly bits: Uint8Array;
  readonly key: string;
}

// An area template (parity P2). Size is in squares.
export interface RenderTemplate {
  readonly id: string;
  readonly shape: 'burst' | 'emanation' | 'cone' | 'line';
  readonly x: number;
  readonly y: number;
  readonly aimX: number;
  readonly aimY: number;
  readonly size: number;
  readonly color: number;
  readonly hidden?: boolean; // GM only
}

// Painted terrain (parity P2): a 4-bit kind per cell, two cells per byte, low
// nibble first. `key` changes exactly when the terrain does.
export interface RenderTerrain {
  readonly cols: number;
  readonly rows: number;
  readonly cells: Uint8Array;
  readonly key: string;
}

export interface RenderScene {
  readonly width: number;
  readonly height: number;
  // Scene px per grid square. Always a usable unit, because token size is
  // measured in squares even on a gridless map.
  readonly gridSize: number;
  // False for a gridless scene. Gridless is explicit, never encoded as a zero
  // or negative gridSize.
  readonly showGrid: boolean;
  // Background image (M6): a same-origin media URL, drawn under the grid and
  // stretched to the scene's size.
  readonly backgroundUrl?: string;
  readonly fog?: RenderFog;
  readonly terrain?: RenderTerrain;
  readonly templates?: readonly RenderTemplate[];
  readonly tokens: readonly RenderToken[];
  readonly walls: readonly RenderWall[];
  readonly lights: readonly RenderLight[];
}

export const EMPTY_SCENE: RenderScene = {
  width: 2048,
  height: 2048,
  gridSize: 64,
  showGrid: true,
  tokens: [],
  walls: [],
  lights: [],
};

// Defensive bounds for the renderer. The adapter and the shared schema must
// reject bad geometry first; these only guarantee that invalid persisted or
// imported data can never hang the main thread (VTT-20261012-010 finding 2).
export const SCENE_LIMITS = {
  maxDimension: 16384,
  minGrid: 8,
  maxGrid: 1024,
  defaultGrid: 64,
  maxTokenSize: 10,
} as const;

const finiteIn = (v: number, min: number, max: number): boolean =>
  Number.isFinite(v) && v >= min && v <= max;

// Returns a scene the renderer can always draw in bounded time. Inner arrays
// are passed through by identity so the runtime's change detection still works,
// and an already-valid scene is returned as the same object.
export function normalizeScene(scene: RenderScene): RenderScene {
  const width = finiteIn(scene.width, 1, SCENE_LIMITS.maxDimension)
    ? scene.width
    : clampDimension(scene.width);
  const height = finiteIn(scene.height, 1, SCENE_LIMITS.maxDimension)
    ? scene.height
    : clampDimension(scene.height);
  const gridOk = finiteIn(scene.gridSize, SCENE_LIMITS.minGrid, SCENE_LIMITS.maxGrid);
  const gridSize = gridOk ? scene.gridSize : SCENE_LIMITS.defaultGrid;
  const showGrid = scene.showGrid && gridOk;
  if (
    width === scene.width &&
    height === scene.height &&
    gridSize === scene.gridSize &&
    showGrid === scene.showGrid
  ) {
    return scene;
  }
  return { ...scene, width, height, gridSize, showGrid };
}

function clampDimension(v: number): number {
  if (!Number.isFinite(v) || v < 1) return 1;
  return Math.min(v, SCENE_LIMITS.maxDimension);
}

// Grid line positions along one axis, or an empty list when the grid must not
// be drawn. Bounded: at most maxDimension / minGrid + 1 entries per axis.
export function gridLines(length: number, step: number): number[] {
  if (!finiteIn(step, SCENE_LIMITS.minGrid, SCENE_LIMITS.maxGrid)) return [];
  if (!finiteIn(length, 1, SCENE_LIMITS.maxDimension)) return [];
  const lines: number[] = [];
  for (let i = 0; i * step <= length; i += 1) lines.push(i * step);
  return lines;
}

// Only a same-origin media URL the adapter could have built (M6); anything
// else is never handed to the loader.
const MEDIA_URL = /^\/vtt\/(?:media\/[A-Za-z0-9_-]{1,64}|table-media)\/[A-Za-z0-9_-]{1,128}$/;
export function isMediaUrl(url: unknown): url is string {
  return typeof url === 'string' && MEDIA_URL.test(url);
}

// Token radius in scene px, never negative or non-finite.
export function tokenRadius(size: number, gridSize: number): number {
  const squares = finiteIn(size, 0.25, SCENE_LIMITS.maxTokenSize) ? size : 1;
  return Math.max(2, (squares * gridSize) / 2 - 3);
}
