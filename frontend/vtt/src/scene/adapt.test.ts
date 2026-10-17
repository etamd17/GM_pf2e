import { describe, expect, it } from 'vitest';
import { adaptEntities, newAdaptCache } from './adapt';

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
