import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { GmWorkspace } from './app/GmWorkspace';
import { initialDevStatus } from './app/devStatus';
import { useConnection } from './state/connection';
import { buildReferenceScene } from './spike/referenceScene';
import './styles/app.css';

const queryClient = new QueryClient();

// No transport yet: development builds show the reference scene and can force
// a connection state with ?status=. Production builds stay in 'connecting'.
const devStatus = initialDevStatus(window.location.search, import.meta.env.DEV);
useConnection.getState().setStatus(devStatus ?? (import.meta.env.DEV ? 'live' : 'connecting'));
const scene = buildReferenceScene();

const root = document.getElementById('root');
if (!root) throw new Error('missing #root');
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <GmWorkspace scene={scene} sceneName="Reference scene" />
    </QueryClientProvider>
  </StrictMode>,
);
