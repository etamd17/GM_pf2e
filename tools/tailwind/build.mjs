// Precompile each standalone page's Tailwind utilities to a static stylesheet,
// replacing the runtime cdn.tailwindcss.com Play CDN (full engine + in-browser
// JIT on every load). Each page compiles with ITS OWN palette, so the pages'
// divergent color configs (e.g. amber-400 differs builder vs sheet) never
// collide — no shared config to reconcile.
//
// Run:  cd tools/tailwind && npm install && node build.mjs
// Re-run whenever a page's Tailwind classes or colors change. Output is
// committed under static/css/tw-<page>.css and linked via url_for('static').
import postcss from 'postcss';
import tailwindcss from 'tailwindcss';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const TEMPLATES = path.join(ROOT, 'templates');
const OUT = path.join(ROOT, 'static', 'css');

// Shared color families (base/accent/gold…) used by base, campaign_intro,
// player_view — aliased to the system.css brand ramps.
const WARM = {
  base:   { 950:'#0c0a07', 900:'#14110c', 800:'#1d1a14', 700:'#2e2920', 600:'#4a4334', 500:'#6f6755', 400:'#9a917a', 300:'#c9bfa3', 200:'#e3dac0', 100:'#f0ead7' },
  accent: { 200:'#f0d88a', 300:'#e0b65a', 400:'#c9a34e', 500:'#a4842b', 600:'#7d6418' },
  gold:   { 200:'#f0d88a', 300:'#e0b65a', 400:'#c9a34e', 500:'#7d6418' },
  danger: { 100:'#f5b7b7', 200:'#d36e6e', 300:'#fca5a5', 400:'#f87171', 500:'#ef4444' },
  success:{ 300:'#86efac', 400:'#4ade80', 500:'#22c55e' },
  info:   { 300:'#93c5fd', 400:'#60a5fa' },
  spell:  { 300:'#c4b5fd', 400:'#a78bfa' },
};
// The gray/amber/red family used by the sheet, builder, levelup, mobile_combat.
const AMBER_DARK = { 50:'#fef9ec', 100:'#fcefc5', 200:'#f9dc86', 300:'#d4a244', 400:'#b8860b', 500:'#9a7209', 600:'#7a5a07', 700:'#5c4305', 800:'#3d2d04', 900:'#1e1602' };
const GRAY_COOL = { 950:'#0d0d12', 900:'#191920', 800:'#24242e', 700:'#2e2e3c', 600:'#42425a', 500:'#6b6b8a', 400:'#9494b0', 300:'#b8b8cc', 200:'#d8d8e6', 100:'#ededf4' };
const RED = { 300:'#fca5a5', 400:'#f87171', 500:'#c53131', 600:'#c43c3c', 800:'#6b1525', 900:'#3D0A13' };
const GREEN = { 200:'#A7F3D0', 400:'#4ADE80', 500:'#22C55E', 800:'#14532D', 900:'#022C22' };
const BLUE = { 300:'#93C5FD', 400:'#60A5FA', 900:'#1e2a4a' };
const PURPLE = { 300:'#C4B5FD', 400:'#A78BFA' };

const F_DISPLAY = { display:['Cinzel','serif'], sans:['Inter','system-ui','sans-serif'] };
const F_HEADER  = { header:['Cinzel','serif'], body:['Inter','system-ui','sans-serif'] };

// Each page: the exact colors + fonts from its (now-removed) inline config.
const PAGES = {
  base:           { colors: WARM, fonts: { ...F_DISPLAY } },
  campaign_intro: { colors: { base: WARM.base, accent: WARM.accent, gold: { 300:'#e0b65a', 400:'#c9a34e' }, danger: { 300:'#fca5a5', 400:'#f87171', 500:'#ef4444' } }, fonts: { ...F_DISPLAY } },
  player_view:    { colors: { base: WARM.base, accent: { 300:'#e0b65a', 400:'#c9a34e', 500:'#a4842b', 600:'#7d6418' }, gold: { 300:'#e0b65a', 400:'#c9a34e' }, danger: { 300:'#fca5a5', 400:'#f87171', 500:'#ef4444' }, success: WARM.success, info: WARM.info, spell: WARM.spell }, fonts: { ...F_DISPLAY } },
  mobile_combat:  { colors: { gray: GRAY_COOL, amber: AMBER_DARK, red: { 300:'#fca5a5', 400:'#f87171', 500:'#c53131', 800:'#6b1525', 900:'#3D0A13' }, green: { 200:'#A7F3D0', 400:'#4ADE80', 500:'#22C55E', 800:'#14532D', 900:'#022C22' } }, fonts: { ...F_HEADER } },
  player_builder: { colors: { gray: { 950:'#1C1917', 900:'#28241F', 800:'#33302A', 700:'#3F3A33', 600:'#524C42', 500:'#7A7062', 400:'#A89C8B', 300:'#CFC5B4', 200:'#E8E0D2', 100:'#F5F0E8' }, amber: { 300:'#f0d88a', 400:'#e0b65a', 500:'#c9a34e', 600:'#a4842b', 700:'#7d6418', 800:'#5a4712', 900:'#3d2f0c' }, green: { 200:'#A7F3D0', 400:'#4ADE80' }, red: { 300:'#FDA4AF', 400:'#FB7185' }, blue: { 300:'#93C5FD', 400:'#60A5FA', 900:'#1E2A4A' } }, fonts: { header:['Cinzel','serif'], body:['Crimson Text','Georgia','serif'] } },
  player_levelup: { colors: { gray: GRAY_COOL, amber: AMBER_DARK, red: RED, green: { 200:'#A7F3D0', 400:'#4ADE80', 900:'#022C22' }, blue: { 300:'#93C5FD', 400:'#60A5FA' }, purple: PURPLE }, fonts: { ...F_HEADER } },
  player_sheet:   { colors: { gray: GRAY_COOL, amber: AMBER_DARK, red: { 300:'#fca5a5', 400:'#f87171', 500:'#c53131', 800:'#6b1525', 900:'#3D0A13' }, green: GREEN, blue: BLUE, purple: PURPLE }, fonts: { header:['Cinzel','serif'], body:['Inter','system-ui','sans-serif'], mono:['ui-monospace','monospace'] } },
};

const INPUT = '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n';

// Scan the ENTIRE template tree for every page — not just the page's own file.
// base.html is a layout extended by many children (gm_hub, gmscreen, calendar,
// cosmere_*, chronicle_*), and the standalone pages {% include %} partials
// (_pc_sheet/*, _player_nav, flourishes). A class used only in a child/partial
// must still be in that page's compiled CSS. Each page compiles with its OWN
// palette, so unused color classes from other pages simply don't generate (or
// generate as harmless unused rules); the rendered page only ever loads its own
// tw-<page>.css. Guarantees coverage at the cost of a slightly larger file.
const ALL_TEMPLATES = path.join(TEMPLATES, '**/*.html');
for (const [page, cfg] of Object.entries(PAGES)) {
  const config = {
    content: [ALL_TEMPLATES],
    theme: { extend: { colors: cfg.colors, fontFamily: cfg.fonts } },
    corePlugins: { preflight: true },
  };
  const result = await postcss([tailwindcss(config)]).process(INPUT, { from: undefined });
  const outPath = path.join(OUT, `tw-${page}.css`);
  fs.writeFileSync(outPath, result.css);
  console.log(`  tw-${page}.css  ${(result.css.length / 1024).toFixed(1)} KB`);
}
console.log('done.');
