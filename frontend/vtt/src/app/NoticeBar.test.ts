import { describe, expect, it } from 'vitest';
import { REJECTION_CODES } from '../protocol/frames';
import { REJECTION_ACTIONS } from '../protocol/rejections';
import { noticeText } from './NoticeBar';

describe('noticeText', () => {
  it('says nothing for an applied command', () => {
    expect(noticeText({ kind: 'applied', commandId: 'c', type: 'token.move' })).toBeNull();
  });

  it.each(REJECTION_CODES.map((c) => [c]))('%s has plain words and no raw code', (code) => {
    const text = noticeText({ kind: 'rejected', commandId: 'c', type: 'token.move', code, action: REJECTION_ACTIONS[code] });
    expect(text).toMatch(/^Moving the token /);
    expect(text).not.toContain(code);
  });

  it('unauthorized never says whether the target exists', () => {
    const text =
      noticeText({
        kind: 'rejected',
        commandId: 'c',
        type: 'entity.delete',
        code: 'unauthorized',
        action: REJECTION_ACTIONS.unauthorized,
      }) ?? '';
    expect(text).not.toMatch(/exist|found|missing/i);
  });

  it('reauthentication and local refusal say what to do', () => {
    expect(noticeText({ kind: 'reauthenticate', commandId: 'c' })).toContain('Sign in');
    expect(noticeText({ kind: 'refused-locally', type: 'wall.create' })).toContain('Reload');
  });
});
