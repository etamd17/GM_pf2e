// Area effects (E-5). Published by Claude for Codex's template panel
// (handoff 087; /map Round 6 decisions). The template proposes its targets;
// the GM reviews and edits the list; "Apply" sends the reviewed list to the
// server, which runs the tracker's own HP engine on each (or, for a save,
// asks the players in the list to roll). The results arrive back through the
// live actor mirror like any tracker change.
//
//   const proposed = templateTargets(template, tokens, gridSize);   // auto-select
//   // ... the GM adds and removes (only linked tracker combatants can be applied to)
//   const r = await areas.apply({ action: 'damage', actorIds, amount: 12, damageType: 'fire',
//                                 encounterToken: currentEncounterToken(entities, null) });
//
// Each apply() call is a new request unless a requestId is passed: retry a
// failed send with the SAME requestId and the server returns what it already
// did instead of applying again. 'ok' with 'incomplete' means the run stopped
// at a target the tracker refused ('failed', never retried; check it in the
// tracker); the same requestId resumes with the targets not reached yet, if
// the encounter is still the same one. Receipts are an in-memory window on
// the server, not kept across a restart.

import { templateContains, type TemplateGeometry } from '../../scene/templates';
import { newRequestId } from './bestiary';

// Targets: a token is caught when the area overlaps ANY square of its space
// (PF2e; /map's rule), so a burst clipping one corner of a Large creature
// catches it. Tested at each square of the footprint: its centre and its
// corners pulled in a little, so an area that only touches a square's edge
// does not catch the creature next to it.
const INSET = 0.1; // of a square

export interface TargetToken {
  id: string;
  x: number; // centre, scene px
  y: number;
  size: number; // squares
  actorId?: string | null;
}

export interface ProposedTarget {
  tokenId: string;
  actorId: string | null; // a tracker combatant (cmb_) can be applied to; anything else cannot
}

export function tokenCovered(t: TemplateGeometry, token: TargetToken, gridSize: number): boolean {
  const squares = Math.max(1, Math.round(token.size));
  const side = token.size * gridSize; // a Tiny token (0.5) is one sub-square
  const cell = side / squares;
  const left = token.x - side / 2;
  const top = token.y - side / 2;
  const inset = Math.min(cell, gridSize) * INSET;
  for (let col = 0; col < squares; col += 1) {
    for (let row = 0; row < squares; row += 1) {
      const x0 = left + col * cell;
      const y0 = top + row * cell;
      const probes = [
        { x: x0 + cell / 2, y: y0 + cell / 2 },
        { x: x0 + inset, y: y0 + inset }, { x: x0 + cell - inset, y: y0 + inset },
        { x: x0 + inset, y: y0 + cell - inset }, { x: x0 + cell - inset, y: y0 + cell - inset },
      ];
      if (probes.some((p) => templateContains(t, gridSize, p))) return true;
    }
  }
  return false;
}

export function templateTargets(t: TemplateGeometry, tokens: Iterable<TargetToken>, gridSize: number): ProposedTarget[] {
  const out: ProposedTarget[] = [];
  for (const token of tokens) {
    if (!tokenCovered(t, token, gridSize)) continue;
    out.push({ tokenId: token.id, actorId: typeof token.actorId === 'string' && token.actorId.startsWith('cmb_') ? token.actorId : null });
  }
  return out;
}

// Each system's damage types, matching the server's lists (checked by
// tests/vtt/test_e5_area.py). Saves are PF2e's only; Cosmere has none.
const PF2E_DAMAGE = [
  'untyped', 'bludgeoning', 'piercing', 'slashing', 'fire', 'cold', 'electricity', 'acid', 'sonic', 'vitality',
  'void', 'force', 'spirit', 'mental', 'poison', 'bleed',
] as const;
const COSMERE_DAMAGE = ['impact', 'keen', 'energy', 'spirit', 'vital'] as const;
export const PF2E_SAVES = ['fortitude', 'reflex', 'will'] as const;

export function damageTypes(system: string | null | undefined): readonly string[] {
  return system === 'pf2e' ? PF2E_DAMAGE : system === 'cosmere' ? COSMERE_DAMAGE : [];
}

export function savesFor(system: string | null | undefined): readonly string[] {
  return system === 'pf2e' ? PF2E_SAVES : [];
}

export type AreaRequest =
  | { action: 'damage'; actorIds: string[]; amount: number; damageType: string; nonlethal?: boolean }
  | { action: 'heal'; actorIds: string[]; amount: number }
  | { action: 'save'; actorIds: string[]; save: string; dc: number; secret?: boolean };

export type AreaOutcome = 'ok' | 'stale' | 'not-live' | 'unsupported' | 'invalid' | 'blocked'
  | 'no-encounter-token' | 'no-targets' | 'busy' | 'failed';

export interface AreaTargetResult {
  actorId: string;
  outcome: 'applied' | 'failed' | 'gone' | 'requested' | 'gm-rolls';
  net?: number;
  defeated?: boolean;
  reason?: string;
}

export interface AreaResult {
  outcome: AreaOutcome;
  results?: AreaTargetResult[];
  incomplete?: string;
  missing?: string[]; // on 'stale': targets no longer in the encounter
  encounterToken?: string; // on 'stale': the current one
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;
const KNOWN: AreaOutcome[] = ['ok', 'stale', 'not-live', 'unsupported', 'invalid', 'blocked'];
const TOKEN = /^[A-Za-z0-9-]{1,64}$/;

export interface AreaApplier {
  apply(request: AreaRequest & { encounterToken: string | null; requestId?: string }): Promise<AreaResult>;
  readonly busy: boolean;
}

export function createAreaApplier(
  boot: { campaignId: string; csrf: string },
  fetchImpl: Fetch = (input, init) => fetch(input, init),
): AreaApplier {
  let inFlight = false;
  return {
    get busy() {
      return inFlight;
    },
    async apply({ encounterToken, requestId = newRequestId(), ...request }) {
      if (!encounterToken || !TOKEN.test(encounterToken)) return { outcome: 'no-encounter-token' };
      const actorIds = [...new Set(request.actorIds.filter((a) => a.startsWith('cmb_')))];
      if (actorIds.length === 0) return { outcome: 'no-targets' };
      if (inFlight) return { outcome: 'busy' };
      inFlight = true;
      try {
        const response = await fetchImpl('/vtt/api/area-effect', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': boot.csrf },
          body: JSON.stringify({ campaignId: boot.campaignId, requestId, encounterToken, ...request, actorIds }),
        });
        const body = (await response.json().catch(() => null)) as (AreaResult & { outcome?: unknown }) | null;
        const outcome = body?.outcome as AreaOutcome | undefined;
        if (!outcome || !KNOWN.includes(outcome)) return { outcome: 'failed' };
        return {
          outcome,
          ...(Array.isArray(body?.results) ? { results: body.results } : {}),
          ...(typeof body?.incomplete === 'string' ? { incomplete: body.incomplete } : {}),
          ...(Array.isArray(body?.missing) ? { missing: body.missing } : {}),
          ...(typeof body?.encounterToken === 'string' ? { encounterToken: body.encounterToken } : {}),
        };
      } catch {
        return { outcome: 'failed' };
      } finally {
        inFlight = false;
      }
    },
  };
}
