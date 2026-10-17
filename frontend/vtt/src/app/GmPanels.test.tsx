// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ImportPanel, PairingPanel } from './GmPanels';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const boot = { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'csrf-x', memberId: 'user_gm' };

function server(routes: Record<string, (body: unknown) => [number, unknown]>) {
  const calls: Array<{ path: string; method: string; body: unknown; csrf: string | null }> = [];
  const fetcher = vi.fn(async (path: string, init: RequestInit) => {
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ path, method: String(init.method), body, csrf: new Headers(init.headers).get('X-CSRF-Token') });
    const route = Object.keys(routes).find((p) => path.startsWith(p));
    const [status, data] = route ? (routes[route] as (b: unknown) => [number, unknown])(body) : [404, {}];
    return { ok: status === 200, status, json: async () => data } as Response;
  });
  return { fetcher: fetcher as unknown as typeof fetch, calls };
}

const flush = () => act(async () => {
  await new Promise((r) => setTimeout(r, 0));
});

describe('PairingPanel', () => {
  it('issues a code with the CSRF token and shows it with its countdown', async () => {
    const { fetcher, calls } = server({
      '/vtt/api/table-grants': () => [200, { grants: [] }],
      '/vtt/api/table-pairing': () => [200, { code: 'ABCD-EFGH', expiresInSeconds: 300 }],
    });
    render(<PairingPanel boot={boot} fetcher={fetcher} />);
    await flush();
    expect(screen.getByText('No display is paired.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Pair a display' }));
    await flush();
    expect(screen.getByText('ABCD-EFGH')).toBeTruthy();
    expect(screen.getByRole('status').textContent).toContain('5:00');
    const issue = calls.find((c) => c.path === '/vtt/api/table-pairing');
    expect(issue).toMatchObject({ method: 'POST', body: { campaignId: 'camp_a' }, csrf: 'csrf-x' });
  });

  it('lists paired displays and unpairs one', async () => {
    let grants = [
      { id: 'grant_1', createdAt: '2026-10-16T18:00:00Z', expiresAt: '2026-10-17T06:00:00Z' },
      { id: 'grant_2', createdAt: '2026-10-16T19:00:00Z', expiresAt: '2026-10-17T07:00:00Z' },
    ];
    const { fetcher, calls } = server({
      '/vtt/api/table-grants/grant_1/revoke': () => {
        grants = grants.slice(1);
        return [200, { revoked: true }];
      },
      '/vtt/api/table-grants': () => [200, { grants }],
    });
    render(<PairingPanel boot={boot} fetcher={fetcher} />);
    await flush();
    expect(screen.getAllByRole('button', { name: 'Unpair' })).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Unpair all' })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Unpair' })[0] as HTMLElement);
    await flush();
    expect(calls.some((c) => c.path === '/vtt/api/table-grants/grant_1/revoke' && c.method === 'POST')).toBe(true);
    expect(screen.getAllByRole('button', { name: 'Unpair' })).toHaveLength(1);
  });

  it('unpairing all asks first', async () => {
    const grants = [
      { id: 'g1', createdAt: '2026-10-16T18:00:00Z', expiresAt: '2026-10-17T06:00:00Z' },
      { id: 'g2', createdAt: '2026-10-16T18:00:00Z', expiresAt: '2026-10-17T06:00:00Z' },
    ];
    const { fetcher, calls } = server({ '/vtt/api/table-grants': () => [200, { grants }] });
    vi.stubGlobal('confirm', vi.fn(() => false));
    render(<PairingPanel boot={boot} fetcher={fetcher} />);
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Unpair all' }));
    await flush();
    expect(calls.some((c) => c.path.endsWith('revoke-all'))).toBe(false);
    vi.unstubAllGlobals();
  });
});

describe('ImportPanel', () => {
  const scenes = (status: string) => ({
    scenes: [{
      sceneId: 'legacy_s1', name: 'Crypt', entities: { token: 2, wall: 4 }, onTable: true, status,
      dropped: ['background image (needs the asset store, M6)', 'fog and revealed areas'], clamped: [],
    }],
  });

  it('checks first, shows what will not come across, then imports', async () => {
    const onImported = vi.fn();
    const { fetcher, calls } = server({
      '/vtt/api/import-legacy': (body) => [200, scenes((body as { apply: boolean }).apply ? 'imported' : 'planned')],
    });
    render(<ImportPanel boot={boot} fetcher={fetcher} onImported={onImported} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check what would import' }));
    await flush();
    expect(calls[0]?.body).toEqual({ campaignId: 'camp_a', apply: false });
    expect(screen.getByText(/Not brought across: background image/)).toBeTruthy();
    expect(screen.getByText(/2 tokens, 4 walls/)).toBeTruthy();
    expect(onImported).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Import 1 scene' }));
    await flush();
    expect(calls[1]?.body).toEqual({ campaignId: 'camp_a', apply: true });
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/- imported/)).toBeTruthy();
  });

  it('a refused scene says so', async () => {
    const { fetcher } = server({
      '/vtt/api/import-legacy': () => [200, { scenes: [{ ...scenes('refused').scenes[0], reason: 'Crypt: over the frame limit' }] }],
    });
    render(<ImportPanel boot={boot} fetcher={fetcher} />);
    fireEvent.click(screen.getByRole('button', { name: 'Check what would import' }));
    await flush();
    expect(screen.getByText(/not imported: Crypt: over the frame limit/)).toBeTruthy();
  });
});

describe('a busy server', () => {
  it('says so instead of blaming the request', async () => {
    const { fetcher } = server({
      '/vtt/api/table-grants': () => [200, { grants: [] }],
      '/vtt/api/table-pairing': () => [503, { error: 'temporarily-unavailable' }],
      '/vtt/api/import-legacy': () => [503, { error: 'temporarily-unavailable' }],
    });
    render(<PairingPanel boot={boot} fetcher={fetcher} />);
    render(<ImportPanel boot={boot} fetcher={fetcher} />);
    await flush();
    fireEvent.click(screen.getByRole('button', { name: 'Pair a display' }));
    fireEvent.click(screen.getByRole('button', { name: 'Check what would import' }));
    await flush();
    expect(screen.getAllByRole('alert').map((a) => a.textContent)).toEqual([
      'The server is busy. Try again in a few seconds.',
      'The server is busy. Try again in a few seconds.',
    ]);
  });
});
