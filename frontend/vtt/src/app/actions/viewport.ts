// Viewport actions for the layout's zoom +/- and Fit buttons (handoff 090).
// Published by Claude, called by Codex's controls. The workspace creates one
// controller, passes it to MapHost (which binds it to the live SceneRuntime)
// and calls it from its buttons. The zoom maths stays in the runtime: zoom
// keeps the view's centre and the runtime's zoom limits, as the + and - keys
// do; fit frames the whole scene and lets later resizes re-fit, as the 0 key
// does. Before MapHost binds (or after it unmounts) the calls do nothing.
//
//   const viewport = useMemo(createViewportController, []);
//   <MapHost viewportController={viewport} onViewportChange={(v) => setZoom(v.scale)} ... />
//   <button onClick={viewport.zoomIn}>+</button>

export interface ViewportControls {
  zoomIn(): void;
  zoomOut(): void;
  fit(): void;
}

export interface ViewportController extends ViewportControls {
  // MapHost only: attach the live runtime, or detach with null.
  bind(target: ViewportControls | null): void;
  readonly bound: boolean;
}

export function createViewportController(): ViewportController {
  let target: ViewportControls | null = null;
  return {
    zoomIn: () => target?.zoomIn(),
    zoomOut: () => target?.zoomOut(),
    fit: () => target?.fit(),
    bind: (next) => {
      target = next;
    },
    get bound() {
      return target !== null;
    },
  };
}
