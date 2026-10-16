// Same-origin asset rules, shared by vite.config.ts (build input) and
// scripts/check-bundle.mjs (release check on the emitted output).
// VTT-20261012-012 finding 2: a misconfigured build must not be able to move
// scripts, styles or fonts to another origin while the release check passes.
//
// Plain erasable TypeScript only: Node runs this file directly, via type
// stripping, from the .mjs release check. No dependencies; the HTML parser
// lives in htmlScan.ts so vite.config.ts does not load it.

export const DEFAULT_ASSET_BASE = '/vtt/assets/';

// A root-relative path: one leading slash (never two, which would be
// protocol-relative), simple segments, a trailing slash, and no dot segments.
const BASE_PATTERN = /^\/(?:[A-Za-z0-9_~-][A-Za-z0-9._~-]*\/)*$/;

export function resolveAssetBase(value: string | undefined): string {
  if (value === undefined || value === '') return DEFAULT_ASSET_BASE;
  if (!BASE_PATTERN.test(value) || value.split('/').some((s) => s === '.' || s === '..')) {
    throw new Error(
      `VTT_ASSET_BASE must be a same-origin root-relative path like "/vtt/assets/", got ${JSON.stringify(value)}`,
    );
  }
  return value;
}

// A stand-in origin for resolution. It is never contacted.
const PROBE_ORIGIN = 'https://vtt-origin.invalid';

// Is a URL found in emitted HTML or CSS safe to ship?
//
// The reference is resolved exactly as a browser would, using the WHATWG URL
// parser, against the document that contains it. That handles every spelling
// a prefix check misses: backslashes (`/\host` is protocol-relative), embedded
// tabs and newlines, surrounding spaces, `..` and `%2e%2e` segments climbing
// out of the base, and any scheme (VTT-20261012-014). It is allowed only if it
// stays on the same origin and inside the asset base. Exceptions: data: URIs,
// and a bare fragment, which names a part of the same document.
//
// documentPath is the path of the file holding the reference. A stylesheet's
// relative url() resolves against the stylesheet's own URL, inside the base.
// An HTML page is served from a route such as /vtt/table, so a relative
// reference in it resolves outside the base and is rejected. Vite never emits
// one with an absolute base.
export function isAllowedAssetRef(ref: string, base: string, documentPath: string = base): boolean {
  if (/^[\s\u0000-\u001f]*#/.test(ref)) return true;
  let url: URL;
  try {
    url = new URL(ref, PROBE_ORIGIN + documentPath);
  } catch {
    return false;
  }
  if (url.protocol === 'data:') return true;
  if (url.origin !== PROBE_ORIGIN) return false;
  return url.pathname.startsWith(base);
}

// Decode CSS escapes (`\68 ttps`, `\:`) so an escaped URL is checked as the
// browser will read it.
export function cssUnescape(value: string): string {
  return value
    .replace(/\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?/g, (_m, hex: string) => {
      const code = Number.parseInt(hex, 16);
      return code === 0 || code > 0x10ffff ? '�' : String.fromCodePoint(code);
    })
    .replace(/\\(.)/g, '$1');
}

// URLs referenced by a stylesheet: url(...), @import "...", and image-set()
// string arguments. Values are unescaped before they are returned.
export function cssRefs(css: string): string[] {
  const refs: string[] = [];
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const m of text.matchAll(/url\(\s*(["']?)((?:\\.|(?!\1\s*\)).)*?)\1\s*\)/gis)) {
    refs.push(cssUnescape(m[2] ?? ''));
  }
  for (const m of text.matchAll(/@import\s+(["'])((?:\\.|(?!\1).)*)\1/gi)) {
    refs.push(cssUnescape(m[2] ?? ''));
  }
  for (const m of text.matchAll(/image-set\(([^)]*)\)/gi)) {
    for (const s of (m[1] ?? '').matchAll(/(["'])((?:\\.|(?!\1).)*)\1/g)) refs.push(cssUnescape(s[2] ?? ''));
  }
  return refs;
}
