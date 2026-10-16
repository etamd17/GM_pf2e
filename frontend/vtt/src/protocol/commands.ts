import type { AckFrame, EntityKey, RejectionCode } from './frames';
import type { RejectionAction } from './rejections';
import type { ProjectionSession } from './session';

// The client command lifecycle (client proposal section 4; VTT-ADR-0002
// sections 7 and 8): queued -> sent -> applied | rejected | timed out.
//
// - A command is checked against the generated schema and the command rules
//   before it leaves; one that fails is a client bug and is never sent.
// - The optimistic overlay lives in the session. An applied ack alone never
//   clears it; the correlated frame or a replacement snapshot does.
// - A timeout, rate-limited or temporarily-unavailable retries the SAME
//   commandId with backoff, so the server's receipt resolves the outcome.
//   authentication-required keeps the command until the user has signed in
//   again, then retries the same id.
// - Every other rejection is final for that id; a corrected command is issued
//   anew with a new id and fresh expected versions.

export interface CommandDraft {
  type: string;
  campaignId: string;
  sceneId: string;
  expected: ReadonlyArray<{ kind: string; id: string; version: string }>;
  payload: Record<string, unknown>;
}

export type CommandOutcome =
  | { kind: 'applied'; commandId: string; type: string }
  | { kind: 'rejected'; commandId: string; type: string; code: RejectionCode; action: RejectionAction }
  | { kind: 'reauthenticate'; commandId: string }
  | { kind: 'refused-locally'; type: string }
  // A plain message from the page itself (for example, nothing left to undo).
  | { kind: 'info'; text: string };

export interface CommandClock {
  setTimeout(fn: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

export interface CommandQueueOptions<E extends EntityKey, O> {
  session: ProjectionSession<E, O>;
  send(command: Record<string, unknown>): boolean; // false: no live socket
  validate(command: Record<string, unknown>): boolean;
  onOutcome?(outcome: CommandOutcome): void;
  onPendingChange?(pending: ReadonlyArray<PendingView>): void;
  clock?: CommandClock;
  newId?(): string;
  ackTimeoutMs?: number;
  backoffMs?: readonly number[];
}

export interface PendingView {
  commandId: string;
  type: string;
  state: 'sent' | 'waiting' | 'reauthenticate';
}

interface Pending {
  command: Record<string, unknown>;
  type: string;
  state: PendingView['state'];
  attempts: number;
  timer: unknown;
}

export const ACK_TIMEOUT_MS = 10_000;
export const BACKOFF_MS = [500, 1_000, 2_000, 5_000, 10_000] as const;

// 128 random bits, base64url: an OpaqueToken (16-128 chars of [A-Za-z0-9_-]).
export function newCommandId(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  let text = '';
  for (const b of bytes) text += String.fromCharCode(b);
  return 'cmd_' + btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export class CommandQueue<E extends EntityKey, O> {
  private readonly pending = new Map<string, Pending>();
  private readonly clock: CommandClock;

  constructor(private readonly options: CommandQueueOptions<E, O>) {
    this.clock = options.clock ?? {
      setTimeout: (fn, ms) => setTimeout(fn, ms),
      clearTimeout: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
    };
  }

  get size(): number {
    return this.pending.size;
  }

  // Issue a command. Returns its id, or null when it was refused locally.
  issue(draft: CommandDraft, overlay: O): string | null {
    const commandId = (this.options.newId ?? newCommandId)();
    const command: Record<string, unknown> = {
      schemaVersion: 1,
      commandId,
      campaignId: draft.campaignId,
      sceneId: draft.sceneId,
      type: draft.type,
      expected: draft.expected.map((e) => ({ kind: e.kind, id: e.id, version: e.version })),
      payload: draft.payload,
    };
    if (!this.options.validate(command)) {
      this.options.onOutcome?.({ kind: 'refused-locally', type: draft.type });
      return null;
    }
    this.pending.set(commandId, { command, type: draft.type, state: 'sent', attempts: 0, timer: null });
    this.options.session.beginCommand(commandId, overlay);
    this.transmit(commandId);
    return commandId;
  }

  onAck(ack: AckFrame): void {
    const entry = this.pending.get(ack.commandId);
    const action = this.options.session.onAck(ack);
    if (!entry) return; // a duplicate, or an id this client no longer tracks
    this.clock.clearTimeout(entry.timer);
    entry.timer = null;
    if (ack.outcome === 'applied') {
      this.pending.delete(ack.commandId);
      this.options.onOutcome?.({ kind: 'applied', commandId: ack.commandId, type: entry.type });
      this.changed();
      return;
    }
    if (action?.kind === 'retry-same-id') {
      if (action.after === 'reauthenticate') {
        entry.state = 'reauthenticate';
        this.options.onOutcome?.({ kind: 'reauthenticate', commandId: ack.commandId });
      } else {
        this.retryLater(ack.commandId, entry);
      }
      this.changed();
      return;
    }
    this.pending.delete(ack.commandId);
    if (action) {
      this.options.onOutcome?.({ kind: 'rejected', commandId: ack.commandId, type: entry.type, code: ack.code, action });
    }
    this.changed();
  }

  // The connection is live again (a new subscription): resend what is waiting,
  // with the same ids. Commands parked for reauthentication stay parked.
  resume(): void {
    for (const [id, entry] of this.pending) {
      if (entry.state === 'waiting') this.transmit(id);
    }
  }

  // The user signed in again: retry the parked commands with the same ids.
  reauthenticated(): void {
    for (const [id, entry] of this.pending) {
      if (entry.state === 'reauthenticate') this.transmit(id);
    }
  }

  // Every command not yet resolved, as sent on the wire. The page saves these
  // when the session ends, so after signing in again they are retried with
  // the SAME ids (ADR-0002 section 8) and the receipts settle each outcome.
  unresolved(): Array<Record<string, unknown>> {
    return [...this.pending.values()].map((p) => p.command);
  }

  // Take over a saved command after a reload. It is sent when the connection is
  // live (resume), under its original id; a command that no longer passes the
  // local checks is dropped.
  adopt(command: Record<string, unknown>): boolean {
    const id = command.commandId;
    if (typeof id !== 'string' || this.pending.has(id) || !this.options.validate(command)) return false;
    this.pending.set(id, { command, type: String(command.type), state: 'waiting', attempts: 0, timer: null });
    this.changed();
    return true;
  }

  views(): PendingView[] {
    return [...this.pending].map(([commandId, p]) => ({ commandId, type: p.type, state: p.state }));
  }

  private transmit(commandId: string): void {
    const entry = this.pending.get(commandId);
    if (!entry) return;
    entry.attempts += 1;
    if (!this.options.send(entry.command)) {
      entry.state = 'waiting'; // no socket: resume() sends it
      this.changed();
      return;
    }
    entry.state = 'sent';
    this.clock.clearTimeout(entry.timer);
    entry.timer = this.clock.setTimeout(() => {
      entry.timer = null;
      if (this.pending.get(commandId) === entry) this.retryLater(commandId, entry);
    }, this.options.ackTimeoutMs ?? ACK_TIMEOUT_MS);
    this.changed();
  }

  private retryLater(commandId: string, entry: Pending): void {
    const steps = this.options.backoffMs ?? BACKOFF_MS;
    const delay = steps[Math.min(entry.attempts - 1, steps.length - 1)] ?? 10_000;
    entry.state = 'waiting';
    entry.timer = this.clock.setTimeout(() => {
      entry.timer = null;
      if (this.pending.get(commandId) === entry && entry.state === 'waiting') this.transmit(commandId);
    }, delay);
  }

  private changed(): void {
    this.options.onPendingChange?.(this.views());
  }
}
