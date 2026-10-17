// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { moveToken } from '../app/gmCommands';
import { inverseFor } from '../app/undo';
import type { GmEntity } from '../protocol/frames';
import type { ProjectionSession } from '../protocol/session';
import { useCommands } from '../state/commands';
import { entityOf } from '../state/entities';
import type { GmBoot } from './boot';

// Handoff 048 finding 3, through the real startGm: the command queue, the
// projection session and the undo manager wired as on the page. Only the
// socket is stubbed; acks and frames are fed to the real session.

const captured = vi.hoisted(() => ({ session: null as unknown }));
vi.mock('../protocol/socketTransport', () => ({
  SocketIoTransport: class {
    connected = true;
    constructor(options: { session: unknown }) {
      captured.session = options.session;
    }
    connect() {}
    requestResubscribe() {}
    stopRetrying() {}
    sendCommand() {
      return true;
    }
  },
}));

const { startGm } = await import('./gm');

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V1 = 'v' + 'A'.repeat(24);
const V2 = 'v' + 'B'.repeat(24);
const boot: GmBoot = { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'csrf', memberId: 'user_gm' };
const scene = {
  kind: 'scene', id: 'scene_a', version: V1, name: 'Hall', width: 640, height: 640, gridSize: 64,
  showGrid: true, backgroundAssetId: null, published: true,
} as Of<'scene'>;
const hero = {
  kind: 'token', id: 'tok_hero', version: V1, x: 96, y: 96, size: 1, label: 'Valeros', color: 1,
  artAssetId: null, actorId: null, hidden: false, locked: false, controllerIds: [],
} as Of<'token'>;
const SUB = 'sub_' + 'q'.repeat(20);

function live() {
  sessionStorage.clear();
  const page = startGm(boot);
  const session = captured.session as ProjectionSession<GmEntity, unknown>;
  const attempt = session.subscribing();
  session.onSnapshot({ frame: 'snapshot', subscriptionId: SUB, seq: 0, sceneId: 'scene_a', capabilities: [], entities: [scene, hero] } as never, attempt);
  let seq = 0;
  // The page's edit path: issue, then record what reverses it.
  const move = (x: number) => {
    const token = entityOf('token', 'tok_hero') as Of<'token'>;
    const draft = moveToken(token, x, 96);
    const id = page.issue(draft, null);
    page.undo.edit(id, inverseFor(draft, token));
    return id as string;
  };
  const frame = (origin: string, token: Of<'token'>) => {
    seq += 1;
    session.onPatch({ frame: 'patch', subscriptionId: SUB, seq, upserted: [token], removed: [], originCommandId: origin } as never);
  };
  const ack = (id: string) => page.queue.onAck({ commandId: id, outcome: 'applied' } as never);
  return { page, move, frame, ack };
}

beforeEach(() => {
  useCommands.setState({ pending: [], notice: null, lastApplied: null, undo: { canUndo: false, canRedo: false, next: null } });
});

describe('undo through the live session', () => {
  it('ack before the correlated frame: undo opens only when the frame lands', () => {
    const { move, frame, ack } = live();
    const id = move(160);
    ack(id);
    expect(useCommands.getState().undo.canUndo).toBe(false);
    frame(id, { ...hero, x: 160, version: V2 });
    expect(useCommands.getState().undo.canUndo).toBe(true);
  });

  it('the frame before the ack: undo opens only with the ack', () => {
    const { move, frame, ack } = live();
    const id = move(160);
    frame(id, { ...hero, x: 160, version: V2 });
    expect(useCommands.getState().undo.canUndo).toBe(false);
    ack(id);
    expect(useCommands.getState().undo.canUndo).toBe(true);
  });

  it('the undo it then builds carries the version the frame brought', () => {
    const { page, move, frame, ack } = live();
    const id = move(160);
    ack(id);
    frame(id, { ...hero, x: 160, version: V2 });
    const entry = page.undo.beginStep('undo');
    const draft = entry?.make(entityOf('token', 'tok_hero'));
    expect(draft?.expected).toEqual([{ kind: 'token', id: 'tok_hero', version: V2 }]);
  });

  it('a rejected edit (stale) never becomes undoable and leaves history free', () => {
    const { page, move } = live();
    const id = move(160);
    page.queue.onAck({ commandId: id, outcome: 'rejected', code: 'stale' } as never);
    expect(page.undo.busy).toBe(false);
    expect(useCommands.getState().undo.canUndo).toBe(false);
  });
});
