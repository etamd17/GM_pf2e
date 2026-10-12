// Event-driven rendering: the map draws one frame when something changes, not
// sixty a second. Carried over from the legacy map (stage 6a), where idle
// rendering was the GM view's single biggest avoidable cost.
//
// - requestRender() coalesces any number of requests into the next frame.
// - lease() keeps frames coming while an animation (ping, tween) is running;
//   the returned function ends it. Frames stop when no lease is held.

export type FrameCallback = (timeMs: number) => void;

export interface FrameClock {
  request(cb: (timeMs: number) => void): number;
  cancel(handle: number): void;
}

export const browserClock: FrameClock = {
  request: (cb) => requestAnimationFrame(cb),
  cancel: (handle) => cancelAnimationFrame(handle),
};

export class RenderScheduler {
  private handle: number | null = null;
  private leases = 0;
  private disposed = false;

  constructor(
    private readonly onFrame: FrameCallback,
    private readonly clock: FrameClock = browserClock,
  ) {}

  requestRender(): void {
    if (this.disposed || this.handle !== null) return;
    this.handle = this.clock.request(this.tick);
  }

  lease(): () => void {
    this.leases += 1;
    this.requestRender();
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.leases -= 1;
    };
  }

  get activeLeases(): number {
    return this.leases;
  }

  dispose(): void {
    this.disposed = true;
    if (this.handle !== null) this.clock.cancel(this.handle);
    this.handle = null;
  }

  private readonly tick = (timeMs: number): void => {
    this.handle = null;
    if (this.disposed) return;
    this.onFrame(timeMs);
    if (this.leases > 0) this.requestRender();
  };
}
