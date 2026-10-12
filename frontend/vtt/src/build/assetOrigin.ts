// Same-origin asset rules, shared by vite.config.ts (build input) and
// scripts/check-bundle.mjs (release check on the emitted output).
// VTT-20261012-012 finding 2: a misconfigured build must not be able to move
// scripts, styles or fonts to another origin while the release check passes.
//
// Plain erasable TypeScript only: Node runs this file directly, via type
// stripping, from the .mjs release check.

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

// Is a URL found in emitted HTML or CSS safe to ship? Allowed: paths under the
// asset base, relative references that stay relative (./, ../, or a bare
// file name), data: URIs and same-document fragments. Rejected: any scheme,
// protocol-relative //host, and root-relative paths outside the base.
export function isAllowedAssetRef(ref: string, base: string): boolean {
  const value = ref.trim();
  if (value === '' || value.startsWith('#')) return true;
  if (value.startsWith('data:')) return true;
  if (value.startsWith('//')) return false;
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value)) return false; // http:, https:, javascript:, ...
  if (value.startsWith('/')) return value.startsWith(base);
  return true; // relative: resolves against the same origin as the document
}

// URLs referenced by an HTML document's src/href attributes.
export function htmlRefs(html: string): string[] {
  const refs: string[] = [];
  for (const m of html.matchAll(/\b(?:src|href)\s*=\s*(["'])(.*?)\1/gi)) refs.push(m[2] ?? '');
  return refs;
}

// URLs referenced by url(...) and @import in a stylesheet.
export function cssRefs(css: string): string[] {
  const refs: string[] = [];
  for (const m of css.matchAll(/url\(\s*(["']?)([^"')]*)\1\s*\)/gi)) refs.push(m[2] ?? '');
  for (const m of css.matchAll(/@import\s+(["'])(.*?)\1/gi)) refs.push(m[2] ?? '');
  return refs;
}
