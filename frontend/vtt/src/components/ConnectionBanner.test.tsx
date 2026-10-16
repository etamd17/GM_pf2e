// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { CONNECTION_STATUSES } from '../state/connection';
import { ConnectionBanner, bannerContent } from './ConnectionBanner';

afterEach(cleanup);

describe('bannerContent', () => {
  it('says nothing when live and the renderer is fine', () => {
    expect(bannerContent('live', 'ok')).toBeNull();
  });

  it('has copy for every non-live state', () => {
    for (const status of CONNECTION_STATUSES) {
      if (status === 'live') continue;
      const c = bannerContent(status, 'ok');
      expect(c, status).not.toBeNull();
      expect(c?.title.length, status).toBeGreaterThan(0);
    }
  });

  it('treats terminal states as errors', () => {
    expect(bannerContent('revoked', 'ok')?.tone).toBe('error');
    expect(bannerContent('version-mismatch', 'ok')?.tone).toBe('error');
    expect(bannerContent('reconnecting', 'ok')?.tone).toBe('warn');
  });

  it('puts renderer problems ahead of connection problems', () => {
    expect(bannerContent('reconnecting', 'lost')?.title).toBe('Display paused');
    expect(bannerContent('live', 'failed')?.tone).toBe('error');
  });
});

describe('ConnectionBanner', () => {
  it('renders nothing at all when live (no hidden-attribute element)', () => {
    const { container } = render(<ConnectionBanner status="live" renderer="ok" variant="gm" />);
    expect(container.innerHTML).toBe('');
  });

  it('shows a full banner on the GM view', () => {
    render(<ConnectionBanner status="reconnecting" renderer="ok" variant="gm" />);
    const banner = screen.getByRole('status');
    expect(banner.textContent).toContain('Reconnecting');
  });

  it('shows only a dot on the table for recoverable states', () => {
    const { container } = render(<ConnectionBanner status="reconnecting" renderer="ok" variant="table" />);
    expect(container.querySelector('.vtt-status-dot')).not.toBeNull();
    expect(container.querySelector('.vtt-banner')).toBeNull();
  });

  it('shows a full alert on the table for terminal states', () => {
    render(<ConnectionBanner status="revoked" renderer="ok" variant="table" />);
    expect(screen.getByRole('alert').textContent).toContain('Access ended');
  });
});
