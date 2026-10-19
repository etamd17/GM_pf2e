import { describe, expect, it } from 'vitest';
import type { GmEntity } from '../../protocol/frames';
import { conditionChoices, conditionValue, createConditionController } from './conditions';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'vAAAAAAAAAAAAAAAAAAAAAAAA';
const actor = (id: string, conditions: Of<'actor'>['conditions'] = []) =>
  ({ kind: 'actor', id, version: V, name: id, pc: false, hp: 1, maxHp: 1, conditions, active: false,
     hidden: false, status: '', size: 1, sheetUrl: null, round: 1, turnToken: 'boot-1' }) as Of<'actor'>;

const boot = { campaignId: 'camp_a', csrf: 'token' };

function fakeFetch(reply: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply), { status });
  };
  return { calls, impl };
}

describe('condition choices', () => {
  it('are each system\'s own, never shared', () => {
    const pf2e = conditionChoices('pf2e').map((c) => c.name);
    const cosmere = conditionChoices('cosmere').map((c) => c.name);
    expect(pf2e).toContain('off_guard');
    expect(pf2e).not.toContain('determined');
    expect(cosmere).toContain('determined');
    expect(cosmere).not.toContain('frightened');
    expect(conditionChoices('dnd5e')).toEqual([]);
    expect(conditionChoices('pf2e').find((c) => c.name === 'off_guard')).toEqual({
      name: 'off_guard', label: 'Off-Guard', valued: false });
    expect(conditionChoices('cosmere').find((c) => c.name === 'exhausted')?.valued).toBe(true);
  });

  it('read the value the mirror shows: 0 absent, 1 a flag, else the value', () => {
    const a = actor('cmb_a', [{ name: 'frightened', value: 2 }, { name: 'prone', value: null }]);
    expect([conditionValue(a, 'frightened'), conditionValue(a, 'prone'), conditionValue(a, 'sickened')])
      .toEqual([2, 1, 0]);
  });
});

describe('the condition controller', () => {
  it('sends the value the page showed, with the CSRF token', async () => {
    const f = fakeFetch({ outcome: 'ok', value: 3 });
    const conditions = createConditionController(boot, f.impl);
    const result = await conditions.change(actor('cmb_a', [{ name: 'frightened', value: 2 }]), 'frightened', 'add', 3);
    expect(result).toEqual({ outcome: 'ok', value: 3 });
    expect(f.calls[0]?.url).toBe('/vtt/api/condition');
    expect(JSON.parse(String(f.calls[0]?.init.body))).toEqual({
      campaignId: 'camp_a', actorId: 'cmb_a', condition: 'frightened', operation: 'add', expectedValue: 2, rounds: 3,
    });
    expect((f.calls[0]?.init.headers as Record<string, string>)['X-CSRF-Token']).toBe('token');
  });

  it('sends nothing for a party character, and nothing twice at once for the same condition', async () => {
    const f = fakeFetch({ outcome: 'ok', value: 1 });
    const conditions = createConditionController(boot, f.impl);
    expect(await conditions.change(actor('chr_valeros'), 'prone', 'add')).toEqual({ outcome: 'not-in-encounter' });
    const first = conditions.change(actor('cmb_a'), 'prone', 'add');
    expect(conditions.busy('cmb_a', 'prone')).toBe(true);
    expect(await conditions.change(actor('cmb_a'), 'prone', 'add')).toEqual({ outcome: 'busy' });
    const other = conditions.change(actor('cmb_a'), 'sickened', 'add');    // another condition is fine
    await Promise.all([first, other]);
    expect(conditions.busy('cmb_a', 'prone')).toBe(false);
    expect(f.calls).toHaveLength(2);
  });

  it('passes a refusal through, and turns anything unexpected into failed', async () => {
    const stale = createConditionController(boot, fakeFetch({ outcome: 'stale', value: 3 }, 409).impl);
    expect(await stale.change(actor('cmb_a'), 'frightened', 'add')).toEqual({ outcome: 'stale', value: 3 });
    const odd = createConditionController(boot, fakeFetch({ outcome: 'nope' }).impl);
    expect((await odd.change(actor('cmb_a'), 'prone', 'add')).outcome).toBe('failed');
    const down = createConditionController(boot, async () => {
      throw new TypeError('network');
    });
    expect((await down.change(actor('cmb_a'), 'prone', 'add')).outcome).toBe('failed');
  });
});
