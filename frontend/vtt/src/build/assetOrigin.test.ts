import { describe, expect, it } from 'vitest';
import {
  DEFAULT_ASSET_BASE,
  cssRefs,
  htmlRefs,
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
  it.each([
    '/vtt/assets/gm-abc.js',
    './inter-latin-600-normal.woff2',
    'inter-latin-600-normal.woff2',
    '../assets/x.css',
    'data:font/woff2;base64,AAAA',
    '#icon',
  ])('allows %s', (ref) => {
    expect(isAllowedAssetRef(ref, base)).toBe(true);
  });

  it.each([
    'https://cdn.example.invalid/vtt/gm.js',
    'http://fonts.googleapis.com/css2',
    '//cdn.example.invalid/x.js',
    '/other/root.js',
    'javascript:alert(1)',
    ' HTTPS://cdn.example.invalid/x.js',
  ])('rejects %s', (ref) => {
    expect(isAllowedAssetRef(ref, base)).toBe(false);
  });
});

describe('reference extraction', () => {
  it('finds src and href in HTML', () => {
    const html = `<script type="module" crossorigin src="/vtt/assets/a.js"></script>
      <link rel="stylesheet" href='https://cdn.example.invalid/x.css'>`;
    expect(htmlRefs(html)).toEqual(['/vtt/assets/a.js', 'https://cdn.example.invalid/x.css']);
  });

  it('finds url() and @import in CSS', () => {
    const css = `@import "https://fonts.googleapis.com/css2";
      @font-face{src:url(/vtt/assets/f.woff2) format("woff2"),url('./g.woff') format("woff")}`;
    expect(cssRefs(css).sort()).toEqual(
      ['./g.woff', '/vtt/assets/f.woff2', 'https://fonts.googleapis.com/css2'].sort(),
    );
  });
});
