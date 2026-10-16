import type { GmEntity } from '../protocol/frames';
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
    case 'wall.create': {
      const kind = draft.type.split('.')[0] as 'token' | 'light' | 'wall';
      const id = String(payload.id);
      // Undoing a create deletes what was created, if it is still there.
      return {
        kind,
        id,
        label,
        make: (now) => (now && now.kind === kind ? deleteEntity(now as Of<'token'> | Of<'light'> | Of<'wall'>) : null),
      };
    }
    case 'entity.delete': {
      if (!before || before.kind === 'scene') return null;
      const snapshot = before;
      // Undoing a delete re-creates it under the same id, unless something
      // with that id exists again.
      return { kind: snapshot.kind, id: snapshot.id, label, make: (now) => (now ? null : recreate(snapshot)) };
    }
    default:
      return null;
  }
}

type Mode = 'edit' | 'undo' | 'redo';

// Applied edits, in order. An entry joins a stack only when its command is
// applied (an ack alone is enough: the change committed), so a rejected edit
// never becomes undoable. A new edit clears redo.
export class UndoManager {
  private undos: UndoEntry[] = [];
  private redos: UndoEntry[] = [];
  private readonly pending = new Map<string, { entry: UndoEntry; mode: Mode }>();

  constructor(
    private readonly limit = 100,
    private readonly onChange: (state: { canUndo: boolean; canRedo: boolean; next: string | null }) => void = () => {},
  ) {}

  track(commandId: string, entry: UndoEntry, mode: Mode): void {
    this.pending.set(commandId, { entry, mode });
  }

  applied(commandId: string): void {
    const p = this.pending.get(commandId);
    if (!p) return;
    this.pending.delete(commandId);
    if (p.mode === 'undo') this.redos.push(p.entry);
    else {
      this.undos.push(p.entry);
      if (p.mode === 'edit') this.redos = [];
    }
    if (this.undos.length > this.limit) this.undos.splice(0, this.undos.length - this.limit);
    if (this.redos.length > this.limit) this.redos.splice(0, this.redos.length - this.limit);
    this.changed();
  }

  settled(commandId: string): void {
    this.pending.delete(commandId);
  }

  popUndo(): UndoEntry | undefined {
    const entry = this.undos.pop();
    this.changed();
    return entry;
  }

  popRedo(): UndoEntry | undefined {
    const entry = this.redos.pop();
    this.changed();
    return entry;
  }

  get canUndo(): boolean {
    return this.undos.length > 0;
  }

  get canRedo(): boolean {
    return this.redos.length > 0;
  }

  private changed(): void {
    this.onChange({ canUndo: this.canUndo, canRedo: this.canRedo, next: this.undos.at(-1)?.label ?? null });
  }
}

// The entity a command targets, to read it as it was just before the command.
export function targetOf(draft: Draft): { kind: GmEntity['kind']; id: string } | null {
  const payload = draft.payload as Record<string, unknown>;
  const [noun] = draft.type.split('.');
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
    const { id: _id, ...patch } = payload;
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
