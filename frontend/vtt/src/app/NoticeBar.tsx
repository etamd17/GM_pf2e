import type { GmLive } from '../live/gm';
import type { CommandOutcome } from '../protocol/commands';
import type { RejectionCode } from '../protocol/frames';
import { useCommands } from '../state/commands';

// One plain-language notice per command outcome worth telling the GM about
// (client proposal section 6). It never shows server detail: the codes carry
// none, and unauthorized never says whether a target exists.

const TYPE_LABEL: Record<string, string> = {
  'scene.create': 'Creating the scene',
  'scene.update': 'Changing the scene',
  'table.show': 'Showing the scene on the table',
  'table.clear': 'Clearing the table',
  'token.create': 'Placing the token',
  'token.update': 'Changing the token',
  'token.move': 'Moving the token',
  'light.create': 'Placing the light',
  'light.update': 'Changing the light',
  'wall.create': 'Drawing the wall',
  'wall.update': 'Changing the wall',
  'entity.delete': 'Deleting',
};

const CODE_MESSAGE: Partial<Record<RejectionCode, string>> = {
  stale: 'did not happen: someone changed it first. The map now shows the latest; try again.',
  conflict: 'did not happen: it clashes with something already there. Try again.',
  'validation-failed': 'did not happen: that is not something this scene can hold.',
  'rule-violation': 'is not allowed right now (for example, clear the table before unpublishing its scene).',
  unauthorized: 'is not allowed for this account.',
  'resync-required': 'did not happen while the map was catching up. Try again.',
  'unsupported-version': 'did not happen: this page is out of date. Reload it.',
};

export function noticeText(outcome: CommandOutcome): string | null {
  switch (outcome.kind) {
    case 'applied':
      return null;
    case 'reauthenticate':
      return 'Your session ended. Sign in again in another tab, then retry here. Unsaved changes are kept and sent again.';
    case 'info':
      return outcome.text;
    case 'refused-locally':
      return `${TYPE_LABEL[outcome.type] ?? 'That change'} could not be sent: this page built an invalid command. Reload the page.`;
    case 'rejected': {
      const what = TYPE_LABEL[outcome.type] ?? 'That change';
      return `${what} ${CODE_MESSAGE[outcome.code] ?? 'failed. Reload the page if it keeps happening.'}`;
    }
  }
}

export function NoticeBar({ live }: { live: GmLive | null }) {
  const notice = useCommands((s) => s.notice);
  const dismiss = useCommands((s) => s.dismiss);
  if (!notice) return null;
  const text = noticeText(notice);
  if (!text) return null;
  return (
    <div className="vtt-notice" role="alert">
      <span className="vtt-notice-text">{text}</span>
      {notice.kind === 'reauthenticate' && (
        <>
          <a className="vtt-notice-action" href="/login" target="_blank" rel="noopener">
            Sign in
          </a>
          <button
            type="button"
            className="vtt-notice-action"
            onClick={() => {
              // A live connection can resend at once; a closed one reloads and
              // the page resends what it saved (live/gm.ts).
              if (live && live.transport.connected) live.queue.reauthenticated();
              else window.location.reload();
              dismiss();
            }}
          >
            Retry
          </button>
        </>
      )}
      <button type="button" className="vtt-notice-close" aria-label="Dismiss" onClick={dismiss}>
        Dismiss
      </button>
    </div>
  );
}
