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
  fogReset,
  fogReveal,
  fogUpdate,
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

  // A manager whose overlays the test opens and closes, like the session does.
  function managed(limit = 100) {
    const open = new Set<string>();
    const m = new UndoManager(limit, () => {}, (id) => open.has(id));
    return {
      m,
      // issue a command: its overlay opens at once
      issue: (id: string) => open.add(id),
      // the correlated frame (or a snapshot) arrives: the overlay ends
      land: (id: string) => {
        open.delete(id);
        m.overlaysChanged();
      },
    };
  }

  // An edit, applied and landed.
  function done(t: ReturnType<typeof managed>, id: string, e = entry(id)) {
    t.issue(id);
    t.m.edit(id, e);
    t.m.applied(id);
    t.land(id);
  }

  // An undo or redo step, issued and left in flight.
  function stepOut(t: ReturnType<typeof managed>, direction: 'undo' | 'redo', id: string, reverse = entry(`${id}-rev`)) {
    const source = t.m.beginStep(direction);
    if (!source) throw new Error(`no ${direction} available`);
    t.issue(id);
    t.m.stepIssued(direction, source, id, reverse);
    return source;
  }

  it('an edit is undoable only once it is applied AND in the projection, in either order', () => {
    const t = managed();
    t.issue('c1');
    t.m.edit('c1', entry('a'));
    t.m.applied('c1');
    expect(t.m.canUndo).toBe(false); // ack before patch: undo would be built on the old version
    t.land('c1');
    expect(t.m.canUndo).toBe(true);
    t.issue('c2');
    t.m.edit('c2', entry('b'));
    t.land('c2');
    expect(t.m.canUndo).toBe(false); // patch before ack
    t.m.applied('c2');
    expect(t.m.beginStep('undo')?.id).toBe('b');
  });

  it('a rejected edit never becomes undoable', () => {
    const t = managed();
    t.issue('c1');
    t.m.edit('c1', entry('a'));
    t.m.rejected('c1');
    expect(t.m.canUndo).toBe(false);
    expect(t.m.busy).toBe(false);
  });

  it('a new edit invalidates redo as soon as it is issued, before any reply', () => {
    const t = managed();
    done(t, 'c1');
    stepOut(t, 'undo', 'u1');
    t.m.applied('u1');
    t.land('u1');
    expect(t.m.canRedo).toBe(true);
    t.issue('c2');
    t.m.edit('c2', entry('b'));
    expect(t.m.canRedo).toBe(false);
    t.m.applied('c2');
    t.land('c2');
    expect(t.m.canRedo).toBe(false);
  });

  it('an edit with nothing to undo (scene create, table) still invalidates redo', () => {
    const t = managed();
    done(t, 'c1');
    stepOut(t, 'undo', 'u1');
    t.m.applied('u1');
    t.land('u1');
    t.m.edit('show-table', null);
    expect(t.m.canRedo).toBe(false);
  });

  it('a late undo reply after a new edit cannot resurrect redo', () => {
    const t = managed();
    done(t, 'c1');
    stepOut(t, 'undo', 'u1');
    t.issue('c2');
    t.m.edit('c2', entry('b'));
    t.m.applied('c2');
    t.land('c2');
    t.m.applied('u1');
    t.land('u1');
    expect(t.m.canRedo).toBe(false);
  });

  it('history steps are serialized: none starts while a command is on its way', () => {
    const t = managed();
    done(t, 'c1');
    done(t, 'c2');
    stepOut(t, 'undo', 'u1');
    expect(t.m.canUndo).toBe(false);
    expect(t.m.beginStep('undo')).toBeUndefined();
    t.m.applied('u1');
    expect(t.m.beginStep('undo')).toBeUndefined(); // applied but not yet in the projection
    t.land('u1');
    expect(t.m.beginStep('undo')?.id).toBe('c1');
  });

  it('a rejected undo puts its entry back; a step that never left does too', () => {
    const t = managed();
    done(t, 'c1');
    const source = stepOut(t, 'undo', 'u1');
    t.m.rejected('u1');
    expect(t.m.canUndo).toBe(true);
    expect(t.m.beginStep('undo')).toBe(source);
    t.m.stepIssued('undo', source, null, null);
    expect(t.m.beginStep('undo')).toBe(source);
  });

  it('a rejected undo after a new edit is dropped, not put back out of order', () => {
    const t = managed();
    done(t, 'c1');
    stepOut(t, 'undo', 'u1');
    t.issue('c2');
    t.m.edit('c2', entry('b'));
    t.m.rejected('u1');
    t.m.applied('c2');
    t.land('c2');
    expect(t.m.beginStep('undo')?.id).toBe('b');
    expect(t.m.beginStep('undo')).toBeUndefined();
  });

  it('undo feeds redo and redo feeds undo', () => {
    const t = managed();
    done(t, 'c1');
    stepOut(t, 'undo', 'u1', entry('redo-a'));
    t.m.applied('u1');
    t.land('u1');
    expect(t.m.canUndo).toBe(false);
    stepOut(t, 'redo', 'r1', entry('undo-a'));
    t.m.applied('r1');
    t.land('r1');
    expect(t.m.canRedo).toBe(false);
    expect(t.m.beginStep('undo')?.id).toBe('undo-a');
  });

  it('a replacement snapshot lands everything in flight', () => {
    const t = managed();
    t.issue('c1');
    t.m.edit('c1', entry('a'));
    t.m.applied('c1');
    t.land('c1'); // a snapshot drops every overlay
    expect(t.m.canUndo).toBe(true);
  });

  it('a late redo ack keeps its entry below a newer edit (action order, not ack order)', () => {
    const t = managed();
    done(t, 'c1', entry('undo-original'));
    stepOut(t, 'undo', 'u1', entry('redo-original'));
    t.m.applied('u1');
    t.land('u1');
    stepOut(t, 'redo', 'r1', entry('undo-redo'));
    t.land('r1');                                // shown, ack delayed
    done(t, 'c2', entry('undo-newer'));
    t.m.applied('r1');
    expect(t.m.beginStep('undo')?.id).toBe('undo-newer');
    expect(t.m.beginStep('undo')?.id).toBe('undo-redo');
  });

  it('edits settling out of order keep issue order', () => {
    const t = managed();
    t.issue('a');
    t.m.edit('a', entry('a'));
    t.issue('b');
    t.m.edit('b', entry('b'));
    t.land('b');
    t.m.applied('b');
    t.land('a');
    t.m.applied('a');
    expect([t.m.beginStep('undo')?.id, t.m.beginStep('undo')?.id]).toEqual(['b', 'a']);
  });

  it('a rejected step goes back to its own place', () => {
    const t = managed();
    done(t, 'c1');
    done(t, 'c2');
    const source = stepOut(t, 'undo', 'u1');
    t.m.rejected('u1');
    expect(t.m.beginStep('undo')).toBe(source);
    expect(t.m.beginStep('undo')?.id).toBe('c1');
  });

  it('is bounded', () => {
    const t = managed(3);
    for (let i = 0; i < 5; i += 1) done(t, `e${i}`);
    const ids = [];
    for (let i = 0; i < 4; i += 1) ids.push(t.m.beginStep('undo')?.id);
    expect(ids).toEqual(['e4', 'e3', 'e2', undefined]);
  });
});

describe('fog undo (parity P1)', () => {
  const V1 = 'v' + 'A'.repeat(24);
  const fogE = (revealed: string, enabled = true) =>
    ({ kind: 'fog', id: 'fog', version: V1, enabled, cols: 4, rows: 3, revealed }) as Extract<GmEntity, { kind: 'fog' }>;

  it('a reveal is undone by hiding exactly the same cells, with no version', () => {
    const back = inverseFor(fogReveal('reveal', [[0, 0], [1, 1]]), fogE('AAA'))?.make(fogE('IQA'));
    expect(back).toEqual({ type: 'fog.reveal', expected: [], payload: { mode: 'hide', cells: [[0, 0], [1, 1]] } });
    expect(valid(back as Draft)).toBe(true);
  });

  it('turning fog on is undone by turning it off against the version after', () => {
    const after = { ...fogE('AAA', true), version: 'v' + 'B'.repeat(24) };
    const back = inverseFor(fogUpdate(fogE('AAA', false), true), fogE('AAA', false))?.make(after);
    expect(back).toEqual({ type: 'fog.update', expected: [{ kind: 'fog', id: 'fog', version: after.version }], payload: { enabled: false } });
    expect(valid(back as Draft)).toBe(true);
  });

  it('a reset is undone by revealing what was revealed; resetting nothing is not undoable', () => {
    const back = inverseFor(fogReset(fogE('IQA')), fogE('IQA'))?.make(fogE('AAA'));
    expect(back?.payload).toEqual({ mode: 'reveal', cells: [[0, 0], [1, 1]] }); // bits 0 and 5 of a 4-wide grid
    expect(inverseFor(fogReset(fogE('AAA')), fogE('AAA'))).toBeNull();
  });

  it('fog commands target the fog entity and show no overlay', () => {
    expect(targetOf(fogReveal('hide', [[0, 0]]))).toEqual({ kind: 'fog', id: 'fog' });
    expect(overlayFor(fogReveal('hide', [[0, 0]]))).toBeNull();
  });
});
