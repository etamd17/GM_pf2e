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
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('b')]), patch('s1', 2, [tok('a', 5)]));
    t.transport.deliver(t.session);
    expect(t.status()).toBe('live');
    expect(t.ids()).toEqual(['a', 'b']);
    expect(t.scene()?.tokens.find((k) => k.id === 'a')?.x).toBe(5);
  });

  it('ignores duplicated frames', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', []), patch('s1', 1, [tok('a')]), patch('s1', 2, [tok('b')]));
    t.transport.deliver(t.session, { duplicate: true });
    expect(t.ids()).toEqual(['a', 'b']);
    expect(t.session.stats.duplicates).toBe(2);
  });

  it('discards a frame for another subscription', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', []), patch('old', 1, [tok('ghost')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual([]);
    expect(t.session.stats.discardedOtherSubscription).toBe(1);
  });

  it('a gap enters resyncing without applying, then a new snapshot recovers', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('b')]), patch('s1', 3, [tok('c')]));
    t.transport.deliver(t.session);
    expect(t.status()).toBe('resyncing');
    expect(t.ids()).toEqual(['a', 'b']); // seq 3 not applied
    expect(t.transport.resubscribeRequests).toEqual(['sequence-gap']);
    // Nothing is accepted until the transport actually sends the new subscribe.
    t.transport.push(snapshot('s2', [tok('early')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['a', 'b']);
    expect(t.session.stats.refusedSnapshots).toBe(1);
    t.transport.subscribe(t.session);
    t.transport.push(patch('s1', 2, [tok('late')]), snapshot('s3', [tok('fresh')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['fresh']); // late old-subscription frame ignored
    expect(t.status()).toBe('live');
  });

  it('reordered frames converge or visibly resync, never partially apply', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', []), patch('s1', 1, [tok('a')]), patch('s1', 2, [tok('b')]));
    t.transport.deliver(t.session, { order: [0, 2, 1] });
    expect(t.status()).toBe('resyncing');
    expect(t.ids()).toEqual([]); // neither out-of-order frame applied
  });

  it('a snapshot is never merged with earlier state', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a'), tok('b')]));
    t.transport.deliver(t.session);
    t.session.onResyncRequired('reconnected', t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
    t.transport.push(snapshot('s2', [tok('c')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['c']);
  });
});

describe('command completion (section 7, criterion 15)', () => {
  function live() {
    const t = setup();
    t.transport.subscribe(t.session);
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
    t.session.onResyncRequired('reconnected', t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
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
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    t.session.onResyncRequired(reason, t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
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
    t.transport.subscribe(t.session);
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
    t.transport.subscribe(t.session);
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
    t.transport.subscribe(t.session); // transport back, subscribe sent, no snapshot yet
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('offline-locked');
  });

  it('a replacement snapshot before the deadline cancels it', () => {
    const t = liveTable();
    t.session.onTransportLost();
    t.time.advance(30_000);
    t.transport.subscribe(t.session);
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
    t.transport.subscribe(t.session);
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

describe('old snapshots cannot revive a retired subscription (sections 6 and 10)', () => {
  function liveTable() {
    const t = setup('table');
    const first = t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-old', [tok('a')]));
    t.transport.deliver(t.session);
    return { t, first };
  }

  it('Codex repro: a delayed old snapshot after transport loss is refused', () => {
    const { t, first } = liveTable();
    t.session.onTransportLost();
    t.transport.push({ ...snapshot('s-old', [tok('a')]), attempt: first });
    t.transport.deliver(t.session);
    expect(t.status()).toBe('reconnecting');
    expect(t.session.currentSubscriptionId).toBeNull();
    expect(t.session.stats.refusedSnapshots).toBe(1);
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('offline-locked');
  });

  it('a delayed old snapshot is refused after a new subscribe begins and cannot cancel the deadline', () => {
    const { t, first } = liveTable();
    t.session.onTransportLost();
    t.transport.subscribe(t.session);
    // Same retired subscription ID, even if it arrives on the new attempt.
    t.transport.push(snapshot('s-old', [tok('a')]));
    // And a frame still carrying the previous attempt's token.
    t.transport.push({ ...snapshot('s-other', [tok('x')]), attempt: first });
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBeNull();
    expect(t.session.stats.refusedSnapshots).toBe(2);
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('offline-locked');
  });

  it('a genuine replacement snapshot for the new subscribe still recovers', () => {
    const { t } = liveTable();
    t.session.onTransportLost();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-old', [tok('a')]), snapshot('s-new', [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['b']);
    expect(t.status()).toBe('live');
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.status()).toBe('live');
  });

  it('only one snapshot answers a subscribe; a second is refused', () => {
    const { t } = liveTable();
    t.transport.push(snapshot('s-dup', [tok('z')]));
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBe('s-old');
    expect(t.ids()).toEqual(['a']);
  });

  it.each(['reconnected', 'scene-changed', 'grant-changed'] as const)(
    'after resync %s, the old subscription snapshot is refused and a new one recovers',
    (reason) => {
      const { t } = liveTable();
      t.session.onResyncRequired(reason, t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
      t.transport.push(snapshot('s-old', [tok('a')]));
      t.transport.deliver(t.session);
      expect(t.session.currentSubscriptionId).toBeNull();
      expect(t.status()).toBe('resyncing');
      t.transport.push(snapshot('s-new', [tok('b')]));
      t.transport.deliver(t.session);
      expect(t.ids()).toEqual(['b']);
      expect(t.status()).toBe('live');
    },
  );

  it('a resync on a transport that is then lost voids the pending replacement', () => {
    const { t, first } = liveTable();
    t.session.onResyncRequired('reconnected', t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
    t.session.onTransportLost();
    t.transport.push({ ...snapshot('s-new', [tok('b')]), attempt: first });
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBeNull();
    t.time.advance(STALE_DISPLAY_DEADLINE_MS);
    expect(t.status()).toBe('offline-locked');
  });

  it('a resync that arrives before the first snapshot keeps the in-flight subscribe valid', () => {
    const t = setup('table');
    t.transport.subscribe(t.session);
    t.session.onResyncRequired('reconnected', t.session.currentSubscriptionId ?? 'sub_in_flight_000000');
    t.transport.push(snapshot('s1', [tok('a')]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['a']);
    expect(t.status()).toBe('live');
  });

  it('a sealed session refuses to start a new attempt', () => {
    const { t } = liveTable();
    t.session.onBootstrapRejected('grant-revoked');
    expect(t.transport.subscribe(t.session)).toBeNull();
  });
});

describe('a resync names the subscription it ends (M0, finding 5)', () => {
  function live() {
    const t = setup('table');
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-old', [tok('a')]));
    t.transport.deliver(t.session);
    t.session.onTransportLost();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-new', [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBe('s-new');
    return t;
  }

  it('a late resync for a retired subscription cannot tear down the fresh one', () => {
    const t = live();
    t.transport.push({ type: 'resync_required', reason: 'state-lost', subscriptionId: 's-old' });
    t.transport.deliver(t.session);
    expect(t.status()).toBe('live');
    expect(t.session.currentSubscriptionId).toBe('s-new');
    expect(t.ids()).toEqual(['b']);
    expect(t.session.stats.ignoredResyncs).toBe(1);
  });

  it('a resync naming an unknown subscription while one is live is ignored', () => {
    const t = live();
    t.session.onResyncRequired('scene-changed', 's-someone-else');
    expect(t.status()).toBe('live');
    expect(t.ids()).toEqual(['b']);
  });

  it('a resync for the live subscription is honoured', () => {
    const t = live();
    t.transport.push({ type: 'resync_required', reason: 'scene-changed', subscriptionId: 's-new' });
    t.transport.deliver(t.session);
    expect(t.status()).toBe('resyncing');
    expect(t.scene()).toBeNull();
  });

  it('a resync for a subscribe still in flight (ID not yet seen) is honoured', () => {
    const t = setup('table');
    t.transport.subscribe(t.session);
    t.session.onResyncRequired('reconnected', 's-not-yet-seen');
    expect(t.status()).toBe('resyncing');
    expect(t.session.stats.ignoredResyncs).toBe(0);
  });
});

describe('frame rules (S5 and README "Corrections")', () => {
  it('a patch that upserts and removes the same key is not applied', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('a', 5)], { removed: [{ kind: 'token', id: 'a' }] }));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['a']);
    expect(t.session.stats.invalidFrames).toBe(1);
    expect(t.transport.resubscribeRequests).toEqual(['state-lost']);
  });

  it('a snapshot whose scene entity is not its sceneId is not applied', () => {
    const t = setup('table');
    t.transport.subscribe(t.session);
    const frame = { ...snapshot('s1', []).frame, sceneId: 'scene_a', entities: [{ kind: 'scene', id: 'scene_b', x: 0 }] };
    t.session.onSnapshot(frame as never, t.transport.attempt);
    expect(t.scene()).toBeNull();
    expect(t.session.stats.invalidFrames).toBe(1);
  });
});

describe('a rejected snapshot retires its subscription ID (handoff 020)', () => {
  function rejected() {
    const t = setup('table');
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-bad', [tok('a'), tok('a', 9)]));
    t.transport.deliver(t.session);
    expect(t.session.stats.invalidFrames).toBe(1);
    expect(t.status()).toBe('resyncing');
    return t;
  }

  it('a later snapshot reusing the rejected ID is refused', () => {
    const t = rejected();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-bad', [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBeNull();
    expect(t.session.stats.refusedSnapshots).toBe(1);
    expect(t.scene()).toBeNull();
    expect(t.status()).toBe('resyncing');
  });

  it("Codex's repro: from a live projection, a reused rejected ID is refused and the good scene stays", () => {
    const t = setup('table');
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-good-1', [tok('a')]));
    t.transport.deliver(t.session);
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('sub_bad0000000000', [tok('x'), tok('x', 4)]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['a']);
    expect(t.status()).toBe('resyncing');
    expect(t.transport.resubscribeRequests).toEqual(['state-lost']);
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('sub_bad0000000000', [tok('y')]));
    t.transport.deliver(t.session);
    expect(t.session.stats.refusedSnapshots).toBe(1);
    expect(t.session.currentSubscriptionId).toBeNull();
    expect(t.ids()).toEqual(['a']);
    expect(t.status()).toBe('resyncing');
  });

  it('a genuinely new ID still recovers', () => {
    const t = rejected();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s-good', [tok('b')]));
    t.transport.deliver(t.session);
    expect(t.session.currentSubscriptionId).toBe('s-good');
    expect(t.ids()).toEqual(['b']);
    expect(t.status()).toBe('live');
  });

  it('late resyncs naming the rejected ID are ignored, while resyncing and once live again', () => {
    const t = rejected();
    t.transport.subscribe(t.session);
    t.session.onResyncRequired('state-lost', 's-bad');           // subscribe in flight
    expect(t.session.stats.ignoredResyncs).toBe(1);
    t.transport.push(snapshot('s-good', [tok('b')]));
    t.transport.deliver(t.session);
    t.session.onResyncRequired('scene-changed', 's-bad');        // live again
    expect(t.session.stats.ignoredResyncs).toBe(2);
    expect(t.status()).toBe('live');
    expect(t.ids()).toEqual(['b']);
  });
});

describe('duplicate entity keys (S5)', () => {
  it('a snapshot naming an entity twice is not applied and triggers a resubscribe', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a'), tok('a', 9)]));
    t.transport.deliver(t.session);
    expect(t.scene()).toBeNull();
    expect(t.session.stats.invalidFrames).toBe(1);
    expect(t.transport.resubscribeRequests).toEqual(['state-lost']);
  });

  it('a patch upserting an entity twice keeps the last good projection and resyncs', () => {
    const t = setup();
    t.transport.subscribe(t.session);
    t.transport.push(snapshot('s1', [tok('a')]), patch('s1', 1, [tok('b'), tok('b', 3)]));
    t.transport.deliver(t.session);
    expect(t.ids()).toEqual(['a']);
    expect(t.status()).toBe('resyncing');
    expect(t.session.currentSubscriptionId).toBeNull();
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
