import { useEffect, useRef } from 'react';
import { useTransients } from '../state/transients';
import { SceneRuntime, type RuntimeInteraction } from '../scene/SceneRuntime';
import type { Hit, Point } from '../scene/interaction';
import type { Surface } from '../scene/layers';
import type { RenderScene } from '../scene/renderModel';
import type { Viewport } from '../scene/viewport';
import { useConnection } from '../state/connection';

interface Props {
  surface: Surface;
  scene: RenderScene;
  onViewportChange?: (viewport: Viewport) => void;
  label: string;
  // GM only (M4b): map input, the selection and a wall being drawn.
  interaction?: RuntimeInteraction;
  selection?: Hit | null;
  wallDraft?: { a: Point; b: Point } | null;
}

declare global {
  interface Window {
    __vttRuntime?: SceneRuntime;
    __vttProbe?: () => ReturnType<SceneRuntime['probe']>;
  }
}

// The browser harness sets localStorage "vtt.probe" to "1" before the page
// loads; only then is the render probe exposed. It reads what the runtime is
// drawing, nothing the page does not already hold.
function probeEnabled(): boolean {
  try {
    return window.localStorage.getItem('vtt.probe') === '1';
  } catch {
    return false;
  }
}

// The only bridge between React and Pixi: one div, one SceneRuntime. React
// re-renders never reach the scene graph; scene changes are pushed through
// runtime.setScene, which diffs by object identity.
export function MapHost({ surface, scene, onViewportChange, label, interaction, selection = null, wallDraft = null }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<SceneRuntime | null>(null);
  const viewportCb = useRef(onViewportChange);
  viewportCb.current = onViewportChange;
  const latestScene = useRef(scene);
  latestScene.current = scene;
  // The runtime is built once per surface; it calls through this ref, so the
  // workspace's latest callbacks apply without rebuilding the renderer.
  const interactionRef = useRef(interaction);
  interactionRef.current = interaction;
  const latestSelection = useRef(selection);
  latestSelection.current = selection;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    // Each mount gets its own liveness flag. Callbacks and a late init()
    // rejection from a runtime that has since been cleaned up (StrictMode,
    // unmount, surface change) must not write the shared renderer state of its
    // replacement (VTT-20261012-010 finding 3).
    let alive = true;
    const isCurrent = () => alive && runtimeRef.current === runtime;
    const runtime = new SceneRuntime(host, {
      surface,
      onRendererStatus: (status) => {
        if (isCurrent()) useConnection.getState().setRenderer(status);
      },
      onViewportChange: (v) => {
        if (isCurrent()) viewportCb.current?.(v);
      },
      ...(surface === 'gm'
        ? {
            interaction: {
              onClick: (p, hit, modifiers) => interactionRef.current?.onClick?.(p, hit, modifiers),
              onTokenDrop: (id, x, y) => interactionRef.current?.onTokenDrop?.(id, x, y),
              canDragToken: (id) => interactionRef.current?.canDragToken?.(id) ?? false,
              onTemplateDrop: (id, dx, dy) => interactionRef.current?.onTemplateDrop?.(id, dx, dy),
              canDragTemplate: (id) => interactionRef.current?.canDragTemplate?.(id) ?? false,
              measuring: () => interactionRef.current?.measuring?.() ?? false,
              onMeasure: (points, done) => interactionRef.current?.onMeasure?.(points, done),
              onPointerMove: (p) => interactionRef.current?.onPointerMove?.(p),
              onKey: (e) => interactionRef.current?.onKey?.(e) ?? false,
            } satisfies RuntimeInteraction,
          }
        : {}),
    });
    runtimeRef.current = runtime;
    useConnection.getState().setRenderer('ok');
    runtime.setScene(latestScene.current);
    runtime.setSelection(latestSelection.current);
    runtime.init().catch(() => {
      if (isCurrent()) useConnection.getState().setRenderer('failed');
    });
    if (import.meta.env.DEV) window.__vttRuntime = runtime;
    if (probeEnabled()) window.__vttProbe = () => runtime.probe();
    return () => {
      alive = false;
      runtime.destroy();
      runtimeRef.current = null;
      if (window.__vttRuntime === runtime) delete window.__vttRuntime;
      delete window.__vttProbe;
    };
  }, [surface]);

  useEffect(() => {
    runtimeRef.current?.setScene(scene);
  }, [scene]);

  useEffect(() => {
    runtimeRef.current?.setSelection(selection);
  }, [selection]);

  useEffect(() => {
    runtimeRef.current?.setWallDraft(wallDraft);
  }, [wallDraft]);

  // Live pings and rulers (beta-1), on both surfaces. Expired ones are pruned
  // a few times a second; a new snapshot clears them (the live wiring does).
  useEffect(() => {
    const push = () => runtimeRef.current?.setTransients([...useTransients.getState().live.values()]);
    push();
    const unsubscribe = useTransients.subscribe(push);
    const timer = window.setInterval(() => useTransients.getState().prune(), 250);
    return () => {
      unsubscribe();
      window.clearInterval(timer);
    };
  }, [surface]);

  // The GM map is keyboard-focusable (arrows pan, +/- zoom, 0 fits). The table
  // has no operator, so it takes no focus.
  return (
    <div
      ref={hostRef}
      className={`vtt-map-host vtt-map-host--${surface}`}
      role="application"
      aria-label={label}
      aria-roledescription="map"
      tabIndex={surface === 'gm' ? 0 : -1}
    />
  );
}
