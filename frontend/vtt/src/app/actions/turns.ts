// Turn controls (E-1). Published by Claude for Codex's turn strip (handoff
// 087). The tracker stays the authority: a turn change goes to the server's
// /vtt/api/turn, which runs the tracker's own next/previous turn, and the
// result arrives back through the live actor mirror like any tracker change.
//
// The request names what the page showed: the tracker's turn token (never
// reused: it changes on every turn change and encounter reload), the
// combatant whose turn it is and the round. The server refuses anything else
// ('stale', with the current values), so a double click, a replay after Next
// and Previous, or a page behind the tracker never moves a turn twice.
// While one request is in flight a second is not sent ('busy').
//
// "Previous turn" moves the turn marker back; it does not undo start- or
// end-of-turn effects the tracker already applied (say so in the UI).

import type { GmEntity } from '../../protocol/frames';

type Actor = Extract<GmEntity, { kind: 'actor' }>;

export type TurnOutcome = 'ok' | 'stale' | 'not-live' | 'no-encounter' | 'unsupported' | 'blocked' | 'busy' | 'failed';

export interface TurnResult {
  outcome: TurnOutcome;
  actorId?: string; // the combatant whose turn it now is (ok, stale)
  round?: number;
  turnToken?: string;
}

// The combatant whose turn it is, and the round, from the mirror; null when no
// encounter is running.
export function currentTurn(
  entities: Iterable<GmEntity>,
): { actorId: string; name: string; round: number; turnToken: string } | null {
  for (const e of entities) {
    if (e.kind !== 'actor') continue;
    const actor = e as Actor;
    if (actor.active && actor.id.startsWith('cmb_') && typeof actor.round === 'number'
        && typeof actor.turnToken === 'string') {
      return { actorId: actor.id, name: actor.name, round: actor.round, turnToken: actor.turnToken };
    }
  }
  return null;
}

export interface TurnController {
  change(direction: 'next' | 'prev', entities: Iterable<GmEntity>): Promise<TurnResult>;
  readonly busy: boolean;
}

type Fetch = (input: string, init: RequestInit) => Promise<Response>;

export function createTurnController(
  boot: { campaignId: string; csrf: string },
  fetchImpl: Fetch = (input, init) => fetch(input, init),
): TurnController {
  let inFlight = false;
  return {
    get busy() {
      return inFlight;
    },
    async change(direction, entities) {
      if (inFlight) return { outcome: 'busy' };
      const turn = currentTurn(entities);
      if (!turn) return { outcome: 'no-encounter' };
      inFlight = true;
      try {
        const response = await fetchImpl('/vtt/api/turn', {
          method: 'POST',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': boot.csrf },
          body: JSON.stringify({
            campaignId: boot.campaignId,
            direction,
            expectedActorId: turn.actorId,
            expectedRound: turn.round,
            turnToken: turn.turnToken,
          }),
        });
        const body = (await response.json().catch(() => null)) as Partial<TurnResult> | null;
        const outcome = body?.outcome;
        const known: TurnOutcome[] = ['ok', 'stale', 'not-live', 'no-encounter', 'unsupported', 'blocked'];
        if (!outcome || !known.includes(outcome)) return { outcome: 'failed' };
        return {
          outcome,
          ...(typeof body?.actorId === 'string' ? { actorId: body.actorId } : {}),
          ...(typeof body?.round === 'number' ? { round: body.round } : {}),
          ...(typeof body?.turnToken === 'string' ? { turnToken: body.turnToken } : {}),
        };
      } catch {
        return { outcome: 'failed' };
      } finally {
        inFlight = false;
      }
    },
  };
}
