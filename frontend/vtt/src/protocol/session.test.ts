import { beforeEach, describe, expect, it } from 'vitest';
import { EMPTY_SCENE, type RenderScene, type RenderToken } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { resetProjectionForTest, useProjection } from '../state/projection';
import { BOOTSTRAP_REJECTION_REASONS, REJECTION_CODES, RESYNC_REASONS, type PatchFrame } from './frames';
import { MockTransport } from './mockTransport';
import { REJECTION_ACTIONS, keepsCommandId } from './rejections';
import { ProjectionSession, STALE_DISPLAY_DEADLINE_MS, type SessionClock, type Surface } from './session';
import { storeSink } from './storeSink';

// Client-side acceptance for VTT-ADR-0002 delivery rules (criteria 6, 11, 13,
// 15 and 16, client half), driven through MockTransport against the real
// projection and connection stores.

interface Tok {
  kind: 'token';
  id: string;
  x: number;
}

const tok = (id: string, x = 0): Tok => ({ kind: 'token', id, x });

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
    clearTimeout: (h) => {
      timers.delete(h as number);
    },
  };
  return {
    clock,
    advance(ms: number) {
      now += ms;
      for (const [id, t] of [...timers]) {
        if (t.at <= now) {
          timers.delete(id);
          t.fn();
        }
      }
    },
  };
}

const adapt = (entities: ReadonlyMap<string, Tok>): RenderScene => ({
  ...EMPTY_SCENE,
  tokens: [...entities.values()].map(
    (t): RenderToken => ({ id: t.id, x: t.x, y: 0, size: 1, label: t.id, color: 0 }),
  ),
});

function setup(surface: Surface = 'gm') {
  const transport = new MockTransport<Tok>();
  const time = fakeClock();
  let overlays: ReadonlyMap<string, string> = new Map();
  const session = new ProjectionSession<Tok, string>(surface, storeSink(adapt, transport), time.clock, (o) => {
    overlays = o;
  });
  const scene = () => useProjection.getState().scene;
  const ids = () => scene()?.tokens.map((t) => t.id) ?? null;
  const status = () => useConnection.getState().status;
  return { transport, time, session, scene, ids, status, overlays: () => overlays };
}

const patch = (sub: string, seq: number, upserted: Tok[] = [], extra: Partial<PatchFrame<Tok>> = {}) => ({
  type: 'patch' as const,
  frame: { frame: 'patch' as const, subscriptionId: sub, seq, upserted, removed: [], ...extra },
});
const snapshot = (sub: string, entities: Tok[]) => ({
  type: 'snapshot' as const,
  frame: { frame: 'snapshot' as const, subscriptionId: sub, seq: 0 as const, entities },
});

beforeEach(() => {
  resetProjectionForTest();
  useConnection.setState({ status: 'connecting', renderer: 'ok' });
});

describe('snapshot and patch sequencing (section 6)', () => {
  it('applies a snapshot then contiguous patches', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('b')]), patch('s1', 2, [tok('a', 5)]));
    t.transport.deliver(t.session);
    expect(t.status()).toBe('live');
    expect(t.ids()).toEqual(['a', 'b']);
    expect(t.scene()?.tokens.find((k) => k.id === 'a')?.x).toBe(5);
  });

  it('ignores duplicated frames', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', []), patch('s1', 1, [tok('a')]), patch('s1', 2, [tok('b')]));
    t.transport.deliver(t.session, { duplicate: true });
    expect(t.ids()).toEqual(['a', 'b']);
    expect(t.session.stats.duplicates).toBe(2);
  });

  it('discards a frame for another subscription', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', []), patch('old', 1, [tok('ghost')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual([]);
    expect(t.session.stats.discardedOtherSubscription).toBe(1);
  });

  it('a gap enters resyncing without applying, then a new snapshot recovers', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('b')]), patch('s1', 3, [tok('c')]));
    t.transport.deliver(t.session);
    expect(t.status()).toBe('resyncing');
    expect(t.ids()).toEqual(['a', 'b']); // seq 3 not applied
    expect(t.transport.resubscribeRequests).toEqual(['sequence-gap']);
    t.transport.push(patch('s1', 2, [tok('late')]), snapshot('s2', [tok('fresh')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['fresh']); // late old-subscription frame ignored
    expect(t.status()).toBe('live');
  });

  it('reordered frames converge or visibly resync, never partially apply', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', []), patch('s1', 1, [tok('a')]), patch('s1', 2, [tok('b')]));
    t.transport.deliver(t.session, { order: [0, 2, 1] });
    expect(t.status()).toBe('resyncing');
    expect(t.ids()).toEqual([]); // neither out-of-order frame applied
  });

  it('a snapshot is never merged with earlier state', () => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a'), tok('b')]));
    t.transport.deliver(t.session);
    t.session.onResyncRequired('reconnected');
    t.transport.push(snapshot('s2', [tok('c')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['c']);
  });
});

describe('command completion (section 7, criterion 15)', () => {
  function live() {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    t.session.beginCommand('c1', 'move a');
    return t;
  }

  it('ack before frame: the applied ack alone keeps the overlay; the frame ends it', () => {
    const t = live();
    t.transport.push({ type: 'ack', ack: { commandId: 'c1', outcome: 'applied' } });
    t.transport.deliver(t.session);
    expect([...t.overlays().keys()]).toEqual(['c1']);
    t.transport.push(patch('s1', 1, [tok('a', 64)], { originCommandId: 'c1' }));
    t.transport.deliver(t.session);
    expect(t.overlays().size).toBe(0);
  });

  it('frame before ack: the frame ends it and the late ack is a no-op', () => {
    const t = live();
    t.transport.push(patch('s1', 1, [tok('a', 64)], { originCommandId: 'c1' }), {
      type: 'ack',
      ack: { commandId: 'c1', outcome: 'applied' },
    });
    t.transport.deliver(t.session, { duplicate: true });
    expect(t.overlays().size).toBe(0);
    expect(t.session.stats.applied).toBe(1);
  });

  it('an empty completion patch ends the overlay and consumes one seq', () => {
    const t = live();
    t.transport.push(patch('s1', 1, [], { originCommandId: 'c1' }), patch('s1', 2, [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.overlays().size).toBe(0);
    expect(t.ids()).toEqual(['a', 'b']);
  });

  it('a replacement snapshot supersedes overlays from the old subscription', () => {
    const t = live();
    t.session.onResyncRequired('reconnected');
    t.transport.push(snapshot('s2', [tok('a', 64)]));
    t.transport.deliver(t.session);
    expect(t.overlays().size).toBe(0);
  });

  it('a stale rejection reverts; a rate limit keeps the overlay for a same-id retry', () => {
    const t = live();
    t.session.beginCommand('c2', 'move b');
    expect(t.session.onAck({ commandId: 'c1', outcome: 'rejected', code: 'stale' })?.kind).toBe('revert');
    expect(t.session.onAck({ commandId: 'c2', outcome: 'rejected', code: 'rate-limited' })?.kind).toBe(
      'retry-same-id',
    );
    expect([...t.overlays().keys()]).toEqual(['c2']);
  });

  it('a resync-required rejection resubscribes; unsupported-version seals', () => {
    const t = live();
    t.session.onAck({ commandId: 'c1', outcome: 'rejected', code: 'resync-required' });
    expect(t.status()).toBe('resyncing');
    expect(t.transport.resubscribeRequests).toEqual(['state-lost']);
    const u = live();
    u.session.onAck({ commandId: 'c1', outcome: 'rejected', code: 'unsupported-version' });
    expect(u.status()).toBe('version-mismatch');
    expect(u.session.isSealed).toBe(true);
  });
});

describe('resync reasons (section 3, criterion 16)', () => {
  it.each(RESYNC_REASONS)('handles %s', (reason) => {
    const t = setup();
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    t.session.onResyncRequired(reason);
    if (reason === 'revoked' || reason === 'version-changed') {
      expect(t.session.isSealed).toBe(true);
      expect(t.scene()).toBeNull();
      expect(t.status()).toBe(reason === 'revoked' ? 'revoked' : 'version-mismatch');
      expect(t.transport.retrying).toBe(false);
      // No replacement subscription is accepted after a terminal reason.
      t.transport.push(snapshot('s2', [tok('b')]));
      t.transport.deliver(t.session);
      expect(t.scene()).toBeNull();
    } else {
      expect(t.status()).toBe('resyncing');
      const cleared = reason === 'scene-changed' || reason === 'grant-changed';
      expect(t.scene() === null).toBe(cleared);
      t.transport.push(snapshot('s2', [tok('b')]));
      t.transport.deliver(t.session);
      expect(t.ids()).toEqual(['b']);
      expect(t.status()).toBe('live');
    }
  });
});

describe('bootstrap rejections (section 1, criterion 16)', () => {
  const expected = {
    'grant-revoked': 'revoked',
    'grant-expired': 'revoked',
    'session-ended': 'session-ended',
    'unsupported-version': 'version-mismatch',
  } as const;

  it.each(BOOTSTRAP_REJECTION_REASONS)('%s clears, seals and stops retrying', (reason) => {
    const t = setup('table');
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    t.session.onBootstrapRejected(reason);
    expect(t.scene()).toBeNull();
    expect(t.session.isSealed).toBe(true);
    expect(t.transport.retrying).toBe(false);
    expect(t.status()).toBe(expected[reason]);
    expect(useProjection.getState().open()).toBeNull();
  });
});

describe('table stale-display deadline (section 10, criterion 11)', () => {
  function liveTable(surface: Surface = 'table') {
    const t = setup(surface);
    t.session.subscribing();
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    return t;
  }

  it('keeps the scene until the deadline, then clears and locks', () => {
    const t = liveTable();
    t.session.onTransportLost();
    expect(t.status()).toBe('reconnecting');
    t.time.advance(STALE_DISPLAY_DEADLINE_MS - 1);
    expect(t.ids()).toEqual(['a']);
    t.time.advance(1);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('offline-locked');
  });

  it('a reconnect without a replacement snapshot does not cancel the deadline', () => {
    const t = liveTable();
    t.session.onTransportLost();
    t.session.subscribing(); // transport back, subscribe sent, no snapshot yet
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('offline-locked');
  });

  it('a replacement snapshot before the deadline cancels it', () => {
    const t = liveTable();
    t.session.onTransportLost();
    t.time.advance(30_000);
    t.session.subscribing();
    t.transport.push(snapshot('s2', [tok('b')]));
    t.transport.deliver(t.session);
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.ids()).toEqual(['b']);
    expect(t.status()).toBe('live');
  });

  it('a locked table recovers when a snapshot finally arrives', () => {
    const t = liveTable();
    t.session.onTransportLost();
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    t.session.subscribing();
    t.transport.push(snapshot('s2', [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['b']);
    expect(t.status()).toBe('live');
  });

  it('the GM surface has no stale-display deadline', () => {
    const t = liveTable('gm');
    t.session.onTransportLost();
    t.time.advance(STALE_DISPLAY_DEADLINE_MS * 2);
    expect(t.ids()).toEqual(['a']);
    expect(t.status()).toBe('reconnecting');
  });

  it('revocation while disconnected clears immediately on the reconnect rejection', () => {
    const t = liveTable();
    t.session.onTransportLost();
    t.time.advance(5_000);
    t.session.onBootstrapRejected('grant-revoked');
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('revoked');
  });
});

describe('rejection behaviour table (section 8, criterion 13)', () => {
  it('has exactly one behaviour per code', () => {
    expect(Object.keys(REJECTION_ACTIONS).sort()).toEqual([...REJECTION_CODES].sort());
  });

  it('keeps the commandId only for the three same-id retry codes', () => {
    const same = REJECTION_CODES.filter(keepsCommandId).sort();
    expect(same).toEqual(['authentication-required', 'rate-limited', 'temporarily-unavailable']);
  });
});
