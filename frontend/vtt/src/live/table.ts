import * as table from '../protocol/generated/table.validators.mjs';
import type { TableEntity } from '../protocol/frames';
import { ProjectionSession } from '../protocol/session';
import { SocketIoTransport } from '../protocol/socketTransport';
import { storeSink } from '../protocol/storeSink';
import { adaptEntities, newAdaptCache } from '../scene/adapt';
import { browserClock } from './sessionClock';

// The table's live wiring. It imports only the table surface's validators; a
// table cannot choose a campaign or scene and sends no commands, so its
// subscribe body is just the version and audience.
export function startTable(): SocketIoTransport<TableEntity> {
  const cache = newAdaptCache();
  const sinkTransport = {
    requestResubscribe: (reason: 'sequence-gap' | 'state-lost') => transport.requestResubscribe(reason),
    stopRetrying: () => transport.stopRetrying(),
  };
  const session = new ProjectionSession<TableEntity>(
    'table',
    storeSink<TableEntity>(
      // The table reads images by asset id alone: its grant decides the campaign.
      (entities) => adaptEntities(entities as never, cache, (id) => `/vtt/table-media/${encodeURIComponent(id)}`),
      sinkTransport,
    ),
    browserClock,
  );
  const transport: SocketIoTransport<TableEntity> = new SocketIoTransport<TableEntity>({
    session,
    validators: {
      frame: (d) => table.validateTable(d),
      resync: (d) => table.validateResyncRequired(d),
      bootstrap: (d) => table.validateBootstrapRejection(d),
    },
    subscribeMessage: () => ({ schemaVersion: 1, audience: 'table' }),
  });
  transport.connect();
  return transport;
}
