import { describe, expect, it } from 'vitest';
import {
  DEFAULT_ASSET_BASE,
  cssRefs,
  cssUnescape,
  isAllowedAssetRef,
  resolveAssetBase,
} from './assetOrigin';

describe('resolveAssetBase', () => {
  it('defaults when unset', () => {
    expect(resolveAssetBase(undefined)).toBe(DEFAULT_ASSET_BASE);
    expect(resolveAssetBase('')).toBe(DEFAULT_ASSET_BASE);
  });

  it.each(['/', '/vtt/assets/', '/static/vtt-1.2/'])('accepts same-origin base %s', (base) => {
    expect(resolveAssetBase(base)).toBe(base);
  });

  it.each([
    'https://cdn.example.invalid/vtt/',
    'http://localhost:5173/',
    '//cdn.example.invalid/vtt/',
    '/\\cdn.example.invalid/',
    'vtt/assets/',
    './',
    '/vtt/assets',
    '/vtt/../etc/',
    '/vtt/./x/',
    'javascript:alert(1)/',
    '/vtt assets/',
  ])('rejects %s', (base) => {
    expect(() => resolveAssetBase(base)).toThrow(/VTT_ASSET_BASE/);
  });
});

describe('isAllowedAssetRef', () => {
  const base = '/vtt/assets/';
  const css = '/vtt/assets/app.css';

  it.each([
    ['/vtt/assets/gm-abc.js', base],
    ['./inter-latin-600-normal.woff2', css],
    ['inter-latin-600-normal.woff2', css],
    ['../assets/x.woff2', css],
    ['data:font/woff2;base64,AAAA', css],
    ['#icon', '/vtt/table'],
    ['  #icon', '/vtt/table'],
  ])('allows %s (in %s)', (ref, doc) => {
    expect(isAllowedAssetRef(ref, base, doc)).toBe(true);
  });

  // Every spelling below was confirmed against a real browser's URL resolution
  // (VTT-20261012-014 follow-up): each one leaves the origin or the base.
  it.each([
    ['https://cdn.example.invalid/vtt/gm.js', base],
    ['http://fonts.googleapis.com/css2', base],
    ['//cdn.example.invalid/x.js', base],
    ['/\\cdn.example.invalid/x.js', base],
    ['\\\\cdn.example.invalid/x.js', base],
    [' \thttps://cdn.example.invalid/x', base],
    ['ht\ntps://cdn.example.invalid/x', base],
    ['HTTPS://cdn.example.invalid/x.js', base],
    ['javascript:alert(1)', base],
    ['blob:https://vtt-origin.invalid/abc', base],
    ['/other/root.js', base],
    ['/vtt/assets/../../x.js', base],
    ['/vtt/assets/%2e%2e/x.js', base],
    ['/vtt/assets/%2E%2E/%2e%2e/x.js', base],
    ['../../outside.woff2', css],
    ['relative-from-a-page.js', '/vtt/table'],
  ])('rejects %j (in %s)', (ref, doc) => {
    expect(isAllowedAssetRef(ref, base, doc)).toBe(false);
  });
});

describe('cssRefs', () => {
  it('finds url(), @import and image-set() references', () => {
    const text = `@import "https://fonts.googleapis.com/css2";
      @font-face{src:url(/vtt/assets/f.woff2) format("woff2"),url('./g.woff') format("woff")}
      .x{background-image:image-set("a.png" 1x, 'https://cdn.example.invalid/b.png' 2x)}`;
    expect(cssRefs(text).sort()).toEqual(
      [
        './g.woff',
        '/vtt/assets/f.woff2',
        'https://fonts.googleapis.com/css2',
        'a.png',
        'https://cdn.example.invalid/b.png',
      ].sort(),
    );
  });

  it('decodes CSS escapes before returning a reference', () => {
    expect(cssRefs('a{b:url(\\68 ttps://cdn.example.invalid/x.png)}')).toEqual([
      'https://cdn.example.invalid/x.png',
    ]);
    expect(cssUnescape('\\2f \\2f cdn')).toBe('//cdn');
  });

  it('ignores references inside comments', () => {
    expect(cssRefs('/* url(https://cdn.example.invalid/x) */ a{}')).toEqual([]);
  });
});
