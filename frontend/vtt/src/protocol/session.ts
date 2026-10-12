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
}

export class ProjectionSession<E extends EntityKey, O = unknown> {
  private subscriptionId: string | null = null;
  private lastSeq = 0;
  private awaitingSnapshot = false;
  private sealed = false;
  private epoch: ProjectionEpoch | null = null;
  private entities = new Map<string, E>();
  private overlays = new Map<string, O>();
  private deadline: unknown = null;
  readonly stats: SessionStats = { applied: 0, duplicates: 0, discardedOtherSubscription: 0, gaps: 0 };

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

  // An authenticated subscribe is in flight; the next snapshot starts the new
  // subscription.
  subscribing(): void {
    if (this.sealed) return;
    this.awaitingSnapshot = true;
  }

  // Section 4: a snapshot atomically replaces all state for its subscription
  // and is never merged with what came before. It starts seq at 0, supersedes
  // every overlay from the old subscription (section 7) and, with a successful
  // subscribe, is the only thing that cancels the stale-display deadline
  // (section 10).
  onSnapshot(frame: SnapshotFrame<E>): void {
    if (this.sealed || !this.awaitingSnapshot) return;
    const epoch = this.sink.openProjection();
    if (epoch === null) return;
    this.epoch = epoch;
    this.awaitingSnapshot = false;
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
    if (frame.seq !== this.lastSeq + 1) {
      this.stats.gaps += 1;
      this.awaitingSnapshot = true;
      this.subscriptionId = null;
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
      this.subscriptionId = null;
      this.awaitingSnapshot = true;
      this.sink.setStatus('resyncing');
      this.sink.requestResubscribe('state-lost');
    }
    return action;
  }

  // Section 3. Terminal reasons seal and stop; scene-changed and
  // grant-changed clear the old projection first; the rest keep the current
  // scene on screen while the server sends a replacement snapshot.
  onResyncRequired(reason: ResyncReason): void {
    if (this.sealed) return;
    if (reason === 'revoked' || reason === 'version-changed') {
      this.terminate(reason === 'revoked' ? 'revoked' : 'version-mismatch');
      return;
    }
    this.subscriptionId = null;
    this.awaitingSnapshot = true;
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
  onTransportLost(): void {
    if (this.sealed) return;
    this.subscriptionId = null;
    this.awaitingSnapshot = true;
    this.sink.setStatus('reconnecting');
    if (this.surface === 'table' && this.deadline === null) {
      this.deadline = this.clock.setTimeout(() => this.expireDeadline(), STALE_DISPLAY_DEADLINE_MS);
    }
  }

  private expireDeadline(): void {
    this.deadline = null;
    if (this.sealed || !this.awaitingSnapshot) return;
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
    this.subscriptionId = null;
    this.awaitingSnapshot = false;
    this.entities = new Map();
    this.epoch = null;
    this.dropOverlays(() => true);
    this.sink.sealProjection();
    this.sink.stopRetrying();
    this.sink.setStatus(status);
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
