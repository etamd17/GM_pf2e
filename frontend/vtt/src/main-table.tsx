import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { TableScreen } from './app/TableScreen';
import { initialDevStatus } from './app/devStatus';
import { useConnection } from './state/connection';
import { useProjection } from './state/projection';
import { buildReferenceScene } from './spike/referenceScene';
import './styles/fonts';
import './styles/app.css';

// The table entry imports no GM workspace or authoring code. Keep it that way:
// src/bundleBoundary.test.ts fails if it starts to.
// Seed before the status: a terminal ?status= must clear it like a real one.
{
  const projection = useProjection.getState();
  const epoch = projection.open();
  if (epoch !== null) projection.setScene(epoch, buildReferenceScene());
}
const devStatus = initialDevStatus(window.location.search, import.meta.env.DEV);
useConnection.getState().setStatus(devStatus ?? (import.meta.env.DEV ? 'live' : 'connecting'));

const root = document.getElementById('root');
if (!root) throw new Error('missing #root');
createRoot(root).render(
  <StrictMode>
    <TableScreen />
  </StrictMode>,
);
