import * as gm from '../protocol/generated/gm.validators.mjs';
import { CommandQueue, type CommandDraft } from '../protocol/commands';
import type { GmEntity } from '../protocol/frames';
import { commandRulesHold } from '../protocol/semantics';
import { ProjectionSession } from '../protocol/session';
import { SocketIoTransport } from '../protocol/socketTransport';
import { storeSink } from '../protocol/storeSink';
import { adaptEntities, newAdaptCache } from '../scene/adapt';
import { useCommands } from '../state/commands';
import { useConnection } from '../state/connection';
import { useEntities } from '../state/entities';
import type { TokenOverlay } from '../app/gmCommands';
import { UndoManager } from '../app/undo';
import { useOverlays } from '../state/overlays';
import type { GmBoot } from './boot';
import { browserClock } from './sessionClock';

// The GM's live wiring: one session, one Socket.IO transport, one command
// queue. The renderer gets the adapted scene; GM tools read raw entities
// (with version tokens) from useEntities and issue commands through `issue`.

export interface GmLive {
  transport: SocketIoTransport<GmEntity, OverlayData>;
  queue: CommandQueue<GmEntity, OverlayData>;
  undo: UndoManager;
  issue(draft: Omit<CommandDraft, 'campaignId' | 'sceneId'> & { sceneId?: string }, overlay?: OverlayData): string | null;
  boot: GmBoot;
}

// What a pending command looks like on screen until its frame arrives.
export type OverlayData = TokenOverlay | null;

// One saved slot per tab. The envelope names who saved it; a reload by anyone
// else (another account signing in on this tab) sends nothing, because the
// server's receipts are per principal and the commands would run anew as the
// new member (handoff 043).
export const PARKED_KEY = 'vtt.parked';

interface ParkedEnvelope {
  v: 1;
  memberId: string;
  campaignId: string;
  commands: Array<Record<string, unknown>>;
}

export function parkEnvelope(boot: GmBoot, commands: Array<Record<string, unknown>>): ParkedEnvelope {
  return { v: 1, memberId: boot.memberId, campaignId: boot.campaignId, commands };
}

export function adoptableCommands(boot: GmBoot, saved: string | null): Array<Record<string, unknown>> {
  if (!saved) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(saved);
  } catch {
    return [];
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return []; // unbound legacy form
  const envelope = parsed as Partial<ParkedEnvelope>;
  if (envelope.v !== 1 || envelope.memberId !== boot.memberId || envelope.campaignId !== boot.campaignId) return [];
  if (!Array.isArray(envelope.commands)) return [];
  return envelope.commands.filter(
    (c): c is Record<string, unknown> =>
      c !== null && typeof c === 'object' && (c as { campaignId?: unknown }).campaignId === boot.campaignId,
  );
}

export function startGm(boot: GmBoot): GmLive {
  const cache = newAdaptCache();
  const sinkTransport = {
    requestResubscribe: (reason: 'sequence-gap' | 'state-lost') => transport.requestResubscribe(reason),
    stopRetrying: () => transport.stopRetrying(),
  };
  const sink = storeSink<GmEntity>((entities) => {
    useEntities.getState().set(entities);
    return adaptEntities(entities as never, cache);
  }, sinkTransport);
  const baseClear = sink.clearProjection;
  const baseSeal = sink.sealProjection;
  sink.clearProjection = () => {
    useEntities.getState().clear();
    baseClear();
  };
  sink.sealProjection = () => {
    useEntities.getState().clear();
    baseSeal();
  };
  const parkedKey = PARKED_KEY;
  const baseStatus = sink.setStatus;
  sink.setStatus = (status) => {
    // The session ended: keep what has not been confirmed, so the reload after
    // signing in again retries it under the same ids.
    if (status === 'session-ended') park(queue.unresolved());
    baseStatus(status);
    if (status === 'live') queue.resume();     // resend commands waiting for a socket
  };
  function park(commands: Array<Record<string, unknown>>) {
    if (commands.length === 0) return;
    try {
      sessionStorage.setItem(parkedKey, JSON.stringify(parkEnvelope(boot, commands)));
    } catch {
      /* storage unavailable: nothing to retry after the reload */
    }
  }
  // History waits for each command to land in the projection: its overlay
  // ends only with the correlated frame or a replacement snapshot.
  const undo = new UndoManager(
    100,
    (state) => useCommands.getState().setUndo(state),
    (commandId) => useOverlays.getState().overlays.has(commandId),
  );
  const session = new ProjectionSession<GmEntity, OverlayData>('gm', sink, browserClock, (overlays) => {
    useOverlays.getState().set(overlays);
    undo.overlaysChanged();
  });
  const transport: SocketIoTransport<GmEntity, OverlayData> = new SocketIoTransport<GmEntity, OverlayData>({
    session,
    csrf: boot.csrf,
    validators: {
      frame: (d) => gm.validateGm(d),
      resync: (d) => gm.validateResyncRequired(d),
      bootstrap: (d) => gm.validateBootstrapRejection(d),
      ack: (d) => gm.validateAcknowledgement(d),
    },
    subscribeMessage: () => ({ schemaVersion: 1, audience: 'gm', campaignId: boot.campaignId, sceneId: boot.sceneId }),
    onAck: (ack) => queue.onAck(ack),
  });
  const queue: CommandQueue<GmEntity, OverlayData> = new CommandQueue<GmEntity, OverlayData>({
    session,
    send: (command) => transport.sendCommand(command),
    validate: (command) => gm.validateCommand(command) && commandRulesHold(command),
    onOutcome: (outcome) => {
      if (outcome.kind === 'applied') undo.applied(outcome.commandId);
      else if (outcome.kind === 'rejected') undo.rejected(outcome.commandId);
      if (outcome.kind === 'reauthenticate') park(queue.unresolved());
      useCommands.getState().notify(outcome);
    },
    onPendingChange: (pending) => useCommands.getState().setPending(pending),
  });
  // Commands saved when the last session ended on this page: resent only by
  // the same member in the same campaign. Anything else is discarded unsent.
  try {
    const saved = sessionStorage.getItem(parkedKey);
    sessionStorage.removeItem(parkedKey);
    for (const command of adoptableCommands(boot, saved)) queue.adopt(command);
  } catch {
    /* nothing saved, or storage unavailable */
  }
  transport.connect();
  useConnection.getState().setStatus('connecting');
  return {
    transport,
    queue,
    undo,
    boot,
    issue: (draft, overlay = null) =>
      queue.issue({ ...draft, campaignId: boot.campaignId, sceneId: draft.sceneId ?? boot.sceneId ?? '' }, overlay),
  };
}
