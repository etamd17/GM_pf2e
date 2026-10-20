import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { feetToPx, lightingState, pxToFeet, setLighting, setTokenVision, tokenVisionFeet } from './lighting';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const scene = { kind: 'scene', id: 'scene_a', version: V, name: 'S', width: 640, height: 480, gridSize: 64,
                showGrid: true, backgroundAssetId: null, published: true, lighting: false, defaultVision: 768 } as Of<'scene'>;
const token = { kind: 'token', id: 'tok_a', version: V, x: 0, y: 0, size: 1, label: 'A', color: 1, artAssetId: null,
                actorId: 'chr_v', hidden: false, locked: false, controllerIds: [], vision: null } as Of<'token'>;

describe('lighting actions', () => {
  it('convert feet and scene pixels by the grid (5 ft a square)', () => {
    expect(feetToPx(60, 64)).toBe(768);
    expect(pxToFeet(768, 64)).toBe(60);
    expect(feetToPx(-5, 64)).toBe(0);
  });

  it('read and set the scene switch and default vision in feet', () => {
    expect(lightingState(scene)).toEqual({ lighting: false, defaultVisionFeet: 60 });
    expect(setLighting(scene, true, 30)).toMatchObject({
      type: 'scene.update', sceneId: 'scene_a', payload: { lighting: true, defaultVision: 384 } });
    expect(setLighting(scene, false).payload).toEqual({ lighting: false });
  });

  it('set a token vision in feet, or back to the scene default', () => {
    expect(setTokenVision(token, 60, 64).payload).toEqual({ id: 'tok_a', vision: 768 });
    expect(setTokenVision(token, null, 64).payload).toEqual({ id: 'tok_a', vision: null });
    expect(tokenVisionFeet(token, 64)).toBeNull();
    expect(tokenVisionFeet({ ...token, vision: 384 }, 64)).toBe(30);
  });
});
