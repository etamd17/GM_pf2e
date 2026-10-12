import { useEffect, useRef } from 'react';
import { SceneRuntime } from '../scene/SceneRuntime';
import type { Surface } from '../scene/layers';
import type { RenderScene } from '../scene/renderModel';
import type { Viewport } from '../scene/viewport';
import { useConnection } from '../state/connection';

interface Props {
  surface: Surface;
  scene: RenderScene;
  onViewportChange?: (viewport: Viewport) => void;
  label: string;
}

declare global {
  interface Window {
    __vttRuntime?: SceneRuntime;
  }
}

// The only bridge between React and Pixi: one div, one SceneRuntime. React
// re-renders never reach the scene graph; scene changes are pushed through
// runtime.setScene, which diffs by object identity.
export function MapHost({ surface, scene, onViewportChange, label }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const runtimeRef = useRef<SceneRuntime | null>(null);
  const viewportCb = useRef(onViewportChange);
  viewportCb.current = onViewportChange;
  const latestScene = useRef(scene);
  latestScene.current = scene;

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
    });
    runtimeRef.current = runtime;
    useConnection.getState().setRenderer('ok');
    runtime.setScene(latestScene.current);
    runtime.init().catch(() => {
      if (isCurrent()) useConnection.getState().setRenderer('failed');
    });
    if (import.meta.env.DEV) window.__vttRuntime = runtime;
    return () => {
      alive = false;
      runtime.destroy();
      runtimeRef.current = null;
      if (window.__vttRuntime === runtime) delete window.__vttRuntime;
    };
  }, [surface]);

  useEffect(() => {
    runtimeRef.current?.setScene(scene);
  }, [scene]);

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
