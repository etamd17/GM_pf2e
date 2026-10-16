// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PairingScreen, redeemCode, type RedeemResult } from './PairingScreen';

afterEach(cleanup);

const response = (status: number, body: unknown) =>
  ({ ok: status === 200, status, json: async () => body }) as unknown as Response;

describe('redeemCode', () => {
  it('posts the code same-origin and maps every answer', async () => {
    const fetcher = vi.fn(async () => response(200, { paired: true }));
    expect(await redeemCode('ABCD-EFGH', fetcher as never)).toBe('paired');
    expect(fetcher).toHaveBeenCalledWith('/vtt/api/table-pairing/redeem', expect.objectContaining({
      method: 'POST', credentials: 'same-origin', body: JSON.stringify({ code: 'ABCD-EFGH' }),
    }));
    const answer = (status: number, body: unknown) => redeemCode('x', (async () => response(status, body)) as never);
    expect(await answer(400, { error: 'pairing-failed' })).toBe('pairing-failed');
    expect(await answer(429, { error: 'rate-limited' })).toBe('rate-limited');
    expect(await answer(400, { error: 'insecure-origin' })).toBe('insecure-origin');
    expect(await answer(403, { error: 'origin-refused' })).toBe('pairing-failed');
    expect(await redeemCode('x', (async () => { throw new TypeError('offline'); }) as never)).toBe('network');
  });
});

describe('PairingScreen', () => {
  async function submit(result: RedeemResult, code = 'abcd-efgh') {
    const redeem = vi.fn(async () => result);
    const onPaired = vi.fn();
    render(<PairingScreen reason="unpaired" redeem={redeem} onPaired={onPaired} />);
    fireEvent.change(screen.getByLabelText('Pairing code'), { target: { value: code } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Pair' }));
    });
    return { redeem, onPaired };
  }

  it('pairs and hands over to a reload', async () => {
    const { redeem, onPaired } = await submit('paired');
    expect(redeem).toHaveBeenCalledWith('abcd-efgh');
    expect(onPaired).toHaveBeenCalledTimes(1);
  });

  it('a wrong code says so without detail', async () => {
    await submit('pairing-failed');
    expect(screen.getByRole('alert').textContent).toContain('did not work');
  });

  it('an insecure origin fails visibly and blocks further attempts', async () => {
    await submit('insecure-origin');
    expect(screen.getByRole('alert').textContent).toContain('HTTPS');
    expect((screen.getByRole('button', { name: 'Pair' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText('Pairing code') as HTMLInputElement).disabled).toBe(true);
  });

  it('a revoked display says it was unpaired', () => {
    render(<PairingScreen reason="revoked" />);
    expect(screen.getByText(/no longer paired/)).toBeTruthy();
  });
});
