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
// - a wall (GM, or the table's since E-6) is not zero-length
// Commands: no repeated expected key; updates, moves, deletes and table
// commands name their target's expected version; no repeated controller;
// a created (or fully re-ended) wall is not zero-length.

const key = (item) => JSON.stringify([item?.kind, item?.id]);
const distinct = (values) => new Set(values).size === values.length;

const TARGETED = {
  'scene.update': 'scene', 'table.show': 'scene', 'table.clear': 'scene',
  'token.update': 'token', 'token.move': 'token', 'light.update': 'light', 'wall.update': 'wall',
  'entity.delete': null,
  'fog.update': 'fog', 'fog.reset': 'fog',
  'template.update': 'template', 'terrain.reset': 'terrain',
};

// Parity-2 terrain: cols*rows <= 262144, cells is the canonical unpadded
// base64url of exactly ceil(cols*rows/2) bytes of 4-bit kinds (low nibble
// first), each nibble <= 4, the spare nibble zero.
export function terrainOk(item) {
  const { cols, rows, cells: text } = item ?? {};
  if (!Number.isInteger(cols) || !Number.isInteger(rows) || typeof text !== 'string') return false;
  const cells = cols * rows;
  if (cells > FOG_MAX_CELLS) return false;
  if (!/^[A-Za-z0-9_-]*$/.test(text) || text.length % 4 === 1) return false;
  const data = Buffer.from(text, 'base64url');
  if (data.length !== Math.ceil(cells / 2)) return false;
  if (data.toString('base64url') !== text) return false;
  for (const byte of data) if ((byte & 0x0f) > 4 || byte >> 4 > 4) return false;
  return cells % 2 === 0 || data[data.length - 1] >> 4 === 0;
}

// Parity-1 fog bitset: cols*rows <= 262144, revealed is the canonical unpadded
// base64url of exactly ceil(cols*rows/8) bytes (round trip gives the same
// string), and bits past cols*rows are zero.
const FOG_MAX_CELLS = 262144;
export function fogOk(item) {
  const { cols, rows, revealed } = item ?? {};
  if (!Number.isInteger(cols) || !Number.isInteger(rows) || typeof revealed !== 'string') return false;
  const cells = cols * rows;
  if (cells > FOG_MAX_CELLS) return false;
  if (!/^[A-Za-z0-9_-]*$/.test(revealed) || revealed.length % 4 === 1) return false;
  const data = Buffer.from(revealed, 'base64url');
  if (data.length !== Math.ceil(cells / 8)) return false;
  if (data.toString('base64url') !== revealed) return false;
  const spare = cells % 8;
  return spare === 0 || data[data.length - 1] >> spare === 0;
}

function checkCommand(message) {
  const expected = (message.expected ?? []).map(key);
  if (!distinct(expected)) return false;
  const payload = message.payload ?? {};
  if (Object.hasOwn(TARGETED, message.type)) {
    const kind = TARGETED[message.type];
    const target = kind === 'scene' ? ['scene', message.sceneId]
      : kind === 'fog' || kind === 'terrain' ? [kind, kind]
        : kind === null ? [payload.kind, payload.id] : [kind, payload.id];
    if (!expected.includes(key({ kind: target[0], id: target[1] }))) return false;
  }
  if (!distinct(payload.controllerIds ?? [])) return false;
  if ((message.type === 'wall.create' || message.type === 'wall.update')
      && ['x1', 'y1', 'x2', 'y2'].every((k) => Object.hasOwn(payload, k))
      && payload.x1 === payload.x2 && payload.y1 === payload.y2) return false;
  if (message.type === 'terrain.paint' && (payload.cells ?? []).some((cell) => cell[2] > 4)) return false;
  return true;
}

export function checkFrame(schemaKey, message) {
  if (schemaKey === 'command' && message !== null && typeof message === 'object') return checkCommand(message);
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
  for (const item of items) {
    if (item.kind === 'fog' && !fogOk(item)) return false;
    if (item.kind === 'terrain' && !terrainOk(item)) return false;
    // A wall on either surface (the table's since E-6) is not zero-length.
    if (item.kind === 'wall' && item.x1 === item.x2 && item.y1 === item.y2) return false;
  }
  if (schemaKey === 'gm') {
    for (const item of items) {
      if (item.kind === 'token' && !distinct(item.controllerIds ?? [])) return false;
    }
  }
  return true;
}
