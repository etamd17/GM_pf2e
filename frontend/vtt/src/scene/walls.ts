import type { RenderWall } from './renderModel';

// How each wall and door state is drawn (GM only: the table never receives
// walls). Pure, so the plan is tested without Pixi; SceneRuntime.drawWalls
// strokes exactly these segments with these styles.
//
// - wall:   a solid light line;
// - closed: a solid gilt bar across the opening;
// - open:   only the two jambs (the outer quarters), so the gap reads as open;
// - locked: a solid red bar;
// - secret: a dashed violet line, so the GM knows the table sees a plain wall.

export type DoorState = 'none' | 'open' | 'closed' | 'locked' | 'secret';

export interface WallStyle {
  width: number;
  color: number;
  alpha: number;
  cap: 'round' | 'butt';
}

export const WALL_STYLES: Record<DoorState, WallStyle> = {
  none: { width: 4, color: 0xede4cf, alpha: 0.85, cap: 'round' },
  closed: { width: 7, color: 0xc9a34e, alpha: 0.95, cap: 'butt' },
  open: { width: 7, color: 0xc9a34e, alpha: 0.55, cap: 'butt' },
  locked: { width: 7, color: 0xa83a3a, alpha: 0.95, cap: 'butt' },
  secret: { width: 4, color: 0x8a6ab8, alpha: 0.9, cap: 'butt' },
};

export type Segment = [number, number, number, number];

const DASH = 10;
const GAP = 7;

function lerp(w: RenderWall, t: number): [number, number] {
  return [w.x1 + (w.x2 - w.x1) * t, w.y1 + (w.y2 - w.y1) * t];
}

export function doorStateOf(w: RenderWall): DoorState {
  return w.doorState ?? (w.kind === 'door' ? 'closed' : 'none');
}

export function wallSegments(w: RenderWall): Segment[] {
  const state = doorStateOf(w);
  if (state === 'open') {
    const [ax, ay] = lerp(w, 0.25);
    const [bx, by] = lerp(w, 0.75);
    return [
      [w.x1, w.y1, ax, ay],
      [bx, by, w.x2, w.y2],
    ];
  }
  if (state === 'secret') {
    const length = Math.hypot(w.x2 - w.x1, w.y2 - w.y1);
    if (length === 0) return [];
    const out: Segment[] = [];
    // Bounded: at most length / (DASH + GAP) + 1 dashes, and length is capped
    // by the scene bounds (16384 px).
    for (let d = 0; d < length; d += DASH + GAP) {
      const [ax, ay] = lerp(w, d / length);
      const [bx, by] = lerp(w, Math.min(1, (d + DASH) / length));
      out.push([ax, ay, bx, by]);
    }
    return out;
  }
  return [[w.x1, w.y1, w.x2, w.y2]];
}

// Segments grouped by state, in a fixed drawing order (doors over walls).
export function wallPlan(walls: readonly RenderWall[]): Array<{ state: DoorState; segments: Segment[] }> {
  const order: DoorState[] = ['none', 'secret', 'open', 'closed', 'locked'];
  const groups = new Map<DoorState, Segment[]>(order.map((s) => [s, []]));
  for (const w of walls) groups.get(doorStateOf(w))?.push(...wallSegments(w));
  return order.map((state) => ({ state, segments: groups.get(state) ?? [] })).filter((g) => g.segments.length > 0);
}
