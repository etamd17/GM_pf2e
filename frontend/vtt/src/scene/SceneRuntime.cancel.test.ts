// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { SceneRuntime } from './SceneRuntime';

// Handoff 043 finding 2: a cancelled drag restored the token's position but
// requested no frame. Rendering is event-driven, so once the drag's last frame
// had drawn, the token stayed painted where the drag left it.

function cancelled(type: 'pointercancel' | 'lostpointercapture') {
  const onTokenDrop = vi.fn();
  const runtime = new SceneRuntime(document.createElement('div'), {
    surface: 'gm',
    interaction: { onTokenDrop },
  }) as unknown as {
    scene: unknown;
    drag: unknown;
    syncTokens: (...args: unknown[]) => void;
    drawOverlay: () => void;
    scheduler: { requestRender: () => void };
    onPointerUp: (e: { pointerId: number; type: string }) => void;
  };
  runtime.scene = { tokens: [] };
  runtime.drag = { pointerId: 1, moved: true, token: { id: 'hero', dx: 0, dy: 0 }, hit: null };
  const calls: string[] = [];
  runtime.syncTokens = vi.fn(() => calls.push('restore'));
  runtime.drawOverlay = vi.fn(() => calls.push('overlay'));
  runtime.scheduler.requestRender = vi.fn(() => calls.push('frame'));
  runtime.onPointerUp({ pointerId: 1, type });
  return { runtime, calls, onTokenDrop };
}

describe('cancelling a token drag', () => {
  it.each(['pointercancel', 'lostpointercapture'] as const)(
    '%s restores the token, redraws the selection and requests a frame, and sends nothing',
    (type) => {
      const { runtime, calls, onTokenDrop } = cancelled(type);
      expect(runtime.syncTokens).toHaveBeenCalledWith(runtime.scene, true);
      expect(calls).toEqual(['restore', 'overlay', 'frame']);
      expect(onTokenDrop).not.toHaveBeenCalled();
      expect(runtime.drag).toBeNull();
    },
  );
});
