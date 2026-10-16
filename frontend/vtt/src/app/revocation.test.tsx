// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_SCENE } from '../scene/renderModel';
import { useConnection } from '../state/connection';
import { resetProjectionForTest, useProjection, type ProjectionEpoch } from '../state/projection';

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
    setSelection() {}
    setWallDraft() {}
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

let epoch: ProjectionEpoch;

beforeEach(() => {
  runtimes.length = 0;
  useConnection.setState({ status: 'live', renderer: 'ok' });
  resetProjectionForTest();
  const opened = useProjection.getState().open();
  if (opened === null) throw new Error('projection unexpectedly sealed');
  epoch = opened;
  useProjection.getState().setScene(epoch, EMPTY_SCENE);
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

  it.each(['revoked', 'version-mismatch'] as const)(
    'a late write after %s neither remounts the map nor repopulates memory',
    (terminal) => {
      const { container } = render(view());
      act(() => useConnection.getState().setStatus(terminal));
      // An in-flight snapshot from the old subscription lands late.
      let accepted = true;
      act(() => {
        accepted = useProjection.getState().setScene(epoch, EMPTY_SCENE);
      });
      expect(accepted).toBe(false);
      expect(useProjection.getState().scene).toBeNull();
      // A brand-new subscription cannot open either: only a reload leaves.
      expect(useProjection.getState().open()).toBeNull();
      expect(container.querySelector('.vtt-map-host')).toBeNull();
    },
  );
});

it('version-mismatch also unmounts the map', () => {
  const { container } = render(<TableScreen />);
  act(() => useConnection.getState().setStatus('version-mismatch'));
  expect(container.querySelector('.vtt-map-host')).toBeNull();
  expect(useProjection.getState().scene).toBeNull();
});
