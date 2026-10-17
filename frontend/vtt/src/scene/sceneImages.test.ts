import { describe, expect, it } from 'vitest';
import { EMPTY_SCENE } from './renderModel';
import { sceneImageUrls } from './SceneRuntime';

// What SceneRuntime hands TextureLoader.keep() on every scene: an image that
// is still drawn keeps retrying after a failure; one that left stops.
describe('sceneImageUrls', () => {
  it('lists the background and every token art, and nothing else', () => {
    const tok = (id: string, artUrl?: string) => ({ id, x: 0, y: 0, size: 1, label: id, color: 0, ...(artUrl ? { artUrl } : {}) });
    expect(sceneImageUrls(EMPTY_SCENE)).toEqual([]);
    expect(
      sceneImageUrls({
        ...EMPTY_SCENE,
        backgroundUrl: '/vtt/table-media/asset_bg',
        tokens: [tok('a', '/vtt/table-media/asset_a'), tok('b'), tok('c', '/vtt/table-media/asset_c')],
      }),
    ).toEqual(['/vtt/table-media/asset_bg', '/vtt/table-media/asset_a', '/vtt/table-media/asset_c']);
  });
});
