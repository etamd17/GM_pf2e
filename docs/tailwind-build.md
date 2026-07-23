# Tailwind: precompiled static CSS

The standalone pages used to load `cdn.tailwindcss.com` — the **Play CDN**, which
ships the full ~400 KB Tailwind engine and **compiles utility classes in the
browser on every page load**. Tailwind's own docs label that "not for
production": it's an external-domain dependency (styling breaks if the CDN is
blocked/down), a large download, and a runtime JIT pass that delays first paint.

We replaced it with **precompiled static stylesheets** — one per page — under
`static/css/tw-<page>.css`, linked via `url_for('static', ...)`. No engine, no
runtime compile, no external dependency. Each page is ~45–50 KB (gzips to
~8–10 KB) vs the ~400 KB CDN.

## Pages

Seven pages carried their own Tailwind config + CDN and are now precompiled:

- `base.html` — **layout**, extended by gm_hub, gmscreen, calendar,
  campaign_stats, loot_ledger, cosmere_*, chronicle_base, … Those children load
  `tw-base.css`.
- `campaign_intro.html`, `player_view.html`, `mobile_combat.html`,
  `player_builder.html`, `player_levelup.html`, `player_sheet.html` — standalone.

## Regenerating (after adding/removing Tailwind classes or changing colors)

```bash
cd tools/tailwind
npm install        # first time only (installs tailwindcss@3 + postcss locally; gitignored)
node build.mjs     # writes static/css/tw-*.css
```

Then commit the regenerated `static/css/tw-*.css`.

## How it works / gotchas

- **Per-page palettes.** Each page compiles with its OWN colors (defined in
  `build.mjs`). This is deliberate — the pages' inline configs disagreed (e.g.
  `amber-400` is `#e0b65a` on the builder but `#b8860b` on the sheet), so a
  single shared config is impossible. A page only ever loads its own
  `tw-<page>.css`, so there's no cross-contamination.
- **The build scans the ENTIRE `templates/` tree for every page**, not just the
  page's own file. `base.html` is extended by many children, and the standalone
  pages `{% include %}` partials (`_pc_sheet/*`, `_player_nav`, flourishes) — a
  class used only in a child/partial must still land in that page's CSS. Unused
  color classes from other pages simply don't generate (or are harmless unused
  rules).
- **Colors are the single source of truth in `build.mjs` now** — the inline
  `tailwind.config` blocks were removed from the templates. Change a palette
  there and rebuild.
- **Dynamic classes must be literal tokens.** Tailwind's scanner extracts
  complete class strings from the source (including inside `<script>` JS). The
  app already avoids prefix concatenation (`'bg-' + x`), so all classes are
  caught. If you ever add `'text-' + shade`-style construction, safelist it in
  `build.mjs` or the class won't be generated.
- Output is intentionally unminified (debuggable); gzip handles the wire size.
