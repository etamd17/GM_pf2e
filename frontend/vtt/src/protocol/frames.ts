// Client views of the wire frames. The canonical shapes are generated from
// contracts/vtt/v1/ into ./generated (scripts/generate-protocol.mjs); the
// session works against these narrower structural views so it stays generic
// over the GM and table entity unions. Every view, enumeration and constant
// below is checked against the generated types at compile time (bottom of this
// file), so a contract change that the client does not follow fails typecheck.

import type { Acknowledgement, RejectedAck } from './generated/types/acknowledgement';
import type { BootstrapRejection } from './generated/types/bootstrap-rejection';
import type { GmEntity, GmEntityRef, GmPatch, GmSnapshot } from './generated/types/gm';
import type { ResyncRequired } from './generated/types/resync-required';
import type { TableEntity, TableEntityRef, TablePatch, TableSnapshot } from './generated/types/table';

export type { GmEntity, GmPatch, GmSnapshot, TableEntity, TablePatch, TableSnapshot };

export interface EntityKey {
  kind: string;
  id: string;
}

export interface SnapshotFrame<E extends EntityKey> {
  frame: 'snapshot';
  subscriptionId: string;
  seq: 0;
  entities: readonly E[];
}

export interface PatchFrame<E extends EntityKey> {
  frame: 'patch';
  subscriptionId: string;
  seq: number;
  upserted: readonly E[];
  removed: readonly EntityKey[];
  originCommandId?: string;
}

// VTT-ADR-0002 section 3.
export const RESYNC_REASONS = [
  'scene-changed',
  'reconnected',
  'sequence-gap',
  'buffer-overflow',
  'state-lost',
  'grant-changed',
  'revoked',
  'version-changed',
] as const;
export type ResyncReason = (typeof RESYNC_REASONS)[number];

// VTT-ADR-0002 section 1.
export const BOOTSTRAP_REJECTION_REASONS = [
  'grant-revoked',
  'grant-expired',
  'session-ended',
  'unsupported-version',
] as const;
export type BootstrapRejectionReason = (typeof BOOTSTRAP_REJECTION_REASONS)[number];

// VTT-ADR-0002 section 8.
export const REJECTION_CODES = [
  'invalid-schema',
  'unsupported-version',
  'unknown-command',
  'authentication-required',
  'unauthorized',
  'rate-limited',
  'command-id-reused',
  'stale',
  'conflict',
  'validation-failed',
  'rule-violation',
  'recipient-offline',
  'resync-required',
  'temporarily-unavailable',
  'server-error',
] as const;
export type RejectionCode = (typeof REJECTION_CODES)[number];

// The session ignores schemaVersion (the validator already enforced it).
export type AckFrame =
  | { commandId: string; outcome: 'applied' }
  | { commandId: string; outcome: 'rejected'; code: RejectionCode };

export const entityKey = (e: EntityKey): string => `${e.kind}\u0000${e.id}`;

// Entity-key uniqueness and the other frame rules live in ./semantics.ts.

// ---- compile-time agreement with the generated contract types ----
type Exactly<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const agree = <T extends true>(_proof?: T): void => {};
agree<Exactly<ResyncReason, ResyncRequired['reason']>>();
agree<Exactly<BootstrapRejectionReason, BootstrapRejection['reason']>>();
agree<Exactly<RejectionCode, RejectedAck['code']>>();
// Every generated frame and ack is accepted by the session's views.
const views = <T extends true>(_proof?: T): void => {};
views<TableSnapshot extends SnapshotFrame<TableEntity> ? true : false>();
views<TablePatch extends PatchFrame<TableEntity> ? true : false>();
views<GmSnapshot extends SnapshotFrame<GmEntity> ? true : false>();
views<GmPatch extends PatchFrame<GmEntity> ? true : false>();
views<TableEntityRef extends EntityKey ? true : false>();
views<GmEntityRef extends EntityKey ? true : false>();
views<Acknowledgement extends AckFrame ? true : false>();
