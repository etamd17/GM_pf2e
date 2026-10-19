import { describe, expect, it } from 'vitest';
import { asCreature, createBestiaryAdder, newRequestId, searchBestiary } from './bestiary';

const boot = { campaignId: 'camp_a', csrf: 'token' };
const entry = { ref: 'b\\goblin.json', name: 'Goblin Warrior', level: -1, size: 1 };

function fakeFetch(reply: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply), { status });
  };
  return { calls, impl };
}

describe('bestiary search', () => {
  it('asks the server for this campaign, and keeps only well-formed results', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [entry, { name: 'broken' }] });
    const found = await searchBestiary(boot, '  gob ', f.impl);
    expect(found).toEqual({ outcome: 'ok', results: [entry] });
    expect(f.calls[0]?.url).toBe('/vtt/api/bestiary?campaignId=camp_a&q=gob');
  });

  it('sends nothing under two characters, and reports a failure as failed', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [] });
    expect(await searchBestiary(boot, 'g', f.impl)).toEqual({ outcome: 'ok', results: [] });
    expect(f.calls).toHaveLength(0);
    expect((await searchBestiary(boot, 'gob', async () => { throw new TypeError('x'); })).outcome).toBe('failed');
  });
});

describe('the bestiary adder', () => {
  it('adds through the server with a request id and returns the linked actor', async () => {
    const f = fakeFetch({ outcome: 'ok', actorId: 'cmb_123', name: 'Goblin Warrior', size: 1 });
    const adder = createBestiaryAdder(boot, f.impl);
    const added = await adder.add(entry, 'req-0123456789');
    expect(added).toEqual({ outcome: 'ok', creature: { actorId: 'cmb_123', name: 'Goblin Warrior', size: 1 } });
    expect(JSON.parse(String(f.calls[0]?.init.body))).toEqual({ campaignId: 'camp_a', ref: entry.ref, requestId: 'req-0123456789' });
    expect((f.calls[0]?.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('token');
    expect(asCreature(added.creature!)).toMatchObject({ actorId: 'cmb_123', size: 1, pc: false, onScene: [] });
  });

  it('sends nothing twice at once for the same entry, and makes a fresh request id per add', async () => {
    const f = fakeFetch({ outcome: 'ok', actorId: 'cmb_1', name: 'G', size: 1 });
    const adder = createBestiaryAdder(boot, f.impl);
    const first = adder.add(entry);
    expect(adder.busy(entry.ref)).toBe(true);
    expect(await adder.add(entry)).toEqual({ outcome: 'busy' });
    await first;
    await adder.add(entry);
    const ids = f.calls.map((c) => JSON.parse(String(c.init.body)).requestId);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
    expect(newRequestId()).toMatch(/^req-[0-9a-f]{24}$/);
  });

  it('passes a refusal through, and turns anything unexpected into failed', async () => {
    expect(await createBestiaryAdder(boot, fakeFetch({ outcome: 'not-live' }, 409).impl).add(entry))
      .toEqual({ outcome: 'not-live' });
    expect((await createBestiaryAdder(boot, fakeFetch({ outcome: 'ok', actorId: 'chr_x', name: 'x', size: 1 }).impl)
      .add(entry)).outcome).toBe('failed');
    expect((await createBestiaryAdder(boot, fakeFetch({ outcome: 'weird' }).impl).add(entry)).outcome).toBe('failed');
  });
});
