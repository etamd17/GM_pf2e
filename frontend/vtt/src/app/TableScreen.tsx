import { ConnectionBanner } from '../components/ConnectionBanner';
import { MapHost } from '../components/MapHost';
import type { RenderScene } from '../scene/renderModel';
import { useConnection } from '../state/connection';

// Display-only table: the whole viewport is the map. No controls, no focus,
// no authoring layers (see layersFor('table')). What it draws must come from a
// dedicated server-side table projection, never a GM payload with the controls
// hidden (VTT-ADR-0001, VTT-20261012-006 D6).
export function TableScreen({ scene }: { scene: RenderScene }) {
  const status = useConnection((s) => s.status);
  const renderer = useConnection((s) => s.renderer);
  return (
    <div className="vtt-table">
      <MapHost surface="table" scene={scene} label="Table display" />
      <ConnectionBanner status={status} renderer={renderer} variant="table" />
    </div>
  );
}
