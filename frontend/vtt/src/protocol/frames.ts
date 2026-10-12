// PROVISIONAL message shapes for the client state machine, limited to the
// fields VTT-ADR-0002 names explicitly. They are replaced by the generated
// types from contracts/vtt/v1/ once the first canonical batch lands
// (VTT-20261012-014/015). Do not add fields here: a new field is a contract
// change and goes through the schema proposal.

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

export type AckFrame =
  | { commandId: string; outcome: 'applied' }
  | { commandId: string; outcome: 'rejected'; code: RejectionCode };

export const entityKey = (e: EntityKey): string => `${e.kind}\u0000${e.id}`;
