// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GmLive } from '../live/gm';
import type { GmEntity } from '../protocol/frames';
import { adaptEntities } from '../scene/adapt';
import type { RuntimeInteraction } from '../scene/SceneRuntime';
import { encodeFogBits } from '../protocol/semantics';
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

  it('the scene picker refetches once a table command applies, not on other edits', async () => {
    const { issued, interaction } = setup();
    await act(async () => {});
    const calls = () => (fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
    const before = calls();
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => useCommands.getState().notify({ kind: 'applied', commandId: 'cmd_0000000000000001' } as never));
    expect(calls()).toBe(before);
    fireEvent.click(screen.getByRole('button', { name: 'Show on table' }));
    expect((issued[1]?.draft as { type: string }).type).toBe('table.show');
    act(() => useCommands.getState().notify({ kind: 'applied', commandId: 'cmd_0000000000000002' } as never));
    expect(calls()).toBe(before + 1);
  });

  it('images: token art and the scene background upload, then point at the new asset', async () => {
    const { issued, interaction } = setup();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => (url.startsWith('/vtt/api/assets')
      ? { ok: true, status: 201, json: async () => ({ asset: { id: 'asset_new' } }) }
      : { ok: true, status: 200, json: async () => ({ scenes: [] }) })));
    const file = new File([new Uint8Array(8)], 'a.png', { type: 'image/png' });
    // nothing selected: the scene section offers the background
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Background file'), { target: { files: [file] } });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(issued.at(-1)?.draft).toEqual({
      type: 'scene.update', sceneId: 'scene_a', expected: [{ kind: 'scene', id: 'scene_a', version: V1 }],
      payload: { backgroundAssetId: 'asset_new' },
    });
    // a token: its art, with no optimistic overlay (the frame brings the image)
    act(() => interaction.onClick?.({ x: 96, y: 96 }, { kind: 'token', id: 'tok_hero' }));
    await act(async () => {
      fireEvent.change(screen.getByLabelText('Art file'), { target: { files: [file] } });
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(issued.at(-1)).toEqual({
      draft: { type: 'token.update', expected: [{ kind: 'token', id: 'tok_hero', version: V1 }], payload: { id: 'tok_hero', artAssetId: 'asset_new' } },
      overlay: null,
    });
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
  // `open` mirrors the session's overlays: an issued command's overlay stays
  // open until the test lands it (its correlated frame), when `track` is set.
  function setupWithUndo({ track = false } = {}) {
    const issued: Array<{ draft: Record<string, unknown>; overlay: unknown }> = [];
    const open = new Set<string>();
    const undo = new UndoManager(100, (state) => useCommands.getState().setUndo(state), (id) => open.has(id));
    const live = {
      boot: { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'x' },
      issue: (draft: Record<string, unknown>, overlay: unknown) => {
        issued.push({ draft, overlay });
        const id = `cmd_${issued.length.toString().padStart(16, '0')}`;
        if (track) open.add(id);
        return id;
      },
      queue: { reauthenticated: vi.fn() },
      undo,
      transport: { connected: true },
    } as unknown as GmLive;
    render(<GmWorkspace sceneName="Hall" live={live} />);
    const runtime = runtimes.at(-1);
    if (!runtime?.interaction) throw new Error('map not mounted');
    const land = (id: string) => {
      open.delete(id);
      undo.overlaysChanged();
    };
    return { issued, undo, interaction: runtime.interaction, land };
  }
  const V2 = 'v' + 'B'.repeat(24);
  const V3 = 'v' + 'C'.repeat(24);
  const cmd = (n: number) => `cmd_${n.toString().padStart(16, '0')}`;
  const undoButton = () => screen.getByRole('button', { name: /^Undo/ }) as HTMLButtonElement;

  it('ack before patch: undo waits for the frame, then uses the version the frame brought', () => {
    const { issued, undo, interaction, land } = setupWithUndo({ track: true });
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => undo.applied(cmd(1)));                       // the ack...
    expect(undoButton().disabled).toBe(true);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued).toHaveLength(1);                         // ...is not enough: the projection is behind
    act(() => {
      seed([scene, { ...hero, x: 160, version: V2 }, wall]); // the correlated frame
      land(cmd(1));
    });
    expect(undoButton().disabled).toBe(false);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued[1]?.draft).toMatchObject({ type: 'token.move', expected: [{ id: 'tok_hero', version: V2 }] });
  });

  it('a second undo waits until the first has landed, and a new edit ends redo at once', () => {
    const { issued, undo, interaction, land } = setupWithUndo({ track: true });
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => {
      undo.applied(cmd(1));
      seed([scene, { ...hero, x: 160, version: V2 }, wall]);
      land(cmd(1));
    });
    act(() => interaction.onTokenDrop?.('tok_hero', 224, 96));
    act(() => {
      undo.applied(cmd(2));
      seed([scene, { ...hero, x: 224, version: V3 }, wall]);
      land(cmd(2));
    });
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });   // undo the second move
    expect(issued).toHaveLength(3);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });   // the first undo is still on its way
    expect(issued).toHaveLength(3);
    act(() => {
      undo.applied(cmd(3));
      seed([scene, { ...hero, x: 160, version: 'v' + 'D'.repeat(24) }, wall]);
      land(cmd(3));
    });
    expect(undo.canRedo).toBe(true);
    act(() => interaction.onTokenDrop?.('tok_hero', 288, 96)); // a new edit, not yet answered
    expect(undo.canRedo).toBe(false);
    expect((screen.getByRole('button', { name: /^Redo/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a rejected undo keeps the entry, so the GM can try again', () => {
    const { issued, undo, interaction, land } = setupWithUndo({ track: true });
    act(() => interaction.onTokenDrop?.('tok_hero', 160, 96));
    act(() => {
      undo.applied(cmd(1));
      seed([scene, { ...hero, x: 160, version: V2 }, wall]);
      land(cmd(1));
    });
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    act(() => {
      undo.rejected(cmd(2));                                  // e.g. stale: someone moved it
      land(cmd(2));
      seed([scene, { ...hero, x: 192, version: V3 }, wall]);
    });
    expect(undoButton().disabled).toBe(false);
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(issued[2]?.draft).toMatchObject({ type: 'token.move', expected: [{ version: V3 }], payload: { x: 96, y: 96 } });
  });

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

describe('fog of war (parity P1)', () => {
  // The 640 x 640 scene at 64 px is 10 x 10 cells: 13 bytes of bits.
  const fogOf = (bits: Uint8Array, enabled = true) =>
    ({ kind: 'fog', id: 'fog', version: V1, enabled, cols: 10, rows: 10, revealed: encodeFogBits(bits) }) as Of<'fog'>;
  // A wall straight down the grid line x = 320, the full height: two rooms.
  const divider = { kind: 'wall', id: 'wall_mid', version: V1, x1: 320, y1: 0, x2: 320, y2: 640, doorState: 'closed' } as Of<'wall'>;

  it('the Reveal tool sends the room under the click, bounded by walls and closed doors', () => {
    act(() => seed([scene, hero, wall, divider, fogOf(new Uint8Array(13))]));
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Reveal' }));
    act(() => interaction.onClick?.({ x: 96, y: 96 }, null));
    const draft = issued.at(-1)?.draft as { type: string; expected: unknown[]; payload: { mode: string; cells: number[][] } };
    expect(draft.type).toBe('fog.reveal');
    expect(draft.expected).toEqual([]);
    expect(draft.payload.mode).toBe('reveal');
    expect(draft.payload.cells).toHaveLength(50); // the left room only
    expect(draft.payload.cells.every(([c]) => (c as number) < 5)).toBe(true);
  });

  it('the Hide tool sends only cells that are revealed now', () => {
    const bits = new Uint8Array(13);
    bits[0] = 0b00000011; // cells (0,0) and (1,0)
    act(() => seed([scene, hero, wall, divider, fogOf(bits)]));
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }));
    act(() => interaction.onClick?.({ x: 96, y: 96 }, null));
    const payload = (issued.at(-1)?.draft as { payload: { mode: string; cells: number[][] } }).payload;
    expect(payload.mode).toBe('hide');
    expect(payload.cells.map((c) => c.join(',')).sort()).toEqual(['0,0', '1,0']); // flood order is BFS order
  });

  it('turns fog on against its version, and asks before a reset', () => {
    act(() => seed([scene, hero, wall, fogOf(new Uint8Array(13), false)]));
    const { issued } = setup();
    fireEvent.click(screen.getByLabelText('Fog of war'));
    expect(issued.at(-1)?.draft).toEqual({ type: 'fog.update', expected: [{ kind: 'fog', id: 'fog', version: V1 }], payload: { enabled: true } });
    vi.stubGlobal('confirm', vi.fn(() => false));
    fireEvent.click(screen.getByRole('button', { name: 'Reset fog' }));
    expect((issued.at(-1)?.draft as { type: string }).type).toBe('fog.update'); // declined: nothing sent
    vi.stubGlobal('confirm', vi.fn(() => true));
    fireEvent.click(screen.getByRole('button', { name: 'Reset fog' }));
    expect(issued.at(-1)?.draft).toEqual({ type: 'fog.reset', expected: [{ kind: 'fog', id: 'fog', version: V1 }], payload: {} });
  });

  it('a scene without a fog entity says it is too large for fog', () => {
    setup();
    expect(screen.getByText('This scene is too large for fog.')).toBeTruthy();
  });
});

describe('templates and terrain (parity P2)', () => {
  // 640 x 640 at 64 px: 10 x 10 cells, 50 bytes of 4-bit kinds.
  const terrainOf = (cells: Uint8Array) =>
    ({ kind: 'terrain', id: 'terrain', version: V1, cols: 10, rows: 10, cells: encodeFogBits(cells) }) as Of<'terrain'>;
  const divider = { kind: 'wall', id: 'wall_mid', version: V1, x1: 320, y1: 0, x2: 320, y2: 640, doorState: 'none' } as Of<'wall'>;
  const lastDraft = (issued: Array<{ draft: Record<string, unknown> }>) =>
    issued.at(-1)?.draft as { type: string; payload: Record<string, unknown> };

  it('a burst is placed with one click at the grid point, sized in squares', () => {
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Template' }));
    fireEvent.change(screen.getByLabelText('Size (squares)'), { target: { value: '4' } });
    act(() => interaction.onClick?.({ x: 130, y: 190 }, null));
    const draft = lastDraft(issued);
    expect(draft.type).toBe('template.create');
    expect(draft.payload).toMatchObject({ shape: 'burst', x: 128, y: 192, aimX: 128, aimY: 192, size: 4, hidden: false });
  });

  it('a cone takes two clicks: the origin, then the aim', () => {
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Template' }));
    fireEvent.change(screen.getByLabelText('Shape'), { target: { value: 'cone' } });
    act(() => interaction.onClick?.({ x: 64, y: 64 }, null));
    expect(issued).toHaveLength(0);
    act(() => interaction.onClick?.({ x: 300, y: 64 }, null));
    expect(lastDraft(issued).payload).toMatchObject({ shape: 'cone', x: 64, y: 64, aimX: 300, aimY: 64, size: 3 });
  });

  it('an emanation started on a token is centred on it and grown by its footprint', () => {
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Template' }));
    fireEvent.change(screen.getByLabelText('Shape'), { target: { value: 'emanation' } });
    act(() => interaction.onClick?.({ x: 100, y: 100 }, { kind: 'token', id: 'tok_hero' }));
    expect(lastDraft(issued).payload).toMatchObject({ shape: 'emanation', x: 96, y: 96, size: 4 }); // 3 + ceil(1/2)
  });

  it('arrow keys move a selected template one square, aim and all', () => {
    const tpl = { kind: 'template', id: 'tpl_a', version: V1, shape: 'cone', x: 64, y: 64, aimX: 192, aimY: 64, size: 3, color: 7, hidden: false } as Of<'template'>;
    act(() => seed([scene, hero, wall, tpl]));
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 100, y: 64 }, { kind: 'template', id: 'tpl_a' }));
    act(() => interaction.onKey?.(new KeyboardEvent('keydown', { key: 'ArrowDown' })));
    expect(lastDraft(issued)).toEqual({
      type: 'template.update',
      expected: [{ kind: 'template', id: 'tpl_a', version: V1 }],
      payload: { id: 'tpl_a', x: 64, y: 128, aimX: 192, aimY: 128 },
    });
  });

  it('terrain paints the room under the click, and Alt-click paints one square', () => {
    act(() => seed([scene, hero, wall, divider, terrainOf(new Uint8Array(50))]));
    const { issued, interaction } = setup();
    fireEvent.click(screen.getByRole('button', { name: 'Terrain' }));
    fireEvent.change(screen.getByLabelText('Paint'), { target: { value: '2' } });
    act(() => interaction.onClick?.({ x: 96, y: 96 }, null, { alt: false }));
    const cells = lastDraft(issued).payload.cells as number[][];
    expect(lastDraft(issued).type).toBe('terrain.paint');
    expect(cells).toHaveLength(50); // the left half, bounded by the wall
    expect(cells.every(([c, , k]) => (c as number) < 5 && k === 2)).toBe(true);
    act(() => interaction.onClick?.({ x: 500, y: 500 }, null, { alt: true }));
    expect(lastDraft(issued).payload).toEqual({ cells: [[7, 7, 2]] });
  });

  it('the template inspector changes size and visibility', () => {
    const tpl = { kind: 'template', id: 'tpl_a', version: V1, shape: 'burst', x: 64, y: 64, aimX: 64, aimY: 64, size: 3, color: 7, hidden: false } as Of<'template'>;
    act(() => seed([scene, hero, wall, tpl]));
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 64, y: 64 }, { kind: 'template', id: 'tpl_a' }));
    const size = screen.getByLabelText('Size (squares)') as HTMLInputElement;
    fireEvent.focus(size);
    fireEvent.change(size, { target: { value: '6' } });
    fireEvent.blur(size);
    expect(lastDraft(issued).payload).toEqual({ id: 'tpl_a', size: 6 });
    fireEvent.click(screen.getByLabelText('Hidden from the table'));
    expect(lastDraft(issued).payload).toEqual({ id: 'tpl_a', hidden: true });
  });

  it('the template inspector changes colour, and the current one is marked', () => {
    const tpl = { kind: 'template', id: 'tpl_a', version: V1, shape: 'burst', x: 64, y: 64, aimX: 64, aimY: 64, size: 3, color: 0x75c5eb, hidden: false } as Of<'template'>;
    act(() => seed([scene, hero, wall, tpl]));
    const { issued, interaction } = setup();
    act(() => interaction.onClick?.({ x: 64, y: 64 }, { kind: 'template', id: 'tpl_a' }));
    expect(screen.getByRole('radio', { name: '#75c5eb' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('radio', { name: '#75c5eb' }));
    expect(issued).toHaveLength(0); // already that colour
    fireEvent.click(screen.getByRole('radio', { name: '#a83a3a' }));
    expect(lastDraft(issued).payload).toEqual({ id: 'tpl_a', color: 0xa83a3a });
  });

  it('a template dragged with the select tool moves by the dropped offset, aim and all', () => {
    const tpl = { kind: 'template', id: 'tpl_a', version: V1, shape: 'cone', x: 64, y: 64, aimX: 192, aimY: 64, size: 3, color: 7, hidden: false } as Of<'template'>;
    act(() => seed([scene, hero, wall, tpl]));
    const { issued, interaction } = setup();
    expect(interaction.canDragTemplate?.('tpl_a')).toBe(true);
    expect(interaction.canDragTemplate?.('tpl_missing')).toBe(false);
    act(() => interaction.onTemplateDrop?.('tpl_a', 128, -64));
    expect(lastDraft(issued)).toEqual({
      type: 'template.update',
      expected: [{ kind: 'template', id: 'tpl_a', version: V1 }],
      payload: { id: 'tpl_a', x: 192, y: 0, aimX: 320, aimY: 0 },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Terrain' }));
    expect(interaction.canDragTemplate?.('tpl_a')).toBe(false); // only the select tool drags
  });
});
