import type { ViewportControls } from '../actions/viewport';

export function ViewportButtons({ controls, enabled }: { controls: ViewportControls; enabled: boolean }) {
  return (
    <div role="group" aria-label="Map view">
      <button type="button" className="vtt-topbar-btn" aria-label="Zoom out" disabled={!enabled} onClick={controls.zoomOut}>-</button>
      <button type="button" className="vtt-topbar-btn" aria-label="Zoom in" disabled={!enabled} onClick={controls.zoomIn}>+</button>
      <button type="button" className="vtt-topbar-btn" disabled={!enabled} onClick={controls.fit}>Fit</button>
    </div>
  );
}
