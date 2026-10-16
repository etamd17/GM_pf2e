import { beforeEach, describe, expect, it } from 'vitest';
import { EMPTY_SCENE } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { resetProjectionForTest } from '../state/projection';
import { CommandQueue, newCommandId, type CommandOutcome } from './commands';
import { REJECTION_CODES, type AckFrame, type RejectionCode } from './frames';
import { MockTransport } from './mockTransport';
import { REJECTION_ACTIONS } from './rejections';
import { ProjectionSession, type SessionClock } from './session';
import { storeSink } from './storeSink';

// The command lifecycle (client proposal section 4; ADR-0002 sections 7-8):
// local refusal, same-id retries, new-id rejections, reauthentication, and one
// behaviour for every rejection code (acceptance criterion 13, client half).

interface Tok {
  kind: 'token';
  id: string;
}

function fakeClock() {
  let now = 0;
  let next = 1;
  const timers = new Map<number, { at: number; fn: () => void }>();
  const clock: SessionClock = {
    now: () => now,
    setTimeout: (fn, ms) => {
      const id = next++;
      timers.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimeout: (h) => void timers.delete(h as number),
  };
  return {
    clock,
    advance(ms: number) {
      now += ms;
      for (const [id, t] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
        if (t.at <= now) {
          timers.delete(id);
          t.fn();
        }
      }
    },
  };
}

function setup(options: { valid?: boolean; online?: boolean } = {}) {
  const time = fakeClock();
  const mock = new MockTransport<Tok>();
  let overlays: ReadonlyMap<string, string> = new Map();
  const session = new ProjectionSession<Tok, string>('gm', storeSink(() => EMPTY_SCENE, mock), time.clock, (o) => {
    overlays = o;
  });
  mock.subscribe(session);
  mock.push({ type: 'snapshot', frame: { frame: 'snapshot', subscriptionId: 'sub_1', seq: 0, entities: [] } }).deliver(session);
  const sent: Array<Record<string, unknown>> = [];
  const outcomes: CommandOutcome[] = [];
  let online = options.online ?? true;
  let n = 0;
  const queue = new CommandQueue<Tok, string>({
    session,
    send: (c) => {
      if (!online) return false;
      sent.push(c);
      return true;
    },
    validate: () => options.valid ?? true,
    onOutcome: (o) => outcomes.push(o),
    clock: time.clock,
    newId: () => `cmd_${String(++n).padStart(16, '0')}`,
    ackTimeoutMs: 1000,
    backoffMs: [100, 200],
  });
  const draft = { type: 'token.move', campaignId: 'c', sceneId: 's', expected: [{ kind: 'token', id: 't', version: 'v'.repeat(25) }], payload: { id: 't', x: 1, y: 2 } };
  return {
    time, session, queue, sent, outcomes, draft, overlays: () => overlays,
    setOnline: (v: boolean) => { online = v; },
    ack: (commandId: string, code?: RejectionCode): AckFrame =>
      code === undefined
        ? { commandId, outcome: 'applied' }
        : { commandId, outcome: 'rejected', code },
  };
}

beforeEach(() => {
  resetProjectionForTest();
  useConnection.setState({ status: 'connecting', renderer: 'ok' });
});

describe('CommandQueue', () => {
  it('sends a complete v1 command and keeps the overlay after an applied ack', () => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'moving');
    expect(s.sent).toEqual([{ schemaVersion: 1, commandId: id, ...s.draft }]);
    expect([...s.overlays().keys()]).toEqual([id]);
    s.queue.onAck(s.ack(id as string));
    expect(s.queue.size).toBe(0);
    expect([...s.overlays().keys()]).toEqual([id]);    // only the correlated frame clears it
    expect(s.outcomes.at(-1)).toMatchObject({ kind: 'applied' });
  });

  it('refuses a command that fails the schema or the command rules, and sends nothing', () => {
    const s = setup({ valid: false });
    expect(s.queue.issue(s.draft, 'x')).toBeNull();
    expect(s.sent).toEqual([]);
    expect(s.outcomes).toEqual([{ kind: 'refused-locally', type: 'token.move' }]);
  });

  it('retries a timed-out command with the same id', () => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'x');
    s.time.advance(1000);           // ack timeout
    s.time.advance(100);            // backoff
    expect(s.sent.map((c) => c.commandId)).toEqual([id, id]);
  });

  it.each(['rate-limited', 'temporarily-unavailable'] as const)('%s retries the same id after backoff', (code) => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'x') as string;
    s.queue.onAck(s.ack(id, code));
    expect(s.sent).toHaveLength(1);
    s.time.advance(100);
    expect(s.sent.map((c) => c.commandId)).toEqual([id, id]);
    expect(s.overlays().has(id)).toBe(true);           // still pending, overlay kept
  });

  it('authentication-required parks the command until the user signs in, then resends it', () => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'x') as string;
    s.queue.onAck(s.ack(id, 'authentication-required'));
    expect(s.outcomes.at(-1)).toEqual({ kind: 'reauthenticate', commandId: id });
    s.time.advance(60_000);
    expect(s.sent).toHaveLength(1);                    // parked, not retried on its own
    s.queue.reauthenticated();
    expect(s.sent.map((c) => c.commandId)).toEqual([id, id]);
  });

  it('waits for a socket and resends when the connection is live again', () => {
    const s = setup({ online: false });
    const id = s.queue.issue(s.draft, 'x');
    expect(s.queue.views()).toEqual([{ commandId: id, type: 'token.move', state: 'waiting' }]);
    s.setOnline(true);
    s.queue.resume();
    expect(s.sent.map((c) => c.commandId)).toEqual([id]);
  });

  it('a duplicate ack is idempotent', () => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'x') as string;
    s.queue.onAck(s.ack(id, 'stale'));
    s.queue.onAck(s.ack(id, 'stale'));
    expect(s.outcomes.filter((o) => o.kind === 'rejected')).toHaveLength(1);
  });

  // Criterion 13 (client half): exactly one behaviour per code, and the id is
  // kept only where the ADR says "retry the same commandId".
  it.each(REJECTION_CODES.map((c) => [c]))('%s has exactly one behaviour', (code) => {
    const s = setup();
    const id = s.queue.issue(s.draft, 'x') as string;
    s.queue.onAck(s.ack(id, code));
    const action = REJECTION_ACTIONS[code];
    if (action.kind === 'retry-same-id') {
      expect(s.queue.size).toBe(1);
      expect(s.overlays().has(id)).toBe(true);
    } else {
      expect(s.queue.size).toBe(0);
      expect(s.overlays().has(id)).toBe(false);
      expect(s.outcomes.at(-1)).toMatchObject({ kind: 'rejected', code, action });
    }
  });
});

it('command ids are 128-bit opaque tokens', () => {
  const ids = new Set(Array.from({ length: 200 }, () => newCommandId()));
  expect(ids.size).toBe(200);
  for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{16,128}$/);
});
