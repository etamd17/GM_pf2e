import { describe, expect, it, vi } from 'vitest';
import { RETRY_MS, TextureLoader } from './textures';

const URL_GM = '/vtt/media/camp_a/asset_abc';
const URL_TABLE = '/vtt/table-media/asset_abc';

function setup(responses: Array<() => Response | Promise<Response>>, decode?: () => Promise<ImageBitmap>) {
  let now = 0;
  const loaded: string[] = [];
  const destroyed: string[] = [];
  const fetcher = vi.fn(async () => (responses.shift() ?? (() => new Response(null, { status: 404 })))());
  const loader = new TextureLoader<string>(
    {
      fetcher: fetcher as unknown as typeof fetch,
      decode: decode ?? (async () => ({ width: 1 }) as unknown as ImageBitmap),
      toTexture: () => 'texture',
      destroy: (t) => destroyed.push(t),
      now: () => now,
    },
    (url) => loaded.push(url),
  );
  const settle = () => new Promise((r) => setTimeout(r, 0));
  return { loader, fetcher, loaded, destroyed, settle, advance: (ms: number) => (now += ms) };
}

const ok = () => new Response(new Blob([new Uint8Array([1, 2, 3])]), { status: 200 });

describe('TextureLoader', () => {
  it('loads a media URL once, same-origin, and announces it', async () => {
    const t = setup([ok]);
    expect(t.loader.get(URL_GM)).toBeNull();
    expect(t.loader.get(URL_GM)).toBeNull(); // still loading: not fetched twice
    await t.settle();
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

  it('a refused or failed load retries after a while (a TV has nobody to reload it)', async () => {
    const t = setup([() => new Response(null, { status: 404 }), () => new Response(null, { status: 503 }), ok]);
    t.loader.get(URL_TABLE);
    await t.settle();
    expect(t.loader.get(URL_TABLE)).toBeNull();
    expect(t.fetcher).toHaveBeenCalledTimes(1); // not hammered
    t.advance(RETRY_MS);
    t.loader.get(URL_TABLE);
    await t.settle();
    t.advance(RETRY_MS);
    t.loader.get(URL_TABLE);
    await t.settle();
    expect(t.loader.get(URL_TABLE)).toBe('texture');
    expect(t.fetcher).toHaveBeenCalledTimes(3);
  });

  it('a network error is retried too; an undecodable image is not', async () => {
    const t = setup([() => Promise.reject(new TypeError('offline')), ok], async () => {
      throw new Error('bad image');
    });
    t.loader.get(URL_GM);
    await t.settle();
    t.advance(RETRY_MS);
    t.loader.get(URL_GM);
    await t.settle(); // arrived but cannot be decoded: final
    t.advance(RETRY_MS);
    expect(t.loader.get(URL_GM)).toBeNull();
    expect(t.fetcher).toHaveBeenCalledTimes(2);
    expect(t.loaded).toEqual([]);
  });

  it('textures loaded after disposal are destroyed, and dispose destroys the rest', async () => {
    const t = setup([ok, ok]);
    t.loader.get(URL_GM);
    await t.settle();
    t.loader.get(URL_TABLE);
    t.loader.dispose();
    expect(t.destroyed).toEqual(['texture']);
    await t.settle();
    expect(t.destroyed).toEqual(['texture', 'texture']);
    expect(t.loaded).toEqual([URL_GM]);
  });
});
