// Every URL an emitted HTML document can make the browser fetch or navigate
// to, extracted with a real HTML parser (jsdom). A regex misses unquoted
// attributes and character references (VTT-20261012-014); the parser decodes
// both exactly as a browser does.
//
// Plain erasable TypeScript. Imported only by scripts/check-bundle.mjs and
// tests, never by vite.config.ts or the application.

import { JSDOM } from 'jsdom';
import { cssRefs } from './assetOrigin.ts';

// Attributes whose value is a URL.
const URL_ATTRS = ['src', 'href', 'poster', 'data', 'action', 'formaction', 'background', 'manifest', 'xlink:href'];
// Attributes holding several URLs.
const LIST_ATTRS = ['srcset', 'imagesrcset'];
const SPACE_LIST_ATTRS = ['ping'];

export interface HtmlScan {
  refs: string[];
  // Constructs the release check refuses outright, because they change how
  // every other URL resolves or load code from data the scan cannot vet.
  problems: string[];
}

export function scanHtml(html: string): HtmlScan {
  const doc = new JSDOM(html).window.document;
  const refs: string[] = [];
  const problems: string[] = [];

  for (const el of Array.from(doc.querySelectorAll('*'))) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (URL_ATTRS.includes(name)) refs.push(attr.value);
      else if (LIST_ATTRS.includes(name)) refs.push(...srcsetUrls(attr.value));
      else if (SPACE_LIST_ATTRS.includes(name)) refs.push(...attr.value.split(/\s+/).filter(Boolean));
      else if (name === 'style') refs.push(...cssRefs(attr.value));
    }
  }
  for (const style of Array.from(doc.querySelectorAll('style'))) refs.push(...cssRefs(style.textContent ?? ''));

  if (doc.querySelector('base')) problems.push('<base> element changes how every URL resolves');
  for (const meta of Array.from(doc.querySelectorAll('meta'))) {
    if ((meta.getAttribute('http-equiv') ?? '').toLowerCase() === 'refresh') {
      const target = /url\s*=\s*['"]?([^'"]*)/i.exec(meta.getAttribute('content') ?? '');
      if (target?.[1]) refs.push(target[1]);
    }
  }
  for (const script of Array.from(doc.querySelectorAll('script[type]'))) {
    const type = (script.getAttribute('type') ?? '').toLowerCase();
    if (type === 'importmap' || type === 'speculationrules') problems.push(`<script type="${type}"> is not allowed`);
  }
  return { refs, problems };
}

function srcsetUrls(value: string): string[] {
  return value
    .split(',')
    .map((candidate) => candidate.trim().split(/\s+/)[0] ?? '')
    .filter(Boolean);
}
