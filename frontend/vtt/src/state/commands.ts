import { create } from 'zustand';
import type { CommandOutcome, PendingView } from '../protocol/commands';

// Pending commands (for the "waiting" markers) and the latest outcome worth
// telling the GM about (rejections, reauthentication). GM only.

interface CommandState {
  pending: ReadonlyArray<PendingView>;
  notice: CommandOutcome | null;
  // The id of the most recent applied command (a scene create waits on it).
  lastApplied: string | null;
  setPending(pending: ReadonlyArray<PendingView>): void;
  notify(outcome: CommandOutcome): void;
  dismiss(): void;
}

export const useCommands = create<CommandState>()((set) => ({
  pending: [],
  notice: null,
  lastApplied: null,
  setPending: (pending) => set({ pending }),
  notify: (outcome) =>
    set(outcome.kind === 'applied' ? { notice: null, lastApplied: outcome.commandId } : { notice: outcome }),
  dismiss: () => set({ notice: null }),
}));
