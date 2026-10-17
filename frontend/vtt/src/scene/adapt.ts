import type { RenderLight, RenderScene, RenderToken, RenderWall } from './renderModel';
import { decodeFogBits } from '../protocol/semantics';
import { EMPTY_SCENE, isMediaUrl, type RenderFog } from './renderModel';

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

const DOOR_STATES = ['none', 'open', 'closed', 'locked', 'secret'] as const;
const isDoorState = (v: unknown): v is (typeof DOOR_STATES)[number] =>
  typeof v === 'string' && (DOOR_STATES as readonly string[]).includes(v);

const num = (v: unknown, fallback = 0): number => (typeof v === 'number' && Number.isFinite(v) ? v : fallback);

export interface AdaptCache {
  fog?: { version: string; fog: RenderFog };
  tokens: Map<string, { version: string; token: RenderToken }>;
  lights: Map<string, { version: string; light: RenderLight }>;
  walls: Map<string, { version: string; wall: RenderWall }>;
}

export function newAdaptCache(): AdaptCache {
  return { tokens: new Map(), lights: new Map(), walls: new Map() };
}

// Asset id -> the URL this surface reads it from (M6): the GM page reads
// /vtt/media/<campaign>/<asset>, the table /vtt/table-media/<asset>. Without
// one, no image is drawn.
export type MediaUrl = (assetId: string) => string;

const ASSET_ID = /^[A-Za-z0-9_-]{1,128}$/;
const assetUrl = (media: MediaUrl | undefined, id: unknown): string | undefined => {
  if (!media || typeof id !== 'string' || !ASSET_ID.test(id)) return undefined;
  const url = media(id);
  return isMediaUrl(url) ? url : undefined;
};

export function adaptEntities(
  entities: ReadonlyMap<string, WireEntity>,
  cache: AdaptCache = newAdaptCache(),
  media?: MediaUrl,
): RenderScene {
  let scene: WireEntity | undefined;
  const tokens: RenderToken[] = [];
  const lights: RenderLight[] = [];
  const walls: RenderWall[] = [];
  const seen = { tokens: new Set<string>(), lights: new Set<string>(), walls: new Set<string>() };
  let fog: RenderFog | undefined;
  for (const e of entities.values()) {
    if (e.kind === 'scene') {
      scene = e;
    } else if (e.kind === 'fog') {
      if (cache.fog?.version === e.version) {
        fog = cache.fog.fog;
        continue;
      }
      const cols = num(e.cols, 1);
      const rows = num(e.rows, 1);
      const bits = typeof e.revealed === 'string' ? decodeFogBits(e.revealed) : null;
      // The frame rules already checked the length; a mismatch here draws no fog
      // rather than misreading the cells.
      if (bits && bits.length === Math.ceil((cols * rows) / 8)) {
        fog = { enabled: e.enabled === true, cols, rows, bits, key: e.version };
        cache.fog = { version: e.version, fog };
      }
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
        ...(assetUrl(media, e.artAssetId) ? { artUrl: assetUrl(media, e.artAssetId) as string } : {}),
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
        ...(isDoorState(e.doorState) ? { doorState: e.doorState } : {}),
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
    ...(assetUrl(media, scene?.backgroundAssetId) ? { backgroundUrl: assetUrl(media, scene?.backgroundAssetId) as string } : {}),
    ...(fog ? { fog } : {}),
    tokens,
    walls,
    lights,
  };
}
