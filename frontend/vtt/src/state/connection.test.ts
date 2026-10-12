import { beforeEach, describe, expect, it } from 'vitest';
import { acceptsDurableInput, isConnectionStatus, useConnection } from './connection';
import { initialDevStatus } from '../app/devStatus';

beforeEach(() => {
  useConnection.setState({ status: 'connecting', renderer: 'ok' });
});

describe('connection state', () => {
  it('accepts durable input only while live', () => {
    expect(acceptsDurableInput('live')).toBe(true);
    for (const s of ['connecting', 'degraded', 'reconnecting', 'resyncing', 'revoked', 'version-mismatch'] as const) {
      expect(acceptsDurableInput(s), s).toBe(false);
    }
  });

  it('keeps terminal states sticky', () => {
    const { setStatus } = useConnection.getState();
    setStatus('revoked');
    setStatus('live');
    expect(useConnection.getState().status).toBe('revoked');
  });

  it('validates status strings', () => {
    expect(isConnectionStatus('resyncing')).toBe(true);
    expect(isConnectionStatus('LIVE')).toBe(false);
    expect(isConnectionStatus(undefined)).toBe(false);
  });

  it('only honours ?status= in development builds', () => {
    expect(initialDevStatus('?status=degraded', true)).toBe('degraded');
    expect(initialDevStatus('?status=degraded', false)).toBeNull();
    expect(initialDevStatus('?status=nonsense', true)).toBeNull();
  });
});
