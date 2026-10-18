import { describe, expect, it } from 'vitest';
import type { RelayBody } from '../../state/transients';
import { createRuler, ping, RULER_LINGER_MS, RULER_SEND_MS, type TimerApi } from './transients';

function fakeTimers() {
  let now = 1000;
  let next = 1;
  const queue = new Map<number, { at: number; fn: () => void }>();
  const api: TimerApi = {
    now: () => now,
    setTimeout: (fn, ms) => {
      const id = next++;
      queue.set(id, { at: now + ms, fn });
      return id;
    },
    clearTimeout: (id) => {
      queue.delete(id);
    },
  };
  const advance = (ms: number) => {
    now += ms;
    for (const [id, t] of [...queue].sort((a, b) => a[1].at - b[1].at)) {
      if (t.at <= now) {
        queue.delete(id);
        t.fn();
      }
    }
  };
  return { api, advance };
}

describe('ping', () => {
  it('sends one ping at a whole-pixel, non-negative point', () => {
    const sent: RelayBody[] = [];
    ping((b) => (sent.push(b), true), { x: 10.6, y: -3 });
    expect(sent).toEqual([{ type: 'ping', x: 11, y: 0 }]);
  });
});

describe('ruler', () => {
  it('sends with the system rule, throttled, and always sends the final position', () => {
    const sent: RelayBody[] = [];
    const t = fakeTimers();
    const ruler = createRuler((b) => (sent.push(b), true), 'pf2e', t.api);
    ruler.update([{ x: 0, y: 0 }, { x: 10, y: 0 }]);
    ruler.update([{ x: 0, y: 0 }, { x: 20, y: 0 }]);        // inside the window: held back
    ruler.update([{ x: 0, y: 0 }, { x: 30, y: 0 }]);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toEqual({ type: 'ruler', rule: 'alternating', points: [{ x: 0, y: 0 }, { x: 10, y: 0 }] });
    t.advance(RULER_SEND_MS);
    expect(sent).toHaveLength(2);
    expect(sent[1]?.points?.[1]).toEqual({ x: 30, y: 0 });     // the latest, not the skipped one
  });

  it('lingers after release, then clears; a new drag cancels the clear', () => {
    const sent: RelayBody[] = [];
    const t = fakeTimers();
    const ruler = createRuler((b) => (sent.push(b), true), 'cosmere', t.api);
    ruler.update([{ x: 0, y: 0 }, { x: 64, y: 0 }]);
    ruler.end([{ x: 0, y: 0 }, { x: 128, y: 0 }]);
    expect(sent.at(-1)).toEqual({ type: 'ruler', rule: 'uniform', points: [{ x: 0, y: 0 }, { x: 128, y: 0 }] });
    t.advance(RULER_LINGER_MS - 1);
    expect(sent.some((b) => b.type === 'ruler-clear')).toBe(false);
    ruler.update([{ x: 0, y: 0 }, { x: 64, y: 64 }]);            // dragging again keeps it
    t.advance(RULER_LINGER_MS);
    expect(sent.some((b) => b.type === 'ruler-clear')).toBe(false);
    ruler.end(null);                                              // a cancelled drag clears at once
    t.advance(0);
    expect(sent.at(-1)).toEqual({ type: 'ruler-clear' });
  });

  it('never sends more than eight points', () => {
    const sent: RelayBody[] = [];
    const ruler = createRuler((b) => (sent.push(b), true), 'dnd5e', fakeTimers().api);
    ruler.update(Array.from({ length: 12 }, (_, i) => ({ x: i, y: i })));
    expect(sent[0]?.points).toHaveLength(8);
  });
});
