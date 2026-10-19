// Bestiary to tracker (E-3). Published by Claude for Codex's Creatures tab
// (handoff 087). Search the campaign's bestiary (PF2e monsters, or Cosmere
// adversaries; never the other system's), add one to the live encounter
// through the tracker's own add, and get back the linked actor to place a
// token for, all as one GM action:
//
//   const encounterToken = currentEncounterToken(entities, search.encounterToken);
//   const added = await bestiary.add(entry, { encounterToken });
//   if (added.outcome === 'ok') send(placeCreature(asCreature(added.creature), point, scene).draft)
//
// The request is bound to the encounter the page showed (its encounter token,
// from the actor mirror; from the search answer when the mirror has no actor
// at all). If the tracker's roster was cleared, loaded over or replaced since,
// the answer is 'stale' and nothing is added.
//
// The new creature's HP and turn arrive through the live actor mirror like
// any tracker change; a token linked to it shows them as soon as they do.
// Each add() call is a new request (a new creature) unless a requestId is
// passed: retry a failed send with the SAME requestId and the server returns
// the first result instead of adding again (an in-memory window of the last
// 256 adds, so not across a server restart). 'ok' with 'incomplete' means the
// creature was added but a later tracker step failed; it is still added, do
// not add it again. If placing the token fails after an 'ok', place the
// creature again from the Creatures list; never re-add it.

import type { GmEntity } from '../../protocol/frames';
import type { Creature } from './creatures';

export interface BestiaryEntry {
  ref: string; // server-issued; echo it back unchanged
  name: string;
  level: number; // PF2e level, or Cosmere tier
  size: number; // token size in squares
}

export type BestiaryOutcome =
  | 'ok' | 'stale' | 'not-live' | 'not-found' | 'unsupported' | 'blocked' | 'no-encounter-token' | 'busy' | 'failed';

export interface AddedCreature {
  actorId: string;
  name: string;
  size: number;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;
const defaultFetch: Fetch = (input, init) => fetch(input, init);
const KNOWN: BestiaryOutcome[] = ['ok', 'stale', 'not-live', 'not-found', 'unsupported', 'blocked'];
const TOKEN = /^[A-Za-z0-9-]{1,64}$/;

// The encounter the page shows: the token every mirrored actor carries, or
// the search answer's when the mirror has no actor at all.
export function currentEncounterToken(entities: Iterable<GmEntity>, fromSearch?: string | null): string | null {
  for (const e of entities) {
    if (e.kind === 'actor' && typeof e.encounterToken === 'string' && TOKEN.test(e.encounterToken)) {
      return e.encounterToken;
    }
  }
  return typeof fromSearch === 'string' && TOKEN.test(fromSearch) ? fromSearch : null;
}

export async function searchBestiary(
  boot: { campaignId: string },
  query: string,
  fetchImpl: Fetch = defaultFetch,
): Promise<{ outcome: BestiaryOutcome; results: BestiaryEntry[]; encounterToken?: string }> {
  const q = query.trim().slice(0, 80);
  if (q.length < 2) return { outcome: 'ok', results: [] };
  try {
    const params = new URLSearchParams({ campaignId: boot.campaignId, q });
    const response = await fetchImpl(`/vtt/api/bestiary?${params}`, { method: 'GET', credentials: 'same-origin' });
    const body = (await response.json().catch(() => null)) as
      | { outcome?: BestiaryOutcome; results?: unknown; encounterToken?: unknown } | null;
    const outcome = body?.outcome;
    if (!outcome || !KNOWN.includes(outcome)) return { outcome: 'failed', results: [] };
    const results = Array.isArray(body?.results) ? body.results.filter(isEntry) : [];
    const token = body?.encounterToken;
    return { outcome, results, ...(typeof token === 'string' && TOKEN.test(token) ? { encounterToken: token } : {}) };
  } catch {
    return { outcome: 'failed', results: [] };
  }
}

function isEntry(value: unknown): value is BestiaryEntry {
  const v = value as Partial<BestiaryEntry> | null;
  return !!v && typeof v.ref === 'string' && typeof v.name === 'string' && typeof v.level === 'number'
    && typeof v.size === 'number';
}

export function newRequestId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return 'req-' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export interface AddResult {
  outcome: BestiaryOutcome;
  creature?: AddedCreature;
  incomplete?: string; // added, but a later tracker step failed (named)
  encounterToken?: string; // the current one, on 'stale'
}

export interface BestiaryAdder {
  add(entry: BestiaryEntry, options: { encounterToken: string | null; requestId?: string }): Promise<AddResult>;
  // True while an add of this entry is in flight.
  busy(ref: string): boolean;
}

export function createBestiaryAdder(
  boot: { campaignId: string; csrf: string },
  fetchImpl: Fetch = defaultFetch,
): BestiaryAdder {
  const inFlight = new Set<string>();
  return {
    busy: (ref) => inFlight.has(ref),
    async add(entry, { encounterToken, requestId = newRequestId() }) {
      if (!encounterToken || !TOKEN.test(encounterToken)) return { outcome: 'no-encounter-token' };
      if (inFlight.has(entry.ref)) return { outcome: 'busy' };
      inFlight.add(entry.ref);
      try {
        const response = await fetchImpl('/vtt/api/bestiary/add', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': boot.csrf },
          body: JSON.stringify({ campaignId: boot.campaignId, ref: entry.ref, requestId, encounterToken }),
        });
        const body = (await response.json().catch(() => null)) as
          | ({ outcome?: BestiaryOutcome; incomplete?: unknown; encounterToken?: unknown } & Partial<AddedCreature>)
          | null;
        const outcome = body?.outcome;
        if (!outcome || !KNOWN.includes(outcome)) return { outcome: 'failed' };
        if (outcome === 'stale') {
          const token = body?.encounterToken;
          return { outcome, ...(typeof token === 'string' && TOKEN.test(token) ? { encounterToken: token } : {}) };
        }
        if (outcome !== 'ok') return { outcome };
        if (typeof body?.actorId !== 'string' || !body.actorId.startsWith('cmb_') || typeof body.name !== 'string'
            || typeof body.size !== 'number') {
          return { outcome: 'failed' };
        }
        return {
          outcome,
          creature: { actorId: body.actorId, name: body.name, size: body.size },
          ...(typeof body.incomplete === 'string' ? { incomplete: body.incomplete } : {}),
        };
      } catch {
        return { outcome: 'failed' };
      } finally {
        inFlight.delete(entry.ref);
      }
    },
  };
}

// The added creature as a Creatures-list entry, for placeCreature, before the
// mirror has delivered it.
export function asCreature(added: AddedCreature): Creature {
  return {
    actorId: added.actorId, name: added.name, pc: false, hp: 0, maxHp: 0, size: added.size, sheetUrl: '/tracker',
    hiddenInTracker: false, active: false, onScene: [],
  };
}
