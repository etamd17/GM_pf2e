import { create } from 'zustand';
import type { TokenOverlay } from '../app/gmCommands';

// Pending commands' optimistic overlays, as the session reports them. The
// session alone decides when one ends (correlated frame, replacement snapshot,
// or a rejection); this store only mirrors it for rendering. GM only.

interface OverlayState {
  overlays: ReadonlyMap<string, TokenOverlay | null>;
  set(overlays: ReadonlyMap<string, TokenOverlay | null>): void;
}

export const useOverlays = create<OverlayState>()((set) => ({
  overlays: new Map(),
  set: (overlays) => set({ overlays }),
}));
