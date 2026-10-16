// Frame rules a JSON schema cannot express, checked after the generated
// validator accepts a gm or table frame (contracts/vtt/v1/README.md,
// "Corrections"). The platform enforces the same rules in
// vtt/protocol/semantics.py, with a reference Node copy in
// tools/vtt/semantics.mjs; the shared fixture corpus keeps all three in
// agreement (generated.test.ts).
//
// A frame breaking a rule is never applied. The server never sends one, so
// the session treats it as lost state: retire, resync, resubscribe with
// state-lost, keeping the last good projection on screen.

interface Keyed {
  kind?: unknown;
  id?: unknown;
}

const key = (item: Keyed): string => `${String(item.kind)}\u0000${String(item.id)}`;

function distinct(values: readonly unknown[]): boolean {
  return new Set(values).size === values.length;
}

export type FrameSurface = 'gm' | 'table';

// True when a schema-valid frame passes every frame rule:
// - snapshot entities / patch upserted / patch removed: no repeated (kind, id);
// - a patch never upserts and removes the same key;
// - a snapshot has at most one scene entity and it is sceneId's; sceneId null
//   means no entities;
// - GM controllerIds and capabilities hold no repeats;
// - a GM wall is not zero-length.
export function frameRulesHold(surface: FrameSurface, message: unknown): boolean {
  if (message === null || typeof message !== 'object') return true;
  const m = message as {
    frame?: unknown;
    sceneId?: unknown;
    capabilities?: readonly unknown[];
    entities?: readonly Record<string, unknown>[];
    upserted?: readonly Record<string, unknown>[];
    removed?: readonly Keyed[];
  };
  let items: readonly Record<string, unknown>[];
  if (m.frame === 'snapshot') {
    const entities = m.entities ?? [];
    if (!distinct(entities.map(key))) return false;
    const scenes = entities.filter((e) => e.kind === 'scene');
    if (m.sceneId === null) {
      if (entities.length > 0) return false;
    } else if (scenes.length > 1 || scenes.some((s) => s.id !== m.sceneId)) {
      return false;
    }
    if (surface === 'gm' && !distinct(m.capabilities ?? [])) return false;
    items = entities;
  } else if (m.frame === 'patch') {
    const up = (m.upserted ?? []).map(key);
    const rm = (m.removed ?? []).map(key);
    if (!distinct(up) || !distinct(rm)) return false;
    const removed = new Set(rm);
    if (up.some((k) => removed.has(k))) return false;
    items = m.upserted ?? [];
  } else {
    return true;
  }
  if (surface === 'gm') {
    for (const item of items) {
      if (item.kind === 'token' && !distinct((item.controllerIds as readonly unknown[] | undefined) ?? [])) return false;
      if (item.kind === 'wall' && item.x1 === item.x2 && item.y1 === item.y2) return false;
    }
  }
  return true;
}
