import { describe, expect, it } from 'vitest';
import { adaptEntities, conditionLabel, newAdaptCache } from './adapt';
import { sameLive } from './renderModel';

const scene = { kind: 'scene', id: 's', version: 'vs', name: 'S', width: 1000, height: 800, gridSize: 50, showGrid: false };
const token = (id: string, version: string, x = 10) => ({ kind: 'token', id, version, x, y: 20, size: 2, label: id, color: 7 });
const map = (...entities: Array<Record<string, unknown> & { kind: string; id: string; version: string }>) =>
  new Map(entities.map((e) => [`${e.kind}\u0000${e.id}`, e]));

describe('adaptEntities', () => {
  it('maps the scene, tokens, lights and walls, doors included', () => {
    const out = adaptEntities(
      map(
        scene,
        token('a', 'v1'),
        { kind: 'light', id: 'l', version: 'vl', x: 1, y: 2, radius: 30, color: 5 },
        { kind: 'wall', id: 'w1', version: 'vw', x1: 0, y1: 0, x2: 10, y2: 0, doorState: 'none' },
        { kind: 'wall', id: 'w2', version: 'vw', x1: 0, y1: 0, x2: 0, y2: 10, doorState: 'secret' },
      ),
    );
    expect(out).toMatchObject({ width: 1000, height: 800, gridSize: 50, showGrid: false });
    expect(out.tokens).toEqual([{ id: 'a', x: 10, y: 20, size: 2, label: 'a', color: 7 }]);
    expect(out.lights).toEqual([{ id: 'l', x: 1, y: 2, radius: 30, color: 5 }]);
    expect(out.walls.map((w) => w.kind)).toEqual(['wall', 'door']);
  });

  it('a table projection has no walls and none are invented', () => {
    const out = adaptEntities(map(scene, token('a', 'v1')));
    expect(out.walls).toEqual([]);
  });

  it('reuses a token object while its version token is unchanged', () => {
    const cache = newAdaptCache();
    const first = adaptEntities(map(scene, token('a', 'v1'), token('b', 'v1')), cache);
    const second = adaptEntities(map(scene, token('a', 'v1'), token('b', 'v2', 99)), cache);
    expect(second.tokens[0]).toBe(first.tokens[0]);
    expect(second.tokens[1]).not.toBe(first.tokens[1]);
    expect(second.tokens[1]?.x).toBe(99);
  });

  it('forgets removed entities', () => {
    const cache = newAdaptCache();
    adaptEntities(map(scene, token('a', 'v1')), cache);
    adaptEntities(map(scene), cache);
    expect(cache.tokens.size).toBe(0);
  });
});

describe('images (M6)', () => {
  const gmMedia = (id: string) => `/vtt/media/camp_a/${id}`;
  const tableMedia = (id: string) => `/vtt/table-media/${id}`;

  it('maps a background and token art to this surface media URL', () => {
    const out = adaptEntities(
      map({ ...scene, backgroundAssetId: 'asset_bg' }, { ...token('a', 'v1'), artAssetId: 'asset_art' }),
      newAdaptCache(),
      gmMedia,
    );
    expect(out.backgroundUrl).toBe('/vtt/media/camp_a/asset_bg');
    expect(out.tokens[0]?.artUrl).toBe('/vtt/media/camp_a/asset_art');
    const table = adaptEntities(map({ ...scene, backgroundAssetId: 'asset_bg' }), newAdaptCache(), tableMedia);
    expect(table.backgroundUrl).toBe('/vtt/table-media/asset_bg');
  });

  it('draws no image without a media mapping, for a missing id, or for an id that is not one', () => {
    const entities = map({ ...scene, backgroundAssetId: 'asset_bg' }, { ...token('a', 'v1'), artAssetId: 'asset_art' });
    expect(adaptEntities(entities).backgroundUrl).toBeUndefined();
    expect(adaptEntities(entities).tokens[0]).not.toHaveProperty('artUrl');
    const odd = adaptEntities(
      map({ ...scene, backgroundAssetId: '../../etc/passwd' }, { ...token('a', 'v1'), artAssetId: null }),
      newAdaptCache(),
      gmMedia,
    );
    expect(odd.backgroundUrl).toBeUndefined();
    expect(odd.tokens[0]).not.toHaveProperty('artUrl');
    // a mapping that builds anything but a media URL is ignored too
    const hostile = adaptEntities(map({ ...scene, backgroundAssetId: 'asset_bg' }), newAdaptCache(), () => 'https://evil.example/x');
    expect(hostile.backgroundUrl).toBeUndefined();
  });
});

describe('fog (parity P1)', () => {
  // 4 x 3 = 12 cells -> 2 bytes; cells 0 and 5 revealed.
  const fogEntity = (version = 'vf1', revealed = 'IQA') => ({ kind: 'fog', id: 'fog', version, enabled: true, cols: 4, rows: 3, revealed });

  it('decodes the fog entity into the render model and caches it by version', () => {
    const cache = newAdaptCache();
    const out = adaptEntities(map(scene, fogEntity()), cache);
    expect(out.fog?.enabled).toBe(true);
    expect([out.fog?.cols, out.fog?.rows, out.fog?.key]).toEqual([4, 3, 'vf1']);
    expect(Array.from(out.fog?.bits ?? [])).toEqual([0b00100001, 0]);
    const again = adaptEntities(map(scene, fogEntity()), cache);
    expect(again.fog).toBe(out.fog); // same version: same object, so the mask is not rebuilt
  });

  it('a bitset of the wrong length draws no fog rather than misreading cells', () => {
    expect(adaptEntities(map(scene, fogEntity('vf2', 'IQAA'))).fog).toBeUndefined();
  });
});

describe('templates and terrain (parity P2)', () => {
  it('maps templates (hidden kept for the GM) and decodes terrain, caching both by version', () => {
    const cache = newAdaptCache();
    const tpl = { kind: 'template', id: 'tpl_a', version: 'vt1', shape: 'cone', x: 10, y: 20, aimX: 90, aimY: 20, size: 3, color: 5, hidden: true };
    // 3 x 1 cells -> 2 bytes: kinds 1, 4, 3
    const terrain = { kind: 'terrain', id: 'terrain', version: 'vr1', cols: 3, rows: 1, cells: 'QQM' };
    const out = adaptEntities(map(scene, tpl, terrain), cache);
    expect(out.templates).toEqual([{ id: 'tpl_a', shape: 'cone', x: 10, y: 20, aimX: 90, aimY: 20, size: 3, color: 5, hidden: true }]);
    expect(Array.from(out.terrain?.cells ?? [])).toEqual([0x41, 0x03]);
    const again = adaptEntities(map(scene, tpl, terrain), cache);
    expect(again.templates?.[0]).toBe(out.templates?.[0]);
    expect(again.terrain).toBe(out.terrain);
    expect(adaptEntities(map(scene), cache).templates).toBeUndefined(); // gone: dropped from the cache
  });
});

describe('live tracker state (parity P3)', () => {
  const actor = (version: string, extra: Record<string, unknown> = {}) => ({
    kind: 'actor', id: 'cmb_skel', version, name: 'Skeleton', pc: false, hp: 7, maxHp: 20,
    conditions: [{ name: 'off_guard', value: null }, { name: 'frightened', value: 2 }],
    active: true, hidden: false, status: 'Wounded', ...extra,
  });

  it('labels conditions for display', () => {
    expect(conditionLabel('off_guard', null)).toBe('Off guard');
    expect(conditionLabel('frightened', 2)).toBe('Frightened 2');
  });

  it('a table token carries the live block the server chose', () => {
    const out = adaptEntities(map(scene, {
      ...token('a', 'v1'), actorId: 'cmb_skel',
      live: { hp: null, maxHp: null, status: 'Wounded', conditions: [{ name: 'prone', value: null }], active: false },
    }));
    expect(out.tokens[0]?.live).toEqual({ hp: null, maxHp: null, status: 'Wounded', conditions: ['Prone'], active: false });
    expect(adaptEntities(map(scene, { ...token('b', 'v1'), live: null })).tokens[0]?.live).toBeUndefined();
  });

  it('a GM token joins its actor: exact HP for every creature, never a status', () => {
    const out = adaptEntities(map(scene, { ...token('a', 'v1'), actorId: 'cmb_skel', hidden: false }, actor('va1')));
    expect(out.tokens[0]?.live).toEqual({ hp: 7, maxHp: 20, status: null, conditions: ['Off guard', 'Frightened 2'], active: true });
    expect(out.tokens[0]?.hidden).toBeUndefined();
  });

  it('a GM token whose actor is hidden in the tracker is drawn dimmed', () => {
    const out = adaptEntities(map(scene, { ...token('a', 'v1'), actorId: 'cmb_skel', hidden: false }, actor('va1', { hidden: true })));
    expect(out.tokens[0]?.hidden).toBe(true);
  });

  it('a GM token is rebuilt when its actor changes and reused when neither does', () => {
    const cache = newAdaptCache();
    const linked = { ...token('a', 'v1'), actorId: 'cmb_skel', hidden: false };
    const first = adaptEntities(map(scene, linked, actor('va1')), cache);
    expect(adaptEntities(map(scene, linked, actor('va1')), cache).tokens[0]).toBe(first.tokens[0]);
    const hurt = adaptEntities(map(scene, linked, actor('va2', { hp: 3 })), cache);
    expect(hurt.tokens[0]).not.toBe(first.tokens[0]);
    expect(hurt.tokens[0]?.live?.hp).toBe(3);
    expect(adaptEntities(map(scene, linked), cache).tokens[0]?.live).toBeUndefined(); // the actor left
  });

  it('a GM token carries the Speed of its actor for the drag reading (E-4)', () => {
    const linked = { ...token('a', 'v1'), actorId: 'cmb_skel', hidden: false };
    expect(adaptEntities(map(scene, linked, actor('va1', { speed: 25 }))).tokens[0]?.speed).toBe(25);
    expect(adaptEntities(map(scene, linked, actor('va1', { speed: null }))).tokens[0]?.speed).toBeUndefined();
    expect(adaptEntities(map(scene, token('b', 'v1'))).tokens[0]?.speed).toBeUndefined();   // unlinked
  });
});

describe('sameLive', () => {
  const live = { hp: 1, maxHp: 2, status: null, conditions: ['Prone'], active: false } as const;
  it('compares by content', () => {
    expect(sameLive(live, { ...live, conditions: ['Prone'] })).toBe(true);
    expect(sameLive(live, { ...live, hp: 0 })).toBe(false);
    expect(sameLive(live, { ...live, conditions: ['Prone', 'Dying 1'] })).toBe(false);
    expect(sameLive(undefined, undefined)).toBe(true);
    expect(sameLive(live, undefined)).toBe(false);
  });
});

describe('lighting and vision (E-6)', () => {
  const litScene = { ...scene, lighting: true, defaultVision: 320 };
  const pc = (version: string, pcFlag: boolean) => ({
    kind: 'actor', id: 'chr_valeros', version, name: 'Valeros', pc: pcFlag, hp: 1, maxHp: 1, conditions: [],
    active: false, hidden: false, status: '' });

  it('a GM party token sees by its own radius or the scene default, and other tokens do not', () => {
    const own = { ...token('a', 'v1'), actorId: 'chr_valeros', hidden: false, locked: false, vision: 128 };
    const byDefault = { ...token('b', 'v1'), actorId: 'chr_valeros', hidden: false, locked: false, vision: null };
    const unlinked = { ...token('c', 'v1'), hidden: false, locked: false, vision: 500 };
    const out = adaptEntities(map(litScene, own, byDefault, unlinked, pc('va1', true)));
    expect(out.tokens.map((t) => t.vision)).toEqual([128, 320, undefined]);
    expect(adaptEntities(map(litScene, own, pc('va1', false))).tokens[0]?.vision).toBeUndefined();   // a monster
    expect(adaptEntities(map(scene, own, pc('va1', true))).tokens[0]?.vision).toBeUndefined();        // lighting off
  });

  it('a table token sees as far as the server says', () => {
    const out = adaptEntities(map({ ...scene, lighting: true }, { ...token('a', 'v1'), vision: 256 }));
    expect(out.tokens[0]?.vision).toBe(256);
  });

  it('occluders are the walls and doors that are not open, only while lighting is on', () => {
    const walls = [
      { kind: 'wall', id: 'w1', version: 'v1', x1: 0, y1: 0, x2: 10, y2: 0, doorState: 'none' },
      { kind: 'wall', id: 'w2', version: 'v1', x1: 0, y1: 0, x2: 0, y2: 10, doorState: 'open' },
      { kind: 'wall', id: 'w3', version: 'v1', x1: 5, y1: 5, x2: 9, y2: 9, doorState: 'secret' },
    ];
    const out = adaptEntities(map(litScene, ...walls));
    expect(out.lighting).toBe(true);
    expect(out.occluders).toEqual([{ x1: 0, y1: 0, x2: 10, y2: 0 }, { x1: 5, y1: 5, x2: 9, y2: 9 }]);
    const off = adaptEntities(map(scene, ...walls));
    expect(off.lighting).toBeUndefined();
    expect(off.occluders).toBeUndefined();
    // The table's walls have no door state: every one blocks.
    const table = adaptEntities(map({ ...scene, lighting: true }, { kind: 'wall', id: 'w9', version: 'v1', x1: 1, y1: 1, x2: 2, y2: 2 }));
    expect(table.occluders).toEqual([{ x1: 1, y1: 1, x2: 2, y2: 2 }]);
  });
});
