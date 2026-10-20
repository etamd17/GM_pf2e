// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneRuntime } from './SceneRuntime';
import type { RenderScene } from './renderModel';

// E-6: the darkness is drawn on the table when lighting is on, on the GM page
// only as a preview, and rebuilt only when what casts or blocks sight changes.

interface Internals {
  layers: Map<string, unknown>;
  scheduler: { requestRender: () => void };
  drawVision: (scene: RenderScene) => void;
  setVisionPreview: (on: boolean) => void;
  scene: RenderScene | null;
  probe: () => { vision: { lighting: boolean; drawn: boolean; sources: number; occluders: number; builds: number } };
}

const fakeContext = {
  fillRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(), fill: vi.fn(),
  fillStyle: '', globalCompositeOperation: 'source-over',
};

function runtime(surface: 'gm' | 'table') {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(fakeContext as never);
  const r = new SceneRuntime(document.createElement('div'), { surface }) as unknown as Internals;
  r.layers.set('vision', { removeChildren: () => [], addChild: () => undefined });
  r.scheduler.requestRender = vi.fn();
  return r;
}

const lit: RenderScene = {
  width: 640, height: 480, gridSize: 64, showGrid: true, walls: [], lights: [{ id: 'l', x: 300, y: 300, radius: 64, color: 1 }],
  tokens: [{ id: 'tok_a', x: 100, y: 100, size: 1, label: 'A', color: 1, vision: 256 }],
  lighting: true, occluders: [{ x1: 200, y1: 0, x2: 200, y2: 480 }],
};

afterEach(() => vi.restoreAllMocks());

describe('the darkness', () => {
  it('is drawn on the table from every source, and cached until something that matters changes', () => {
    const r = runtime('table');
    r.drawVision(lit);
    expect(r.probe().vision).toMatchObject({ drawn: true, sources: 2, occluders: 1, builds: 1 });
    r.drawVision({ ...lit, showGrid: false });               // nothing that casts or blocks sight
    expect(r.probe().vision.builds).toBe(1);
    r.drawVision({ ...lit, tokens: [{ ...lit.tokens[0]!, x: 120 }] });
    expect(r.probe().vision.builds).toBe(2);
  });

  it('goes away when lighting is turned off', () => {
    const r = runtime('table');
    r.drawVision(lit);
    r.drawVision({ ...lit, lighting: false });
    expect(r.probe().vision.drawn).toBe(false);
  });

  it('shows on the GM page only as a preview', () => {
    const r = runtime('gm');
    r.drawVision(lit);
    expect(r.probe().vision.drawn).toBe(false);
    r.scene = lit;
    r.setVisionPreview(true);
    expect(r.probe().vision.drawn).toBe(true);
    r.setVisionPreview(false);
    expect(r.probe().vision.drawn).toBe(false);
  });
});

describe('the darkness after a renderer rebuild (review 106)', () => {
  it('is attached again to the new layer, from the cached texture', () => {
    const r = runtime('table');
    const first = { removeChildren: () => [], addChild: vi.fn() };
    r.layers.set('vision', first);
    r.drawVision(lit);
    expect(first.addChild).toHaveBeenCalledTimes(1);
    // createApplication replaces every layer, then repaints the same scene.
    r.layers.clear();
    const rebuilt = { removeChildren: () => [], addChild: vi.fn() };
    r.layers.set('vision', rebuilt);
    r.drawVision(lit);
    expect(rebuilt.addChild).toHaveBeenCalledTimes(1);
    expect(r.probe().vision.builds).toBe(1);               // the texture was reused, not rebuilt
    r.drawVision(lit);
    expect(rebuilt.addChild).toHaveBeenCalledTimes(1);     // and not attached twice
  });

  it('is attached once the layers exist when the GM preview was switched on before them', () => {
    const r = runtime('gm');
    r.layers.clear();                                       // before init: no layers
    r.scene = lit;
    r.setVisionPreview(true);
    expect(r.probe().vision.builds).toBe(1);
    const layer = { removeChildren: () => [], addChild: vi.fn() };
    r.layers.set('vision', layer);
    r.drawVision(lit);
    expect(layer.addChild).toHaveBeenCalledTimes(1);
    expect(r.probe().vision.builds).toBe(1);
  });
});
