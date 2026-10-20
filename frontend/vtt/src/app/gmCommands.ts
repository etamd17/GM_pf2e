import type { CommandDraft } from '../protocol/commands';
import type { GmEntity } from '../protocol/frames';
import type { Point } from '../scene/interaction';
import type { RenderScene, RenderToken } from '../scene/renderModel';

// Every GM tool's command, built from the entity it changes. Each update, move
// or delete carries that entity's current version token as its precondition,
// so an edit made against a stale view is refused as stale rather than
// silently overwriting a newer one (VTT-ADR-0002 section 7). Pure functions:
// the workspace hands the result to live.issue().

export type Draft = Omit<CommandDraft, 'campaignId' | 'sceneId'> & { sceneId?: string };

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;

const expect = (e: GmEntity) => [{ kind: e.kind, id: e.id, version: e.version }];

// A client-chosen id (M2: creates name their own ids, so a retry is idempotent).
export function newEntityId(prefix: string): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return `${prefix}_${btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')}`;
}

export const TOKEN_COLORS = [0xc9a34e, 0xa83a3a, 0x4a7c2a, 0x5a7fa8, 0x8a6a14, 0x7d5aa8] as const;

export interface TokenExtras {
  size?: number;
  hidden?: boolean;
  locked?: boolean;
  artAssetId?: string | null;
  actorId?: string | null;
  controllerIds?: string[];
}

export function createToken(id: string, at: Point, label: string, color: number, extras: TokenExtras = {}): Draft {
  return {
    type: 'token.create',
    expected: [],
    payload: {
      id, x: at.x, y: at.y, size: extras.size ?? 1, label, color,
      artAssetId: extras.artAssetId ?? null, actorId: extras.actorId ?? null,
      hidden: extras.hidden ?? false, locked: extras.locked ?? false, controllerIds: extras.controllerIds ?? [],
    },
  };
}

export function moveToken(token: Of<'token'>, x: number, y: number): Draft {
  return { type: 'token.move', expected: expect(token), payload: { id: token.id, x, y } };
}

export type TokenPatch = Partial<
  Pick<Of<'token'>, 'label' | 'size' | 'color' | 'hidden' | 'locked' | 'artAssetId' | 'actorId' | 'vision'>
>;

export function updateToken(token: Of<'token'>, patch: TokenPatch): Draft {
  return { type: 'token.update', expected: expect(token), payload: { id: token.id, ...patch } };
}

export function createLight(id: string, at: Point, gridSize: number): Draft {
  return {
    type: 'light.create',
    expected: [],
    payload: { id, x: at.x, y: at.y, radius: Math.min(4 * gridSize, 16384), color: 0xffd580, hidden: false },
  };
}

export type LightPatch = Partial<Pick<Of<'light'>, 'radius' | 'color' | 'hidden'>>;

export function updateLight(light: Of<'light'>, patch: LightPatch): Draft {
  return { type: 'light.update', expected: expect(light), payload: { id: light.id, ...patch } };
}

export function createWall(id: string, a: Point, b: Point): Draft {
  return {
    type: 'wall.create',
    expected: [],
    payload: { id, x1: a.x, y1: a.y, x2: b.x, y2: b.y, doorState: 'none' },
  };
}

export type DoorState = Of<'wall'>['doorState'];

export function setDoorState(wall: Of<'wall'>, doorState: DoorState): Draft {
  return { type: 'wall.update', expected: expect(wall), payload: { id: wall.id, doorState } };
}

export function deleteEntity(entity: Of<'token'> | Of<'light'> | Of<'wall'> | Of<'template'>): Draft {
  return { type: 'entity.delete', expected: expect(entity), payload: { kind: entity.kind, id: entity.id } };
}

export function createScene(sceneId: string, name: string): Draft {
  return {
    type: 'scene.create',
    sceneId,
    expected: [],
    payload: { name, width: 2048, height: 2048, gridSize: 64, showGrid: true, backgroundAssetId: null },
  };
}

export type ScenePatch = Partial<
  Pick<
    Of<'scene'>,
    'name' | 'width' | 'height' | 'gridSize' | 'showGrid' | 'published' | 'backgroundAssetId' | 'lighting' | 'defaultVision'
  >
>;

export function updateScene(scene: Of<'scene'>, patch: ScenePatch): Draft {
  return { type: 'scene.update', sceneId: scene.id, expected: expect(scene), payload: { ...patch } };
}

export function showOnTable(scene: Of<'scene'>): Draft {
  return { type: 'table.show', sceneId: scene.id, expected: expect(scene), payload: {} };
}

export function clearTable(scene: Of<'scene'>): Draft {
  return { type: 'table.clear', sceneId: scene.id, expected: expect(scene), payload: {} };
}

// --- fog (parity P1) --------------------------------------------------------------------------

export type FogCell = [number, number];

export function fogUpdate(fog: Of<'fog'>, enabled: boolean): Draft {
  return { type: 'fog.update', expected: expect(fog), payload: { enabled } };
}

// A set operation: no version precondition (concurrent reveals compose).
export function fogReveal(mode: 'reveal' | 'hide', cells: readonly FogCell[]): Draft {
  return { type: 'fog.reveal', expected: [], payload: { mode, cells: cells.map(([c, r]) => [c, r]) } };
}

export function fogReset(fog: Of<'fog'>): Draft {
  return { type: 'fog.reset', expected: expect(fog), payload: {} };
}

// --- area templates and terrain (parity P2) ---------------------------------------------------

export type TemplateShape = Of<'template'>['shape'];
export const TEMPLATE_COLOR = 0x75c5eb;

export function templateCreate(
  id: string,
  shape: TemplateShape,
  at: Point,
  aim: Point,
  size: number,
  extras: { color?: number; hidden?: boolean } = {},
): Draft {
  return {
    type: 'template.create',
    expected: [],
    payload: {
      id, shape, x: at.x, y: at.y, aimX: aim.x, aimY: aim.y, size,
      color: extras.color ?? TEMPLATE_COLOR, hidden: extras.hidden ?? false,
    },
  };
}

export type TemplatePatch = Partial<Pick<Of<'template'>, 'x' | 'y' | 'aimX' | 'aimY' | 'size' | 'color' | 'hidden'>>;

export function templateUpdate(template: Of<'template'>, patch: TemplatePatch): Draft {
  return { type: 'template.update', expected: expect(template), payload: { id: template.id, ...patch } };
}

export type TerrainCell = [number, number, number]; // col, row, kind (0 none, 1-4)
export const TERRAIN_KINDS = [
  { kind: 1, label: 'Lava' },
  { kind: 2, label: 'Water' },
  { kind: 3, label: 'Poison' },
  { kind: 4, label: 'Blood' },
] as const;

// A kind per cell: a set operation with no precondition, whose inverse is the
// same command with each cell's previous kind.
export function terrainPaint(cells: readonly TerrainCell[]): Draft {
  return { type: 'terrain.paint', expected: [], payload: { cells: cells.map(([c, r, k]) => [c, r, k]) } };
}

export function terrainReset(terrain: Of<'terrain'>): Draft {
  return { type: 'terrain.reset', expected: expect(terrain), payload: {} };
}

// --- optimistic overlays ---------------------------------------------------------------------
// What a pending command shows until its correlated frame (or a replacement
// snapshot) arrives. Only tokens get one: they are what moves under the GM's
// hand. Everything else waits for its frame.

export type TokenOverlay =
  // (A token's vision is not overlaid: the GM's setting and the drawn radius differ.)
  | { kind: 'token'; id: string; patch: { x?: number; y?: number } & Omit<TokenPatch, 'artAssetId' | 'vision'> }
  | { kind: 'token'; id: string; create: RenderToken }
  | { kind: 'token'; id: string; deleted: true };

export function applyOverlays(scene: RenderScene, overlays: Iterable<TokenOverlay | null>): RenderScene {
  const byId = new Map(scene.tokens.map((t) => [t.id, t]));
  let changed = false;
  for (const o of overlays) {
    if (!o) continue;
    const current = byId.get(o.id);
    if ('deleted' in o) {
      if (current) {
        byId.delete(o.id);
        changed = true;
      }
    } else if ('create' in o) {
      if (!current) {
        byId.set(o.id, { ...o.create, pending: true });
        changed = true;
      }
    } else if (current) {
      byId.set(o.id, { ...current, ...o.patch, pending: true });
      changed = true;
    }
  }
  return changed ? { ...scene, tokens: [...byId.values()] } : scene;
}
