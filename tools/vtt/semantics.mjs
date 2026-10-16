// Reference Node copy of vtt/protocol/semantics.py: frame rules a JSON schema
// cannot express, checked after schema validation. The fixture corpus keeps
// this, the Python boundary and the client's src/protocol/semantics.ts in
// agreement. Returns true when the frame passes.
//
// - snapshot entities / patch upserted / patch removed: no repeated (kind, id)
// - a patch never upserts and removes the same key
// - a snapshot has at most one scene entity and it is sceneId's;
//   sceneId null means no entities
// - GM controllerIds and capabilities hold no repeats
// - a GM wall is not zero-length

const key = (item) => JSON.stringify([item?.kind, item?.id]);
const distinct = (values) => new Set(values).size === values.length;

export function checkFrame(schemaKey, message) {
  if ((schemaKey !== 'gm' && schemaKey !== 'table') || message === null || typeof message !== 'object') return true;
  let items;
  if (message.frame === 'snapshot') {
    const entities = message.entities ?? [];
    if (!distinct(entities.map(key))) return false;
    const scenes = entities.filter((e) => e.kind === 'scene');
    if (message.sceneId === null) {
      if (entities.length) return false;
    } else if (scenes.length > 1 || scenes.some((s) => s.id !== message.sceneId)) {
      return false;
    }
    if (schemaKey === 'gm' && !distinct(message.capabilities ?? [])) return false;
    items = entities;
  } else if (message.frame === 'patch') {
    const up = (message.upserted ?? []).map(key);
    const rm = (message.removed ?? []).map(key);
    if (!distinct(up) || !distinct(rm)) return false;
    const removed = new Set(rm);
    if (up.some((k) => removed.has(k))) return false;
    items = message.upserted ?? [];
  } else {
    return true;
  }
  if (schemaKey === 'gm') {
    for (const item of items) {
      if (item.kind === 'token' && !distinct(item.controllerIds ?? [])) return false;
      if (item.kind === 'wall' && item.x1 === item.x2 && item.y1 === item.y2) return false;
    }
  }
  return true;
}
