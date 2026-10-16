import { ConnectionBanner } from '../components/ConnectionBanner';
import { MapHost } from '../components/MapHost';
import { isTerminal, useConnection } from '../state/connection';
import { useProjection } from '../state/projection';

// Display-only table: the whole viewport is the map. No controls, no focus,
// no authoring layers (see layersFor('table')). What it draws must come from a
// dedicated server-side table projection, never a GM payload with the controls
// hidden (VTT-ADR-0001, VTT-ADR-0002 section 2).
//
// On a terminal state the map is unmounted, not covered: the Pixi runtime is
// destroyed and the projection is already cleared, so a revoked table keeps
// nothing on screen or in memory to draw.
export function TableScreen() {
  const status = useConnection((s) => s.status);
  const renderer = useConnection((s) => s.renderer);
  const scene = useProjection((s) => s.scene);
  const showMap = scene !== null && !isTerminal(status);
  return (
    <div className="vtt-table">
      {showMap && <MapHost surface="table" scene={scene} label="Table display" />}
      <ConnectionBanner status={status} renderer={renderer} variant="table" />
    </div>
  );
}
