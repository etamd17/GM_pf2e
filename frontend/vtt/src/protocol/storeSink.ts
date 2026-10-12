import type { RenderScene } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { useProjection } from '../state/projection';
import type { EntityKey } from './frames';
import type { SessionSink } from './session';

// Connects a ProjectionSession to the app's stores. `adapt` maps the projected
// entities to the renderer's model. It is the single adapter point between
// wire entities and RenderScene, and it waits for the generated entity types.
export function storeSink<E extends EntityKey>(
  adapt: (entities: ReadonlyMap<string, E>) => RenderScene,
  transport: { requestResubscribe(reason: 'sequence-gap' | 'state-lost'): void; stopRetrying(): void },
): SessionSink<E> {
  return {
    setStatus: (status) => useConnection.getState().setStatus(status),
    openProjection: () => useProjection.getState().open(),
    writeProjection: (epoch, entities) => {
      useProjection.getState().setScene(epoch, adapt(entities));
    },
    clearProjection: () => useProjection.getState().clear(),
    sealProjection: () => useProjection.getState().seal(),
    requestResubscribe: (reason) => transport.requestResubscribe(reason),
    stopRetrying: () => transport.stopRetrying(),
  };
}
