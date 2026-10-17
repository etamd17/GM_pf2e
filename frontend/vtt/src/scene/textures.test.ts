import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MAX_RETRY_MS, RETRY_MS, TextureLoader } from './textures';

const URL_GM = '/vtt/media/camp_a/asset_abc';
const URL_TABLE = '/vtt/table-media/asset_abc';

// Plain response objects: a real Response body is read on Node's I/O queue,
// which fake time does not drive.
const ok = () => ({ ok: true, status: 200, blob: async () => new Blob([new Uint8Array([1, 2, 3])]) }) as Response;
const fail = (status: number) => () => ({ ok: false, status, blob: async () => new Blob([]) }) as Response;

function setup(responses: Array<() => Response | Promise<Response>>, decode?: () => Promise<ImageBitmap>) {
  const loaded: string[] = [];
  const destroyed: string[] = [];
  const fetcher = vi.fn(async () => (responses.shift() ?? ok)());
  const loader = new TextureLoader<string>(
    {
      fetcher: fetcher as unknown as typeof fetch,
      decode: decode ?? (async () => ({ width: 1 }) as unknown as ImageBitmap),
      toTexture: () => 'texture',
      destroy: (t) => destroyed.push(t),
    },
    (url) => loaded.push(url),
  );
  return { loader, fetcher, loaded, destroyed };
}

// Let pending promise callbacks run without moving the clock.
const settle = () => vi.advanceTimersByTimeAsync(0);

// Only the timers the loader uses: Node's Response.blob() needs real setImmediate.
beforeEach(() => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }));
afterEach(() => vi.useRealTimers());

describe('TextureLoader', () => {
  it('loads a media URL once, same-origin, and announces it', async () => {
    const t = setup([ok]);
    expect(t.loader.get(URL_GM)).toBeNull();
    expect(t.loader.get(URL_GM)).toBeNull(); // still loading: not fetched twice
    await settle();
    expect(t.loader.get(URL_GM)).toBe('texture');
    expect(t.loaded).toEqual([URL_GM]);
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    expect(t.fetcher).toHaveBeenCalledWith(URL_GM, { credentials: 'same-origin' });
  });

  it('never fetches anything but a media URL', () => {
    const t = setup([ok]);
    for (const bad of ['https://evil.example/x.png', '/vtt/media/../secret', 'javascript:alert(1)', '/vtt/assets/app.js', undefined]) {
      expect(t.loader.get(bad)).toBeNull();
    }
    expect(t.fetcher).not.toHaveBeenCalled();
  });

  it('an idle display recovers on its own after a transient failure (handoff 055)', async () => {
    const t = setup([fail(503), ok]);
    t.loader.get(URL_TABLE); // asked once, never again
    await settle();
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    expect(t.loaded).toEqual([URL_TABLE]);
    expect(t.loader.get(URL_TABLE)).toBe('texture');
  });

  it('retries back off and are bounded, and a lookup does not fetch early', async () => {
    const t = setup([fail(404), fail(503), () => Promise.reject(new TypeError('offline')), fail(503), fail(503), ok]);
    t.loader.get(URL_TABLE);
    await settle();
    t.loader.get(URL_TABLE); // waiting for its retry: no extra fetch
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    // 10 s, 20 s, 40 s, then capped at 60 s
    for (const [wait, calls] of [[RETRY_MS, 2], [2 * RETRY_MS, 3], [4 * RETRY_MS, 4], [MAX_RETRY_MS, 5], [MAX_RETRY_MS, 6]] as const) {
      await vi.advanceTimersByTimeAsync(wait - 1);
      expect(t.fetcher).toHaveBeenCalledTimes(calls - 1);
      await vi.advanceTimersByTimeAsync(1);
      expect(t.fetcher).toHaveBeenCalledTimes(calls);
    }
    expect(t.loaded).toEqual([URL_TABLE]);
  });

  it('an undecodable image is final and is not retried', async () => {
    const t = setup([ok], async () => {
      throw new Error('bad image');
    });
    t.loader.get(URL_GM);
    await settle();
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    expect(t.loader.get(URL_GM)).toBeNull();
    expect(t.loader.state(URL_GM)).toBe('failed');
    expect(t.loaded).toEqual([]);
  });

  it('an image the scene no longer uses stops retrying', async () => {
    const t = setup([fail(503), fail(503)]);
    t.loader.get(URL_TABLE);
    await settle();
    t.loader.keep([]); // it left the scene
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('an image the scene still uses keeps its retry', async () => {
    const t = setup([fail(503), ok]);
    t.loader.get(URL_TABLE);
    await settle();
    t.loader.keep([URL_TABLE]);
    await vi.advanceTimersByTimeAsync(RETRY_MS);
    expect(t.loaded).toEqual([URL_TABLE]);
  });

  it('disposal cancels pending retries and destroys textures, including late ones', async () => {
    const t = setup([ok, fail(503)]);
    t.loader.get(URL_GM);
    await settle();
    t.loader.get(URL_TABLE);
    await settle();
    expect(vi.getTimerCount()).toBe(1); // URL_TABLE waiting to retry
    t.loader.dispose();
    expect(vi.getTimerCount()).toBe(0);
    expect(t.destroyed).toEqual(['texture']);
    await vi.advanceTimersByTimeAsync(10 * MAX_RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    expect(t.loaded).toEqual([URL_GM]);
  });

  it('reports each URL state for the test probe', async () => {
    const t = setup([ok, fail(503)]);
    expect(t.loader.state(URL_GM)).toBe('none');
    t.loader.get(URL_GM);
    expect(t.loader.state(URL_GM)).toBe('pending');
    t.loader.get(URL_TABLE);
    await settle();
    expect(t.loader.state(URL_GM)).toBe('loaded');
    expect(t.loader.state(URL_TABLE)).toBe('failed');
  });

  // Handoff 057: an image that leaves the scene while a fetch is in flight
  // must not be revived when that fetch fails.
  function deferred() {
    const gates: Array<(r: Response) => void> = [];
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => gates.push(resolve)));
    const loaded: string[] = [];
    const loader = new TextureLoader<string>(
      { fetcher: fetcher as unknown as typeof fetch, decode: async () => ({}) as ImageBitmap, toTexture: () => 'texture' },
      (url) => loaded.push(url),
    );
    const answer = async (r: Response) => {
      (gates.shift() as (r: Response) => void)(r);
      await settle();
    };
    return { loader, fetcher, loaded, answer };
  }

  it('leaving during the first fetch: its failure schedules nothing', async () => {
    const t = deferred();
    t.loader.get(URL_TABLE);
    t.loader.keep([]);
    await t.answer(fail(503)());
    await vi.advanceTimersByTimeAsync(4 * MAX_RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('leaving during a retry fetch: its failure schedules nothing', async () => {
    const t = deferred();
    t.loader.get(URL_TABLE);
    await t.answer(fail(503)());
    await vi.advanceTimersByTimeAsync(RETRY_MS); // the retry fetch is now in flight
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    t.loader.keep([]);
    await t.answer(fail(503)());
    await vi.advanceTimersByTimeAsync(4 * MAX_RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('an image that comes back into the scene recovers again', async () => {
    const t = deferred();
    t.loader.get(URL_TABLE);
    t.loader.keep([]);
    await t.answer(fail(503)());
    t.loader.keep([URL_TABLE]);
    t.loader.get(URL_TABLE); // the runtime draws it again
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    await t.answer(fail(503)());
    await vi.advanceTimersByTimeAsync(RETRY_MS);
    expect(t.fetcher).toHaveBeenCalledTimes(3);
    await t.answer(ok());
    expect(t.loaded).toEqual([URL_TABLE]);
  });
});
