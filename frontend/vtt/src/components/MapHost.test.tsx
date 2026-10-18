// @vitest-environment jsdom
import { StrictMode } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_SCENE } from '../scene/renderModel';
import { useConnection } from '../state/connection';

// VTT-20261012-010 finding 3: a late init() rejection or status callback from a
// cleaned-up runtime must not poison its replacement.

interface FakeRuntime {
  destroyed: boolean;
  reject: (e: unknown) => void;
  status: (s: 'ok' | 'lost' | 'restoring' | 'failed') => void;
}

const runtimes = vi.hoisted(() => [] as FakeRuntime[]);

vi.mock('../scene/SceneRuntime', () => ({
  SceneRuntime: class {
    destroyed = false;
    reject: (e: unknown) => void = () => {};
    status: FakeRuntime['status'];
    constructor(_host: HTMLElement, options: { onRendererStatus?: FakeRuntime['status'] }) {
      this.status = (s) => options.onRendererStatus?.(s);
      runtimes.push(this);
    }
    setScene() {}
    setTransients() {}
    setSelection() {}
    setWallDraft() {}
    init() {
      return new Promise<void>((_resolve, reject) => {
        this.reject = reject;
      });
    }
    destroy() {
      this.destroyed = true;
    }
  },
}));

const { MapHost } = await import('./MapHost');

beforeEach(() => {
  runtimes.length = 0;
  useConnection.setState({ status: 'live', renderer: 'ok' });
});

afterEach(cleanup);

describe('MapHost runtime lifecycle', () => {
  it('ignores a late init rejection from a runtime replaced by StrictMode remount', async () => {
    render(
      <StrictMode>
        <MapHost surface="table" scene={EMPTY_SCENE} label="t" />
      </StrictMode>,
    );
    // StrictMode mounts, cleans up and mounts again: two runtimes, first destroyed.
    expect(runtimes).toHaveLength(2);
    expect(runtimes[0]?.destroyed).toBe(true);
    await act(async () => runtimes[0]?.reject(new Error('late')));
    expect(useConnection.getState().renderer).toBe('ok');
  });

  it('ignores a late rejection after unmount and remount', async () => {
    const first = render(<MapHost surface="gm" scene={EMPTY_SCENE} label="a" />);
    first.unmount();
    render(<MapHost surface="gm" scene={EMPTY_SCENE} label="b" />);
    await act(async () => runtimes[0]?.reject(new Error('late')));
    expect(useConnection.getState().renderer).toBe('ok');
  });

  it('ignores status callbacks from a destroyed runtime', () => {
    const first = render(<MapHost surface="gm" scene={EMPTY_SCENE} label="a" />);
    first.unmount();
    render(<MapHost surface="gm" scene={EMPTY_SCENE} label="b" />);
    act(() => runtimes[0]?.status('lost'));
    expect(useConnection.getState().renderer).toBe('ok');
  });

  it('still reports a failure from the current runtime', async () => {
    render(<MapHost surface="gm" scene={EMPTY_SCENE} label="a" />);
    await act(async () => runtimes.at(-1)?.reject(new Error('no webgl')));
    expect(useConnection.getState().renderer).toBe('failed');
  });

  it('resets a failed renderer state when a new runtime mounts', () => {
    useConnection.setState({ renderer: 'failed' });
    render(<MapHost surface="gm" scene={EMPTY_SCENE} label="a" />);
    expect(useConnection.getState().renderer).toBe('ok');
  });
});
