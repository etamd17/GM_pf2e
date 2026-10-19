import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { createTurnController, currentTurn } from './turns';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const actor = (id: string, extra: Partial<Of<'actor'>> = {}) =>
  ({ kind: 'actor', id, version: V, name: id, pc: false, hp: 1, maxHp: 1, conditions: [], active: false,
     hidden: false, status: '', size: 1, sheetUrl: null, round: id.startsWith('cmb_') ? 2 : null, ...extra }) as Of<'actor'>;

const boot = { campaignId: 'camp_a', csrf: 'token' };

function fakeFetch(reply: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply), { status });
  };
  return { calls, impl };
}

describe('currentTurn', () => {
  it('is the active combatant and its round, ignoring an active party character', () => {
    expect(currentTurn([actor('chr_valeros', { active: true }), actor('cmb_skel', { active: true, name: 'Skeleton' })]))
      .toEqual({ actorId: 'cmb_skel', name: 'Skeleton', round: 2 });
    expect(currentTurn([actor('cmb_skel')])).toBeNull();
  });
});

describe('the turn controller', () => {
  it('sends what the page showed, with the CSRF token, and reports the new turn', async () => {
    const f = fakeFetch({ outcome: 'ok', actorId: 'cmb_zombie', round: 2 });
    const turns = createTurnController(boot, f.impl);
    const result = await turns.change('next', [actor('cmb_skel', { active: true })]);
    expect(result).toEqual({ outcome: 'ok', actorId: 'cmb_zombie', round: 2 });
    expect(f.calls[0]?.url).toBe('/vtt/api/turn');
    expect(JSON.parse(String(f.calls[0]?.init.body))).toEqual({
      campaignId: 'camp_a', direction: 'next', expectedActorId: 'cmb_skel', expectedRound: 2,
    });
    expect((f.calls[0]?.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('token');
  });

  it('sends nothing without an encounter, and nothing twice at once', async () => {
    const f = fakeFetch({ outcome: 'ok' });
    const turns = createTurnController(boot, f.impl);
    expect(await turns.change('next', [actor('cmb_skel')])).toEqual({ outcome: 'no-encounter' });
    const first = turns.change('next', [actor('cmb_skel', { active: true })]);
    expect(turns.busy).toBe(true);
    expect(await turns.change('next', [actor('cmb_skel', { active: true })])).toEqual({ outcome: 'busy' });
    await first;
    expect(turns.busy).toBe(false);
    expect(f.calls).toHaveLength(1);
  });

  it('passes a refusal through, and turns anything unexpected into failed', async () => {
    const stale = createTurnController(boot, fakeFetch({ outcome: 'stale', actorId: 'cmb_z', round: 3 }, 409).impl);
    expect(await stale.change('prev', [actor('cmb_skel', { active: true })]))
      .toEqual({ outcome: 'stale', actorId: 'cmb_z', round: 3 });
    const odd = createTurnController(boot, fakeFetch({ outcome: 'launch-missiles' }).impl);
    expect((await odd.change('next', [actor('cmb_skel', { active: true })])).outcome).toBe('failed');
    const down = createTurnController(boot, async () => {
      throw new TypeError('network');
    });
    expect((await down.change('next', [actor('cmb_skel', { active: true })])).outcome).toBe('failed');
  });
});
