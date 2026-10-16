// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { GmBoot } from './boot';

// Handoff 043 finding 1: commands saved when a session ended were keyed only
// by campaign, so another member signing in on the same tab resent them as
// themselves. Receipts are per principal, so they would have run anew.

const sent = vi.hoisted(() => [] as Array<Record<string, unknown>>);
vi.mock('../protocol/socketTransport', () => ({
  SocketIoTransport: class {
    connected = true;
    connect() {}
    requestResubscribe() {}
    stopRetrying() {}
    sendCommand(c: Record<string, unknown>) {
      sent.push(c);
      return true;
    }
  },
}));

const { PARKED_KEY, adoptableCommands, parkEnvelope, startGm } = await import('./gm');

const memberA: GmBoot = { campaignId: 'camp_a', sceneId: 'scene_a', csrf: 'token-1', memberId: 'user_a' };
const command = {
  schemaVersion: 1,
  commandId: 'cmd_1234567890123456',
  campaignId: 'camp_a',
  sceneId: 'scene_a',
  type: 'token.move',
  expected: [{ kind: 'token', id: 'tok_1', version: 'v' + 'A'.repeat(24) }],
  payload: { id: 'tok_1', x: 64, y: 64 },
};

function reloadAs(boot: GmBoot, saved: unknown) {
  sent.length = 0;
  sessionStorage.setItem(PARKED_KEY, JSON.stringify(saved));
  const live = startGm(boot);
  live.queue.resume();
  return live;
}

beforeEach(() => sessionStorage.clear());

describe('commands saved when a session ends', () => {
  it('are resent by the same member after reauthentication, under the same ids', () => {
    // A new CSRF token after signing in again; the member is the same.
    reloadAs({ ...memberA, csrf: 'token-2' }, parkEnvelope(memberA, [command]));
    expect(sent.map((c) => c.commandId)).toEqual(['cmd_1234567890123456']);
    expect(sessionStorage.getItem(PARKED_KEY)).toBeNull();
  });

  it('are never sent by another member signing in on the same tab', () => {
    reloadAs({ ...memberA, csrf: 'token-b', memberId: 'user_b' }, parkEnvelope(memberA, [command]));
    expect(sent).toEqual([]);
    expect(sessionStorage.getItem(PARKED_KEY)).toBeNull(); // discarded, not kept for later
  });

  it('are not sent into another campaign', () => {
    reloadAs({ ...memberA, campaignId: 'camp_b' }, parkEnvelope(memberA, [command]));
    expect(sent).toEqual([]);
  });

  it('the old unbound form (a bare list) is discarded', () => {
    reloadAs(memberA, [command]);
    expect(sent).toEqual([]);
  });

  it('a command naming another campaign inside a matching envelope is dropped', () => {
    expect(adoptableCommands(memberA, JSON.stringify(parkEnvelope(memberA, [{ ...command, campaignId: 'camp_b' }])))).toEqual([]);
    expect(adoptableCommands(memberA, 'not json')).toEqual([]);
    expect(adoptableCommands(memberA, null)).toEqual([]);
  });
});
