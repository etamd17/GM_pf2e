// Conditions from the map (E-2). Published by Claude for Codex's token
// inspector / condition picker (handoff 087). The tracker stays the
// authority: a change goes to /vtt/api/condition, which runs the tracker's own
// condition engine for the campaign's system, and the result arrives back
// through the live actor mirror like any tracker change.
//
// Systems never share mechanics: each system offers only its own list.
// PF2e: valued conditions go up (add) and down (decrease); flags go on (add)
// and off (remove); 'rounds' sets the tracker's expiry timer on add.
// Cosmere: add and remove only (a valued Cosmere condition stacks on add and
// clears on remove); no timer from here. D&D 5e has no integrated tracker.
//
// The request names the value the page showed (0 absent, 1 a flag, else the
// value), so a double click never stacks a condition twice ('stale').
// These lists are checked against the server's by tests/vtt/test_e2_conditions.py.

import type { GmEntity } from '../../protocol/frames';

type Actor = Extract<GmEntity, { kind: 'actor' }>;

const PF2E_VALUED = [
  'clumsy', 'doomed', 'drained', 'dying', 'enfeebled', 'frightened', 'sickened', 'slowed', 'stunned',
  'stupefied', 'wounded',
] as const;
const PF2E_FLAGS = ['concealed', 'hidden', 'off_guard', 'prone', 'undetected'] as const;
const COSMERE_VALUED = ['afflicted', 'enhanced', 'exhausted'] as const;
const COSMERE_FLAGS = [
  'determined', 'disoriented', 'empowered', 'focused', 'immobilized', 'prone', 'restrained', 'slowed',
  'stunned', 'surprised', 'unconscious',
] as const;

export type ConditionSystem = 'pf2e' | 'cosmere';

export interface ConditionChoice {
  name: string;
  label: string; // 'off_guard' -> 'Off-Guard'
  valued: boolean;
}

const label = (name: string) =>
  name.split('_').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join('-');

// The conditions this system's tracker can apply, by name; empty for a system
// without an integrated tracker (D&D 5e, or unknown).
export function conditionChoices(system: string | null | undefined): ConditionChoice[] {
  const lists: Record<ConditionSystem, [readonly string[], readonly string[]]> = {
    pf2e: [PF2E_VALUED, PF2E_FLAGS],
    cosmere: [COSMERE_VALUED, COSMERE_FLAGS],
  };
  const pair = system === 'pf2e' || system === 'cosmere' ? lists[system] : null;
  if (!pair) return [];
  const [valued, flags] = pair;
  return [...valued.map((name) => ({ name, label: label(name), valued: true })),
    ...flags.map((name) => ({ name, label: label(name), valued: false }))]
    .sort((a, b) => a.label.localeCompare(b.label));
}

// The value the mirror shows for a condition: 0 absent, 1 a flag, else its value.
export function conditionValue(actor: Actor, name: string): number {
  const held = actor.conditions.find((c) => c.name === name);
  if (!held) return 0;
  return typeof held.value === 'number' ? held.value : 1;
}

export type ConditionOperation = 'add' | 'decrease' | 'remove';
export type ConditionOutcome =
  | 'ok' | 'stale' | 'not-live' | 'not-in-encounter' | 'unsupported' | 'invalid' | 'blocked' | 'busy' | 'failed';

export interface ConditionResult {
  outcome: ConditionOutcome;
  value?: number; // the condition's value now (ok, stale)
}

export interface ConditionController {
  change(actor: Actor, name: string, operation: ConditionOperation, rounds?: number): Promise<ConditionResult>;
  // True while a change for this actor and condition is in flight.
  busy(actorId: string, name: string): boolean;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;
const KNOWN: ConditionOutcome[] = ['ok', 'stale', 'not-live', 'not-in-encounter', 'unsupported', 'invalid', 'blocked'];

export function createConditionController(
  boot: { campaignId: string; csrf: string },
  fetchImpl: Fetch = (input, init) => fetch(input, init),
): ConditionController {
  const inFlight = new Set<string>();
  return {
    busy: (actorId, name) => inFlight.has(`${actorId} ${name}`),
    async change(actor, name, operation, rounds = 0) {
      // Only a tracker combatant can carry a condition from here.
      if (!actor.id.startsWith('cmb_')) return { outcome: 'not-in-encounter' };
      const key = `${actor.id} ${name}`;
      if (inFlight.has(key)) return { outcome: 'busy' };
      inFlight.add(key);
      try {
        const response = await fetchImpl('/vtt/api/condition', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': boot.csrf },
          body: JSON.stringify({
            campaignId: boot.campaignId,
            actorId: actor.id,
            condition: name,
            operation,
            expectedValue: conditionValue(actor, name),
            rounds,
          }),
        });
        const body = (await response.json().catch(() => null)) as Partial<ConditionResult> | null;
        const outcome = body?.outcome;
        if (!outcome || !KNOWN.includes(outcome)) return { outcome: 'failed' };
        return { outcome, ...(typeof body?.value === 'number' ? { value: body.value } : {}) };
      } catch {
        return { outcome: 'failed' };
      } finally {
        inFlight.delete(key);
      }
    },
  };
}
