// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GmLive } from '../live/gm';
import type { GmEntity } from '../protocol/frames';
import { adaptEntities } from '../scene/adapt';
import type { RuntimeInteraction } from '../scene/SceneRuntime';
import { useCommands } from '../state/commands';
import { useConnection } from '../state/connection';
import { useEntities } from '../state/entities';
import { useOverlays } from '../state/overlays';
import { resetProjectionForTest, useProjection } from '../state/projection';

// The GM tools end to end inside the page: map input (through the runtime's
// interaction callbacks), the inspector and the scene bar each issue the right
// command, with the edited entity's current version, through live.issue.

const runtimes = vi.hoisted(
  () => [] as Array<{ interaction?: RuntimeInteraction | undefined; selection: unknown; draft: unknown }>,
);

vi.mock('../scene/SceneRuntime', () => ({
  SceneRuntime: class {
    interaction?: RuntimeInteraction | undefined;
    selection: unknown = null;
    draft: unknown = null;
    constructor(_host: HTMLElement, options: { interaction?: RuntimeInteraction }) {
      this.interaction = options.interaction;
      runtimes.push(this);
    }
    setScene() {}
    setSelection(s: unknown) {
      this.selection = s;
    }
    setWallDraft(d: unknown) {
      this.draft = d;
    }
    init() {
      return Promise.resolve();
    }
    destroy() {}
  },
}));

const { GmWorkspace } = await import('./GmWorkspace');
const { UndoManager } = await import('./undo');

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V1 = 'v' + 'A'.repeat(24);
const scene = {
  kind: 'scene', id: 'scene_a', version: V1, name: 'Hall', width: 640, height: 640, gridSize: 64,
  showGrid: true, backgroundAssetId: null, published: true,
} as Of<'scene'>;
const hero = {
  kind: 'token', id: 'tok_hero', version: V1, x: 96, y: 96, size: 1, label: 'Valeros', color: 1,
  artAssetId: null, actorId: null, hidden: false, locked: false, controllerIds: [],
} as Of<'token'>;
const wall = { kind: 'wall', id: 'wall_1', version: V1, x1: 0, y1: 0, x2: 128, y2: 0, doorState: 'closed' } as Of<'wall'>;

function seed(entities: GmEntity[]) {
  const map = new Map(entities.map((e) => [`${e.kind}\u0000${e.id}`, e]));
  useEntities.getState().set(map);
  const projection = useProjection.getState();
  const epoch = projection.open();
  if (epoch !== null) projection.setScene(epoch, adaptEntities(map as never));
}

function setup() {
  const issued: Array<{ draft: Record<string, unknown>; overlay: unknown }> = [];
  const live = {
    boot: { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'x' },
    issue: (draft: Record<string, unknown>, overlay: unknown) => {
      issued.push({ draft, overlay });
      return `cmd_${issued.length.toString().padStart(16, '0')}`;
    },
    queue: { reauthenticated: vi.fn() },
    undo: new UndoManager(),
    transport: { connected: true },
  } as unknown as GmLive;
  render(<GmWorkspace sceneName="Hall" live={live} />);
  const runtime = runtimes.at(-1);
  if (!runtime?.interaction) throw new Error('map not mounted');
  return { issued, interaction: runtime.interaction, runtime };
}

beforeEach(() => {
  runtimes.length = 0;
  resetProjectionForTest();
  useEntities.getState().clear();
  useOverlays.setState({ overlays: new Map() });
  useCommands.setState({ pending: [], notice: null, lastApplied: null });
  useConnection.setState({ status: 'live', renderer: 'ok' });
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ scenes: [] }) })));
  seed([scene, hero, wall]);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('map input', () => {
  it('a dropped token is a move with its current version and a moving overlay', () => {
    const { issued, interaction } = setup();
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    expect(issued).toEqual([
      {
        draft: {
          type: 'token.move',
          expected: [{ kind: 'token', id: 'tok_hero', version: V1 }],
          payload: { id: 'tok_hero', x: 160, y: 96 },
        },
        overlay: { kind: 'token', id: 'tok_hero', patch: { x: 160, y: 96 } },
      },
    ]);
  });

  it('a drop back where it was sends nothing, and a locked token cannot be dragged', () => {
    const { issued, interaction } = setup();
    act(() => interaction.onTokenDrop?.('tok_hero', 96, 96));
    expect(issued).toEqual([]);
    expect(interaction.canDragToken?.('tok_hero')).toBe(true);
    act(() => seed([scene, { ...hero, locked: true }, wall]));
    expect(interaction.canDragToken?.('tok_hero')).toBe(false);
  });

  it('nothing is draggable while the connection is not live', () => {
    const { interaction } = setup();
    act(() => useConnection.getState().setStatus('reconnecting'));
    const latest = runtimes.at(-1)?.interaction ?? interaction;
    expect(latest.canDragToken?.('tok_hero')).toBe(false);
  });

  it('the token tool places a snapped token, shown at once as pending', () => {
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Token' }));
    act(() => interaction.onClick?.({ x: 300, y: 300 }, null));
    const [{ draft, overlay }] = issued as [{ draft: { type: string; payload: { id: string; x: number; y: number } }; overlay: { create: { x: number } } }];
    expect(draft.type).toBe('token.create');
    expect([draft.payload.x, draft.payload.y]).toEqual([288, 288]);
    expect(overlay.create.x).toBe(288);
  });

  it('the wall tool draws a chain of walls between snapped points, and Esc stops', () => {
    const { issued, interaction, runtime } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Walls' }));
    act(() => interaction.onClick?.({ x: 10, y: 10 }, null));
    act(() => interaction.onPointerMove?.({ x: 130, y: 5 }));
    expect(runtime.draft).toEqual({ a: { x: 0, y: 0 }, b: { x: 128, y: 0 } });
    act(() => interaction.onClick?.({ x: 130, y: 5 }, null));
    act(() => interaction.onClick?.({ x: 130, y: 120 }, null));
    expect(issued.map((i) => (i.draft as { payload: unknown }).payload)).toEqual([
      expect.objectContaining({ x1: 0, y1: 0, x2: 128, y2: 0, doorState: 'none' }),
      expect.objectContaining({ x1: 128, y1: 0, x2: 128, y2: 128 }),
    ]);
    act(() => {
      interaction.onKey?.(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(runtime.draft).toBeNull();
  });

  it('arrow keys move the selected token one square; Delete deletes it', () => {
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 96, y: 96 }, { kind: 'token', id: 'tok_hero' }));
    act(() => {
      interaction.onKey?.(new KeyboardEvent('keydown', { key: 'ArrowRight' }));
    });
    act(() => {
      interaction.onKey?.(new KeyboardEvent('keydown', { key: 'Delete' }));
    });
    expect(issued.map((i) => (i.draft as { type: string }).type)).toEqual(['token.move', 'entity.delete']);
    expect((issued[0]?.draft as { payload: unknown }).payload).toEqual({ id: 'tok_hero', x: 160, y: 96 });
    expect(issued[1]?.overlay).toEqual({ kind: 'token', id: 'tok_hero', deleted: true });
  });
});

describe('inspector and scene bar', () => {
  it('edits a token: label on commit, hidden as a toggle, each with the current version', () => {
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 96, y: 96 }, { kind: 'token', id: 'tok_hero' }));
    const label = screen.getByLabelText('Label');
    fireEvent.focus(label);
    fireEvent.change(label, { target: { value: 'Valeros the Brave' } });
    fireEvent.blur(label);
    fireEvent.click(screen.getByLabelText('Hidden from the table'));
    expect(issued.map((i) => i.draft)).toEqual([
      { type: 'token.update', expected: [{ kind: 'token', id: 'tok_hero', version: V1 }], payload: { id: 'tok_hero', label: 'Valeros the Brave' } },
      { type: 'token.update', expected: [{ kind: 'token', id: 'tok_hero', version: V1 }], payload: { id: 'tok_hero', hidden: true } },
    ]);
  });

  it('a field being edited is not overwritten by a newer frame', () => {
    const { interaction } = setup();
    act(() => interaction.onClick?.({ x: 96, y: 96 }, { kind: 'token', id: 'tok_hero' }));
    const label = screen.getByLabelText('Label') as HTMLInputElement;
    fireEvent.focus(label);
    fireEvent.change(label, { target: { value: 'Mine' } });
    act(() => seed([scene, { ...hero, label: 'Theirs', version: 'v' + 'B'.repeat(24) }, wall]));
    expect(label.value).toBe('Mine');
  });

  it('changes a door state', () => {
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 50, y: 0 }, { kind: 'wall', id: 'wall_1' }));
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'open' } });
    expect(issued.at(-1)?.draft).toEqual({
      type: 'wall.update',
      expected: [{ kind: 'wall', id: 'wall_1', version: V1 }],
      payload: { id: 'wall_1', doorState: 'open' },
    });
  });

  it('publishes, shows on the table and clears it, against the scene version', () => {
    const { issued } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Unpublish' }));
    fireEvent.click(screen.getByRole('button', { name: 'Show on table' }));
    fireEvent.click(screen.getByRole('button', { name: 'Clear table' }));
    expect(issued.map((i) => (i.draft as { type: string }).type)).toEqual(['scene.update', 'table.show', 'table.clear']);
    for (const i of issued) expect((i.draft as { expected: unknown }).expected).toEqual([{ kind: 'scene', id: 'scene_a', version: V1 }]);
  });

  it('every editing control is disabled while not live', () => {
    const { interaction } = setup();
    act(() => interaction.onClick?.({ x: 96, y: 96 }, { kind: 'token', id: 'tok_hero' }));
    act(() => useConnection.getState().setStatus('resyncing'));
    expect((screen.getByLabelText('Label') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Show on table' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Token' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('lists tokens for screen readers, hidden ones marked', () => {
    seed([scene, hero, { ...hero, id: 'tok_spy', label: 'Spy', hidden: true }, wall]);
    setup();
    const list = screen.getByRole('list', { name: 'Tokens on the map' });
    expect(list.textContent).toContain('Valeros');
    expect(list.textContent).toContain('Spy (hidden from the table)');
  });
});

describe('undo', () => {
  function setupWithUndo() {
    const issued: Array<{ draft: Record<string, unknown>; overlay: unknown }> = [];
    const undo = new UndoManager(100, (state) => useCommands.getState().setUndo(state));
    const live = {
      boot: { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'x' },
      issue: (draft: Record<string, unknown>, overlay: unknown) => {
        issued.push({ draft, overlay });
        return `cmd_${issued.length.toString().padStart(16, '0')}`;
      },
      queue: { reauthenticated: vi.fn() },
      undo,
      transport: { connected: true },
    } as unknown as GmLive;
    render(<GmWorkspace sceneName="Hall" live={live} />);
    const runtime = runtimes.at(-1);
    if (!runtime?.interaction) throw new Error('map not mounted');
    return { issued, undo, interaction: runtime.interaction };
  }
  const V2 = 'v' + 'B'.repeat(24);

  it('Ctrl+Z after an applied move sends the move back against the version it now has', () => {
    const { issued, undo, interaction } = setupWithUndo();
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    // nothing to undo until the server applies it
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued).toHaveLength(1);
    act(() => {
      undo.applied('cmd_0000000000000001');
      seed([scene, { ...hero, x: 160, version: V2 }, wall]);
    });
    expect((screen.getByRole('button', { name: /^Undo/ }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued[1]?.draft).toEqual({
      type: 'token.move',
      expected: [{ kind: 'token', id: 'tok_hero', version: V2 }],
      payload: { id: 'tok_hero', x: 96, y: 96 },
    });
    // once the undo applies, redo is offered
    act(() => undo.applied('cmd_0000000000000002'));
    expect(undo.canRedo).toBe(true);
  });

  it('an undo onto something deleted since says so and sends nothing', () => {
    const { issued, undo, interaction } = setupWithUndo();
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => {
      undo.applied('cmd_0000000000000001');
      seed([scene, wall]);
    });
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued).toHaveLength(1);
    expect(useCommands.getState().notice).toMatchObject({ kind: 'info' });
  });

  it('Ctrl+Z inside a text field is the browser\'s, not the map\'s', () => {
    const { issued, undo, interaction } = setupWithUndo();
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => undo.applied('cmd_0000000000000001'));
    const field = document.createElement('input');
    document.body.appendChild(field);
    fireEvent.keyDown(field, { key: 'z', ctrlKey: true });
    expect(issued).toHaveLength(1);
    field.remove();
  });
});
