// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_SCENE } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { useProjection } from '../state/projection';

// VTT-20261012-010 finding 1: a terminal state must clear the projection and
// destroy the Pixi runtime, not just cover the map with a banner.

const runtimes = vi.hoisted(() => [] as Array<{ destroyed: boolean; surface: string }>);

vi.mock('../scene/SceneRuntime', () => ({
  SceneRuntime: class {
    destroyed = false;
    surface: string;
    constructor(_host: HTMLElement, options: { surface: string }) {
      this.surface = options.surface;
      runtimes.push(this);
    }
    setScene() {}
    init() {
      return Promise.resolve();
    }
    destroy() {
      this.destroyed = true;
    }
  },
}));

const { TableScreen } = await import('./TableScreen');
const { GmWorkspace } = await import('./GmWorkspace');

beforeEach(() => {
  runtimes.length = 0;
  useConnection.setState({ status: 'live', renderer: 'ok' });
  useProjection.setState({ scene: EMPTY_SCENE });
});

afterEach(cleanup);

describe.each([
  ['table', () => <TableScreen />],
  ['gm', () => <GmWorkspace sceneName="Test" />],
])('%s surface on revocation', (surface, view) => {
  it('unmounts the map, destroys the runtime and keeps no scene', () => {
    const { container } = render(view());
    expect(container.querySelector('.vtt-map-host')).not.toBeNull();
    expect(runtimes.at(-1)?.surface).toBe(surface);

    act(() => useConnection.getState().setStatus('revoked'));

    expect(container.querySelector('.vtt-map-host')).toBeNull();
    expect(container.querySelector('canvas')).toBeNull();
    expect(runtimes.every((r) => r.destroyed)).toBe(true);
    expect(useProjection.getState().scene).toBeNull();
    expect(screen.getByRole('alert').textContent).toContain('Access ended');
  });

  it('shows revocation even while the renderer is lost', () => {
    useConnection.setState({ renderer: 'lost' });
    render(view());
    act(() => useConnection.getState().setStatus('revoked'));
    expect(screen.getByRole('alert').textContent).toContain('Access ended');
  });

  it('does not remount the map if a scene arrives after revocation', () => {
    const { container } = render(view());
    act(() => useConnection.getState().setStatus('revoked'));
    act(() => useProjection.getState().setScene(EMPTY_SCENE));
    expect(container.querySelector('.vtt-map-host')).toBeNull();
  });
});

it('version-mismatch also unmounts the map', () => {
  const { container } = render(<TableScreen />);
  act(() => useConnection.getState().setStatus('version-mismatch'));
  expect(container.querySelector('.vtt-map-host')).toBeNull();
  expect(useProjection.getState().scene).toBeNull();
});
