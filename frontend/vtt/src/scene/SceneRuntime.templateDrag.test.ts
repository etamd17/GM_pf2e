// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SceneRuntime } from './SceneRuntime';

// Parity P2 (handoff 067): a template dragged with the select tool. The drag
// is drawn shifted by whole squares; the drop reports that offset once, and
// the shift is held until the projection changes the templates, or a fallback
// puts it back when the command changes nothing.

interface Internals {
  scene: unknown;
  drag: unknown;
  templateShift: { id: string; dx: number; dy: number } | null;
  drawTemplates: (scene: unknown) => void;
  worldPoint: (e: unknown) => { x: number; y: number };
  scheduler: { requestRender: () => void };
  onPointerMove: (e: { pointerId: number; clientX: number; clientY: number }) => void;
  onPointerUp: (e: { pointerId: number; type: string; clientX: number; clientY: number }) => void;
}

const TEMPLATE = { id: 'tpl_a', shape: 'burst', x: 64, y: 64, aimX: 64, aimY: 64, size: 2, color: 7 };

function dragging() {
  const onTemplateDrop = vi.fn();
  const runtime = new SceneRuntime(document.createElement('div'), {
    surface: 'gm',
    interaction: { onTemplateDrop, canDragTemplate: () => true },
  }) as unknown as Internals;
  runtime.scene = { gridSize: 64, templates: [TEMPLATE], tokens: [] };
  runtime.drag = {
    pointerId: 1, lastX: 0, lastY: 0, startX: 0, startY: 0, moved: false,
    token: null, template: { id: 'tpl_a', start: { x: 70, y: 70 } }, hit: { kind: 'template', id: 'tpl_a' },
  };
  runtime.drawTemplates = vi.fn();
  runtime.scheduler.requestRender = vi.fn();
  let world = { x: 70, y: 70 };
  runtime.worldPoint = () => world;
  const moveTo = (x: number, y: number) => {
    world = { x, y };
    runtime.onPointerMove({ pointerId: 1, clientX: 50, clientY: 50 }); // past the drag threshold
  };
  return { runtime, onTemplateDrop, moveTo };
}

afterEach(() => vi.useRealTimers());

describe('dragging a template', () => {
  it('shifts it by whole squares and reports the offset once on release', () => {
    const { runtime, onTemplateDrop, moveTo } = dragging();
    moveTo(70 + 90, 70 - 40); // 1.41 squares right, 0.63 up: rounds to 1 and 1
    expect(runtime.templateShift).toEqual({ id: 'tpl_a', dx: 64, dy: -64 });
    expect(runtime.drawTemplates).toHaveBeenCalledTimes(1);
    moveTo(70 + 80, 70 - 50); // same squares: nothing redrawn
    expect(runtime.drawTemplates).toHaveBeenCalledTimes(1);
    runtime.onPointerUp({ pointerId: 1, type: 'pointerup', clientX: 50, clientY: 50 });
    expect(onTemplateDrop).toHaveBeenCalledExactlyOnceWith('tpl_a', 64, -64);
    expect(runtime.templateShift).not.toBeNull(); // held until the projection arrives
  });

  it('a drag that ends where it began sends nothing', () => {
    const { runtime, onTemplateDrop, moveTo } = dragging();
    moveTo(80, 75);
    runtime.onPointerUp({ pointerId: 1, type: 'pointerup', clientX: 50, clientY: 50 });
    expect(onTemplateDrop).not.toHaveBeenCalled();
    expect(runtime.templateShift).toBeNull();
  });

  it('a cancelled drag puts the template back and sends nothing', () => {
    const { runtime, onTemplateDrop, moveTo } = dragging();
    moveTo(200, 200);
    runtime.onPointerUp({ pointerId: 1, type: 'pointercancel', clientX: 50, clientY: 50 });
    expect(onTemplateDrop).not.toHaveBeenCalled();
    expect(runtime.templateShift).toBeNull();
  });

  it('a command that changes nothing lets the fallback put it back', () => {
    vi.useFakeTimers();
    const { runtime, moveTo } = dragging();
    moveTo(200, 70);
    runtime.onPointerUp({ pointerId: 1, type: 'pointerup', clientX: 50, clientY: 50 });
    expect(runtime.templateShift).not.toBeNull();
    vi.advanceTimersByTime(2000);
    expect(runtime.templateShift).toBeNull();
  });
});
