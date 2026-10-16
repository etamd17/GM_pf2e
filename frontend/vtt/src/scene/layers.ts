// Fixed z-order of the map's scene graph, bottom to top. Each name becomes one
// Pixi Container with its own invalidation; see design/client-architecture-
// proposal.md section 2.
export const LAYER_ORDER = [
  'background',
  'grid',
  'tiles',
  'templates',
  'walls',
  'tokens',
  'lighting',
  'fog',
  'measure',
  'effects',
  'overlay',
] as const;

export type LayerName = (typeof LAYER_ORDER)[number];

export type Surface = 'gm' | 'table';

// Layers a surface is allowed to build at all. The table never builds the wall
// layer or the authoring overlay. Its projection must not contain walls anyway;
// leaving the layer out means a projection bug cannot draw them either.
const TABLE_EXCLUDED: ReadonlySet<LayerName> = new Set(['walls', 'overlay']);

export function layersFor(surface: Surface): LayerName[] {
  if (surface === 'gm') return [...LAYER_ORDER];
  return LAYER_ORDER.filter((name) => !TABLE_EXCLUDED.has(name));
}
