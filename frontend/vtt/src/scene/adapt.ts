import type { RenderLight, RenderScene, RenderToken, RenderWall } from './renderModel';
import { decodeFogBits } from '../protocol/semantics';
import { EMPTY_SCENE, isMediaUrl, type RenderFog, type RenderLive, type RenderTemplate, type RenderTerrain } from './renderModel';

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
  terrain?: { version: string; terrain: RenderTerrain };
  templates?: Map<string, { version: string; template: RenderTemplate }>;
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

// "off_guard" + null -> "Off guard"; "frightened" + 2 -> "Frightened 2".
export function conditionLabel(name: string, value: number | null): string {
  const words = name.replace(/_/g, ' ');
  const label = words.charAt(0).toUpperCase() + words.slice(1);
  return value === null ? label : `${label} ${value}`;
}

const conditionsOf = (raw: unknown): string[] =>
  Array.isArray(raw)
    ? raw.flatMap((c) =>
        c && typeof c === 'object' && typeof (c as { name?: unknown }).name === 'string'
          ? [conditionLabel((c as { name: string }).name, typeof (c as { value?: unknown }).value === 'number' ? (c as { value: number }).value : null)]
          : [],
      )
    : [];

const intOrNull = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) ? v : null);

// The table's own live block (the server already chose what it may show).
function tableLive(raw: unknown): RenderLive | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const live = raw as Record<string, unknown>;
  return {
    hp: intOrNull(live.hp),
    maxHp: intOrNull(live.maxHp),
    status: live.status === 'Wounded' || live.status === 'Dead' ? live.status : null,
    conditions: conditionsOf(live.conditions),
    active: live.active === true,
  };
}

// The GM's view of a linked actor: exact HP for every creature.
function gmLive(actor: WireEntity): RenderLive {
  return {
    hp: intOrNull(actor.hp),
    maxHp: intOrNull(actor.maxHp),
    status: null,
    conditions: conditionsOf(actor.conditions),
    active: actor.active === true,
  };
}

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
  let terrain: RenderTerrain | undefined;
  const templates: RenderTemplate[] = [];
  const templateCache = (cache.templates ??= new Map());
  const seenTemplates = new Set<string>();
  // Tracker actors (parity P3) are GM-only entities; a GM token joins its own.
  const actors = new Map<string, WireEntity>();
  let sceneEntity: WireEntity | undefined;
  for (const e of entities.values()) {
    if (e.kind === 'actor') actors.set(e.id, e);
    else if (e.kind === 'scene') sceneEntity = e;
  }
  // E-6: lighting, and (GM) the scene's default vision for a party token.
  const lit = sceneEntity?.lighting === true;
  const defaultVision = num(sceneEntity?.defaultVision, 0);
  for (const e of entities.values()) {
    if (e.kind === 'scene') {
      scene = e;
    } else if (e.kind === 'template') {
      seenTemplates.add(e.id);
      const hit = templateCache.get(e.id);
      if (hit && hit.version === e.version) {
        templates.push(hit.template);
        continue;
      }
      const shape = e.shape === 'emanation' || e.shape === 'cone' || e.shape === 'line' ? e.shape : 'burst';
      const template: RenderTemplate = {
        id: e.id,
        shape,
        x: num(e.x),
        y: num(e.y),
        aimX: num(e.aimX, num(e.x)),
        aimY: num(e.aimY, num(e.y)),
        size: num(e.size, 1),
        color: num(e.color),
        ...(e.hidden === true ? { hidden: true } : {}),
      };
      templateCache.set(e.id, { version: e.version, template });
      templates.push(template);
    } else if (e.kind === 'terrain') {
      if (cache.terrain?.version === e.version) {
        terrain = cache.terrain.terrain;
        continue;
      }
      const cols = num(e.cols, 1);
      const rows = num(e.rows, 1);
      const cells = typeof e.cells === 'string' ? decodeFogBits(e.cells) : null;
      if (cells && cells.length === Math.ceil((cols * rows) / 2)) {
        terrain = { cols, rows, cells, key: e.version };
        cache.terrain = { version: e.version, terrain };
      }
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
      const actor = typeof e.actorId === 'string' ? actors.get(e.actorId) : undefined;
      // A GM token redraws when its actor changes too, so both versions key it,
      // and (E-6) when the scene's lighting or default vision does.
      const version = `${actor ? `${e.version}:${actor.version}` : e.version}:${lit ? defaultVision : '-'}`;
      const hit = cache.tokens.get(e.id);
      if (hit && hit.version === version) {
        tokens.push(hit.token);
        continue;
      }
      const live = actor ? gmLive(actor) : tableLive(e.live);
      const token: RenderToken = {
        id: e.id,
        x: num(e.x),
        y: num(e.y),
        size: num(e.size, 1),
        label: typeof e.label === 'string' ? e.label : '',
        color: num(e.color),
        ...(assetUrl(media, e.artAssetId) ? { artUrl: assetUrl(media, e.artAssetId) as string } : {}),
        // GM only: hidden on the map, or its actor hidden in the tracker
        // (either way the table does not see it).
        ...(e.hidden === true || actor?.hidden === true ? { hidden: true } : {}),
        ...(live ? { live } : {}),
        ...(actor && typeof actor.speed === 'number' && actor.speed > 0 ? { speed: actor.speed } : {}),
        ...(typeof e.order === 'number' && e.order !== 0 ? { order: e.order } : {}),
        // E-6: the table's projection says how far a party token sees; the GM
        // works it out the same way (a player character's token, its own radius
        // or the scene's default), so the GM can preview the table's darkness.
        // (A GM token always carries `locked`; the table's never does.)
        ...(lit && typeof e.locked !== 'boolean' && typeof e.vision === 'number' ? { vision: Math.max(0, e.vision) } : {}),
        ...(lit && typeof e.locked === 'boolean' && actor?.pc === true
          ? { vision: Math.max(0, typeof e.vision === 'number' ? e.vision : defaultVision) } : {}),
      };
      cache.tokens.set(e.id, { version, token });
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
  for (const id of [...templateCache.keys()]) if (!seenTemplates.has(id)) templateCache.delete(id);
  for (const key of ['tokens', 'lights', 'walls'] as const) {
    for (const id of [...cache[key].keys()]) if (!seen[key].has(id)) cache[key].delete(id);
  }
  const byId = <T extends { id: string }>(a: T, b: T) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  // E-10: by stacking order first (a higher order draws on top), then by id.
  tokens.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || byId(a, b));
  lights.sort(byId);
  // E-6: what blocks sight. The table is sent only those segments (no door
  // state); the GM's walls say, and an open door does not block.
  const occluders = lit
    ? walls.filter((w) => w.doorState !== 'open').map((w) => ({ x1: w.x1, y1: w.y1, x2: w.x2, y2: w.y2 }))
    : [];
  return {
    width: num(scene?.width, EMPTY_SCENE.width),
    height: num(scene?.height, EMPTY_SCENE.height),
    gridSize: num(scene?.gridSize, EMPTY_SCENE.gridSize),
    showGrid: scene ? scene.showGrid === true : EMPTY_SCENE.showGrid,
    ...(assetUrl(media, scene?.backgroundAssetId) ? { backgroundUrl: assetUrl(media, scene?.backgroundAssetId) as string } : {}),
    ...(fog ? { fog } : {}),
    ...(terrain ? { terrain } : {}),
    ...(templates.length ? { templates: templates.sort(byId) } : {}),
    tokens,
    walls,
    lights,
    ...(lit ? { lighting: true, occluders } : {}),
  };
}
