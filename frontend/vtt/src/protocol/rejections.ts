import type { RejectionCode } from './frames';

// One client behaviour per rejection code: VTT-ADR-0002 section 8, as agreed
// in VTT-20261012-007 item 3b. The exhaustive Record makes a new code a
// compile error until it has a behaviour (acceptance criterion 13).

export type RejectionAction =
  // Keep the command pending and retry it later with the SAME commandId.
  | { kind: 'retry-same-id'; after: 'backoff' | 'reauthenticate' }
  // Revert the optimistic overlay; a user may re-issue with a NEW commandId.
  | { kind: 'revert'; message: 'stale' | 'conflict' | 'reason' | 'generic' | 'not-allowed'; offerRetry: boolean }
  // Revert and log: the client and server disagree about the contract.
  | { kind: 'revert-contract-fault' }
  // Revert, record telemetry, and do not retry.
  | { kind: 'revert-server-fault' }
  // Leave the command path entirely and go through a connection state.
  | { kind: 'enter-state'; state: 'version-mismatch' | 'resyncing' }
  // A whisper-only notice; never retried automatically.
  | { kind: 'whisper-offline' };

export const REJECTION_ACTIONS: Record<RejectionCode, RejectionAction> = {
  'invalid-schema': { kind: 'revert-contract-fault' },
  'unsupported-version': { kind: 'enter-state', state: 'version-mismatch' },
  'unknown-command': { kind: 'revert-contract-fault' },
  'authentication-required': { kind: 'retry-same-id', after: 'reauthenticate' },
  unauthorized: { kind: 'revert', message: 'not-allowed', offerRetry: false },
  'rate-limited': { kind: 'retry-same-id', after: 'backoff' },
  'command-id-reused': { kind: 'revert-contract-fault' },
  stale: { kind: 'revert', message: 'stale', offerRetry: true },
  conflict: { kind: 'revert', message: 'conflict', offerRetry: true },
  'validation-failed': { kind: 'revert', message: 'reason', offerRetry: false },
  'rule-violation': { kind: 'revert', message: 'reason', offerRetry: false },
  'recipient-offline': { kind: 'whisper-offline' },
  'resync-required': { kind: 'enter-state', state: 'resyncing' },
  'temporarily-unavailable': { kind: 'retry-same-id', after: 'backoff' },
  'server-error': { kind: 'revert-server-fault' },
};

// Does this outcome keep the command's identity for a later retry? Only the
// codes the ADR marks "retry the same commandId"; every other outcome is
// final for that id, and any re-issue gets a new one.
export function keepsCommandId(code: RejectionCode): boolean {
  return REJECTION_ACTIONS[code].kind === 'retry-same-id';
}
