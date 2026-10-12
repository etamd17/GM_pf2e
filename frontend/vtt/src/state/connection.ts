import { create } from 'zustand';

// Client-side connection and renderer state. These are local UI states, not
// protocol values: the transport (once VTT-ADR-0002 is accepted) drives them.
//
// connecting -> live -> degraded -> reconnecting -> resyncing -> live
// plus two terminal states: revoked and version-mismatch.

export type ConnectionStatus =
  | 'connecting'
  | 'live'
  | 'degraded'
  | 'reconnecting'
  | 'resyncing'
  | 'revoked'
  | 'version-mismatch';

export type RendererStatus = 'ok' | 'lost' | 'restoring' | 'failed';

export const CONNECTION_STATUSES: readonly ConnectionStatus[] = [
  'connecting',
  'live',
  'degraded',
  'reconnecting',
  'resyncing',
  'revoked',
  'version-mismatch',
];

export function isConnectionStatus(value: unknown): value is ConnectionStatus {
  return typeof value === 'string' && (CONNECTION_STATUSES as readonly string[]).includes(value);
}

// Durable map input is accepted only while live. Every other state disables it
// and shows a banner, so a GM never drags a token that silently goes nowhere.
export function acceptsDurableInput(status: ConnectionStatus): boolean {
  return status === 'live';
}

export function isTerminal(status: ConnectionStatus): boolean {
  return status === 'revoked' || status === 'version-mismatch';
}

interface ConnectionState {
  status: ConnectionStatus;
  renderer: RendererStatus;
  setStatus(status: ConnectionStatus): void;
  setRenderer(renderer: RendererStatus): void;
}

export const useConnection = create<ConnectionState>()((set, get) => ({
  status: 'connecting',
  renderer: 'ok',
  setStatus(status) {
    // Terminal states are sticky: only a reload leaves them.
    if (isTerminal(get().status)) return;
    set({ status });
  },
  setRenderer(renderer) {
    set({ renderer });
  },
}));
