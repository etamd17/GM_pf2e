import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { TableScreen } from './app/TableScreen';
import { initialDevStatus } from './app/devStatus';
import { useConnection } from './state/connection';
import { buildReferenceScene } from './spike/referenceScene';
import './styles/app.css';

// The table entry imports no GM workspace or authoring code. Keep it that way:
// src/bundleBoundary.test.ts fails if it starts to.
const devStatus = initialDevStatus(window.location.search, import.meta.env.DEV);
useConnection.getState().setStatus(devStatus ?? (import.meta.env.DEV ? 'live' : 'connecting'));
const scene = buildReferenceScene();

const root = document.getElementById('root');
if (!root) throw new Error('missing #root');
createRoot(root).render(
  <StrictMode>
    <TableScreen scene={scene} />
  </StrictMode>,
);
