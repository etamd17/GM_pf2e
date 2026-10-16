import { describe, expect, it } from 'vitest';
import * as gm from '../protocol/generated/gm.validators.mjs';
import type { GmEntity } from '../protocol/frames';
import { commandRulesHold } from '../protocol/semantics';
import {
  createLight,
  createToken,
  createWall,
  deleteEntity,
  moveToken,
  setDoorState,
  updateLight,
  updateScene,
  updateToken,
  clearTable,
  createScene,
  showOnTable,
  type Draft,
} from './gmCommands';
import { UndoManager, inverseFor, overlayFor, targetOf } from './undo';

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;
const V1 = 'v' + 'A'.repeat(24);
const V2 = 'v' + 'B'.repeat(24);
const scene = { kind: 'scene', id: 'scene_a', version: V1, name: 'S', width: 640, height: 640, gridSize: 64, showGrid: true, backgroundAssetId: null, published: false } as Of<'scene'>;
const token = { kind: 'token', id: 'tok_1', version: V1, x: 96, y: 96, size: 1, label: 'Hero', color: 1, artAssetId: null, actorId: null, hidden: false, locked: false, controllerIds: ['u1'] } as Of<'token'>;
const light = { kind: 'light', id: 'light_1', version: V1, x: 10, y: 10, radius: 128, color: 3, hidden: false } as Of<'light'>;
const wall = { kind: 'wall', id: 'wall_1', version: V1, x1: 0, y1: 0, x2: 64, y2: 0, doorState: 'closed' } as Of<'wall'>;

const valid = (draft: Draft) => {
  const command = { schemaVersion: 1, commandId: 'cmd_' + 'x'.repeat(16), campaignId: 'camp_a', sceneId: draft.sceneId ?? 'scene_a', type: draft.type, expected: draft.expected, payload: draft.payload };
  return gm.validateCommand(command) && commandRulesHold(command);
};

// The reversing command for `draft`, made against `now` (the entity after the edit).
function inverse(draft: Draft, before: GmEntity | undefined, now: GmEntity | undefined) {
  const entry = inverseFor(draft, before);
  if (!entry) return null;
  return entry.make(now);
}

describe('inverseFor', () => {
  it('a move moves back, against the version after the move', () => {
    const back = inverse(moveToken(token, 160, 96), token, { ...token, x: 160, version: V2 });
    expect(back).toEqual({ type: 'token.move', expected: [{ kind: 'token', id: 'tok_1', version: V2 }], payload: { id: 'tok_1', x: 96, y: 96 } });
    expect(valid(back as Draft)).toBe(true);
  });

  it('an update restores exactly the fields it changed', () => {
    const back = inverse(updateToken(token, { label: 'Valeros', hidden: true }), token, { ...token, label: 'Valeros', hidden: true, version: V2 });
    expect(back?.payload).toEqual({ id: 'tok_1', label: 'Hero', hidden: false });
    expect(inverse(updateLight(light, { radius: 64 }), light, { ...light, radius: 64, version: V2 })?.payload).toEqual({ id: 'light_1', radius: 128 });
    expect(inverse(setDoorState(wall, 'open'), wall, { ...wall, doorState: 'open', version: V2 })?.payload).toEqual({ id: 'wall_1', doorState: 'closed' });
    expect(inverse(updateScene(scene, { published: true }), scene, { ...scene, published: true, version: V2 })?.payload).toEqual({ published: false });
  });

  it('a create is undone by deleting what was created', () => {
    const created = createToken('tok_new', { x: 32, y: 32 }, 'New', 2);
    const now = { ...token, id: 'tok_new', version: V2 };
    expect(inverse(created, undefined, now)).toEqual(deleteEntity(now));
    expect(inverse(createLight('l_new', { x: 1, y: 1 }, 64), undefined, { ...light, id: 'l_new' })?.type).toBe('entity.delete');
    expect(inverse(createWall('w_new', { x: 0, y: 0 }, { x: 64, y: 0 }), undefined, { ...wall, id: 'w_new' })?.type).toBe('entity.delete');
  });

  it('a delete is undone by re-creating everything under the same id', () => {
    for (const before of [token, light, wall]) {
      const back = inverse(deleteEntity(before), before, undefined) as Draft;
      expect(back.type).toBe(`${before.kind}.create`);
      expect((back.payload as { id: string }).id).toBe(before.id);
      expect(valid(back)).toBe(true);
    }
    const tokenBack = inverse(deleteEntity(token), token, undefined) as Draft;
    expect(tokenBack.payload).toMatchObject({ x: 96, y: 96, size: 1, label: 'Hero', color: 1, hidden: false, locked: false, controllerIds: ['u1'] });
    expect((inverse(deleteEntity(wall), wall, undefined) as Draft).payload).toMatchObject({ doorState: 'closed' });
  });

  it('cannot reverse onto something changed out from under it', () => {
    // the moved token was deleted since: nothing to move back
    expect(inverse(moveToken(token, 160, 96), token, undefined)).toBeNull();
    // the deleted token's id is taken again: no re-create over it
    expect(inverse(deleteEntity(token), token, token)).toBeNull();
    // a created token already gone: nothing to delete
    expect(inverse(createToken('t', { x: 1, y: 1 }, 'x', 0), undefined, undefined)).toBeNull();
  });

  it('scene creation and the table are not undoable here', () => {
    expect(inverseFor(createScene('s2', 'X'), undefined)).toBeNull();
    expect(inverseFor(showOnTable(scene), scene)).toBeNull();
    expect(inverseFor(clearTable(scene), scene)).toBeNull();
  });

  it('finds each command target and its pending overlay', () => {
    expect(targetOf(moveToken(token, 1, 1))).toEqual({ kind: 'token', id: 'tok_1' });
    expect(targetOf(deleteEntity(wall))).toEqual({ kind: 'wall', id: 'wall_1' });
    expect(targetOf(updateScene(scene, { name: 'x' }))).toEqual({ kind: 'scene', id: 'scene_a' });
    expect(overlayFor(moveToken(token, 5, 6))).toEqual({ kind: 'token', id: 'tok_1', patch: { x: 5, y: 6 } });
    expect(overlayFor(deleteEntity(token))).toEqual({ kind: 'token', id: 'tok_1', deleted: true });
    expect(overlayFor(setDoorState(wall, 'open'))).toBeNull();
  });
});

describe('UndoManager', () => {
  const entry = (id: string) => ({ kind: 'token' as const, id, label: 'move', make: () => null });

  it('only applied edits become undoable; rejected ones never do', () => {
    const m = new UndoManager();
    m.track('c1', entry('a'), 'edit');
    m.track('c2', entry('b'), 'edit');
    m.settled('c2');
    m.applied('c2');
    expect(m.canUndo).toBe(false);
    m.applied('c1');
    expect(m.popUndo()?.id).toBe('a');
    expect(m.canUndo).toBe(false);
  });

  it('undo feeds redo, and a new edit clears redo', () => {
    const m = new UndoManager();
    m.track('c1', entry('a'), 'edit');
    m.applied('c1');
    m.popUndo();
    m.track('u1', entry('a-inverse'), 'undo');
    m.applied('u1');
    expect(m.canRedo).toBe(true);
    m.track('r1', entry('a-again'), 'redo');
    m.popRedo();
    m.applied('r1');
    expect(m.canUndo).toBe(true);
    m.track('u2', entry('x'), 'undo');
    m.applied('u2');
    m.track('c2', entry('b'), 'edit');
    m.applied('c2');
    expect(m.canRedo).toBe(false);
  });

  it('is bounded', () => {
    const m = new UndoManager(3);
    for (let i = 0; i < 5; i += 1) {
      m.track(`c${i}`, entry(`e${i}`), 'edit');
      m.applied(`c${i}`);
    }
    expect([m.popUndo()?.id, m.popUndo()?.id, m.popUndo()?.id, m.popUndo()]).toEqual(['e4', 'e3', 'e2', undefined]);
  });
});
