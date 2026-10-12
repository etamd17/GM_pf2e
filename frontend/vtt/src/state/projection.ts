import { create } from 'zustand';
import type { RenderScene } from '../scene/renderModel';

// The scene currently on screen, in renderer form. Once VTT-ADR-0002 lands,
// the transport's adapter writes it here from sanitized snapshots and patches.
// Until then, development entries seed it with the reference scene.
//
// clear() is the revocation path: a terminal connection state drops the scene
// synchronously, so a revoked table or removed member keeps nothing to draw
// (VTT-20261012-010 finding 1).

interface ProjectionState {
  scene: RenderScene | null;
  setScene(scene: RenderScene): void;
  clear(): void;
}

export const useProjection = create<ProjectionState>()((set) => ({
  scene: null,
  setScene(scene) {
    set({ scene });
  },
  clear() {
    set({ scene: null });
  },
}));
