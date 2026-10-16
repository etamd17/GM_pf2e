import type { RenderLight, RenderScene, RenderToken, RenderWall } from './renderModel';
import { EMPTY_SCENE } from './renderModel';

// The one adapter between projected wire entities and the renderer's model
// (renderModel.ts). It reads only the fields its surface's projection carries,
// so it is generic over the GM and table entity unions: the table projection
// simply has no walls and no hidden flags, and nothing here invents them.
//
// Objects are rebuilt per call but reused by identity when an entity's version
// token is unchanged, so SceneRuntime's identity-based change detection still
// skips what did not move.

interface WireEntity {
  kind: string;
  id: string;
  version: string;
  [field: string]: unknown;
}

const num = (v: unknown, fallback = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

export interface AdaptCache {
  tokens: Map<string, { version: string; token: RenderToken }>;
  lights: Map<string, { version: string; light: RenderLight }>;
  walls: Map<string, { version: string; wall: RenderWall }>;
}

export function newAdaptCache(): AdaptCache {
  return { tokens: new Map(), lights: new Map(), walls: new Map() };
}

export function adaptEntities(entities: ReadonlyMap<string, WireEntity>, cache: AdaptCache = newAdaptCache()): RenderScene {
  let scene: WireEntity | undefined;
  const tokens: RenderToken[] = [];
  const lights: RenderLight[] = [];
  const walls: RenderWall[] = [];
  const seen = { tokens: new Set<string>(), lights: new Set<string>(), walls: new Set<string>() };
  for (const e of entities.values()) {
    if (e.kind === 'scene') {
      scene = e;
    } else if (e.kind === 'token') {
      seen.tokens.add(e.id);
      const hit = cache.tokens.get(e.id);
      if (hit && hit.version === e.version) {
        tokens.push(hit.token);
        continue;
      }
      const token: RenderToken = {
        id: e.id,
        x: num(e.x),
        y: num(e.y),
        size: num(e.size, 1),
        label: typeof e.label === 'string' ? e.label : '',
        color: num(e.color),
        ...(e.hidden === true ? { hidden: true } : {}),
      };
      cache.tokens.set(e.id, { version: e.version, token });
      tokens.push(token);
    } else if (e.kind === 'light') {
      seen.lights.add(e.id);
      const hit = cache.lights.get(e.id);
      if (hit && hit.version === e.version) {
        lights.push(hit.light);
        continue;
      }
      const light: RenderLight = {
        id: e.id,
        x: num(e.x),
        y: num(e.y),
        radius: num(e.radius),
        color: num(e.color),
        ...(e.hidden === true ? { hidden: true } : {}),
      };
      cache.lights.set(e.id, { version: e.version, light });
      lights.push(light);
    } else if (e.kind === 'wall') {
      seen.walls.add(e.id);
      const hit = cache.walls.get(e.id);
      if (hit && hit.version === e.version) {
        walls.push(hit.wall);
        continue;
      }
      const wall: RenderWall = {
        id: e.id,
        x1: num(e.x1),
        y1: num(e.y1),
        x2: num(e.x2),
        y2: num(e.y2),
        kind: e.doorState === 'none' || e.doorState === undefined ? 'wall' : 'door',
      };
      cache.walls.set(e.id, { version: e.version, wall });
      walls.push(wall);
    }
  }
  for (const key of ['tokens', 'lights', 'walls'] as const) {
    for (const id of [...cache[key].keys()]) if (!seen[key].has(id)) cache[key].delete(id);
  }
  const byId = <T extends { id: string }>(a: T, b: T) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  tokens.sort(byId);
  lights.sort(byId);
  return {
    width: num(scene?.width, EMPTY_SCENE.width),
    height: num(scene?.height, EMPTY_SCENE.height),
    gridSize: num(scene?.gridSize, EMPTY_SCENE.gridSize),
    showGrid: scene ? scene.showGrid === true : EMPTY_SCENE.showGrid,
    tokens,
    walls,
    lights,
  };
}
