import { describe, expect, it } from 'vitest';
import type { TemplateGeometry } from '../../scene/templates';
import { createAreaApplier, damageTypes, savesFor, templateTargets, tokenCovered } from './areaEffects';

const G = 64;
const centre = (col: number, row: number, size = 1) => ({ x: col * G + (size * G) / 2, y: row * G + (size * G) / 2 });
const burst = (col: number, row: number, size: number): TemplateGeometry => ({
  shape: 'burst', x: col * G, y: row * G, aimX: col * G, aimY: row * G, size });

describe('template targets', () => {
  it('catches a token whose space the area overlaps, and not one it only touches', () => {
    // A 1-square burst at the corner (2,2): it covers parts of the four squares around it.
    const t = burst(2, 2, 1);
    expect(tokenCovered(t, { id: 'a', ...centre(1, 1), size: 1 }, G)).toBe(true);
    expect(tokenCovered(t, { id: 'b', ...centre(3, 1), size: 1 }, G)).toBe(false);   // a square away
    expect(tokenCovered(t, { id: 'c', ...centre(3, 2), size: 1 }, G)).toBe(false);
  });

  it('catches a Large creature clipped at one corner of its space', () => {
    // A Large token over squares (3..4, 3..4); a 1-square burst at (3,3) clips its top-left corner.
    const large = { id: 'ogre', ...centre(3, 3, 2), size: 2, actorId: 'cmb_ogre' };
    expect(tokenCovered(burst(3, 3, 1), large, G)).toBe(true);    // its centre is 1.4 squares away: a centre test misses it
    expect(tokenCovered(burst(2, 2, 1), large, G)).toBe(false);   // one square short of its space
  });

  it('proposes linked combatants as appliable and anything else as not', () => {
    const tokens = [
      { id: 'tok_g', ...centre(0, 0), size: 1, actorId: 'cmb_gob' },
      { id: 'tok_pc', ...centre(1, 0), size: 1, actorId: 'chr_valeros' },
      { id: 'tok_x', ...centre(0, 1), size: 1 },
      { id: 'tok_far', ...centre(9, 9), size: 1, actorId: 'cmb_far' },
    ];
    expect(templateTargets(burst(1, 1, 1.5), tokens, G)).toEqual([
      { tokenId: 'tok_g', actorId: 'cmb_gob' },
      { tokenId: 'tok_pc', actorId: null },
      { tokenId: 'tok_x', actorId: null },
    ]);
  });
});

describe('vocabularies', () => {
  it('are each system own', () => {
    expect(damageTypes('pf2e')).toContain('fire');
    expect(damageTypes('pf2e')).not.toContain('keen');
    expect(damageTypes('cosmere')).toEqual(['impact', 'keen', 'energy', 'spirit', 'vital']);
    expect(damageTypes('dnd5e')).toEqual([]);
    expect(savesFor('pf2e')).toEqual(['fortitude', 'reflex', 'will']);
    expect(savesFor('cosmere')).toEqual([]);
  });
});

function fakeFetch(reply: unknown, status = 200) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const impl = async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(reply), { status });
  };
  return { calls, impl };
}

const boot = { campaignId: 'camp_a', csrf: 'token' };

describe('the area applier', () => {
  it('sends the reviewed combatants with the encounter token and a request id', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [{ actorId: 'cmb_g', outcome: 'applied', net: 6, defeated: false }] });
    const areas = createAreaApplier(boot, f.impl);
    const result = await areas.apply({ action: 'damage', actorIds: ['cmb_g', 'chr_x', 'cmb_g'], amount: 6,
                                       damageType: 'fire', encounterToken: 'boot-g1', requestId: 'req-0123456789' });
    expect(result).toEqual({ outcome: 'ok', results: [{ actorId: 'cmb_g', outcome: 'applied', net: 6, defeated: false }] });
    expect(JSON.parse(String(f.calls[0]?.init.body))).toEqual({
      campaignId: 'camp_a', requestId: 'req-0123456789', encounterToken: 'boot-g1', action: 'damage',
      actorIds: ['cmb_g'], amount: 6, damageType: 'fire' });
  });

  it('sends nothing without a token or a combatant, or while one is in flight', async () => {
    const f = fakeFetch({ outcome: 'ok', results: [] });
    const areas = createAreaApplier(boot, f.impl);
    expect(await areas.apply({ action: 'heal', actorIds: ['cmb_g'], amount: 1, encounterToken: null }))
      .toEqual({ outcome: 'no-encounter-token' });
    expect(await areas.apply({ action: 'heal', actorIds: ['chr_x'], amount: 1, encounterToken: 'boot-g1' }))
      .toEqual({ outcome: 'no-targets' });
    const first = areas.apply({ action: 'heal', actorIds: ['cmb_g'], amount: 1, encounterToken: 'boot-g1' });
    expect(await areas.apply({ action: 'heal', actorIds: ['cmb_g'], amount: 1, encounterToken: 'boot-g1' }))
      .toEqual({ outcome: 'busy' });
    await first;
    expect(f.calls).toHaveLength(1);
  });

  it('passes partial and stale answers through, and anything unexpected as failed', async () => {
    const partial = createAreaApplier(boot, fakeFetch({ outcome: 'ok', incomplete: 'OSError',
      results: [{ actorId: 'cmb_a', outcome: 'failed', reason: 'OSError' }] }).impl);
    expect(await partial.apply({ action: 'heal', actorIds: ['cmb_a'], amount: 1, encounterToken: 'boot-g1' }))
      .toMatchObject({ outcome: 'ok', incomplete: 'OSError' });
    const stale = createAreaApplier(boot, fakeFetch({ outcome: 'stale', missing: ['cmb_a'], encounterToken: 'boot-g2' }, 409).impl);
    expect(await stale.apply({ action: 'heal', actorIds: ['cmb_a'], amount: 1, encounterToken: 'boot-g1' }))
      .toEqual({ outcome: 'stale', missing: ['cmb_a'], encounterToken: 'boot-g2' });
    const odd = createAreaApplier(boot, fakeFetch({ outcome: 'nope' }).impl);
    expect((await odd.apply({ action: 'heal', actorIds: ['cmb_a'], amount: 1, encounterToken: 'boot-g1' })).outcome)
      .toBe('failed');
  });
});
