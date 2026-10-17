import { useCallback, useEffect, useState } from 'react';
import type { GmBoot } from '../live/boot';

// Two GM panels that talk to the server over same-origin HTTP with the page's
// CSRF token (they are not projection state):
// - Table displays: pair a TV with a one-time code, list paired displays,
//   unpair one or all (the M3 routes);
// - Import from /map: a dry-run report of what the legacy scenes would bring
//   across and what they cannot, then the import itself (the M5 route).

type Fetcher = typeof fetch;

async function call(fetcher: Fetcher, boot: GmBoot, path: string, body?: unknown): Promise<{ ok: boolean; data: unknown; busy?: boolean }> {
  try {
    const response = await fetcher(path, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': boot.csrf },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return { ok: response.ok, data: await response.json().catch(() => null), busy: response.status === 503 };
  } catch {
    return { ok: false, data: null };
  }
}

const BUSY = 'The server is busy. Try again in a few seconds.';

interface Grant {
  id: string;
  createdAt: string;
  expiresAt: string;
}

const when = (iso: string) => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
};

export function PairingPanel({ boot, fetcher = fetch }: { boot: GmBoot; fetcher?: Fetcher }) {
  const [code, setCode] = useState<{ code: string; until: number } | null>(null);
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    const result = await call(fetcher, boot, `/vtt/api/table-grants?campaignId=${encodeURIComponent(boot.campaignId)}`);
    if (result.ok) setGrants((result.data as { grants: Grant[] }).grants);
  }, [boot, fetcher]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // The code's countdown; when it runs out it is gone (it is single use anyway).
  useEffect(() => {
    if (!code) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [code]);
  const remaining = code ? Math.max(0, Math.round((code.until - now) / 1000)) : 0;
  useEffect(() => {
    if (code && remaining === 0) setCode(null);
  }, [code, remaining]);

  async function pair() {
    setError(null);
    const result = await call(fetcher, boot, '/vtt/api/table-pairing', { campaignId: boot.campaignId });
    if (!result.ok) {
      setError(result.busy
        ? BUSY
        : result.data && (result.data as { error?: string }).error === 'rate-limited'
          ? 'Too many codes just now. Wait a few minutes.'
          : 'Could not create a code.');
      return;
    }
    const data = result.data as { code: string; expiresInSeconds: number };
    setCode({ code: data.code, until: Date.now() + data.expiresInSeconds * 1000 });
    setNow(Date.now());
  }

  async function revoke(grantId: string | null) {
    if (grantId === null && !window.confirm('Unpair every table display for this campaign?')) return;
    const path = grantId === null ? '/vtt/api/table-grants/revoke-all' : `/vtt/api/table-grants/${encodeURIComponent(grantId)}/revoke`;
    const result = await call(fetcher, boot, path, { campaignId: boot.campaignId });
    if (!result.ok) setError(result.busy ? BUSY : 'Could not unpair.');
    await refresh();
  }

  return (
    <section className="vtt-panel" aria-labelledby="vtt-displays-title">
      <h2 id="vtt-displays-title" className="vtt-inspector-title">Table displays</h2>
      {code ? (
        <div className="vtt-code" role="status">
          <span className="vtt-code-value">{code.code}</span>
          <span className="vtt-code-hint">
            Enter this on the display at /vtt/table. It works once, for {Math.floor(remaining / 60)}:
            {String(remaining % 60).padStart(2, '0')} more.
          </span>
        </div>
      ) : (
        <button type="button" onClick={() => void pair()}>
          Pair a display
        </button>
      )}
      {error && (
        <p className="vtt-pairing-error" role="alert">
          {error}
        </p>
      )}
      {grants && grants.length > 0 && (
        <ul className="vtt-grants">
          {grants.map((g) => (
            <li key={g.id}>
              <span>Paired {when(g.createdAt)}, until {when(g.expiresAt)}</span>
              <button type="button" onClick={() => void revoke(g.id)}>
                Unpair
              </button>
            </li>
          ))}
        </ul>
      )}
      {grants && grants.length === 0 && <p className="vtt-empty">No display is paired.</p>}
      {grants && grants.length > 1 && (
        <button type="button" className="vtt-danger" onClick={() => void revoke(null)}>
          Unpair all
        </button>
      )}
    </section>
  );
}

export interface ImportScene {
  sceneId: string;
  name: string;
  entities: Record<string, number>;
  dropped: string[];
  clamped: string[];
  onTable: boolean;
  status: 'planned' | 'imported' | 'refused';
  reason?: string;
}

export function ImportPanel({ boot, fetcher = fetch, onImported }: { boot: GmBoot; fetcher?: Fetcher; onImported?: () => void }) {
  const [report, setReport] = useState<ImportScene[] | null>(null);
  const [applied, setApplied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(apply: boolean) {
    setBusy(true);
    setError(null);
    const result = await call(fetcher, boot, '/vtt/api/import-legacy', { campaignId: boot.campaignId, apply });
    setBusy(false);
    if (!result.ok) {
      setError(result.busy ? BUSY : 'The import could not run.');
      return;
    }
    setReport((result.data as { scenes: ImportScene[] }).scenes);
    setApplied(apply);
    if (apply) onImported?.();
  }

  const counts = (e: Record<string, number>) =>
    Object.entries(e).map(([k, n]) => `${n} ${k}${n === 1 ? '' : 's'}`).join(', ') || 'empty';

  return (
    <section className="vtt-panel" aria-labelledby="vtt-import-title">
      <h2 id="vtt-import-title" className="vtt-inspector-title">Import from /map</h2>
      {!report && (
        <button type="button" disabled={busy} onClick={() => void run(false)}>
          Check what would import
        </button>
      )}
      {error && (
        <p className="vtt-pairing-error" role="alert">
          {error}
        </p>
      )}
      {report && report.length === 0 && <p className="vtt-empty">This campaign has no /map scenes.</p>}
      {report && report.length > 0 && (
        <>
          <ul className="vtt-import-report">
            {report.map((s) => (
              <li key={s.sceneId}>
                <strong>{s.name}</strong>
                {s.onTable ? ' (on the table)' : ''}: {counts(s.entities)}
                {s.status === 'imported' && ' - imported'}
                {s.status === 'refused' && ` - not imported: ${s.reason ?? 'too large'}`}
                {s.dropped.length > 0 && <div className="vtt-import-dropped">Not brought across: {s.dropped.join('; ')}</div>}
                {s.clamped.length > 0 && <div className="vtt-import-dropped">Adjusted: {s.clamped.length} value(s)</div>}
              </li>
            ))}
          </ul>
          {!applied ? (
            <button type="button" disabled={busy} onClick={() => void run(true)}>
              Import {report.length} scene{report.length === 1 ? '' : 's'}
            </button>
          ) : (
            <p className="vtt-empty">Done. Imported scenes are in the scene picker; running it again updates them.</p>
          )}
        </>
      )}
    </section>
  );
}
