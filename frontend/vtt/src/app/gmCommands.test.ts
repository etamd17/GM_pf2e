import { describe, expect, it } from 'vitest';
import * as gm from '../protocol/generated/gm.validators.mjs';
import type { GmEntity } from '../protocol/frames';
import { commandRulesHold } from '../protocol/semantics';
import { EMPTY_SCENE } from '../scene/renderModel';
import {
  applyOverlays,
  clearTable,
  createLight,
  createScene,
  createToken,
  createWall,
  deleteEntity,
  deleteScene,
  moveToken,
  newEntityId,
  setDoorState,
  showOnTable,
  updateLight,
  updateScene,
  updateToken,
  type Draft,
} from './gmCommands';

// Every builder's output, wrapped as the queue wraps it, must pass the
// generated command schema and the command rules: a tool can never produce a
// command the server would refuse as invalid-schema.

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V = 'v' + 'A'.repeat(24);
const scene = {
  kind: 'scene', id: 'scene_a', version: V, name: 'S', width: 640, height: 640, gridSize: 64,
  showGrid: true, backgroundAssetId: null, published: true,
} as Of<'scene'>;
const token = {
  kind: 'token', id: 'tok_1', version: V, x: 96, y: 96, size: 1, label: 'Hero', color: 1,
  artAssetId: null, actorId: null, hidden: false, locked: false, controllerIds: [],
} as Of<'token'>;
const light = { kind: 'light', id: 'light_1', version: V, x: 10, y: 10, radius: 128, color: 3, hidden: false } as Of<'light'>;
const wall = { kind: 'wall', id: 'wall_1', version: V, x1: 0, y1: 0, x2: 64, y2: 0, doorState: 'none' } as Of<'wall'>;

function wire(draft: Draft) {
  return {
    schemaVersion: 1,
    commandId: 'cmd_' + 'x'.repeat(16),
    campaignId: 'camp_a',
    sceneId: draft.sceneId ?? 'scene_a',
    type: draft.type,
    expected: draft.expected,
    payload: draft.payload,
  };
}

const drafts: Array<[string, Draft]> = [
  ['create token', createToken(newEntityId('tok'), { x: 32, y: 32 }, 'Goblin', 0xa83a3a)],
  ['move token', moveToken(token, 160, 96)],
  ['update token', updateToken(token, { label: 'Valeros', hidden: true, size: 2, locked: true })],
  ['create light', createLight(newEntityId('light'), { x: 64, y: 64 }, 64)],
  ['update light', updateLight(light, { radius: 64, hidden: true })],
  ['create wall', createWall(newEntityId('wall'), { x: 0, y: 0 }, { x: 128, y: 0 })],
  ['door state', setDoorState(wall, 'secret')],
  ['delete token', deleteEntity(token)],
  ['delete wall', deleteEntity(wall)],
  ['create scene', createScene(newEntityId('scene'), 'Docks')],
  ['publish', updateScene(scene, { published: false })],
  ['show on table', showOnTable(scene)],
  ['clear table', clearTable(scene)],
  ['delete scene', deleteScene(scene)],
];

describe('GM command builders', () => {
  it.each(drafts)('%s passes the schema and the command rules', (_name, draft) => {
    const command = wire(draft);
    expect(gm.validateCommand(command)).toBe(true);
    expect(commandRulesHold(command)).toBe(true);
  });

  it('every edit carries its target version, every create none', () => {
    for (const [, d] of drafts) {
      if (d.type.endsWith('.create')) expect(d.expected).toEqual([]);
      else expect(d.expected).toHaveLength(1);
    }
    expect(moveToken(token, 1, 1).expected).toEqual([{ kind: 'token', id: 'tok_1', version: V }]);
  });

  it('a scene delete is version-checked: without its expected version the schema refuses it', () => {
    const command = wire(deleteScene(scene));
    expect(command.expected).toEqual([{ kind: 'scene', id: scene.id, version: V }]);
    expect(gm.validateCommand({ ...command, expected: [] })).toBe(false);
  });

  it('new ids are Id-shaped and unique', () => {
    const ids = new Set(Array.from({ length: 100 }, () => newEntityId('tok')));
    expect(ids.size).toBe(100);
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{1,128}$/);
  });
});

describe('applyOverlays', () => {
  const base = { ...EMPTY_SCENE, tokens: [{ id: 'tok_1', x: 96, y: 96, size: 1, label: 'Hero', color: 1 }] };

  it('moves a token and marks it pending', () => {
    const out = applyOverlays(base, [{ kind: 'token', id: 'tok_1', patch: { x: 160, y: 96 } }]);
    expect(out.tokens).toEqual([{ id: 'tok_1', x: 160, y: 96, size: 1, label: 'Hero', color: 1, pending: true }]);
  });

  it('shows a token being created, until the frame brings the real one', () => {
    const create = { id: 'tok_2', x: 32, y: 32, size: 1, label: 'New', color: 2 };
    expect(applyOverlays(base, [{ kind: 'token', id: 'tok_2', create }]).tokens.map((t) => t.id)).toEqual(['tok_1', 'tok_2']);
    const arrived = { ...base, tokens: [...base.tokens, create] };
    const out = applyOverlays(arrived, [{ kind: 'token', id: 'tok_2', create }]);
    expect(out.tokens.find((t) => t.id === 'tok_2')?.pending).toBeUndefined();
  });

  it('hides a token being deleted', () => {
    expect(applyOverlays(base, [{ kind: 'token', id: 'tok_1', deleted: true }]).tokens).toEqual([]);
  });

  it('returns the same scene object when nothing applies', () => {
    expect(applyOverlays(base, [null, { kind: 'token', id: 'gone', patch: { x: 1 } }])).toBe(base);
  });
});
