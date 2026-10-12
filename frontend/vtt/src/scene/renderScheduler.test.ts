import { describe, expect, it } from 'vitest';
import { RenderScheduler, type FrameClock } from './renderScheduler';

// A manual clock: frames run only when the test says so.
function manualClock() {
  let next = 1;
  const pending = new Map<number, (t: number) => void>();
  const clock: FrameClock = {
    request(cb) {
      const handle = next++;
      pending.set(handle, cb);
      return handle;
    },
    cancel(handle) {
      pending.delete(handle);
    },
  };
  return {
    clock,
    get pending() {
      return pending.size;
    },
    flush(time = 0) {
      const callbacks = [...pending.values()];
      pending.clear();
      for (const cb of callbacks) cb(time);
    },
  };
}

describe('RenderScheduler', () => {
  it('coalesces many requests into one frame', () => {
    const c = manualClock();
    let frames = 0;
    const s = new RenderScheduler(() => (frames += 1), c.clock);
    s.requestRender();
    s.requestRender();
    s.requestRender();
    expect(c.pending).toBe(1);
    c.flush();
    expect(frames).toBe(1);
    expect(c.pending).toBe(0); // idle: no frames scheduled
  });

  it('keeps rendering while a lease is held and stops after release', () => {
    const c = manualClock();
    let frames = 0;
    const s = new RenderScheduler(() => (frames += 1), c.clock);
    const release = s.lease();
    c.flush();
    c.flush();
    c.flush();
    expect(frames).toBe(3);
    release();
    release(); // double release is harmless
    expect(s.activeLeases).toBe(0);
    c.flush(); // the frame already queued still runs
    expect(c.pending).toBe(0);
    expect(frames).toBe(4);
  });

  it('does nothing after dispose', () => {
    const c = manualClock();
    let frames = 0;
    const s = new RenderScheduler(() => (frames += 1), c.clock);
    s.requestRender();
    s.dispose();
    c.flush();
    s.requestRender();
    expect(frames).toBe(0);
    expect(c.pending).toBe(0);
  });
});
