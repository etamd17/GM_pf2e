import { describe, expect, it } from 'vitest';
import { measure, ruleFor } from './measure';

const G = 64;
const at = (col: number, row: number) => ({ x: col * G + G / 2, y: row * G + G / 2 });

describe('measure', () => {
  it('picks the rule from the system', () => {
    expect(ruleFor('pf2e')).toBe('alternating');
    expect(ruleFor('cosmere')).toBe('uniform');
    expect(ruleFor('dnd5e')).toBe('uniform');
  });

  it('counts straight lines the same either way', () => {
    expect(measure([at(0, 0), at(6, 0)], G, 'uniform')).toEqual({ squares: 6, feet: 30 });
    expect(measure([at(0, 0), at(6, 0)], G, 'alternating')).toEqual({ squares: 6, feet: 30 });
  });

  it('alternates 5 and 10 ft on diagonals for PF2e, and not otherwise', () => {
    // 4 diagonal squares: 5 + 10 + 5 + 10 = 30 ft (PF2e); 20 ft uniform.
    expect(measure([at(0, 0), at(4, 4)], G, 'alternating')).toEqual({ squares: 4, feet: 30 });
    expect(measure([at(0, 0), at(4, 4)], G, 'uniform')).toEqual({ squares: 4, feet: 20 });
    // 3 diagonal and 2 straight: 5 + 10 + 5 + 10 = 30 ft, the same as /map's count.
    expect(measure([at(0, 0), at(5, 3)], G, 'alternating').feet).toBe(30);
  });

  it('carries the diagonal parity across waypoints', () => {
    // One diagonal, then one more diagonal after a waypoint: 5 then 10.
    expect(measure([at(0, 0), at(1, 1), at(2, 2)], G, 'alternating').feet).toBe(15);
  });

  it('measures by the squares holding the points, not the exact pixels', () => {
    expect(measure([{ x: 1, y: 1 }, { x: 63, y: 63 }], G, 'uniform')).toEqual({ squares: 0, feet: 0 });
    expect(measure([{ x: 1, y: 1 }, { x: 65, y: 1 }], G, 'uniform')).toEqual({ squares: 1, feet: 5 });
  });
});
