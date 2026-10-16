import { create } from 'zustand';
import type { CommandOutcome, PendingView } from '../protocol/commands';

// Pending commands (for the "waiting" markers) and the latest outcome worth
// telling the GM about (rejections, reauthentication). GM only.

interface CommandState {
  pending: ReadonlyArray<PendingView>;
  notice: CommandOutcome | null;
  setPending(pending: ReadonlyArray<PendingView>): void;
  notify(outcome: CommandOutcome): void;
  dismiss(): void;
}

export const useCommands = create<CommandState>()((set) => ({
  pending: [],
  notice: null,
  setPending: (pending) => set({ pending }),
  notify: (outcome) => set({ notice: outcome.kind === 'applied' ? null : outcome }),
  dismiss: () => set({ notice: null }),
}));
