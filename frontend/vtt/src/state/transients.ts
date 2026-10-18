import { create } from 'zustand';
import type { MeasureRule } from '../scene/measure';

// Live pings and rulers (beta-1, ADR-0002 section 9). Never stored beyond their
// TTL, never replayed: a new snapshot (a new subscription, another scene, a
// reconnect) clears them all. One entry per latest-wins key; a newer transient
// with the same key replaces the older one, and a ruler-clear removes it.

export type LiveTransient =
  | { kind: 'ping'; x: number; y: number; startedAt: number; expiresAt: number }
  | { kind: 'ruler'; points: ReadonlyArray<{ x: number; y: number }>; rule: MeasureRule; expiresAt: number };

export interface RelayBody {
  type: 'ping' | 'ruler' | 'ruler-clear';
  x?: number;
  y?: number;
  points?: ReadonlyArray<{ x: number; y: number }>;
  rule?: MeasureRule;
}

interface TransientState {
  live: ReadonlyMap<string, LiveTransient>;
  apply(key: string, body: RelayBody, ttlMs: number, now?: number): void;
  prune(now?: number): void;
  clear(): void;
}

export const useTransients = create<TransientState>()((set, get) => ({
  live: new Map(),
  apply: (key, body, ttlMs, now = Date.now()) => {
    const next = new Map(get().live);
    if (body.type === 'ruler-clear') {
      next.delete(key);
    } else if (body.type === 'ping' && typeof body.x === 'number' && typeof body.y === 'number') {
      next.set(key, { kind: 'ping', x: body.x, y: body.y, startedAt: now, expiresAt: now + ttlMs });
    } else if (body.type === 'ruler' && body.points && body.points.length >= 2) {
      next.set(key, { kind: 'ruler', points: body.points, rule: body.rule ?? 'uniform', expiresAt: now + ttlMs });
    } else {
      return;
    }
    set({ live: next });
  },
  prune: (now = Date.now()) => {
    const live = get().live;
    let changed = false;
    const next = new Map<string, LiveTransient>();
    for (const [key, value] of live) {
      if (value.expiresAt > now) next.set(key, value);
      else changed = true;
    }
    if (changed) set({ live: next });
  },
  clear: () => {
    if (get().live.size > 0) set({ live: new Map() });
  },
}));
