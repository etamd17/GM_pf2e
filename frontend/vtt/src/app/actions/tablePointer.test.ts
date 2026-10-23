import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { onTvSceneId } from './tablePointer';

const V = 'v' + 'A'.repeat(24);
const pointer = (sceneId: string | null) => ({ kind: 'table', id: 'table', version: V, sceneId }) as GmEntity;

describe('onTvSceneId', () => {
  it('reads the table pointer, null when the table shows nothing, undefined before any snapshot', () => {
    expect(onTvSceneId([pointer('scene_a')])).toBe('scene_a');
    expect(onTvSceneId([pointer(null)])).toBeNull();
    expect(onTvSceneId([])).toBeUndefined();
  });
});
