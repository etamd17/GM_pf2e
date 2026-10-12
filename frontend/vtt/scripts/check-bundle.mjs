// Post-build release check for the table bundle (VTT-20261012-010 answer 2).
//
// The source-level test (src/bundleBoundary.test.ts) is a lint guard; it cannot
// see dynamic imports, aliases or how Rollup actually chunks. This script
// builds for real into a temporary directory with source maps on, walks the
// Vite manifest's transitive closure from the table entry (static and dynamic
// imports), and reads every chunk's source map to find which modules ended up
// in the table's download. It fails if any GM workspace, bench or authoring
// module is in that closure, if any shipped file references a font CDN, or if
// any emitted HTML or CSS references an asset outside the app origin and base.
//
// Usage: npm run check:bundle

import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { build } from 'vite';
import { cssRefs, isAllowedAssetRef, resolveAssetBase } from '../src/build/assetOrigin.ts';
import { scanHtml } from '../src/build/htmlScan.ts';

const root = resolve(import.meta.dirname, '..');
const outDir = mkdtempSync(join(tmpdir(), 'vtt-bundle-check-'));
const FORBIDDEN = [/src\/app\/GmWorkspace/, /src\/bench\//, /src\/authoring\//];
const CDN = [/fonts\.googleapis\.com/, /fonts\.gstatic\.com/];

function closure(manifest, entryKey) {
  const seen = new Set();
  const stack = [entryKey];
  while (stack.length) {
    const key = stack.pop();
    if (seen.has(key)) continue;
    const chunk = manifest[key];
    if (!chunk) throw new Error(`manifest has no entry ${key}`);
    seen.add(key);
    for (const k of [...(chunk.imports ?? []), ...(chunk.dynamicImports ?? [])]) stack.push(k);
  }
  return [...seen].map((k) => manifest[k]);
}

function modulesIn(chunks) {
  const modules = new Set();
  for (const chunk of chunks) {
    if (chunk.src) modules.add(chunk.src.replaceAll('\\', '/'));
    if (!chunk.file.endsWith('.js')) continue;
    const mapPath = join(outDir, `${chunk.file}.map`);
    if (!existsSync(mapPath)) {
      // Rolldown emits no map for near-empty stub chunks (Pixi's worker shim).
      // Anything substantial without a map cannot be audited, so it fails.
      const size = readFileSync(join(outDir, chunk.file)).length;
      if (size > 512) throw new Error(`missing source map for ${chunk.file} (${size} bytes)`);
      continue;
    }
    const map = JSON.parse(readFileSync(mapPath, 'utf-8'));
    for (const s of map.sources ?? []) modules.add(s.replaceAll('\\', '/'));
  }
  return modules;
}

function shippedFiles(chunks) {
  const files = new Set();
  for (const c of chunks) {
    files.add(c.file);
    for (const css of c.css ?? []) files.add(css);
  }
  return [...files];
}

let failed = false;
try {
  process.env.VTT_SOURCEMAP = '1';
  process.env.VTT_OUT_DIR = outDir;
  delete process.env.VTT_BENCH;
  await build({ root, logLevel: 'warn' });
  const manifest = JSON.parse(readFileSync(join(outDir, '.vite', 'manifest.json'), 'utf-8'));

  const tableChunks = closure(manifest, 'table.html');
  const tableModules = modulesIn(tableChunks);
  const leaks = [...tableModules].filter((m) => FORBIDDEN.some((re) => re.test(m)));
  console.log(`table closure: ${tableChunks.length} chunks, ${tableModules.size} modules`);
  if (leaks.length) {
    failed = true;
    console.error('FAIL: table bundle contains forbidden modules:\n  ' + leaks.join('\n  '));
  }

  // Sanity: the walker must see the GM workspace from the GM entry, or the
  // check above proves nothing.
  const gmModules = modulesIn(closure(manifest, 'index.html'));
  if (![...gmModules].some((m) => /src\/app\/GmWorkspace/.test(m))) {
    failed = true;
    console.error('FAIL: sanity check could not find GmWorkspace in the GM closure');
  }

  if (manifest['bench.html']) {
    failed = true;
    console.error('FAIL: bench entry present in a default build');
  }

  // Origin boundary: every src/href in emitted HTML and every url()/@import
  // in emitted CSS must stay on the app origin, under the asset base.
  const base = resolveAssetBase(process.env.VTT_ASSET_BASE);
  const emitted = [
    ...readdirSync(outDir).filter((f) => f.endsWith('.html')),
    ...readdirSync(join(outDir, 'assets'))
      .filter((f) => f.endsWith('.css'))
      .map((f) => `assets/${f}`),
  ];
  for (const file of emitted) {
    const text = readFileSync(join(outDir, file), 'utf-8');
    // Resolve each reference against the URL its file is served from: pages
    // under a /vtt route, stylesheets under the asset base.
    const isHtml = file.endsWith('.html');
    const documentPath = isHtml ? `/vtt/${file.replace(/\.html$/, '')}` : `${base}${file.slice('assets/'.length)}`;
    const scan = isHtml ? scanHtml(text) : { refs: cssRefs(text), problems: [] };
    for (const problem of scan.problems) {
      failed = true;
      console.error(`FAIL: ${file}: ${problem}`);
    }
    for (const ref of scan.refs.filter((r) => !isAllowedAssetRef(r, base, documentPath))) {
      failed = true;
      console.error(`FAIL: ${file} references an off-origin or out-of-base asset: ${JSON.stringify(ref)}`);
    }
  }
  console.log(`origin check: ${emitted.length} HTML/CSS files under base ${base}`);

  for (const file of [...shippedFiles(tableChunks), ...shippedFiles(closure(manifest, 'index.html'))]) {
    const text = readFileSync(join(outDir, file), 'utf-8');
    if (CDN.some((re) => re.test(text))) {
      failed = true;
      console.error(`FAIL: ${file} references a font CDN`);
    }
  }
} finally {
  rmSync(outDir, { recursive: true, force: true });
}

if (failed) process.exit(1);
console.log('OK: table bundle boundary and asset-origin checks passed');
