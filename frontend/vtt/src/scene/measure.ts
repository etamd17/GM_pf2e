// Distance along a ruler (beta-1). The rule is explicit, never baked into the
// renderer: the GM page picks it from the campaign's system and sends it with
// every ruler, so the TV counts exactly as the GM does.
//
// - uniform: every square is 5 ft, diagonals included (Cosmere starter rules,
//   "Movement"; D&D 5e's default rule).
// - alternating: diagonals alternate 5 and 10 ft, the first diagonal 5 (PF2e;
//   the same count /map's ruler uses).
//
// Distance is counted square to square along each segment, by the squares
// holding its end points, and summed over the segments. For alternating, the
// diagonal parity carries across segments, as it does along one move.

export type MeasureRule = 'uniform' | 'alternating';
export type GameSystem = 'pf2e' | 'cosmere' | 'dnd5e';

export const FEET_PER_SQUARE = 5;

export function ruleFor(system: GameSystem): MeasureRule {
  return system === 'pf2e' ? 'alternating' : 'uniform';
}

export interface Point {
  x: number;
  y: number;
}

export interface Measured {
  squares: number; // squares moved (each step, diagonal or not, is one)
  feet: number;
}

export function measure(points: readonly Point[], gridSize: number, rule: MeasureRule): Measured {
  let squares = 0;
  let feet = 0;
  let diagonals = 0; // diagonals so far, for the alternating count
  if (!(gridSize > 0)) return { squares: 0, feet: 0 };
  const cell = (p: Point) => [Math.floor(p.x / gridSize), Math.floor(p.y / gridSize)] as const;
  for (let i = 1; i < points.length; i += 1) {
    const [c0, r0] = cell(points[i - 1] as Point);
    const [c1, r1] = cell(points[i] as Point);
    const dx = Math.abs(c1 - c0);
    const dy = Math.abs(r1 - r0);
    const diagonal = Math.min(dx, dy);
    const straight = Math.max(dx, dy) - diagonal;
    squares += diagonal + straight;
    if (rule === 'uniform') {
      feet += (diagonal + straight) * FEET_PER_SQUARE;
    } else {
      for (let d = 0; d < diagonal; d += 1) {
        diagonals += 1;
        feet += diagonals % 2 === 0 ? 2 * FEET_PER_SQUARE : FEET_PER_SQUARE;
      }
      feet += straight * FEET_PER_SQUARE;
    }
  }
  return { squares, feet };
}

// A dragged token's reading (E-4): how far it has come by the scene's rule,
// against the feet one move covers (PF2e Speed, Cosmere movement rate). Each
// further multiple of Speed is another move (a second Stride, a Dash).
export interface MoveReading {
  feet: number;
  speed: number | null; // null: the token has no linked actor with a Speed
  moves: number; // moves needed; 0 when nothing moved or there is no Speed
  over: boolean; // farther than one move
  label: string;
}

export function moveReading(from: Point, to: Point, gridSize: number, rule: MeasureRule,
                            speed: number | null | undefined): MoveReading {
  const { feet } = measure([from, to], gridSize, rule);
  const known = typeof speed === 'number' && speed > 0 ? speed : null;
  const moves = known && feet > 0 ? Math.ceil(feet / known) : 0;
  const label = known
    ? `${feet} / ${known} ft${moves > 1 ? ` (${moves} moves)` : ''}`
    : `${feet} ft`;
  return { feet, speed: known, moves, over: known !== null && feet > known, label };
}
