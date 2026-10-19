import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { EMPTY_SCENE, type RenderScene } from '../../scene/renderModel';
import { freeSpotNear, listCreatures, placeCreature } from './creatures';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const actor = (id: string, name: string, extra: Partial<Of<'actor'>> = {}) =>
  ({ kind: 'actor', id, version: V, name, pc: false, hp: 10, maxHp: 10, conditions: [], active: false,
     hidden: false, status: '', size: 1, sheetUrl: null, ...extra }) as Of<'actor'>;
const token = (id: string, actorId: string | null, x = 32, y = 32, size = 1) =>
  ({ kind: 'token', id, version: V, x, y, size, label: id, color: 1, artAssetId: null, actorId, hidden: false,
     locked: false, controllerIds: [] }) as Of<'token'>;
const scene: RenderScene = { ...EMPTY_SCENE, width: 640, height: 640, gridSize: 64, tokens: [] };

describe('listCreatures', () => {
  it('lists the party first, then the encounter, each with its tokens on this scene', () => {
    const list = listCreatures([
      actor('cmb_zombie', 'Zombie'),
      actor('chr_valeros', 'Valeros', { pc: true, sheetUrl: '/player/sheet/Valeros' }),
      actor('cmb_skel', 'Skeleton', { size: 2 }),
      token('tok_a', 'cmb_skel'),
      token('tok_b', null),
    ]);
    expect(list.map((c) => c.actorId)).toEqual(['chr_valeros', 'cmb_skel', 'cmb_zombie']);
    expect(list[0]).toMatchObject({ pc: true, sheetUrl: '/player/sheet/Valeros', onScene: [] });
    expect(list[1]).toMatchObject({ size: 2, onScene: ['tok_a'] });
  });

  it('falls back to size 1 and no sheet when an older mirror lacks the fields', () => {
    const old = { ...actor('cmb_x', 'X') } as Record<string, unknown>;
    delete old.size;
    delete old.sheetUrl;
    expect(listCreatures([old as unknown as GmEntity])[0]).toMatchObject({ size: 1, sheetUrl: null });
  });
});

describe('placeCreature', () => {
  it('creates one linked token with the creature name, size and a snapped centre', () => {
    const [skel] = listCreatures([actor('cmb_skel', 'Skeleton', { size: 2 })]);
    const placed = placeCreature(skel!, { x: 100, y: 100 }, scene, 'tok_new');
    expect(placed.at).toEqual({ x: 128, y: 128 });                 // a Large creature snaps to a grid point
    expect(placed.draft).toMatchObject({
      type: 'token.create',
      expected: [],
      payload: { id: 'tok_new', x: 128, y: 128, size: 2, label: 'Skeleton', actorId: 'cmb_skel', hidden: false },
    });
  });

  it('gives a creature the same colour every time', () => {
    const [c] = listCreatures([actor('cmb_skel', 'Skeleton')]);
    expect(placeCreature(c!, { x: 1, y: 1 }, scene, 'a').draft.payload.color)
      .toBe(placeCreature(c!, { x: 300, y: 300 }, scene, 'b').draft.payload.color);
  });
});

describe('freeSpotNear', () => {
  it('returns the centre when it is free and the nearest free square when it is not', () => {
    expect(freeSpotNear({ x: 320, y: 320 }, 1, scene)).toEqual({ x: 352, y: 352 });
    const busy: RenderScene = { ...scene, tokens: [{ id: 't', x: 352, y: 352, size: 1, label: '', color: 1 }] };
    const spot = freeSpotNear({ x: 320, y: 320 }, 1, busy);
    expect(spot).not.toEqual({ x: 352, y: 352 });
    expect(Math.max(Math.abs(spot.x - 352), Math.abs(spot.y - 352))).toBe(64);   // one square away
  });
});
