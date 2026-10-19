// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest';
import { SceneRuntime } from './SceneRuntime';

// E-4: while a token is dragged, the runtime reads its distance from where it
// started to the square it would land on, against the linked actor's Speed,
// by the scene's rule; the reading goes when the drag ends.

interface Internals {
  scene: unknown;
  drag: unknown;
  tokens: Map<string, { model: unknown; root: { x: number; y: number; position: { set(x: number, y: number): void } } }>;
  worldPoint: (e: unknown) => { x: number; y: number };
  scheduler: { requestRender: () => void };
  layers: Map<string, unknown>;
  onPointerMove: (e: { pointerId: number; clientX: number; clientY: number }) => void;
  onPointerUp: (e: { pointerId: number; type: string; clientX: number; clientY: number }) => void;
  setMeasureRule: (rule: 'uniform' | 'alternating') => void;
  probe: () => { drag: { feet: number; speed: number | null; moves: number; over: boolean; label: string } | null };
}

const G = 64;
const centre = (col: number, row: number) => col * G + G / 2 + (row - row);

function dragging(speed?: number) {
  const onTokenDrop = vi.fn();
  const runtime = new SceneRuntime(document.createElement('div'), {
    surface: 'gm',
    interaction: { onTokenDrop, canDragToken: () => true },
  }) as unknown as Internals;
  const model = { id: 'tok_a', x: centre(0, 0), y: centre(0, 0), size: 1, label: 'A', color: 1, ...(speed ? { speed } : {}) };
  runtime.scene = { gridSize: G, width: 2000, height: 2000, tokens: [model], lights: [], walls: [], templates: [] };
  const root = { x: model.x, y: model.y, position: { set(x: number, y: number) { root.x = x; root.y = y; } } };
  runtime.tokens.set('tok_a', { model, root });
  runtime.layers.set('overlay', { removeChildren: () => [], addChild: () => undefined });
  runtime.drag = {
    pointerId: 1, lastX: 0, lastY: 0, startX: 0, startY: 0, moved: false,
    token: { id: 'tok_a', dx: 0, dy: 0, from: { x: model.x, y: model.y } }, template: null, measure: null,
    hit: { kind: 'token', id: 'tok_a' },
  };
  runtime.scheduler.requestRender = vi.fn();
  let world = { x: model.x, y: model.y };
  runtime.worldPoint = () => world;
  const moveTo = (col: number, row: number) => {
    world = { x: col * G + G / 2, y: row * G + G / 2 };
    runtime.onPointerMove({ pointerId: 1, clientX: 50, clientY: 50 });
  };
  return { runtime, onTokenDrop, moveTo };
}

describe('the drag reading', () => {
  it('reads the distance against the actor Speed as the token moves', () => {
    const { runtime, moveTo } = dragging(25);
    moveTo(4, 0);
    expect(runtime.probe().drag).toEqual({ feet: 20, speed: 25, moves: 1, over: false, label: '20 / 25 ft' });
    moveTo(7, 0);
    expect(runtime.probe().drag).toMatchObject({ feet: 35, moves: 2, over: true });
  });

  it('counts by the scene rule', () => {
    const { runtime, moveTo } = dragging(30);
    runtime.setMeasureRule('alternating');
    moveTo(3, 3);
    expect(runtime.probe().drag?.feet).toBe(20);
    runtime.setMeasureRule('uniform');
    expect(runtime.probe().drag?.feet).toBe(15);
  });

  it('shows plain distance for a token without a Speed, and goes away on release', () => {
    const { runtime, moveTo, onTokenDrop } = dragging();
    moveTo(2, 0);
    expect(runtime.probe().drag).toMatchObject({ feet: 10, speed: null, label: '10 ft' });
    runtime.onPointerUp({ pointerId: 1, type: 'pointerup', clientX: 50, clientY: 50 });
    expect(onTokenDrop).toHaveBeenCalledOnce();
    expect(runtime.probe().drag).toBeNull();
  });
});
