import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import * as gm from './generated/gm.validators.mjs';
import * as table from './generated/table.validators.mjs';
import { hasDuplicateEntityKeys, type EntityKey } from './frames';
import { decodeStrict } from './strictJson';

// The generated validators and the strict decoder must agree with the shared
// contract corpus (contracts/vtt/v1/fixtures/cases.json, copied into
// generated/ by the generator) case for case, as the platform's Python and
// Node runners do.

interface Case {
  id: string;
  schema: string;
  valid: boolean;
  message?: unknown;
  raw?: string;
}

const here = import.meta.dirname;
const cases = JSON.parse(readFileSync(join(here, 'generated', 'fixtures.json'), 'utf8')) as Case[];

const VALIDATORS: Record<string, (data: unknown) => boolean> = {
  gm: gm.validateGm,
  'gm-subscribe': gm.validateGmSubscribe,
  acknowledgement: gm.validateAcknowledgement,
  'resync-required': table.validateResyncRequired,
  'bootstrap-rejection': table.validateBootstrapRejection,
  table: table.validateTable,
  'table-subscribe': table.validateTableSubscribe,
  // The aggregate subscribe type has no runtime validator by design (a table
  // must never be validated against the GM shape); its cases are the union.
  subscribe: (d) => gm.validateGmSubscribe(d) || table.validateTableSubscribe(d),
};

function verdict(c: Case): boolean {
  const validate = VALIDATORS[c.schema];
  if (!validate) throw new Error(`no validator for ${c.schema}`);
  try {
    const message = c.raw !== undefined ? decodeStrict(c.raw) : c.message;
    if (validate(message) !== true) return false;
    // S5: entity-key uniqueness is a code check, not a schema keyword.
    const m = message as { entities?: EntityKey[]; upserted?: EntityKey[]; removed?: EntityKey[] };
    return ![m.entities, m.upserted, m.removed].some((list) => list !== undefined && hasDuplicateEntityKeys(list));
  } catch {
    return false;
  }
}

describe('generated validators agree with the contract corpus', () => {
  it('covers every case', () => {
    expect(cases.length).toBeGreaterThan(0);
  });
  it.each(cases.map((c) => [c.id, c] as const))('%s', (_id, c) => {
    expect(verdict(c)).toBe(c.valid);
  });
});

describe('surface isolation', () => {
  it('the table module exports no GM validator', () => {
    expect(Object.keys(table).sort()).toEqual([
      'validateBootstrapRejection',
      'validateResyncRequired',
      'validateTable',
      'validateTableSubscribe',
    ]);
    const source = readFileSync(join(here, 'generated', 'table.validators.mjs'), 'utf8');
    expect(source).not.toMatch(/gm(-subscribe)?\.schema\.json/);
  });
});
