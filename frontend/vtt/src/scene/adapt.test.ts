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
