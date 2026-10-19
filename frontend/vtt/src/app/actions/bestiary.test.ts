import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { asCreature, createBestiaryAdder, currentEncounterToken, newRequestId, searchBestiary } from './bestiary';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const actor = (id: string, encounterToken: string | null) =>
  ({ kind: 'actor', id, version: V, name: id, pc: true, hp: 1, maxHp: 1, conditions: [], active: false,
     hidden: false, status: '', size: 1, sheetUrl: null, round: null, turnToken: null, encounterToken }) as Of<'actor'>;

const boot = { campaignId: 'camp_a', csrf: 'token' };
const entry = { ref: 'b\\goblin.json', name: 'Goblin Warrior', level: -1, size: 1 };
const shown = { encounterToken: 'boot-g3' };

function fakeFetch(reply: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply), { status });
  };
  return { calls, impl };
}

describe('bestiary search', () => {
  it('asks the server for this campaign, keeps only well-formed results, and returns the encounter token', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [entry, { name: 'broken' }], encounterToken: 'boot-g3' });
    const found = await searchBestiary(boot, '  gob ', f.impl);
    expect(found).toEqual({ outcome: 'ok', results: [entry], encounterToken: 'boot-g3' });
    expect(f.calls[0]?.url).toBe('/vtt/api/bestiary?campaignId=camp_a&q=gob');
  });

  it('sends nothing under two characters, and reports a failure as failed', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [] });
    expect(await searchBestiary(boot, 'g', f.impl)).toEqual({ outcome: 'ok', results: [] });
    expect(f.calls).toHaveLength(0);
    expect((await searchBestiary(boot, 'gob', async () => { throw new TypeError('x'); })).outcome).toBe('failed');
  });
});

describe('the encounter token', () => {
  it('comes from the mirror first, and from the search answer only when no actor carries one', () => {
    expect(currentEncounterToken([actor('chr_valeros', 'boot-g7')], 'boot-g3')).toBe('boot-g7');
    expect(currentEncounterToken([actor('chr_valeros', null)], 'boot-g3')).toBe('boot-g3');
    expect(currentEncounterToken([], null)).toBeNull();
    expect(currentEncounterToken([], 'bad token')).toBeNull();
  });
});

describe('the bestiary adder', () => {
  it('adds through the server bound to the encounter, with a request id, and returns the linked actor', async () => {
    const f = fakeFetch({ outcome: 'ok', actorId: 'cmb_123', name: 'Goblin Warrior', size: 1 });
    const adder = createBestiaryAdder(boot, f.impl);
    const added = await adder.add(entry, { ...shown, requestId: 'req-0123456789' });
    expect(added).toEqual({ outcome: 'ok', creature: { actorId: 'cmb_123', name: 'Goblin Warrior', size: 1 } });
    expect(JSON.parse(String(f.calls[0]?.init.body))).toEqual({
      campaignId: 'camp_a', ref: entry.ref, requestId: 'req-0123456789', encounterToken: 'boot-g3' });
    expect((f.calls[0]?.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('token');
    expect(asCreature(added.creature!)).toMatchObject({ actorId: 'cmb_123', size: 1, pc: false, onScene: [] });
  });

  it('sends nothing without an encounter token, nothing twice at once, and a fresh request id per add', async () => {
    const f = fakeFetch({ outcome: 'ok', actorId: 'cmb_1', name: 'G', size: 1 });
    const adder = createBestiaryAdder(boot, f.impl);
    expect(await adder.add(entry, { encounterToken: null })).toEqual({ outcome: 'no-encounter-token' });
    const first = adder.add(entry, shown);
    expect(adder.busy(entry.ref)).toBe(true);
    expect(await adder.add(entry, shown)).toEqual({ outcome: 'busy' });
    await first;
    await adder.add(entry, shown);
    const ids = f.calls.map((c) => JSON.parse(String(c.init.body)).requestId);
    expect(ids).toHaveLength(2);
    expect(ids[0]).not.toBe(ids[1]);
    expect(newRequestId()).toMatch(/^req-[0-9a-f]{24}$/);
  });

  it('reports an incomplete add as added, and a stale encounter with the current token', async () => {
    const incomplete = createBestiaryAdder(boot, fakeFetch(
      { outcome: 'ok', actorId: 'cmb_1', name: 'G', size: 1, incomplete: 'OSError' }).impl);
    expect(await incomplete.add(entry, shown)).toEqual({
      outcome: 'ok', creature: { actorId: 'cmb_1', name: 'G', size: 1 }, incomplete: 'OSError' });
    const stale = createBestiaryAdder(boot, fakeFetch({ outcome: 'stale', encounterToken: 'boot-g4' }, 409).impl);
    expect(await stale.add(entry, shown)).toEqual({ outcome: 'stale', encounterToken: 'boot-g4' });
  });

  it('passes a refusal through, and turns anything unexpected into failed', async () => {
    expect(await createBestiaryAdder(boot, fakeFetch({ outcome: 'not-live' }, 409).impl).add(entry, shown))
      .toEqual({ outcome: 'not-live' });
    expect((await createBestiaryAdder(boot, fakeFetch({ outcome: 'ok', actorId: 'chr_x', name: 'x', size: 1 }).impl)
      .add(entry, shown)).outcome).toBe('failed');
    expect((await createBestiaryAdder(boot, fakeFetch({ outcome: 'weird' }).impl).add(entry, shown)).outcome)
      .toBe('failed');
  });
});
