import { describe, expect, it } from 'vitest';
import { adaptEntities } from './adapt';
import type { RenderWall } from './renderModel';
import { WALL_STYLES, doorStateOf, wallPlan, wallSegments, type DoorState } from './walls';

// Handoff 043 finding 3: every door state reached the renderer as the same
// 'door', so changing one in the inspector left the map unchanged.

const wall = (doorState: DoorState, x2 = 128): RenderWall => ({
  id: `w_${doorState}`,
  x1: 0,
  y1: 0,
  x2,
  y2: 0,
  kind: doorState === 'none' ? 'wall' : 'door',
  doorState,
});

describe('door states', () => {
  it('survive the adapter, one render wall per state', () => {
    const adapt = (doorState: string) =>
      adaptEntities(
        new Map([
          ['wall\u0000d', { kind: 'wall', id: 'd', version: doorState, x1: 0, y1: 0, x2: 64, y2: 0, doorState }],
        ]),
      ).walls[0];
    const states = ['none', 'open', 'closed', 'locked', 'secret'] as const;
    expect(states.map((s) => adapt(s)?.doorState)).toEqual([...states]);
    expect(adapt('open')).not.toEqual(adapt('closed'));
  });

  it('each state is drawn differently', () => {
    const states: DoorState[] = ['none', 'open', 'closed', 'locked', 'secret'];
    const looks = states.map((s) => JSON.stringify([WALL_STYLES[s], wallSegments(wall(s))]));
    expect(new Set(looks).size).toBe(states.length);
  });

  it('an open door shows only its jambs; a secret door is dashed and bounded', () => {
    expect(wallSegments(wall('open'))).toEqual([
      [0, 0, 32, 0],
      [96, 0, 128, 0],
    ]);
    const dashes = wallSegments(wall('secret', 170));
    expect(dashes.length).toBe(10);
    expect(dashes.every(([x1, , x2]) => x2 > x1 && x2 <= 170)).toBe(true);
    expect(wallSegments({ ...wall('secret'), x2: 0 })).toEqual([]);
  });

  it('the plan groups by state, doors drawn over walls', () => {
    const plan = wallPlan([wall('locked'), wall('none'), wall('open')]);
    expect(plan.map((g) => g.state)).toEqual(['none', 'open', 'locked']);
  });

  it('a wall without a state (an older render model) still draws', () => {
    expect(doorStateOf({ x1: 0, y1: 0, x2: 1, y2: 0, kind: 'wall' })).toBe('none');
    expect(doorStateOf({ x1: 0, y1: 0, x2: 1, y2: 0, kind: 'door' })).toBe('closed');
  });
});
