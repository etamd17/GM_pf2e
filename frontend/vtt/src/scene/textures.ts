import { isMediaUrl } from './renderModel';

// Scene images (M6): backgrounds and token art, by media URL. Asset URLs carry
// no file extension, so Pixi's extension-based loader is not used; the bytes
// are fetched same-origin (the GM session cookie, or the table's grant cookie,
// authorizes them), decoded to an ImageBitmap and wrapped by `toTexture`.
//
// Each URL is loaded once. Only an image that arrived but cannot be decoded is
// remembered as a failure. Anything else (a 404, which can be a race with the
// projection, a 503, a dropped connection) is retried ON ITS OWN, on a timer
// that backs off from RETRY_MS to MAX_RETRY_MS: a table display is idle most
// of the time and has nobody to press reload, so recovery cannot wait for the
// next redraw to ask again (handoff 055). Retrying stops when the runtime no
// longer uses the URL (`keep`) or the loader is disposed.
//
// Meanwhile the caller keeps drawing the plain disc or scene: a missing image
// must never break a frame. `onLoaded` fires once per URL that produced a
// texture, so the runtime can redraw what uses it.

export const RETRY_MS = 10_000;
export const MAX_RETRY_MS = 60_000;

export interface TextureLoaderDeps<T> {
  fetcher?: typeof fetch;
  decode?: (blob: Blob) => Promise<ImageBitmap>;
  toTexture: (bitmap: ImageBitmap) => T;
  destroy?: (texture: T) => void;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export class TextureLoader<T> {
  private readonly ready = new Map<string, T | null>();
  private readonly loading = new Set<string>();
  // A transient failure: how many in a row, and the retry waiting for it.
  private readonly retries = new Map<string, { attempts: number; timer: unknown }>();
  // URLs the runtime currently draws (null until the first keep(): everything
  // asked for is wanted). A failure schedules a retry, and a retry fires, only
  // for a wanted URL, so an image that left the scene while its fetch was in
  // flight is not revived when that fetch fails (handoff 057).
  private wanted: Set<string> | null = null;
  private disposed = false;

  constructor(
    private readonly deps: TextureLoaderDeps<T>,
    private readonly onLoaded: (url: string) => void,
  ) {}

  // The texture when it is ready; otherwise starts loading it (once) and
  // answers null for now. A URL waiting for its retry is not fetched early.
  get(url: string | undefined): T | null {
    if (!url || !isMediaUrl(url)) return null;
    if (this.ready.has(url)) return this.ready.get(url) ?? null;
    this.wanted?.add(url); // asked for again: wanted again
    if (!this.loading.has(url) && !this.retries.has(url)) void this.load(url);
    return null;
  }

  // The URLs the runtime still draws. Retries for anything else stop: an image
  // that left the scene is not fetched forever.
  keep(urls: Iterable<string>): void {
    const wanted = new Set(urls);
    this.wanted = wanted;
    for (const [url, retry] of this.retries) {
      if (wanted.has(url)) continue;
      this.clear(retry.timer);
      this.retries.delete(url);
    }
  }

  private async load(url: string): Promise<void> {
    this.loading.add(url);
    let texture: T | null = null;
    let permanent = false;
    try {
      const fetcher = this.deps.fetcher ?? fetch;
      const response = await fetcher(url, { credentials: 'same-origin' });
      if (response.ok) {
        const blob = await response.blob();
        permanent = true; // from here on, a failure is the image itself
        const bitmap = await (this.deps.decode ?? ((b: Blob) => createImageBitmap(b)))(blob);
        texture = this.deps.toTexture(bitmap);
      }
    } catch {
      texture = null;
    } finally {
      this.loading.delete(url);
    }
    if (this.disposed) {
      if (texture !== null) this.deps.destroy?.(texture);
      return;
    }
    if (texture === null && !permanent) {
      if (this.isWanted(url)) this.scheduleRetry(url);
      else this.retries.delete(url); // left the scene while in flight: forget it
      return;
    }
    this.retries.delete(url);
    this.ready.set(url, texture);
    if (texture !== null) this.onLoaded(url);
  }

  private scheduleRetry(url: string): void {
    const attempts = (this.retries.get(url)?.attempts ?? 0) + 1;
    const delay = Math.min(RETRY_MS * 2 ** (attempts - 1), MAX_RETRY_MS);
    const timer = (this.deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms)))(() => {
      const retry = this.retries.get(url);
      if (this.disposed || !retry || retry.timer !== timer) return; // cancelled
      if (!this.isWanted(url)) {
        this.retries.delete(url); // not wanted: a later get() starts fresh
        return;
      }
      retry.timer = null;
      void this.load(url);
    }, delay);
    this.retries.set(url, { attempts, timer });
  }

  // For the test probe: where a URL stands.
  state(url: string): 'loaded' | 'failed' | 'pending' | 'none' {
    if (this.ready.has(url)) return this.ready.get(url) === null ? 'failed' : 'loaded';
    if (this.loading.has(url)) return 'pending';
    return this.retries.has(url) ? 'failed' : 'none';
  }

  private isWanted(url: string): boolean {
    return this.wanted === null || this.wanted.has(url);
  }

  private clear(timer: unknown): void {
    if (timer !== null && timer !== undefined) (this.deps.clearTimer ?? ((h) => clearTimeout(h as number)))(timer);
  }

  dispose(): void {
    this.disposed = true;
    for (const retry of this.retries.values()) this.clear(retry.timer);
    this.retries.clear();
    for (const texture of this.ready.values()) if (texture !== null) this.deps.destroy?.(texture);
    this.ready.clear();
  }
}
