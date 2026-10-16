import { beforeEach, describe, expect, it } from 'vitest';
import { EMPTY_SCENE, type RenderScene } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { resetProjectionForTest, useProjection } from '../state/projection';
import { ProjectionSession, type SessionClock } from './session';
import { SocketIoTransport, type IoFactory } from './socketTransport';
import { storeSink } from './storeSink';
import { StrictDecoder } from './strictParser';

// The production transport against a fake socket: what it sends, and that
// nothing reaches the session without passing its surface's validators.

interface Tok {
  kind: 'token';
  id: string;
  version: string;
  x: number;
}

class FakeSocket {
  connected = false;
  handlers = new Map<string, Array<(...args: unknown[]) => void>>();
  emitted: Array<{ event: string; data: unknown; ack?: ((reply: unknown) => void) | undefined }> = [];
  connects = 0;
  disconnects = 0;
  options: Record<string, unknown> = {};
  on(event: string, fn: (...args: unknown[]) => void) {
    this.handlers.set(event, [...(this.handlers.get(event) ?? []), fn]);
    return this;
  }
  emit(event: string, data: unknown, ack?: (reply: unknown) => void) {
    this.emitted.push({ event, data, ack });
    return this;
  }
  connect() {
    this.connects += 1;
    this.connected = true;
    this.fire('connect');
    return this;
  }
  disconnect() {
    this.disconnects += 1;
    this.connected = false;
    return this;
  }
  fire(event: string, ...args: unknown[]) {
    for (const fn of this.handlers.get(event) ?? []) fn(...args);
  }
  serverClose(reason = 'io server disconnect') {
    this.connected = false;
    this.fire('disconnect', reason);
  }
}

const clock: SessionClock = { now: () => 0, setTimeout: () => 0, clearTimeout: () => {} };
const adapt = (entities: ReadonlyMap<string, Tok>): RenderScene => ({
  ...EMPTY_SCENE,
  tokens: [...entities.values()].map((t) => ({ id: t.id, x: t.x, y: 0, size: 1, label: t.id, color: 0 })),
});
const isFrame = (d: unknown) => typeof d === 'object' && d !== null && 'subscriptionId' in d && 'seq' in d;

function setup(options: { bootstrapValid?: boolean } = {}) {
  const socket = new FakeSocket();
  const timers: Array<() => void> = [];
  const acks: unknown[] = [];
  let transport!: SocketIoTransport<Tok>;
  const session = new ProjectionSession<Tok>(
    'gm',
    storeSink(adapt, {
      requestResubscribe: (r) => transport.requestResubscribe(r),
      stopRetrying: () => transport.stopRetrying(),
    }),
    clock,
  );
  const io: IoFactory = (_url, opts) => {
    socket.options = opts as Record<string, unknown>;
    return socket as never;
  };
  transport = new SocketIoTransport<Tok>({
    session,
    io,
    csrf: 'csrf-token',
    validators: {
      frame: isFrame,
      resync: (d) => typeof d === 'object' && d !== null && 'reason' in d && 'subscriptionId' in d,
      bootstrap: (d) => options.bootstrapValid !== false && typeof d === 'object' && d !== null && 'reason' in d,
      ack: (d) => typeof d === 'object' && d !== null && 'commandId' in d,
    },
    subscribeMessage: () => ({ schemaVersion: 1, audience: 'gm', campaignId: 'c', sceneId: 's' }),
    onAck: (ack) => acks.push(ack),
    setTimeout: (fn) => {
      timers.push(fn);
      return 0;
    },
  });
  return { socket, session, transport, timers, acks };
}

const snapshot = (sub: string, entities: Tok[] = []) => ({ frame: 'snapshot', subscriptionId: sub, seq: 0, entities });
const subscribes = (socket: FakeSocket) => socket.emitted.filter((e) => e.event === 'vtt.subscribe');

beforeEach(() => {
  resetProjectionForTest();
  useConnection.setState({ status: 'connecting', renderer: 'ok' });
});

describe('SocketIoTransport', () => {
  it('connects to /vtt with the strict parser, credentials and the CSRF token, then subscribes', () => {
    const { socket, transport } = setup();
    transport.connect();
    expect(socket.options.path).toBe('/socket.io');
    expect(socket.options.withCredentials).toBe(true);
    expect(socket.options.auth).toEqual({ csrf: 'csrf-token' });
    expect((socket.options.parser as { Decoder: unknown }).Decoder).toBe(StrictDecoder);
    expect(subscribes(socket).map((e) => e.data)).toEqual([
      { schemaVersion: 1, audience: 'gm', campaignId: 'c', sceneId: 's' },
    ]);
  });

  it('applies a valid snapshot for its own subscribe and goes live', () => {
    const { socket, transport } = setup();
    transport.connect();
    socket.fire('vtt.snapshot', snapshot('sub_1', [{ kind: 'token', id: 't1', version: 'v1', x: 5 }]));
    expect(useConnection.getState().status).toBe('live');
    expect(useProjection.getState().scene?.tokens.map((t) => t.id)).toEqual(['t1']);
  });

  it('never applies an invalid message: it resubscribes for lost state instead', () => {
    const { socket, transport } = setup();
    transport.connect();
    socket.fire('vtt.snapshot', snapshot('sub_1', [{ kind: 'token', id: 't1', version: 'v1', x: 5 }]));
    socket.fire('vtt.patch', { frame: 'patch', bogus: true });
    expect(transport.stats.invalid).toBe(1);
    expect(subscribes(socket)).toHaveLength(2);
    expect(useConnection.getState().status).toBe('resyncing');
    // a patch arriving as a snapshot event is invalid too
    socket.fire('vtt.snapshot', { frame: 'patch', subscriptionId: 'x', seq: 1, upserted: [], removed: [] });
    expect(transport.stats.invalid).toBe(2);
  });

  it('a refused handshake seals the session and stops retrying', () => {
    const { socket, transport } = setup();
    transport.connect();
    socket.fire('connect_error', Object.assign(new Error('bootstrap-rejected'), { data: { schemaVersion: 1, reason: 'grant-revoked' } }));
    expect(useConnection.getState().status).toBe('revoked');
    expect(transport.retrying).toBe(false);
    expect(socket.disconnects).toBe(1);
  });

  it('a network error is left to Socket.IO to retry', () => {
    const { socket, transport } = setup();
    transport.connect();
    socket.fire('connect_error', new Error('xhr poll error'));
    expect(transport.retrying).toBe(true);
    expect(useConnection.getState().status).not.toBe('revoked');
  });

  it('a subscribe refusal in the ack ends the session', () => {
    const { socket, transport } = setup();
    transport.connect();
    subscribes(socket)[0]?.ack?.({ schemaVersion: 1, reason: 'session-ended' });
    expect(useConnection.getState().status).toBe('session-ended');
  });

  it('reconnects after the server closed the socket, so the table learns why', () => {
    const { socket, transport, timers } = setup();
    transport.connect();
    socket.serverClose();
    expect(useConnection.getState().status).toBe('reconnecting');
    expect(timers).toHaveLength(1);
    timers[0]?.();
    expect(socket.connects).toBe(2);
  });

  it('does not reconnect after a terminal state', () => {
    const { socket, transport, timers } = setup();
    transport.connect();
    socket.fire('vtt.snapshot', snapshot('sub_1'));
    socket.fire('vtt.resync_required', { schemaVersion: 1, subscriptionId: 'sub_1', reason: 'revoked' });
    socket.serverClose();
    timers.forEach((fn) => fn());
    expect(socket.connects).toBe(1);
  });

  it('passes valid acks to the command queue and sends commands only on a live socket', () => {
    const { socket, transport, acks } = setup();
    expect(transport.sendCommand({ commandId: 'x' })).toBe(false);
    transport.connect();
    expect(transport.sendCommand({ commandId: 'x' })).toBe(true);
    socket.fire('vtt.ack', { schemaVersion: 1, commandId: 'x', outcome: 'applied' });
    expect(acks).toHaveLength(1);
  });
});

describe('StrictDecoder', () => {
  function decode(raw: string): { packets: unknown[]; error: unknown } {
    const decoder = new StrictDecoder();
    const packets: unknown[] = [];
    decoder.on('decoded', (p: unknown) => packets.push(p));
    try {
      decoder.add(raw);
      return { packets, error: null };
    } catch (error) {
      return { packets, error };
    }
  }

  it('decodes an ordinary event packet', () => {
    const { packets, error } = decode('2/vtt,["vtt.ack",{"schemaVersion":1,"commandId":"cmd_x","outcome":"applied"}]');
    expect(error).toBeNull();
    expect(packets).toEqual([
      { type: 2, nsp: '/vtt', data: ['vtt.ack', { schemaVersion: 1, commandId: 'cmd_x', outcome: 'applied' }] },
    ]);
  });

  it('refuses duplicate keys, which JSON.parse would silently accept', () => {
    const { packets, error } = decode('2/vtt,["vtt.patch",{"seq":1,"seq":2}]');
    expect(packets).toEqual([]);
    expect(error).toBeInstanceOf(Error);
  });

  it('refuses binary packets', () => {
    expect(decode('51-/vtt,["vtt.patch",{"_placeholder":true,"num":0}]').error).toBeInstanceOf(Error);
  });
});
