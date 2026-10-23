import type { GmEntity } from '../../protocol/frames';

// E-9: what the table shows, from the live GM entities. Every GM snapshot
// carries the campaign-wide `table` entity and a patch upserts it whenever the
// table moves, so this replaces polling the scene list for "On TV".
// undefined: no snapshot yet (unknown); null: the table shows nothing.
export function onTvSceneId(entities: Iterable<GmEntity>): string | null | undefined {
  for (const e of entities) if (e.kind === 'table') return e.sceneId;
  return undefined;
}
