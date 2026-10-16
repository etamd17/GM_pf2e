import { isConnectionStatus, type ConnectionStatus } from '../state/connection';

// Until the transport exists (VTT-ADR-0002), development builds can force a
// connection state with ?status=<state> to review every banner. Production
// builds ignore the parameter and start in 'connecting'.
export function initialDevStatus(search: string, isDev: boolean): ConnectionStatus | null {
  if (!isDev) return null;
  const value = new URLSearchParams(search).get('status');
  return isConnectionStatus(value) ? value : null;
}
