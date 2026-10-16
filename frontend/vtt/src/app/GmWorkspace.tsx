import { useState } from 'react';
import { ConnectionBanner } from '../components/ConnectionBanner';
import { MapHost } from '../components/MapHost';
import { acceptsDurableInput, isTerminal, useConnection } from '../state/connection';
import type { GmLive } from '../live/gm';
import { useProjection } from '../state/projection';

interface Props {
  sceneName: string;
  // The live wiring when served by Flask; null on the Vite dev server. The GM
  // tools (M4b) issue their commands through it.
  live?: GmLive | null;
}

const TOOLS = [
  { id: 'select', label: 'Select' },
  { id: 'measure', label: 'Measure' },
  { id: 'ping', label: 'Ping' },
  { id: 'walls', label: 'Walls' },
  { id: 'lights', label: 'Lights' },
  { id: 'fog', label: 'Fog' },
] as const;

// Map-first command deck: compact status bar, collapsible tool rail, map in the
// centre, contextual right dock, bottom tray. Tools are placeholders until the
// command contracts exist; they are visibly disabled rather than absent, so the
// layout can be reviewed at its real density.
export function GmWorkspace({ sceneName }: Props) {
  const status = useConnection((s) => s.status);
  const scene = useProjection((s) => s.scene);
  // A terminal state unmounts the map rather than covering it (see TableScreen).
  const showMap = scene !== null && !isTerminal(status);
  const renderer = useConnection((s) => s.renderer);
  const [zoom, setZoom] = useState(1);
  const [railOpen, setRailOpen] = useState(true);
  const [dockOpen, setDockOpen] = useState(true);
  const inputEnabled = acceptsDurableInput(status) && renderer === 'ok';

  return (
    <div className={`vtt-gm ${railOpen ? '' : 'is-rail-collapsed'} ${dockOpen ? '' : 'is-dock-collapsed'}`}>
      <header className="vtt-topbar">
        <span className="vtt-brand">VTT</span>
        <span className="vtt-scene-name">{sceneName}</span>
        <span className="vtt-topbar-spacer" />
        <span className="vtt-zoom" aria-label="Zoom level">
          {Math.round(zoom * 100)}%
        </span>
        <span className={`vtt-conn vtt-conn--${status}`}>{status}</span>
      </header>

      <ConnectionBanner status={status} renderer={renderer} variant="gm" />

      <nav className="vtt-rail" aria-label="Map tools">
        <button
          type="button"
          className="vtt-rail-toggle"
          aria-expanded={railOpen}
          onClick={() => setRailOpen((v) => !v)}
        >
          {railOpen ? 'Hide tools' : 'Tools'}
        </button>
        {railOpen &&
          TOOLS.map((tool) => (
            <button key={tool.id} type="button" className="vtt-tool" disabled={!inputEnabled || tool.id !== 'select'}>
              {tool.label}
            </button>
          ))}
      </nav>

      <main className="vtt-stage">
        {showMap && (
          <MapHost
            surface="gm"
            scene={scene}
            label={`Map: ${sceneName}`}
            onViewportChange={(v) => setZoom(v.scale)}
          />
        )}
      </main>

      <aside className="vtt-dock" aria-label="Inspector">
        <button
          type="button"
          className="vtt-dock-toggle"
          aria-expanded={dockOpen}
          onClick={() => setDockOpen((v) => !v)}
        >
          {dockOpen ? 'Hide inspector' : 'Inspector'}
        </button>
        {dockOpen && <p className="vtt-empty">Select a token to inspect it.</p>}
      </aside>

      <footer className="vtt-tray" aria-label="Initiative and chat">
        <span className="vtt-empty">No encounter running.</span>
      </footer>
    </div>
  );
}
