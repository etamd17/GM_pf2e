import { useState, type FormEvent } from 'react';

// Pairing a display (VTT-ADR-0002 section 10). The GM reads out an
// eight-character code; the display redeems it once over same-origin HTTPS and
// the server sets its Secure, HttpOnly table cookie. Nothing here ever holds
// the credential itself: a successful redeem reloads the page, which then
// connects with the cookie.

export type RedeemResult = 'paired' | 'pairing-failed' | 'rate-limited' | 'insecure-origin' | 'server-busy' | 'network';

export async function redeemCode(code: string, fetcher: typeof fetch = fetch): Promise<RedeemResult> {
  try {
    const response = await fetcher('/vtt/api/table-pairing/redeem', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (response.ok) return 'paired';
    // The store is briefly unavailable. The code was most likely not used, so
    // the same one is worth retrying (it can be spent if the outage hit the commit).
    if (response.status === 503) return 'server-busy';
    const body = (await response.json().catch(() => ({}))) as { error?: unknown };
    if (body.error === 'rate-limited' || body.error === 'insecure-origin') return body.error;
    return 'pairing-failed';
  } catch {
    return 'network';
  }
}

const MESSAGES: Record<Exclude<RedeemResult, 'paired'>, string> = {
  'pairing-failed': 'That code did not work. Codes last five minutes and work once. Ask the GM for a new one.',
  'rate-limited': 'Too many attempts. Wait a few minutes, then try again.',
  'insecure-origin':
    'This display is not on a secure connection, so it cannot be paired. Open the table page over HTTPS.',
  'server-busy': 'The server is busy. Try the same code again in a few seconds; if it is refused, ask the GM for a new one.',
  network: 'Could not reach the server. Check the connection and try again.',
};

export interface PairingScreenProps {
  reason: 'unpaired' | 'revoked';
  redeem?: (code: string) => Promise<RedeemResult>;
  onPaired?: () => void;
}

export function PairingScreen({
  reason,
  redeem = redeemCode,
  onPaired = () => window.location.reload(),
}: PairingScreenProps) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Exclude<RedeemResult, 'paired'> | null>(null);
  const blocked = error === 'insecure-origin';

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy || blocked) return;
    setBusy(true);
    setError(null);
    const result = await redeem(code.trim());
    setBusy(false);
    if (result === 'paired') onPaired();
    else setError(result);
  }

  return (
    <form className="vtt-pairing" onSubmit={submit} aria-labelledby="vtt-pairing-title">
      <h1 id="vtt-pairing-title" className="vtt-pairing-title">
        Pair this display
      </h1>
      <p className="vtt-pairing-lead">
        {reason === 'revoked'
          ? 'This display is no longer paired with the campaign.'
          : 'Ask the GM for a pairing code.'}
      </p>
      <label className="vtt-pairing-label" htmlFor="vtt-pairing-code">
        Pairing code
      </label>
      <input
        id="vtt-pairing-code"
        className="vtt-pairing-input"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        maxLength={16}
        placeholder="XXXX-XXXX"
        disabled={busy || blocked}
      />
      <button type="submit" className="vtt-pairing-submit" disabled={busy || blocked || code.trim().length < 8}>
        {busy ? 'Pairing...' : 'Pair'}
      </button>
      {error && (
        <p className="vtt-pairing-error" role="alert">
          {MESSAGES[error]}
        </p>
      )}
    </form>
  );
}
