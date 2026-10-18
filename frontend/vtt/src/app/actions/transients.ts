// Ping and ruler actions (beta-1). Published for the layout (Codex owns the
// tools that call these; handoff 087): the rules live here, once, with tests.
//
// - ping(point): one ping at a scene point.
// - A ruler controller: update([start, current]) while dragging, sent at most
//   every RULER_SEND_MS so a drag cannot exceed the server's rate limit; the
//   final position always goes out. end() keeps the ruler up RULER_LINGER_MS,
//   then clears it on every screen. A new drag cancels a pending clear.

import { ruleFor, type GameSystem } from '../../scene/measure';
import type { RelayBody } from '../../state/transients';

export const RULER_SEND_MS = 66; // about 15 a second
export const RULER_LINGER_MS = 2000;

export interface Point {
  x: number;
  y: number;
}

type Send = (body: RelayBody) => boolean;

export interface TimerApi {
  now(): number;
  setTimeout(fn: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

const browserTimers: TimerApi = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => window.setTimeout(fn, ms),
  clearTimeout: (handle) => window.clearTimeout(handle),
};

const clamp = (p: Point): Point => ({ x: Math.max(0, Math.round(p.x)), y: Math.max(0, Math.round(p.y)) });

export function ping(send: Send, point: Point): boolean {
  const p = clamp(point);
  return send({ type: 'ping', x: p.x, y: p.y });
}

export interface RulerController {
  update(points: Point[]): void;
  end(points: Point[] | null): void;
  dispose(): void;
}

export function createRuler(send: Send, system: GameSystem, timers: TimerApi = browserTimers): RulerController {
  const rule = ruleFor(system);
  let lastSent = -Infinity;
  let pending: number | null = null; // a trailing send
  let clearTimer: number | null = null;
  let latest: Point[] | null = null;

  const sendRuler = (points: Point[]) => {
    lastSent = timers.now();
    send({ type: 'ruler', rule, points: points.slice(0, 8).map(clamp) });
  };
  const cancel = () => {
    if (pending !== null) timers.clearTimeout(pending);
    pending = null;
  };

  return {
    update(points) {
      if (points.length < 2) return;
      if (clearTimer !== null) {
        timers.clearTimeout(clearTimer);
        clearTimer = null;
      }
      latest = points;
      const wait = RULER_SEND_MS - (timers.now() - lastSent);
      if (wait <= 0) {
        cancel();
        sendRuler(points);
      } else if (pending === null) {
        pending = timers.setTimeout(() => {
          pending = null;
          if (latest) sendRuler(latest);
        }, wait);
      }
    },
    end(points) {
      cancel();
      if (points && points.length >= 2) sendRuler(points);
      latest = null;
      if (clearTimer !== null) timers.clearTimeout(clearTimer);
      clearTimer = timers.setTimeout(() => {
        clearTimer = null;
        send({ type: 'ruler-clear' });
      }, points ? RULER_LINGER_MS : 0);
    },
    dispose() {
      cancel();
      if (clearTimer !== null) timers.clearTimeout(clearTimer);
      clearTimer = null;
    },
  };
}
