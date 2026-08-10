# Tactical map — feature audit

**Status: not started. Paused 2026-08-10 before round 1.** Nothing has been decided
and no map code has been changed beyond the port itself.

This doc exists so the audit can resume on a different machine. Claude's per-project
memory lives under `~/.claude/projects/<path>/memory/` and does **not** travel between
machines, so anything that matters is written down here instead.

---

## How to resume

Read this file, then start at **Round 1** below and ask its questions with the
AskUserQuestion tool (four at a time, as written). Do not re-derive the findings —
they were verified against a running server, not read off the code. Do not re-ask
anything under "Settled".

The rounds are ordered so earlier answers constrain later ones. Scene lifecycle first
because activation semantics decide what the shared table screen even means.

Target: get through it in one or two sessions. Seven rounds, ~4 questions each.

---

## Settled — do not re-ask

| Decision | Made |
|---|---|
| The map is **GM-only**. No player-facing route exists; `/api/scenes` is gated at the prefix in `GM_API_PREFIXES`. | 2026-08-10 |
| The eventual audience is **one shared screen at the table**, driven by the GM — *not* per-player devices. | 2026-08-10 |
| First priority for design work is **GM workflow**. | 2026-08-10 |

Consequences worth holding onto:

- The per-player payload problem is *out of scope*. `services/scene_sync.py` and its
  player projection are kept and tested, but nothing serves them yet — they become
  load-bearing again only when the table screen is built (Round 7).
- A shared table screen revives something `CLAUDE.md` records as **cancelled and
  unowned** (the Campaign Hub Stage). `CLAUDE.md` needs correcting when Round 7 starts.
- `?audience=table` already exists in `templates/_sse_hub.html` as a client-side
  convention for a passive screen with no operator (it self-reloads on deploy instead
  of showing the "New version" toast). It is the natural hook for the table view.

---

## State of play

Merged to `main` and deployed (2026-08-10):

- `d139688f` — the map port, GM-only (PR #111)
- `f835f816` — `CHRONICLE_PUBLISH_TOKEN` env support (PR #112)
- `bc76007e` — storage-marker test fix (PR #113)

Where the code lives:

| Thing | Path |
|---|---|
| Scene storage + schema (`SCHEMA_VERSION` 3) | `core/scenes.py` |
| Player projection / the GM↔player boundary | `services/scene_sync.py` |
| Routes + helpers (8 helpers, 12 routes) | `app.py`, the "Tactical scenes" block |
| Client (~1400 lines) | `static/js/map.js` |
| Page + sidebar | `templates/map.html` |
| Styles | `static/css/map.css` |
| Tests (36) | `tests/test_scenes.py` |
| Storage paths | `core/storage.py` — `scenes_dir` / `scene_file` / `scene_assets_dir` / `scenes_index_file` |

On-disk layout: `<DATA_DIR>/campaigns/<cid>/scenes/<scene_id>.json`, `.../scenes/index.json`
(holds `active_scene_id`), `.../scenes/assets/<scene_id>.<ext>`. Legacy/no-campaign mode
uses `<DATA_DIR>/scenes/`. `/scenes/` is gitignored — `DATA_DIR` resolves to the checkout
in local dev, so running the app writes scenes into the repo root.

**`feat/chronicle-uploads-and-pc-persistence` must never be merged.** It is a snapshot
import with no common ancestor (`git merge-base` returns nothing) and merging it reverts
real work. It is kept only as an archive — diff against it before concluding a fix was
never written. It already yielded two fixes that were missing from trunk.

---

## Verified findings

Everything below was confirmed against a running server on 2026-08-10, not inferred.
`[browser]` means observed live; `[code]` means read from source but not exercised.

### Confirmed live

1. **The token inspector never hides.** `#map-token-actions` gets `hidden=true` from JS,
   but `.map-token-actions { display:grid }` (`static/css/map.css:34`) has no `[hidden]`
   rule, so computed display stays `grid`. It is permanently visible with stale values,
   and Save/Remove act on an undefined selection. The nested `.map-combat-actions` **does**
   have the rule (`map.css:38`) and correctly computes `display:none` — same mechanism,
   one line apart. `[browser]`
2. **"Open linked sheet" is always visible.** `#map-token-sheet` — `hidden=false`,
   `display:flex`, `href="/tracker"` with nothing selected. `.map-btn` is
   `display:inline-flex` with no `[hidden]` rule (`map.css:8`). `[browser]`
3. **Every manually added token lands on the same square.** Added Alpha/Beta/Gamma →
   all at `(70, 70)`. `[browser]`
4. **Snapping targets grid intersections, not cell centres.** That `(70,70)` is a
   gridline crossing at grid size 70; the cell centre would be `105`. A medium token sits
   on a corner straddling four squares. `map.js:661-664`. `[browser]`
5. **An SSE frame destroys in-progress typing.** Typed into `#map-scene-name`; an
   unrelated *token move from another client* reverted the field. `applyScene` calls
   `fillControls()` + `updateSelectionPanel()` unconditionally on every `scene_update`.
   `[browser]`
6. **Creating a scene force-navigates every connected client.** Creating one activates
   it, and the `scene_activated` broadcast navigates every browser (`map.js:1381-1387`).
   There is no way to prep a scene privately. `[browser]`
7. **The GM gets no initial fit.** Lands at 100% zoom, top-left.
   `map.js:1392` fits only when `!cfg.isGm`. `[browser]`
8. **No delete-scene route.** `DELETE /api/scenes/<id>` → 405. Scenes and their
   background assets accumulate forever. `[browser]`

### Read but not exercised

9. **Backgrounds are stretched.** `ctx.drawImage(background, 0, 0, canvas.width, canvas.height)`
   (`map.js:177`) with canvas fixed to `scene.width`/`height`. A 4096×2304 map is squashed
   into 1400×900. No tiling, no LOD, no `devicePixelRatio`; zoom is CSS scale on the
   canvas element, so it can never reach source resolution. `[code]`
10. **Scene dimensions are fixed at creation** — `PATCH /api/scenes/<id>` accepts only
    `name`, `grid`, `settings`. No resize. `[code]`
11. **`focusActiveTurn` hijacks selection** — on turn advance it force-scrolls *and* sets
    `selectedId` (`map.js:167`), repointing the inspector mid-edit. On by default. `[code]`
12. **Sidebar `<select>`s are render-time Jinja snapshots** (`map.html:44,80,106`).
    Adding a combatant or renaming a PC does not refresh them. *Not confirmed* — the
    forced navigation in finding 6 masked it during testing. `[code]`
13. **Lighting is half-wired.** `light.visible_to_players` and `template.visible_to_players`
    are hardcoded `true` at the only writers (`map.js:893`, `map.js:685`), so the player
    filters at `scene_sync.py:51-54` can never fire. `light.intensity` is hardcoded `.75`.
    Lights cannot be edited after placement — only `add_light` / `delete_light` exist. `[code]`
14. **`dynamic_lighting` does nothing on the GM's own screen.** `drawVisionOverlay` runs
    only when `!cfg.isGm` (`map.js:184`), so the GM cannot preview what the party sees. `[code]`
15. **`default_vision` does not propagate.** Applied only at token creation
    (`core/scenes.py:205`); changing it later leaves existing tokens on their snapshot
    `vision_radius`. `[code]`
16. **No token art upload.** `token.image` is populated only from a linked character
    portrait or combatant image (`app.py`, `_scene_payload`). An unlinked token can only
    ever be a coloured circle with initials. `[code]`
17. **Emanation is a duplicate of Burst** — `drawTemplate` and `templateContainsToken`
    treat `'burst'` and `'emanation'` identically (`map.js:393`, `map.js:432`). `[code]`
18. **Erase is unreliable for templates.** `nearestElement` (`map.js:730-735`) matches by
    distance to the *ring*, not the fill, so clicking a burst's interior misses; cones are
    matched against `template.radius` while their real extent is the drag length, so they
    often cannot be erased at all. `[code]`
19. **Undo is movement-only.** `Ctrl+Z` (`map.js:1360`) pops `movementHistory` (cap 30).
    Walls, doors, lights, templates and fog strokes have no undo — Ctrl+Z silently moves a
    token instead. `[code]`
20. **Fog is an unbounded replayed op-log.** `fog.operations` is capped at 2000 server-side
    and replayed arc-by-arc every frame (`map.js:321-327`). No rasterise-and-cache, no
    compaction. `[code]`
21. **The render loop is a prototype.** 22 direct `draw()` calls, zero `requestAnimationFrame`,
    called synchronously inside `pointermove`. `drawVisionOverlay` and `drawFogOverlay` each
    allocate a **full-scene offscreen canvas every frame** (~5 MB each at 1400×900).
    Raycasting is `(160 + 6W)` angles × W walls **per light source per frame** — roughly
    138k segment intersections at 50 walls and 6 sources. `[code]`
22. **Mobile ceiling.** Scenes may be up to 8000×8000 (64 MP); iOS Safari's canvas limit is
    ~16.7 MP, so anything past ~4000×4000 renders blank on iPad. `[code]`
23. **Uploads are validated on client-supplied MIME only** — no magic-byte check. The
    25 MB limit is checked *after* `request.files.get()` has already buffered the body.
    Re-uploading a different format orphans the old asset (filename is `<scene_id><ext>`). `[code]`

---

## Round 1 — Scene lifecycle & backgrounds

Grounded in findings 6, 8, 9, 10.

1. **Prep vs push.** Selecting or creating a scene instantly pushes it to everyone.
   With a shared table screen, "active" means "what the table is showing".
   - Separate open from push *(recommended)* — picking a scene opens it for you; a
     distinct "Show on table" button is what the table follows.
   - Keep auto-push, add a prep toggle that suppresses the broadcast.
   - Always follow the GM — leave as-is.
2. **Deleting scenes.** No delete exists; scenes and backgrounds accumulate forever.
   - Delete with confirm + asset cleanup, blocked while live *(recommended)*.
   - Archive, purge separately.
   - Delete the scene, keep the image for reuse.
3. **Background sizing.** A real battlemap is stretched into the fixed 1400×900.
   - Image defines the scene *(recommended)* — resize to the image, preserve aspect,
     cap for sanity. Also unlocks zooming to source resolution.
   - Fit inside the scene, letterboxed.
   - Make width/height editable, size it manually.
4. **Grid alignment** for a map with its own printed grid.
   - Drag-to-calibrate over the image (builds on the existing "Align grid by dragging").
   - Type exact numbers (size + offset).
   - Ignore the printed grid, overlay ours.
   - Gridless / theatre of the mind.

## Round 2 — Grid, snapping & token placement

Grounded in findings 3, 4, 7.

1. **Where should a new token land?** All of them currently stack on one hardcoded
   square. Options: where the GM last clicked; first free cell scanning from top-left;
   centre of the current viewport; drag-from-sidebar-to-place.
2. **Snap semantics.** Snapping currently targets gridline intersections, so a medium
   token straddles four squares. Options: snap to cell centres; snap large/huge tokens
   to the correct multi-cell footprint too; free placement with snap held/toggled.
3. **Opening view.** The GM lands at 100% zoom, top-left, with no fit. Options: always
   fit the scene; remember per-scene zoom/pan; fit only when the scene doesn't fit.
4. **Does token size need to follow creature size?** PF2e sizes are already mapped at
   `_scene_token_candidates`. Should Large actually occupy 2×2, or stay cosmetic?

## Round 3 — Tokens: identity, art, ownership, visibility

Grounded in findings 16, 12.

1. **Token art.** No upload exists; unlinked tokens are coloured circles with initials.
   Options: upload per token; pick from a library of previously used art; reuse the
   monster's compendium image; leave as circles.
2. **What identifies a token at a glance** on a shared screen — nameplate always,
   on hover, only for PCs, or never (art alone)?
3. **Hidden tokens.** `visible_to_players` exists and is enforced server-side. On a
   GM-only map today it does nothing visible. Should hiding affect the GM's own view
   at all (e.g. ghosted), or only the table screen?
4. **Stale dropdowns** (finding 12, unconfirmed). Should the token/scene pickers refresh
   live from SSE, or is a manual refresh acceptable?

## Round 4 — Selection & the GM editing loop

Grounded in findings 1, 2, 5, 11, 19. **This is the round that most affects day-to-day use.**

1. **The inspector never hides.** Fix by honouring `[hidden]`, or replace with an
   explicit empty state ("no token selected"), or dock it as a popover on the token?
2. **SSE overwrites what you are typing.** Options: only repaint fields the GM is not
   focused on; make edits explicit (an Apply button) so repaints are safe; debounce
   repaints while a field is dirty.
3. **`focusActiveTurn` steals selection on turn advance.** Options: scroll the viewport
   but never change selection; make follow-turn off by default; keep as-is.
4. **Undo.** Currently movement-only, and Ctrl+Z silently moves a token when you meant
   to undo a wall. Options: a general undo stack across walls/lights/templates/fog;
   per-tool undo; leave movement-only but stop Ctrl+Z firing when a non-move tool is active.

## Round 5 — Walls, doors, fog & vision

Grounded in findings 13, 14, 15, 18, 20.

1. **Wall drawing.** Each segment is one HTTP round-trip with no chaining, no snapping
   and no undo. Options: polyline chaining until Esc; snap wall ends to the grid;
   batch the round-trips.
2. **Fog model.** Today it is a replayed op-log with brush reveal/hide. Options: keep
   painting; switch to room/region reveal (click a walled area to reveal it); both.
3. **Vision.** `dynamic_lighting` currently renders nothing on the GM's screen, so you
   cannot preview what the party sees. Options: add a "preview player view" toggle;
   compute vision server-side (needed anyway if walls must stay secret); drop dynamic
   vision and use manual fog only.
4. **Lights.** Fixed intensity, no editing after placement, no GM preview. Worth
   finishing, or cut lights entirely and rely on fog?

## Round 6 — Templates, targeting & combat from the map

Grounded in findings 17, 18.

1. **Template set.** Burst and Emanation are currently identical. PF2e wants burst,
   emanation, cone, line. Should they behave distinctly, and should cone/line size come
   from the numeric field rather than drag length?
2. **Targeting.** A template auto-selects the tokens it covers. Should that be automatic,
   confirmed, or manual-only?
3. **Combat actions from the map.** Damage/heal/conditions already delegate to the
   tracker's own helpers, so there is one HP engine. What is missing that you would
   actually use mid-fight — saves, persistent damage, conditions with durations?
4. **Bulk actions.** Bulk damage/heal/condition and save requests exist for targeted
   tokens. Is the targeting flow the right entry point, or should it come from the tracker?

## Round 7 — The shared table screen, and performance

Grounded in findings 21, 22, and the settled decision.

1. **What is the table screen, physically?** A browser on a TV driven by you; a second
   window on your machine; a cast/mirror of your view?
2. **What does it show** that your screen doesn't — fog applied, hidden tokens removed,
   no sidebar, larger tokens/nameplates?
3. **Auth.** Simplest is a GM-authenticated route (e.g. `/map/table`) so no player-facing
   auth surface is reintroduced. Acceptable, or does the table screen need its own
   token/link?
4. **How much performance work is worth it?** The render loop allocates two full-scene
   offscreen canvases per frame and raycasts inside `pointermove`. On a desktop driving a
   TV this may never matter; it matters a lot on a tablet. What is the target device?

---

## After the audit

Expect the output to be a prioritised change list, not a single PR. The likely split:

1. **Cheap correctness** — the two CSS `[hidden]` bugs, token placement, snap-to-centre,
   GM auto-fit. Small, independent, immediately felt.
2. **The GM editing loop** — SSE repaint policy, selection stealing, undo.
3. **Scene lifecycle** — prep/push split, delete, background-defines-scene.
4. **The table screen** — new work, revives a cancelled project, needs `CLAUDE.md` updated.
5. **Rendering** — only as far as the target device demands.
