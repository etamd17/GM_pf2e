import { describe, expect, it } from 'vitest';
import { isAllowedAssetRef } from './assetOrigin';
import { scanHtml } from './htmlScan';

// VTT-20261012-014: valid HTML spellings must not bypass the release check.

const base = '/vtt/assets/';
const page = '/vtt/table';
const offending = (html: string) =>
  scanHtml(html).refs.filter((r) => !isAllowedAssetRef(r, base, page));

describe('scanHtml', () => {
  it('finds quoted and unquoted attribute URLs', () => {
    const html = `<link rel=preconnect href=https://cdn.example.invalid>
      <script type="module" src="/vtt/assets/a.js"></script>`;
    expect(scanHtml(html).refs).toEqual(['https://cdn.example.invalid', '/vtt/assets/a.js']);
    expect(offending(html)).toEqual(['https://cdn.example.invalid']);
  });

  it('decodes character references, as a browser does', () => {
    const html = `<link rel=stylesheet href="h&#116;tps://cdn.example.invalid/x.css">
      <a href="javascript&colon;alert(1)">x</a>
      <img src="&#x2f;&#x2f;cdn.example.invalid/i.png">`;
    expect(offending(html)).toEqual([
      'https://cdn.example.invalid/x.css',
      'javascript:alert(1)',
      '//cdn.example.invalid/i.png',
    ]);
  });

  it('checks srcset candidates, ping lists, style attributes and style elements', () => {
    const html = `<img srcset="/vtt/assets/a.png 1x, https://cdn.example.invalid/b.png 2x">
      <a href="/vtt/assets/x" ping="/vtt/assets/p https://tracker.example.invalid/p">x</a>
      <div style="background:url(//cdn.example.invalid/c.png)"></div>
      <style>@import "https://fonts.googleapis.com/css2";</style>`;
    expect(offending(html).sort()).toEqual(
      [
        'https://cdn.example.invalid/b.png',
        'https://tracker.example.invalid/p',
        '//cdn.example.invalid/c.png',
        'https://fonts.googleapis.com/css2',
      ].sort(),
    );
  });

  it('checks a meta refresh target', () => {
    expect(offending(`<meta http-equiv="Refresh" content="0; url=https://cdn.example.invalid/">`)).toEqual([
      'https://cdn.example.invalid/',
    ]);
  });

  it('refuses <base>, import maps and speculation rules outright', () => {
    const { problems } = scanHtml(`<base href="https://cdn.example.invalid/">
      <script type="importmap">{"imports":{}}</script>
      <script type="speculationrules">{}</script>`);
    expect(problems).toHaveLength(3);
  });

  it('passes a clean Vite-style page', () => {
    const html = `<!doctype html><html><head>
      <script type="module" crossorigin src="/vtt/assets/table-abc.js"></script>
      <link rel="modulepreload" crossorigin href="/vtt/assets/app-def.js">
      <link rel="stylesheet" crossorigin href="/vtt/assets/app-ghi.css">
      </head><body><div id="root"></div></body></html>`;
    const scan = scanHtml(html);
    expect(scan.problems).toEqual([]);
    expect(offending(html)).toEqual([]);
    expect(scan.refs).toHaveLength(3);
  });
});
