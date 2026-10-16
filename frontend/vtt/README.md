# VTT client (`frontend/vtt/`)

The web client for the PF2e VTT rebuild. It uses React for the surrounding UI and raw PixiJS for
the map. The client lead (Claude) owns it. Coordination, decisions and handoffs live in
`docs/vtt/`, and the design is in `docs/vtt/design/client-architecture-proposal.md`.

## Run

```bash
npm install          # exact versions are pinned in package.json and package-lock.json
npm run dev          # http://localhost:5173 (GM), /table.html (table), /bench.html (renderer bench)
npm test             # vitest
npm run typecheck
npm run build        # output in dist/ (gitignored), with .vite/manifest.json
npm run check:bundle # release check: table bundle closure and asset origin
```

Node `>=24.12.0 <25` and npm 11.6.2 (see `engines` and `packageManager`).

Build switches, which are environment variables read by `vite.config.ts`:
- `VTT_ASSET_BASE` sets the absolute asset URL prefix. The default is `/vtt/assets/`, a proposal
  until Flask routes land.
- `VTT_SOURCEMAP=1` emits source maps.
- `VTT_BENCH=1` includes the bench entry.

Source maps and the bench entry are off by default and must not ship in a normal image.

Development builds start "live" on a generated reference scene. Add `?status=<state>` to force a
connection state, for example `?status=reconnecting` or `/table.html?status=revoked`. Production
builds ignore the parameter.

Dev console hooks (development builds only): `window.__vttRuntime.renderNow()` forces one frame.
`requestAnimationFrame` does not fire in a hidden tab or a headless preview, so use this there.
`window.__vttRuntime.loseContextForTest(ms | null)` simulates a GPU reset.

## Rules

- **No wire fields yet.** `src/scene/renderModel.ts` is the renderer's own input model. Transport,
  schemas and protocol types wait for the accepted VTT-ADR-0002, and an adapter will map the
  sanitized projection into the render model.
- **React never touches Pixi objects.** `MapHost` hands one `<div>` to `SceneRuntime`; scene changes
  go through `setScene`, which diffs by object identity. Producers must replace objects, never mutate
  them.
- **Render on demand.** There is no free-running ticker. `RenderScheduler` draws one frame per
  change; animations hold a lease.
- **The table bundle stays clean.** `src/bundleBoundary.test.ts` fails if `main-table.tsx` reaches GM
  workspace, bench or authoring code. The table also never builds the wall or overlay layers.
- **The table draws only what its own projection contains.** It must never be given a GM payload
  with the controls hidden (VTT-ADR-0001).
- **No `hidden` attribute on styled elements.** Render conditionally instead (CLAUDE.md recurring
  bug class).
- **Canvas text uses `uiFont()`**, never a hardcoded family.

## Renderer spike (R4), 2026-10-12

Reference scene: 4096x4096, 100 tokens, 500 walls, 20 lights, no fog or vision yet. The viewport
was 1278x718 at DPR 1, in the Claude desktop preview pane on the user's Windows machine. Each
sample is CPU submit time plus a forced GPU sync. There were three runs of 300 frames each.

| Renderer | Scenario | p50 | p95 |
|---|---|---|---|
| Pixi 8 (WebGL) | 1 token moving per frame | 2.4-2.6 ms | 3.7-5.1 ms |
| Pixi 8 (WebGL) | all 100 tokens moving | 2.5-2.6 ms | 3.8-3.9 ms |
| Canvas 2D | 1 token moving per frame | 6.8-7.5 ms | 8.2-10.9 ms |
| Canvas 2D | all 100 tokens moving | 7.2-10.2 ms | 11.6-29.1 ms |

Both are inside the 33 ms p95 target. Pixi is about 3x faster at p50 and far steadier at p95.
Caveats:
- Fog and vision are not drawn yet. They are the expensive part and will move both numbers.
- The Canvas 2D baseline redraws every layer each frame. The legacy map caches layers, which would
  narrow the gap.
- This was one machine at DPR 1. A laptop iGPU at DPR 2 should be measured before alpha.
