import { isMediaUrl } from './renderModel';

// Scene images (M6): backgrounds and token art, by media URL. Asset URLs carry
// no file extension, so Pixi's extension-based loader is not used; the bytes
// are fetched same-origin (the GM session cookie, or the table's grant cookie,
// authorizes them), decoded to an ImageBitmap and wrapped by `toTexture`.
//
// Each URL is loaded once. Only an image that arrived but cannot be decoded is
// remembered as a failure. Anything else (a 404, which can be a race with the
// projection, a 503, a dropped connection) may be retried after RETRY_MS: a
// table display has nobody to press reload. Meanwhile the
// caller keeps drawing the plain disc or scene: a missing image must never
// break a frame. `onLoaded` fires once per URL that produced a texture, so the
// runtime can redraw what uses it.

export const RETRY_MS = 10_000;

export interface TextureLoaderDeps<T> {
  fetcher?: typeof fetch;
  decode?: (blob: Blob) => Promise<ImageBitmap>;
  toTexture: (bitmap: ImageBitmap) => T;
  destroy?: (texture: T) => void;
  now?: () => number;
}

export class TextureLoader<T> {
  private readonly ready = new Map<string, T | null>();
  private readonly loading = new Set<string>();
  private readonly failedAt = new Map<string, number>();
  private disposed = false;

  constructor(
    private readonly deps: TextureLoaderDeps<T>,
    private readonly onLoaded: (url: string) => void,
  ) {}

  // The texture when it is ready; otherwise starts loading it (once) and
  // answers null for now.
  get(url: string | undefined): T | null {
    if (!url || !isMediaUrl(url)) return null;
    if (this.ready.has(url)) return this.ready.get(url) ?? null;
    const failed = this.failedAt.get(url);
    if (failed !== undefined && this.clock() - failed < RETRY_MS) return null;
    if (!this.loading.has(url)) void this.load(url);
    return null;
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
      this.failedAt.set(url, this.clock());
      return;
    }
    this.failedAt.delete(url);
    this.ready.set(url, texture);
    if (texture !== null) this.onLoaded(url);
  }

  // For the test probe: where a URL stands.
  state(url: string): 'loaded' | 'failed' | 'pending' | 'none' {
    if (this.ready.has(url)) return this.ready.get(url) === null ? 'failed' : 'loaded';
    if (this.loading.has(url)) return 'pending';
    return this.failedAt.has(url) ? 'failed' : 'none';
  }

  private clock(): number {
    return (this.deps.now ?? Date.now)();
  }

  dispose(): void {
    this.disposed = true;
    for (const texture of this.ready.values()) if (texture !== null) this.deps.destroy?.(texture);
    this.ready.clear();
  }
}
