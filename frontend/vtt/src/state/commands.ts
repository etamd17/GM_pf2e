import { create } from 'zustand';
import type { CommandOutcome, PendingView } from '../protocol/commands';

// Pending commands (for the "waiting" markers) and the latest outcome worth
// telling the GM about (rejections, reauthentication). GM only.

interface CommandState {
  pending: ReadonlyArray<PendingView>;
  notice: CommandOutcome | null;
  // The id of the most recent applied command (a scene create waits on it).
  lastApplied: string | null;
  undo: { canUndo: boolean; canRedo: boolean; next: string | null };
  setUndo(undo: { canUndo: boolean; canRedo: boolean; next: string | null }): void;
  setPending(pending: ReadonlyArray<PendingView>): void;
  notify(outcome: CommandOutcome): void;
  dismiss(): void;
}

export const useCommands = create<CommandState>()((set) => ({
  pending: [],
  notice: null,
  lastApplied: null,
  undo: { canUndo: false, canRedo: false, next: null },
  setUndo: (undo) => set({ undo }),
  setPending: (pending) => set({ pending }),
  notify: (outcome) =>
    set(outcome.kind === 'applied' ? { notice: null, lastApplied: outcome.commandId } : { notice: outcome }),
  dismiss: () => set({ notice: null }),
}));
