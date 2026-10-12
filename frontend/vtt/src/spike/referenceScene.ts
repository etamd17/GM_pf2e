import type { RenderLight, RenderScene, RenderToken, RenderWall } from '../scene/renderModel';

// The charter's reference scene: 4096 x 4096, 100 tokens, 500 wall segments,
// 20 lights. Deterministic from a seed so a benchmark run is repeatable and two
// machines measure the same picture.

export interface ReferenceSceneOptions {
  seed?: number;
  width?: number;
  height?: number;
  gridSize?: number;
  tokens?: number;
  walls?: number;
  lights?: number;
}

export const REFERENCE = {
  width: 4096,
  height: 4096,
  gridSize: 64,
  tokens: 100,
  walls: 500,
  lights: 20,
} as const;

// mulberry32: small, fast, good enough for test geometry. Not for anything
// security-relevant.
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TOKEN_COLORS = [0xc9a34e, 0xa83a3a, 0x4a7c2a, 0x5a7fa8, 0x8a6a14, 0x7d5aa8];

export function buildReferenceScene(options: ReferenceSceneOptions = {}): RenderScene {
  const width = options.width ?? REFERENCE.width;
  const height = options.height ?? REFERENCE.height;
  const gridSize = options.gridSize ?? REFERENCE.gridSize;
  const rand = mulberry32(options.seed ?? 1);
  const cols = Math.floor(width / gridSize);
  const rows = Math.floor(height / gridSize);
  const cell = (n: number) => Math.floor(rand() * n);

  const tokens: RenderToken[] = [];
  for (let i = 0; i < (options.tokens ?? REFERENCE.tokens); i += 1) {
    const size = rand() < 0.1 ? 2 : 1;
    tokens.push({
      id: `t${i}`,
      x: (cell(cols - size) + size / 2) * gridSize,
      y: (cell(rows - size) + size / 2) * gridSize,
      size,
      label: `Token ${i + 1}`,
      color: TOKEN_COLORS[i % TOKEN_COLORS.length] ?? 0xc9a34e,
    });
  }

  // Grid-aligned segments of 1-6 squares, like rooms and corridors drawn with
  // a wall tool. Every tenth segment is a door.
  const walls: RenderWall[] = [];
  for (let i = 0; i < (options.walls ?? REFERENCE.walls); i += 1) {
    const x1 = cell(cols) * gridSize;
    const y1 = cell(rows) * gridSize;
    const len = (1 + cell(6)) * gridSize;
    const horizontal = rand() < 0.5;
    walls.push({
      x1,
      y1,
      x2: Math.min(width, horizontal ? x1 + len : x1),
      y2: Math.min(height, horizontal ? y1 : y1 + len),
      kind: i % 10 === 9 ? 'door' : 'wall',
    });
  }

  const lights: RenderLight[] = [];
  for (let i = 0; i < (options.lights ?? REFERENCE.lights); i += 1) {
    lights.push({
      id: `l${i}`,
      x: cell(cols) * gridSize + gridSize / 2,
      y: cell(rows) * gridSize + gridSize / 2,
      radius: (4 + cell(8)) * gridSize,
      color: 0xffd9a0,
    });
  }

  return { width, height, gridSize, showGrid: true, tokens, walls, lights };
}

// Move one token by (dx, dy), returning a new scene that shares every other
// object. This is the shape a drag preview produces, and it is what the
// renderer's identity check is designed for.
export function moveToken(scene: RenderScene, id: string, dx: number, dy: number): RenderScene {
  return {
    ...scene,
    tokens: scene.tokens.map((t) => (t.id === id ? { ...t, x: t.x + dx, y: t.y + dy } : t)),
  };
}
