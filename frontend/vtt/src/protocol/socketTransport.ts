import { io as connectIo, type ManagerOptions, type Socket, type SocketOptions } from 'socket.io-client';
import type { AckFrame, BootstrapRejectionReason, EntityKey, PatchFrame, ResyncReason, SnapshotFrame } from './frames';
import type { ProjectionSession, SubscribeAttempt } from './session';
import { strictParser } from './strictParser';

// The production transport (client proposal section 4) over Socket.IO, namespace
// /vtt. It owns the socket and nothing else: every inbound message is decoded
// strictly (strictParser), validated against the generated schema for this
// surface, and only then handed to the ProjectionSession, which applies the
// VTT-ADR-0002 delivery rules. A message that fails validation is never
// applied; the session resubscribes for lost state.
//
// Surfaces stay separate: the entry point injects its own surface's validators,
// so the table bundle never imports a GM validator (bundleBoundary.test.ts).

export interface SurfaceValidators {
  frame(data: unknown): boolean; // snapshot | patch for this surface
  resync(data: unknown): boolean;
  bootstrap(data: unknown): boolean;
  ack?(data: unknown): boolean; // GM only: a table sends no commands
}

export type IoFactory = (url: string, options: Partial<ManagerOptions & SocketOptions>) => Socket;

export interface SocketTransportOptions<E extends EntityKey, O> {
  session: ProjectionSession<E, O>;
  validators: SurfaceValidators;
  // The vtt.subscribe body for this surface (a table sends {schemaVersion, audience}).
  subscribeMessage: () => Record<string, unknown>;
  // GM only: the app's CSRF token for the handshake.
  csrf?: string;
  url?: string; // origin; defaults to the page's own
  io?: IoFactory; // injectable for tests
  onAck?: (ack: AckFrame) => void;
  // Reconnect delay after the server itself closed the socket (Socket.IO does
  // not retry that on its own).
  serverCloseRetryMs?: number;
  setTimeout?: (fn: () => void, ms: number) => unknown;
}

export const NAMESPACE = '/vtt';

export class SocketIoTransport<E extends EntityKey, O = unknown> {
  attempt: SubscribeAttempt | null = null;
  retrying = true;
  readonly stats = { invalid: 0, sent: 0 };
  private socket: Socket | null = null;

  constructor(private readonly options: SocketTransportOptions<E, O>) {}

  get connected(): boolean {
    return this.socket?.connected === true;
  }

  connect(): void {
    if (this.socket) return;
    const factory = this.options.io ?? (connectIo as unknown as IoFactory);
    const base = this.options.url ?? '';
    const socket = factory(`${base}${NAMESPACE}`, {
      path: '/socket.io',
      parser: strictParser,
      withCredentials: true,
      autoConnect: false,
      reconnection: true,
      reconnectionDelay: 500,
      reconnectionDelayMax: 10_000,
      randomizationFactor: 0.5,
      auth: this.options.csrf !== undefined ? { csrf: this.options.csrf } : {},
    });
    this.socket = socket;
    const { session } = this.options;

    socket.on('connect', () => this.subscribe());
    socket.on('connect_error', (error: Error & { data?: unknown }) => {
      // A refused handshake carries the v1 BootstrapRejection as its data;
      // anything else is a network failure that Socket.IO retries itself.
      if (this.options.validators.bootstrap(error.data)) {
        session.onBootstrapRejected((error.data as { reason: BootstrapRejectionReason }).reason);
      }
    });
    socket.on('disconnect', (reason: string) => {
      session.onTransportLost();
      // Socket.IO does not reconnect after the server closed the socket. A
      // table ended by the heartbeat still has to learn why: reconnecting
      // answers with grant-revoked or grant-expired.
      if (reason === 'io server disconnect' && this.retrying) {
        const schedule = this.options.setTimeout ?? ((fn, ms) => setTimeout(fn, ms));
        schedule(() => {
          if (this.retrying) socket.connect();
        }, this.options.serverCloseRetryMs ?? 2000);
      }
    });
    socket.on('vtt.snapshot', (data: unknown) => {
      if (!this.valid((d) => this.options.validators.frame(d) && (d as { frame?: unknown }).frame === 'snapshot', data)) return;
      session.onSnapshot(data as SnapshotFrame<E>, this.attempt);
    });
    socket.on('vtt.patch', (data: unknown) => {
      if (!this.valid((d) => this.options.validators.frame(d) && (d as { frame?: unknown }).frame === 'patch', data)) return;
      session.onPatch(data as PatchFrame<E>);
    });
    socket.on('vtt.resync_required', (data: unknown) => {
      if (!this.valid(this.options.validators.resync, data)) return;
      const m = data as { reason: ResyncReason; subscriptionId: string };
      session.onResyncRequired(m.reason, m.subscriptionId);
    });
    socket.on('vtt.ack', (data: unknown) => {
      const validate = this.options.validators.ack;
      if (!validate || !this.valid(validate, data)) return;
      this.options.onAck?.(data as AckFrame);
    });
    socket.connect();
  }

  // Send an authenticated subscribe on the live socket. Only the snapshot that
  // answers it may start the subscription (the session holds the attempt).
  subscribe(): void {
    const socket = this.socket;
    if (!socket?.connected) return;
    const attempt = this.options.session.subscribing();
    if (attempt === null) return;
    this.attempt = attempt;
    socket.emit('vtt.subscribe', this.options.subscribeMessage(), (reply: unknown) => {
      // Success answers with nothing; a refusal answers with a BootstrapRejection.
      if (reply !== undefined && reply !== null && this.options.validators.bootstrap(reply)) {
        this.options.session.onBootstrapRejected((reply as { reason: BootstrapRejectionReason }).reason);
      }
    });
  }

  // SessionSink half: the client detected a gap or lost state.
  requestResubscribe(_reason: 'sequence-gap' | 'state-lost'): void {
    this.subscribe();
  }

  // SessionSink half: a terminal state. Never retry this credential.
  stopRetrying(): void {
    this.retrying = false;
    this.socket?.disconnect();
  }

  // Returns false when there is no live socket; the command queue then waits.
  sendCommand(command: Record<string, unknown>): boolean {
    if (!this.socket?.connected) return false;
    this.socket.emit('vtt.command', command);
    this.stats.sent += 1;
    return true;
  }

  private valid(validate: (data: unknown) => boolean, data: unknown): boolean {
    if (validate(data)) return true;
    this.stats.invalid += 1;
    this.options.session.onInvalidMessage();
    return false;
  }
}
