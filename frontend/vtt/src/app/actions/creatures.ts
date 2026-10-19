// Creatures for the Creatures tab (U-4). Published by Claude for Codex's
// layout (handoff 087): the list, the duplicate guard and one-step linked
// placement live here, once, with tests. The layout decides how they look and
// when to ask "already on the map, place another?".
//
// - listCreatures: the tracker actors from the projection, party first, then
//   the encounter, each with the tokens already linked to it on this scene.
// - placeCreature: the token.create draft for one linked token, label, size
//   and actor link included, at a snapped centre.
// - freeSpotNear: where a click/keyboard "Place" puts it, the first square
//   near the view centre no token covers.

import type { GmEntity } from '../../protocol/frames';
import type { RenderScene } from '../../scene/renderModel';
import { snapTokenCentre, type Point } from '../../scene/interaction';
import { createToken, newEntityId, TOKEN_COLORS, type Draft } from '../gmCommands';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;

export interface Creature {
  actorId: string;
  name: string;
  pc: boolean;
  hp: number;
  maxHp: number;
  size: number;
  sheetUrl: string | null;
  hiddenInTracker: boolean;
  active: boolean;
  onScene: string[]; // ids of tokens on this scene already linked to it
}

export function listCreatures(entities: Iterable<GmEntity>): Creature[] {
  const all = [...entities];
  const tokens = all.filter((e): e is Of<'token'> => e.kind === 'token');
  return all
    .filter((e): e is Of<'actor'> => e.kind === 'actor')
    .map((a) => ({
      actorId: a.id,
      name: a.name || 'Unnamed',
      pc: a.pc,
      hp: a.hp,
      maxHp: a.maxHp,
      size: typeof a.size === 'number' ? a.size : 1,
      sheetUrl: typeof a.sheetUrl === 'string' ? a.sheetUrl : null,
      hiddenInTracker: a.hidden,
      active: a.active,
      onScene: tokens.filter((t) => t.actorId === a.id).map((t) => t.id),
    }))
    .sort((x, y) => Number(y.pc) - Number(x.pc) || x.name.localeCompare(y.name) || (x.actorId < y.actorId ? -1 : 1));
}

// A colour per creature, stable across sessions (from its actor id).
function colourFor(actorId: string): number {
  let h = 0;
  for (let i = 0; i < actorId.length; i += 1) h = (h * 31 + actorId.charCodeAt(i)) >>> 0;
  return TOKEN_COLORS[h % TOKEN_COLORS.length] ?? 0xc9a34e;
}

export interface Placement {
  tokenId: string;
  draft: Draft;
  at: Point;
}

export function placeCreature(creature: Creature, point: Point, scene: RenderScene, tokenId = newEntityId('tok')): Placement {
  const at = snapTokenCentre(point, creature.size, scene);
  return {
    tokenId,
    at,
    draft: createToken(tokenId, at, creature.name.slice(0, 80), colourFor(creature.actorId), {
      size: creature.size,
      actorId: creature.actorId,
    }),
  };
}

// The first snapped centre, spiralling out from `centre`, whose square is not
// already under a token. Falls back to the centre itself.
export function freeSpotNear(centre: Point, size: number, scene: RenderScene): Point {
  const g = scene.gridSize;
  const covered = (p: Point) =>
    scene.tokens.some((t) => {
      const reach = ((t.size + size) * g) / 2;
      return Math.abs(t.x - p.x) < reach && Math.abs(t.y - p.y) < reach;
    });
  const start = snapTokenCentre(centre, size, scene);
  for (let ring = 0; ring <= 12; ring += 1) {
    for (let dy = -ring; dy <= ring; dy += 1) {
      for (let dx = -ring; dx <= ring; dx += 1) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== ring) continue;
        const p = snapTokenCentre({ x: start.x + dx * g, y: start.y + dy * g }, size, scene);
        if (!covered(p)) return p;
      }
    }
  }
  return start;
}
