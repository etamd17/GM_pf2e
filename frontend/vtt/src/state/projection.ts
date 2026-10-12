import { create } from 'zustand';
import type { RenderScene } from '../scene/renderModel';

// The scene currently on screen, in renderer form. Once VTT-ADR-0002 lands,
// the transport's adapter writes it here from sanitized snapshots and patches.
// Until then, development entries seed it with the reference scene.
//
// Writes are bound to an epoch, so stale writers fail closed
// (VTT-20261012-012 finding 1):
// - open() starts a new epoch, one per subscription, and returns its token.
//   Only a write carrying the current token is accepted, so an in-flight
//   snapshot from an older subscription cannot land after a resync.
// - clear() drops the scene and invalidates every outstanding token. It is
//   used for recoverable resets such as scene-changed or grant-changed.
// - seal() is the terminal path (revoked, version-mismatch). It clears, then
//   refuses every later open() and write until the page reloads, so revoked
//   campaign data cannot re-enter client memory.

export type ProjectionEpoch = number & { readonly __brand: 'ProjectionEpoch' };

interface ProjectionState {
  scene: RenderScene | null;
  epoch: number;
  sealed: boolean;
  open(): ProjectionEpoch | null;
  setScene(epoch: ProjectionEpoch, scene: RenderScene): boolean;
  clear(): void;
  seal(): void;
}

export const useProjection = create<ProjectionState>()((set, get) => ({
  scene: null,
  epoch: 0,
  sealed: false,
  open() {
    if (get().sealed) return null;
    const epoch = get().epoch + 1;
    set({ epoch, scene: null });
    return epoch as ProjectionEpoch;
  },
  setScene(epoch, scene) {
    const state = get();
    if (state.sealed || epoch !== state.epoch) return false;
    set({ scene });
    return true;
  },
  clear() {
    set({ scene: null, epoch: get().epoch + 1 });
  },
  seal() {
    set({ scene: null, epoch: get().epoch + 1, sealed: true });
  },
}));

// Test helper: return the store to its initial, unsealed state.
export function resetProjectionForTest(): void {
  useProjection.setState({ scene: null, epoch: 0, sealed: false });
}
