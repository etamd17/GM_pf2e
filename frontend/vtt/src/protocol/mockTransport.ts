import type {
  AckFrame,
  BootstrapRejectionReason,
  EntityKey,
  PatchFrame,
  ResyncReason,
  SnapshotFrame,
} from './frames';
import type { ProjectionSession } from './session';

// A scripted stand-in for the Socket.IO transport, used by component tests and
// by UX work before the backend exists. A test queues the server's messages,
// then delivers them in order, out of order, twice, or not at all, so every
// VTT-ADR-0002 delivery rule can be exercised without a network.

export type ServerEvent<E extends EntityKey> =
  | { type: 'snapshot'; frame: SnapshotFrame<E> }
  | { type: 'patch'; frame: PatchFrame<E> }
  | { type: 'ack'; ack: AckFrame }
  | { type: 'resync_required'; reason: ResyncReason }
  | { type: 'bootstrap_rejected'; reason: BootstrapRejectionReason }
  | { type: 'transport_lost' }
  | { type: 'subscribing' };

export function dispatch<E extends EntityKey, O>(session: ProjectionSession<E, O>, event: ServerEvent<E>): void {
  switch (event.type) {
    case 'snapshot':
      session.onSnapshot(event.frame);
      return;
    case 'patch':
      session.onPatch(event.frame);
      return;
    case 'ack':
      session.onAck(event.ack);
      return;
    case 'resync_required':
      session.onResyncRequired(event.reason);
      return;
    case 'bootstrap_rejected':
      session.onBootstrapRejected(event.reason);
      return;
    case 'transport_lost':
      session.onTransportLost();
      return;
    case 'subscribing':
      session.subscribing();
      return;
  }
}

export class MockTransport<E extends EntityKey> {
  readonly resubscribeRequests: Array<'sequence-gap' | 'state-lost'> = [];
  retrying = true;
  private queue: Array<ServerEvent<E>> = [];

  // Transport-side half of the sink contract.
  requestResubscribe(reason: 'sequence-gap' | 'state-lost'): void {
    this.resubscribeRequests.push(reason);
  }

  stopRetrying(): void {
    this.retrying = false;
  }

  push(...events: Array<ServerEvent<E>>): this {
    this.queue.push(...events);
    return this;
  }

  // Deliver queued events. `order` reorders by queue index; `duplicate`
  // delivers each selected event twice; `drop` skips queue indexes.
  deliver<O>(
    session: ProjectionSession<E, O>,
    options: { order?: number[]; duplicate?: boolean; drop?: number[] } = {},
  ): void {
    const events = this.queue;
    this.queue = [];
    const indexes = options.order ?? events.map((_e, i) => i);
    for (const i of indexes) {
      if (options.drop?.includes(i)) continue;
      const event = events[i];
      if (!event) continue;
      dispatch(session, event);
      if (options.duplicate) dispatch(session, event);
    }
  }
}
