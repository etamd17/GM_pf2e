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
import { useOverlays } from '../state/overlays';
import type { GmBoot } from './boot';
import { browserClock } from './sessionClock';

// The GM's live wiring: one session, one Socket.IO transport, one command
// queue. The renderer gets the adapted scene; GM tools read raw entities
// (with version tokens) from useEntities and issue commands through `issue`.

export interface GmLive {
  transport: SocketIoTransport<GmEntity, OverlayData>;
  queue: CommandQueue<GmEntity, OverlayData>;
  issue(draft: Omit<CommandDraft, 'campaignId' | 'sceneId'> & { sceneId?: string }, overlay?: OverlayData): string | null;
  boot: GmBoot;
}

// What a pending command looks like on screen until its frame arrives.
export type OverlayData = TokenOverlay | null;

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
  const parkedKey = `vtt.parked.${boot.campaignId}`;
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
      sessionStorage.setItem(parkedKey, JSON.stringify(commands));
    } catch {
      /* storage unavailable: nothing to retry after the reload */
    }
  }
  const session = new ProjectionSession<GmEntity, OverlayData>('gm', sink, browserClock, (overlays) =>
    useOverlays.getState().set(overlays),
  );
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
      if (outcome.kind === 'reauthenticate') park(queue.unresolved());
      useCommands.getState().notify(outcome);
    },
    onPendingChange: (pending) => useCommands.getState().setPending(pending),
  });
  // Commands saved when the last session ended on this page.
  try {
    const saved = sessionStorage.getItem(parkedKey);
    sessionStorage.removeItem(parkedKey);
    const parsed: unknown = saved ? JSON.parse(saved) : [];
    if (Array.isArray(parsed)) {
      for (const command of parsed) if (command && typeof command === 'object') queue.adopt(command as Record<string, unknown>);
    }
  } catch {
    /* nothing saved, or storage unavailable */
  }
  transport.connect();
  useConnection.getState().setStatus('connecting');
  return {
    transport,
    queue,
    boot,
    issue: (draft, overlay = null) =>
      queue.issue({ ...draft, campaignId: boot.campaignId, sceneId: draft.sceneId ?? boot.sceneId ?? '' }, overlay),
  };
}
