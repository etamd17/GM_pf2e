import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GmWorkspace } from './app/GmWorkspace';
import { initialDevStatus } from './app/devStatus';
import { gmBoot, readBoot } from './live/boot';
import { startGm, type GmLive } from './live/gm';
import { useConnection } from './state/connection';
import { useProjection } from './state/projection';
import { buildReferenceScene } from './spike/referenceScene';
import './styles/fonts';
import './styles/app.css';

const queryClient = new QueryClient();

// Served by Flask (/vtt/gm) the page carries its boot data and connects for
// real. Without boot data (the Vite dev server, which has no backend) it shows
// the reference scene and ?status= can force a connection state.
const boot = gmBoot(readBoot());
let live: GmLive | null = null;
if (boot) {
  live = startGm(boot);
} else {
  // Seed before the status: a terminal ?status= must clear it like a real one.
  const projection = useProjection.getState();
  const epoch = projection.open();
  if (epoch !== null) projection.setScene(epoch, buildReferenceScene());
  const devStatus = initialDevStatus(window.location.search, import.meta.env.DEV);
  useConnection.getState().setStatus(devStatus ?? (import.meta.env.DEV ? 'live' : 'connecting'));
}

const root = document.getElementById('root');
if (!root) throw new Error('missing #root');
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <GmWorkspace sceneName={live ? 'Live scene' : 'Reference scene'} live={live} />
    </QueryClientProvider>
  </StrictMode>,
);
