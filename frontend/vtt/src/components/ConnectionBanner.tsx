import type { ConnectionStatus, RendererStatus } from '../state/connection';

// Plain-language copy for every non-live state. The GM must always know when
// map input is disabled and why.
const STATUS_COPY: Record<Exclude<ConnectionStatus, 'live'>, { title: string; detail: string }> = {
  connecting: { title: 'Connecting', detail: 'Map input is paused until the table is connected.' },
  degraded: {
    title: 'Connection unsteady',
    detail: 'No word from the server for a few seconds. Map input is paused.',
  },
  reconnecting: {
    title: 'Reconnecting',
    detail: 'Trying to reach the server. Nothing you did has been lost.',
  },
  resyncing: { title: 'Catching up', detail: 'Reloading the current scene from the server.' },
  revoked: {
    title: 'Access ended',
    detail: 'This screen no longer has access to the campaign. Ask the GM to pair it again.',
  },
  'version-mismatch': {
    title: 'Update required',
    detail: 'The server has a newer version. Reload to continue.',
  },
};

const RENDERER_COPY: Record<Exclude<RendererStatus, 'ok'>, { title: string; detail: string }> = {
  lost: { title: 'Display paused', detail: 'The graphics context was lost. Restoring the map.' },
  restoring: { title: 'Restoring display', detail: 'Rebuilding the map from current state.' },
  failed: {
    title: 'Display could not be restored',
    detail: 'Reload the page. Campaign state is safe on the server.',
  },
};

export interface BannerContent {
  tone: 'info' | 'warn' | 'error';
  title: string;
  detail: string;
}

// Exported for tests: what the banner says for a given state, or null when
// there is nothing to say. Renderer problems outrank connection problems
// because they explain why the map itself looks wrong.
export function bannerContent(
  status: ConnectionStatus,
  renderer: RendererStatus,
): BannerContent | null {
  if (renderer !== 'ok') {
    return { tone: renderer === 'failed' ? 'error' : 'warn', ...RENDERER_COPY[renderer] };
  }
  if (status === 'live') return null;
  const tone = status === 'revoked' || status === 'version-mismatch' ? 'error' : 'warn';
  return { tone, ...STATUS_COPY[status] };
}

interface Props {
  status: ConnectionStatus;
  renderer: RendererStatus;
  // The table has no operator: it shows a small status dot instead of a bar,
  // except for terminal states, which someone in the room must act on.
  variant: 'gm' | 'table';
}

// Rendered conditionally rather than with the hidden attribute: a styled
// element with `display` set ignores [hidden] (CLAUDE.md recurring bug class).
export function ConnectionBanner({ status, renderer, variant }: Props) {
  const content = bannerContent(status, renderer);
  if (!content) return null;
  if (variant === 'table' && content.tone !== 'error') {
    return (
      <div className={`vtt-status-dot vtt-tone-${content.tone}`} role="status" aria-label={content.title} />
    );
  }
  return (
    <div
      className={`vtt-banner vtt-tone-${content.tone}`}
      role={content.tone === 'error' ? 'alert' : 'status'}
      aria-live={content.tone === 'error' ? 'assertive' : 'polite'}
    >
      <strong className="vtt-banner-title">{content.title}</strong>
      <span className="vtt-banner-detail">{content.detail}</span>
    </div>
  );
}
