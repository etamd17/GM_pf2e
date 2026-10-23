import type { GmEntity } from '../protocol/frames';
import { decodeFogBits } from '../protocol/semantics';
import { FOG_CELLS_PER_COMMAND } from '../scene/fogFill';
import {
  createLight,
  createToken,
  createWall,
  deleteEntity,
  fogReveal,
  fogUpdate,
  templateCreate,
  templateUpdate,
  terrainPaint,
  type FogCell,
  type TemplatePatch,
  type TerrainCell,
  moveToken,
  setDoorState,
  updateLight,
  updateScene,
  updateToken,
  type Draft,
  type LightPatch,
  type ScenePatch,
  type TokenPatch,
} from './gmCommands';

// Undo is an inverse, never a restore (the legacy map's rule): each applied GM
// edit records the command that reverses it, computed from the entity as it
// was before. Undoing builds that command against the entity as it is NOW, so
// it carries a fresh expected version; if someone changed the entity since,
// the server answers stale and nothing is overwritten. Redo is the same
// machinery run the other way.

type Of<K extends GmEntity['kind']> = Extract<GmEntity, { kind: K }>;

export interface UndoEntry {
  kind: GmEntity['kind'];
  id: string;
  label: string;
  // The reversing command for the entity as it is now (undefined when it no
  // longer exists), or null when it cannot be reversed any more.
  make(current: GmEntity | undefined): Draft | null;
}

const LABELS: Record<string, string> = {
  'token.move': 'move',
  'token.update': 'token change',
  'token.create': 'new token',
  'light.update': 'light change',
  'light.create': 'new light',
  'wall.update': 'door change',
  'wall.create': 'new wall',
  'entity.delete': 'delete',
  'scene.update': 'scene change',
  'fog.reveal': 'fog change',
  'fog.update': 'fog on/off',
  'fog.reset': 'fog reset',
  'template.create': 'new template',
  'template.update': 'template change',
  'terrain.paint': 'terrain',
  'terrain.reset': 'terrain reset',
};

function pick<T extends object>(source: T, keys: string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const k of keys) out[k] = (source as Record<string, unknown>)[k];
  return out;
}

function recreate(before: GmEntity): Draft | null {
  if (before.kind === 'token') {
    return createToken(before.id, { x: before.x, y: before.y }, before.label, before.color, {
      size: before.size,
      hidden: before.hidden,
      locked: before.locked,
      artAssetId: before.artAssetId,
      actorId: before.actorId,
      controllerIds: [...before.controllerIds],
    });
  }
  if (before.kind === 'light') {
    const draft = createLight(before.id, { x: before.x, y: before.y }, 1);
    draft.payload = { ...draft.payload, radius: before.radius, color: before.color, hidden: before.hidden };
    return draft;
  }
  if (before.kind === 'template') {
    return templateCreate(before.id, before.shape, { x: before.x, y: before.y }, { x: before.aimX, y: before.aimY },
      before.size, { color: before.color, hidden: before.hidden });
  }
  if (before.kind === 'wall') {
    const draft = createWall(before.id, { x: before.x1, y: before.y1 }, { x: before.x2, y: before.y2 });
    draft.payload = { ...draft.payload, doorState: before.doorState };
    return draft;
  }
  return null;
}

// The entry that reverses `draft`, given the entity it targets as it was just
// before (undefined for a create). Null for commands that are not undoable
// here (scene creation, showing or clearing the table).
export function inverseFor(draft: Draft, before: GmEntity | undefined): UndoEntry | null {
  const payload = draft.payload as Record<string, unknown>;
  const label = LABELS[draft.type] ?? draft.type;
  const changed = Object.keys(payload).filter((k) => k !== 'id');
  switch (draft.type) {
    case 'token.move':
    case 'token.update': {
      if (before?.kind !== 'token') return null;
      const back = pick(before, changed);
      // E-10: a token never reordered has no order, which means 0.
      if (changed.includes('order') && back.order === undefined) back.order = 0;
      return {
        kind: 'token',
        id: before.id,
        label,
        make: (now) =>
          now?.kind !== 'token'
            ? null
            : draft.type === 'token.move'
              ? moveToken(now, back.x as number, back.y as number)
              : updateToken(now, back as TokenPatch),
      };
    }
    case 'template.update': {
      if (before?.kind !== 'template') return null;
      const back = pick(before, changed) as TemplatePatch;
      return { kind: 'template', id: before.id, label, make: (now) => (now?.kind === 'template' ? templateUpdate(now, back) : null) };
    }
    case 'terrain.paint': {
      // The previous kind of exactly the cells painted: one command back.
      if (before?.kind !== 'terrain') return null;
      const bytes = decodeFogBits(before.cells);
      if (!bytes) return null;
      const back = (payload.cells as TerrainCell[]).map(([c, r]): TerrainCell => {
        const index = r * before.cols + c;
        const byte = bytes[index >> 1] ?? 0;
        return [c, r, index & 1 ? byte >> 4 : byte & 0x0f];
      });
      return { kind: 'terrain', id: 'terrain', label, make: (now) => (now?.kind === 'terrain' ? terrainPaint(back) : null) };
    }
    case 'terrain.reset': {
      if (before?.kind !== 'terrain') return null;
      const bytes = decodeFogBits(before.cells);
      if (!bytes) return null;
      const back: TerrainCell[] = [];
      for (let index = 0; index < before.cols * before.rows; index += 1) {
        const byte = bytes[index >> 1] ?? 0;
        const kind = index & 1 ? byte >> 4 : byte & 0x0f;
        if (kind) back.push([index % before.cols, Math.floor(index / before.cols), kind]);
      }
      if (back.length === 0 || back.length > FOG_CELLS_PER_COMMAND) return null;
      return { kind: 'terrain', id: 'terrain', label, make: (now) => (now?.kind === 'terrain' ? terrainPaint(back) : null) };
    }
    case 'light.update': {
      if (before?.kind !== 'light') return null;
      const back = pick(before, changed) as LightPatch;
      return { kind: 'light', id: before.id, label, make: (now) => (now?.kind === 'light' ? updateLight(now, back) : null) };
    }
    case 'wall.update': {
      if (before?.kind !== 'wall') return null;
      const back = before.doorState;
      return { kind: 'wall', id: before.id, label, make: (now) => (now?.kind === 'wall' ? setDoorState(now, back) : null) };
    }
    case 'scene.update': {
      if (before?.kind !== 'scene') return null;
      const back = pick(before, changed) as ScenePatch;
      return { kind: 'scene', id: before.id, label, make: (now) => (now?.kind === 'scene' ? updateScene(now, back) : null) };
    }
    case 'token.create':
    case 'light.create':
    case 'wall.create':
    case 'template.create': {
      const kind = draft.type.split('.')[0] as 'token' | 'light' | 'wall' | 'template';
      const id = String(payload.id);
      // Undoing a create deletes what was created, if it is still there.
      return {
        kind,
        id,
        label,
        make: (now) => (now && now.kind === kind ? deleteEntity(now as Of<'token'> | Of<'light'> | Of<'wall'> | Of<'template'>) : null),
      };
    }
    case 'entity.delete': {
      if (!before || before.kind === 'scene') return null;
      const snapshot = before;
      // Undoing a delete re-creates it under the same id, unless something
      // with that id exists again.
      return { kind: snapshot.kind, id: snapshot.id, label, make: (now) => (now ? null : recreate(snapshot)) };
    }
    case 'fog.reveal': {
      // The command carries exactly the cells it changes (fogFill), so the
      // inverse is the opposite mode on the same cells.
      const cells = (payload.cells as FogCell[]).map(([c, r]) => [c, r] as FogCell);
      const back = payload.mode === 'reveal' ? 'hide' : 'reveal';
      return { kind: 'fog', id: 'fog', label, make: (now) => (now?.kind === 'fog' ? fogReveal(back, cells) : null) };
    }
    case 'fog.update': {
      if (before?.kind !== 'fog') return null;
      const back = before.enabled;
      return { kind: 'fog', id: 'fog', label, make: (now) => (now?.kind === 'fog' ? fogUpdate(now, back) : null) };
    }
    case 'fog.reset': {
      if (before?.kind !== 'fog') return null;
      const cells = revealedCells(before);
      // Nothing was revealed, or too much to restore in one command: not undoable.
      if (cells === null || cells.length === 0) return null;
      return { kind: 'fog', id: 'fog', label, make: (now) => (now?.kind === 'fog' ? fogReveal('reveal', cells) : null) };
    }
    default:
      return null;
  }
}

// The revealed cells of a fog entity, or null when there are more than one
// command may carry.
function revealedCells(fog: Of<'fog'>): FogCell[] | null {
  const bits = decodeFogBits(fog.revealed);
  if (!bits) return null;
  const cells: FogCell[] = [];
  for (let index = 0; index < fog.cols * fog.rows; index += 1) {
    if (((bits[index >> 3] ?? 0) >> (index & 7)) & 1) {
      if (cells.length === FOG_CELLS_PER_COMMAND) return null;
      cells.push([index % fog.cols, Math.floor(index / fog.cols)]);
    }
  }
  return cells;
}

type Mode = 'edit' | 'undo' | 'redo';

interface Tracked {
  entry: UndoEntry | null; // what reverses this command, once it has landed
  mode: Mode;
  generation: number; // the edit generation it was issued in
  source?: UndoEntry; // a history step: the entry it was taken from
  applied: boolean; // the ack said applied
  landed: boolean; // the projection shows it (its overlay ended)
}

export interface UndoState {
  canUndo: boolean;
  canRedo: boolean;
  next: string | null;
}

// The page's edit history (handoff 048). The rules:
// - An entry becomes undoable only when its command is applied AND the
//   projection shows it (its overlay ended: the correlated frame or a
//   replacement snapshot arrived, in either order with the ack). Undo is built
//   against the projected entity, so before that it would carry the previous
//   version and be refused as stale.
// - Issuing a new edit invalidates redo at once, before any reply, and starts
//   a new generation. A history step that completes in an older generation
//   cannot put anything back on the redo stack.
// - History steps are serialized: none starts while any tracked command is
//   still on its way, so each one is built against settled state.
// - A rejected step puts its entry back where it came from (if no new edit
//   happened since), so a racing refusal does not silently lose history.
// - Stacks keep ACTION order, not reply order (handoff 050): every entry is
//   numbered when its command is issued and is inserted by that number when
//   it settles. Edits may overlap a pending step or each other, so a late ack
//   (a lost ack answered by the receipt on retry) must not put an older
//   action's entry above a newer one.
export class UndoManager {
  private undos: UndoEntry[] = [];
  private redos: UndoEntry[] = [];
  private readonly pending = new Map<string, Tracked>();
  private generation = 0;
  private issued = 0;
  private readonly order = new WeakMap<UndoEntry, number>();

  constructor(
    private readonly limit = 100,
    private readonly onChange: (state: UndoState) => void = () => {},
    // Whether a command's optimistic overlay is still open in the session.
    private readonly hasOverlay: (commandId: string) => boolean = () => false,
  ) {}

  // An edit was issued (commandId null: it never left). Redo is obsolete from
  // this moment, whether or not the edit itself can be undone.
  edit(commandId: string | null, entry: UndoEntry | null): void {
    if (commandId === null) return;
    this.generation += 1;
    this.redos = [];
    if (entry) this.track(commandId, { entry, mode: 'edit', generation: this.generation });
    this.changed();
  }

  // Take the next step, when one may start now; undefined otherwise. The
  // caller then issues it and reports back with stepIssued (or stepFailed).
  beginStep(direction: 'undo' | 'redo'): UndoEntry | undefined {
    if (this.busy) return undefined;
    const entry = (direction === 'undo' ? this.undos : this.redos).pop();
    this.changed();
    return entry;
  }

  // The step's command left as commandId; `reverse` undoes the step itself.
  // commandId null: it never left, so the entry goes back.
  stepIssued(direction: 'undo' | 'redo', source: UndoEntry, commandId: string | null, reverse: UndoEntry | null): void {
    if (commandId === null) {
      this.restore(direction, source);
      return;
    }
    this.track(commandId, { entry: reverse, mode: direction, generation: this.generation, source });
    this.changed();
  }

  applied(commandId: string): void {
    const p = this.pending.get(commandId);
    if (!p) return;
    p.applied = true;
    this.settle(commandId, p);
  }

  // The session's overlays changed: any tracked command whose overlay ended
  // has landed in the projection.
  overlaysChanged(): void {
    for (const [id, p] of [...this.pending]) {
      if (!p.landed && !this.hasOverlay(id)) {
        p.landed = true;
        this.settle(id, p);
      }
    }
  }

  rejected(commandId: string): void {
    const p = this.pending.get(commandId);
    if (!p) return;
    this.pending.delete(commandId);
    if (p.source && p.mode !== 'edit' && p.generation === this.generation) this.restore(p.mode, p.source);
    else this.changed();
  }

  // Any tracked command still on its way: history waits for it.
  get busy(): boolean {
    return this.pending.size > 0;
  }

  get canUndo(): boolean {
    return !this.busy && this.undos.length > 0;
  }

  get canRedo(): boolean {
    return !this.busy && this.redos.length > 0;
  }

  private track(commandId: string, t: Omit<Tracked, 'applied' | 'landed'>): void {
    // Numbered at issue time: this is the entry's place in history.
    if (t.entry) this.order.set(t.entry, ++this.issued);
    this.pending.set(commandId, { ...t, applied: false, landed: !this.hasOverlay(commandId) });
  }

  private settle(commandId: string, p: Tracked): void {
    if (!p.applied || !p.landed) {
      this.changed();
      return;
    }
    this.pending.delete(commandId);
    if (p.entry) {
      if (p.mode === 'undo') {
        if (p.generation === this.generation) this.push(this.redos, p.entry);
      } else {
        this.push(this.undos, p.entry);
      }
    }
    this.changed();
  }

  private restore(direction: 'undo' | 'redo', entry: UndoEntry): void {
    this.push(direction === 'undo' ? this.undos : this.redos, entry);
    this.changed();
  }

  // Insert by issue order, so the top is always the latest ACTION, whatever
  // order the replies came in. A returned (rejected) entry keeps its number.
  private push(stack: UndoEntry[], entry: UndoEntry): void {
    const rank = this.order.get(entry) ?? Number.MAX_SAFE_INTEGER;
    let at = stack.length;
    while (at > 0 && (this.order.get(stack[at - 1] as UndoEntry) ?? 0) > rank) at -= 1;
    stack.splice(at, 0, entry);
    if (stack.length > this.limit) stack.splice(0, stack.length - this.limit);
  }

  private changed(): void {
    this.onChange({ canUndo: this.canUndo, canRedo: this.canRedo, next: this.undos.at(-1)?.label ?? null });
  }
}

// The entity a command targets, to read it as it was just before the command.
export function targetOf(draft: Draft): { kind: GmEntity['kind']; id: string } | null {
  const payload = draft.payload as Record<string, unknown>;
  const [noun] = draft.type.split('.');
  if (noun === 'fog' || noun === 'terrain') return { kind: noun, id: noun };
  if (noun === 'template') return { kind: 'template', id: String(payload.id) };
  if (draft.type === 'entity.delete') return { kind: payload.kind as GmEntity['kind'], id: String(payload.id) };
  if (noun === 'token' || noun === 'light' || noun === 'wall') return { kind: noun, id: String(payload.id) };
  if (draft.type === 'scene.update' && draft.sceneId) return { kind: 'scene', id: draft.sceneId };
  return null;
}

// What a token command shows while it is pending (also for undo and redo).
export function overlayFor(draft: Draft): import('./gmCommands').TokenOverlay | null {
  const payload = draft.payload as Record<string, unknown>;
  const id = String(payload.id);
  if (draft.type === 'token.move' || draft.type === 'token.update') {
    // Only what the render model draws directly; art waits for its frame (the
    // URL is built by the adapter from the projected asset id).
    const { id: _id, artAssetId: _art, locked: _locked, controllerIds: _c, actorId: _a, ...patch } = payload;
    if (Object.keys(patch).length === 0) return null;
    return { kind: 'token', id, patch: patch as Record<string, number> };
  }
  if (draft.type === 'entity.delete' && payload.kind === 'token') return { kind: 'token', id, deleted: true };
  if (draft.type === 'token.create') {
    return {
      kind: 'token',
      id,
      create: {
        id,
        x: payload.x as number,
        y: payload.y as number,
        size: payload.size as number,
        label: payload.label as string,
        color: payload.color as number,
      },
    };
  }
  return null;
}
