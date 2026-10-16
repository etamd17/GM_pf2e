import { create } from 'zustand';
import { entityKey, type GmEntity } from '../protocol/frames';

// The GM's projected entities as the server sent them, version tokens included.
// GM tools read versions from here to fill a command's expected preconditions;
// the renderer never sees this store (it gets the adapted RenderScene).
// The table bundle does not import this module.

interface EntitiesState {
  entities: ReadonlyMap<string, GmEntity>;
  set(entities: ReadonlyMap<string, GmEntity>): void;
  clear(): void;
}

export const useEntities = create<EntitiesState>()((set) => ({
  entities: new Map(),
  set: (entities) => set({ entities }),
  clear: () => set({ entities: new Map() }),
}));

export function entityOf(kind: string, id: string): GmEntity | undefined {
  return useEntities.getState().entities.get(entityKey({ kind, id }));
}
