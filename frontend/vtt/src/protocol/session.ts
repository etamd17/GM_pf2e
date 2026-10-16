import type { ConnectionStatus } from '../state/connection';
import type { ProjectionEpoch } from '../state/projection';
import {
  entityKey,
  type AckFrame,
  type BootstrapRejectionReason,
  type EntityKey,
  type PatchFrame,
  type ResyncReason,
  type SnapshotFrame,
} from './frames';
import { REJECTION_ACTIONS, type RejectionAction } from './rejections';
import { frameRulesHold } from './semantics';

// Client side of the VTT-ADR-0002 delivery rules, independent of the socket
// and of React. A transport feeds it server messages; it drives the projection
// and connection stores through a sink. Every rule below cites the ADR section
// it implements.

export type Surface = 'gm' | 'table';

export interface SessionSink<E extends EntityKey> {
  setStatus(status: ConnectionStatus): void;
  openProjection(): ProjectionEpoch | null;
  writeProjection(epoch: ProjectionEpoch, entities: ReadonlyMap<string, E>): void;
  clearProjection(): void;
  sealProjection(): void;
  // Ask the transport for a fresh subscription. Used when the client itself
  // detects a gap (section 6); server-initiated resyncs need no request.
  requestResubscribe(reason: 'sequence-gap' | 'state-lost'): void;
  // Stop automatic retries with the current credential (section 1).
  stopRetrying(): void;
}

export interface SessionClock {
  // Monotonic milliseconds. performance.now() in the browser.
  now(): number;
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export const STALE_DISPLAY_DEADLINE_MS = 60_000;

export interface SessionStats {
  applied: number;
  duplicates: number;
  discardedOtherSubscription: number;
  gaps: number;
  refusedSnapshots: number;
  invalidFrames: number;
  ignoredResyncs: number;
}

// Retired subscription IDs are remembered so a delayed duplicate can never
// revive one (sections 6 and 10). IDs are server-issued and unique, so a
// bounded memory of the most recent ones is enough.
const RETIRED_ID_MEMORY = 256;

// A subscribe attempt, issued by subscribing(). The transport hands the token
// back with the snapshot that answers that subscribe. It is local transport
// metadata, never a wire field.
export type SubscribeAttempt = number;

export class ProjectionSession<E extends EntityKey, O = unknown> {
  private subscriptionId: string | null = null;
  private lastSeq = 0;
  private sealed = false;
  // The attempt whose snapshot may be accepted now, or null when no
  // authenticated subscribe is outstanding. Losing the transport clears it;
  // only subscribing() (a new authenticated subscribe) or a server resync on a
  // still-authenticated transport sets it.
  private pendingAttempt: SubscribeAttempt | null = null;
  // The attempt that produced the current live subscription.
  private liveAttempt: SubscribeAttempt | null = null;
  private attemptCounter = 0;
  private retired = new Set<string>();
  private epoch: ProjectionEpoch | null = null;
  private entities = new Map<string, E>();
  private overlays = new Map<string, O>();
  private deadline: unknown = null;
  readonly stats: SessionStats = {
    applied: 0,
    duplicates: 0,
    discardedOtherSubscription: 0,
    gaps: 0,
    refusedSnapshots: 0,
    invalidFrames: 0,
    ignoredResyncs: 0,
  };

  constructor(
    private readonly surface: Surface,
    private readonly sink: SessionSink<E>,
    private readonly clock: SessionClock,
    private readonly onOverlaysChanged: (overlays: ReadonlyMap<string, O>) => void = () => {},
  ) {}

  get isSealed(): boolean {
    return this.sealed;
  }

  get currentSubscriptionId(): string | null {
    return this.subscriptionId;
  }

  // An authenticated subscribe has just been sent. Only the snapshot answering
  // this attempt may start the new subscription; any earlier attempt is void.
  // Returns null once sealed: a terminal session never subscribes again.
  subscribing(): SubscribeAttempt | null {
    if (this.sealed) return null;
    this.retireCurrent();
    this.attemptCounter += 1;
    this.pendingAttempt = this.attemptCounter;
    return this.pendingAttempt;
  }

  // Section 4: a snapshot atomically replaces all state for its subscription
  // and is never merged with what came before. It starts seq at 0, supersedes
  // every overlay from the old subscription (section 7) and, with a successful
  // subscribe, is the only thing that cancels the stale-display deadline
  // (section 10).
  //
  // A snapshot is accepted only for the outstanding attempt and only for a
  // subscription ID that has never been retired. A delayed copy of an old
  // snapshot therefore cannot restore an old subscription or cancel the
  // deadline, whether it arrives before or after the next subscribe.
  onSnapshot(frame: SnapshotFrame<E>, attempt: SubscribeAttempt | null): void {
    if (this.sealed) return;
    if (
      this.pendingAttempt === null ||
      attempt !== this.pendingAttempt ||
      this.retired.has(frame.subscriptionId)
    ) {
      this.stats.refusedSnapshots += 1;
      return;
    }
    // Frame rules (README "Corrections"): repeated keys, scene/sceneId mismatch,
    // GM controller/capability repeats, zero-length walls.
    if (!frameRulesHold(this.surface, frame)) {
      this.invalidFrame(frame.subscriptionId);
      return;
    }
    const epoch = this.sink.openProjection();
    if (epoch === null) return;
    this.epoch = epoch;
    this.liveAttempt = this.pendingAttempt;
    this.pendingAttempt = null;
    this.subscriptionId = frame.subscriptionId;
    this.lastSeq = 0;
    this.entities = new Map(frame.entities.map((e) => [entityKey(e), e]));
    this.dropOverlays(() => true);
    this.cancelDeadline();
    this.sink.writeProjection(epoch, this.entities);
    this.sink.setStatus('live');
  }

  // Section 6, exactly: another subscription's frame is discarded, seq <= last
  // is a duplicate, seq == last + 1 applies atomically, and a larger seq is a
  // gap that enters resyncing without applying anything.
  onPatch(frame: PatchFrame<E>): void {
    if (this.sealed || this.subscriptionId === null || this.epoch === null) return;
    if (frame.subscriptionId !== this.subscriptionId) {
      this.stats.discardedOtherSubscription += 1;
      return;
    }
    if (frame.seq <= this.lastSeq) {
      this.stats.duplicates += 1;
      return;
    }
    if (!frameRulesHold(this.surface, frame)) {
      this.invalidFrame();
      return;
    }
    if (frame.seq !== this.lastSeq + 1) {
      this.stats.gaps += 1;
      // The transport sends a new subscribe and reports it via subscribing().
      this.retireCurrent();
      this.sink.setStatus('resyncing');
      this.sink.requestResubscribe('sequence-gap');
      return;
    }
    const next = new Map(this.entities);
    for (const ref of frame.removed) next.delete(entityKey(ref));
    for (const e of frame.upserted) next.set(entityKey(e), e);
    this.entities = next;
    this.lastSeq = frame.seq;
    this.stats.applied += 1;
    this.sink.writeProjection(this.epoch, this.entities);
    // Section 7: the correlated frame, empty or not, ends the overlay.
    if (frame.originCommandId !== undefined) this.dropOverlays((id) => id === frame.originCommandId);
  }

  // An optimistic overlay for a command just sent on the current subscription.
  beginCommand(commandId: string, data: O): void {
    if (this.sealed || this.subscriptionId === null) return;
    this.overlays.set(commandId, data);
    this.onOverlaysChanged(this.overlayView());
  }

  // Section 7: an applied ack confirms commit but never clears the overlay;
  // only the correlated frame or a replacement snapshot does, in either
  // arrival order. A rejection reverts it. Duplicates are idempotent.
  onAck(ack: AckFrame): RejectionAction | null {
    if (ack.outcome === 'applied' || this.sealed) return null;
    const action = REJECTION_ACTIONS[ack.code];
    if (action.kind !== 'retry-same-id') this.dropOverlays((id) => id === ack.commandId);
    // Two codes are connection states rather than command outcomes.
    if (action.kind === 'enter-state' && action.state === 'version-mismatch') {
      this.terminate('version-mismatch');
    } else if (action.kind === 'enter-state' && action.state === 'resyncing') {
      this.retireCurrent();
      this.sink.setStatus('resyncing');
      this.sink.requestResubscribe('state-lost');
    }
    return action;
  }

  // Section 3. Terminal reasons seal and stop; scene-changed and
  // grant-changed clear the old projection first; the rest keep the current
  // scene on screen while the server sends a replacement snapshot.
  //
  // The resync names the subscription it ends (M0, handoff 017 finding 5).
  // Subscription IDs are server-issued, so a subscribe still in flight has an
  // ID this client has not seen yet. A resync is ignored when it names a
  // retired subscription, or another subscription while one is live and no
  // subscribe is in flight: a late resync from a fan-out race must not tear
  // down a fresh subscription.
  onResyncRequired(reason: ResyncReason, subscriptionId: string): void {
    if (this.sealed) return;
    if (
      this.retired.has(subscriptionId) ||
      (this.subscriptionId !== null && subscriptionId !== this.subscriptionId && this.pendingAttempt === null)
    ) {
      this.stats.ignoredResyncs += 1;
      return;
    }
    if (reason === 'revoked' || reason === 'version-changed') {
      this.terminate(reason === 'revoked' ? 'revoked' : 'version-mismatch');
      return;
    }
    // The server sends the replacement on the same authenticated transport, so
    // the live attempt (or the subscribe still in flight on it) may answer it,
    // but only with a new subscription ID.
    const attempt = this.liveAttempt ?? this.pendingAttempt;
    this.retireCurrent();
    this.pendingAttempt = attempt;
    if (reason === 'scene-changed' || reason === 'grant-changed') {
      this.entities = new Map();
      this.epoch = null;
      this.dropOverlays(() => true);
      this.sink.clearProjection();
    }
    this.sink.setStatus('resyncing');
  }

  // Section 1: every bootstrap rejection clears synchronously and stops
  // retrying with that credential.
  onBootstrapRejected(reason: BootstrapRejectionReason): void {
    if (this.sealed) return;
    const status: ConnectionStatus =
      reason === 'unsupported-version'
        ? 'version-mismatch'
        : reason === 'session-ended'
          ? 'session-ended'
          : 'revoked';
    this.terminate(status);
  }

  // Section 10: a table that loses its transport starts a monotonic deadline.
  // If no authenticated subscribe and replacement snapshot arrive in time, it
  // clears its projection and locks, even while transport retries continue.
  // Losing the transport voids every outstanding attempt: nothing is accepted
  // until a new authenticated subscribe begins.
  onTransportLost(): void {
    if (this.sealed) return;
    this.retireCurrent();
    this.sink.setStatus('reconnecting');
    if (this.surface === 'table' && this.deadline === null) {
      this.deadline = this.clock.setTimeout(() => this.expireDeadline(), STALE_DISPLAY_DEADLINE_MS);
    }
  }

  private expireDeadline(): void {
    this.deadline = null;
    if (this.sealed || this.subscriptionId !== null) return;
    this.entities = new Map();
    this.epoch = null;
    this.dropOverlays(() => true);
    this.sink.clearProjection();
    this.sink.setStatus('offline-locked');
  }

  private cancelDeadline(): void {
    if (this.deadline !== null) this.clock.clearTimeout(this.deadline);
    this.deadline = null;
  }

  private terminate(status: ConnectionStatus): void {
    this.sealed = true;
    this.cancelDeadline();
    this.retireCurrent();
    this.entities = new Map();
    this.epoch = null;
    this.dropOverlays(() => true);
    this.sink.sealProjection();
    this.sink.stopRetrying();
    this.sink.setStatus(status);
  }

  // A frame for the current subscription that names an entity twice cannot be
  // applied unambiguously. Nothing from it is applied; the client resubscribes
  // as for lost state and keeps the last good projection on screen.
  // `offendingId` is the subscription ID a rejected snapshot carried. It never
  // became live, so retireCurrent() would not remember it; without this a later
  // snapshot could reuse the rejected ID and go live (Codex, handoff 020).
  private invalidFrame(offendingId?: string): void {
    this.stats.invalidFrames += 1;
    if (offendingId !== undefined) this.retireId(offendingId);
    this.retireCurrent();
    this.sink.setStatus('resyncing');
    this.sink.requestResubscribe('state-lost');
  }

  // Retire the live subscription ID (if any) and void every attempt.
  private retireCurrent(): void {
    if (this.subscriptionId !== null) this.retireId(this.subscriptionId);
    this.subscriptionId = null;
    this.pendingAttempt = null;
    this.liveAttempt = null;
  }

  private retireId(id: string): void {
    this.retired.add(id);
    if (this.retired.size > RETIRED_ID_MEMORY) {
      const oldest = this.retired.values().next().value;
      if (oldest !== undefined) this.retired.delete(oldest);
    }
  }

  private dropOverlays(match: (commandId: string) => boolean): void {
    let changed = false;
    for (const id of [...this.overlays.keys()]) {
      if (match(id)) {
        this.overlays.delete(id);
        changed = true;
      }
    }
    if (changed) this.onOverlaysChanged(this.overlayView());
  }

  private overlayView(): ReadonlyMap<string, O> {
    return new Map(this.overlays);
  }
}
