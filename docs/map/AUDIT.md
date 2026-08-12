# Tactical map — feature audit

**Status: audit COMPLETE (all seven rounds answered 2026-08-11). Build in progress.**
**[The plan](#the-plan) is the running order; tick items off there.**

| Stage | State |
|---|---|
| 1 — cheap correctness | **shipped** |
| 2a — the image is the map | **shipped** |
| 2b — scene lifecycle (prep vs push, delete) | **shipped** |
| 3 — tokens, geometry, art | **shipped** |
| 4a — walls as runs | **shipped** |
| 4b — render by view mode | **shipped** |
| 4c — region fog | **shipped** |
| 4d — editable lights | **shipped** |
| 5a — templates | **shipped** |
| 5b — durations, persistent damage, move measure | **shipped** |
| 6a — frame scheduling, mask reuse, vision cache | **shipped** |
| 6b — the shared table screen (`/map/table`) | **shipped** |
| 6c — two-zone light, flicker, feathered fog | **shipped** |
| 6d — token glide, floating damage | **shipped** |
| 6e — environmental terrain (lava/water/poison/blood) | in review |
| 7 — UI audit | **next** |

**Verification note for stage 7.** `requestAnimationFrame` does not fire while
`document.hidden` is true, and the headless preview pane is permanently hidden — so
after stage 6a the canvas does not render there on its own. Call
`window.__mapRenderNow()` to force a frame. Without it the map appears blank and
every visual check silently measures nothing.

Stage 6e extended that seam to take a timestamp: `window.__mapRenderNow(3000)`
renders one frame **at** that point on the animation clock. The clock is only ever
advanced by the rAF loop, so without an argument every forced frame is frame zero
and nothing animated can be told apart from something merely present. That is how
6e proved blood is motionless and lava is not.

**Two measurement lessons from 6e, both of which cost a wrong conclusion first.**
Sample against a *control*, not against intuition: forcing two frames at the same
timestamp showed a noise floor of ~0.45 per-pixel, and poison's bubbles were
initially only 1.6x that — present in the code, invisible in the room. And
*look at the picture*, not only at the aggregates. Terrain measured correctly on
every statistic while a full-width caustic pattern rendered as hard parallel
hatching, and while a 0.42-square feather quietly merged two pools a full empty
row apart. Neither was visible in any number. Rendering an amplified
with-minus-without difference image is what exposed both.

This doc exists so the audit could survive moving between machines. Claude's per-project
memory lives under `~/.claude/projects/<path>/memory/` and does **not** travel, so
everything that matters is written down here instead.

---

## How to use this now

The audit is done. **Go to [The plan](#the-plan)** — six dependency-ordered stages, each
independently shippable. Stage 1 is a few hours and fixes the two defects that make the
tool feel broken.

The rounds below are kept as the record of *why* each decision was made, with the
original questions preserved for reference. The findings section is the evidence base:
`[browser]` items were verified against a running server, `[code]` items were read but
not exercised — that distinction still matters when planning work.

Two things carried forward that are easy to get wrong:

- **The name-reveal design is unresolved** (Round 3 open sub-question). "NPCs unnamed
  until revealed" was chosen, but no such state exists today. Settle it before coding.
- **Finding 12 (stale pickers) was never confirmed** — a forced navigation masked it
  during testing. It is a claim, not a fact, though the fix was chosen anyway.

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

## Round 1 — Scene lifecycle & backgrounds — **ANSWERED 2026-08-11**

**Decisions:**

1. **Separate open from push.** Selecting a scene opens it on the GM's screen only.
   A distinct "Show on table" action controls what the table follows. Prep during a
   live session must not reveal anything.
2. **Delete with confirm + asset cleanup.** Delete removes the scene *and* its
   background image. Blocked while the scene is live on the table.
3. **The image defines the scene.** On upload, resize the scene to the image's real
   dimensions (capped), preserving aspect ratio. Kills the stretch, and unlocks
   zooming to source resolution.
4. **Drag to calibrate the grid** over the uploaded image, deriving size + offset from
   two gridline intersections. Build on the existing "Align grid by dragging".

**Implications for later rounds:** scenes are now arbitrary-sized (a 4096px map is a
4096px scene), so the opening view and any per-scene zoom/pan memory matter more than
they did at a fixed 1400×900. Grid size becomes *derived* from calibration rather than
typed, which makes "snap to cell centre" well-defined. And `active_scene_id` stops
meaning "what the GM is looking at" — it becomes "what the table is showing", so every
read of it needs revisiting.

---

### Original questions (for reference)

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

## Round 2 — Grid, snapping & token placement — **ANSWERED 2026-08-11**

**Decisions:**

1. **Drag from the sidebar onto the map** to place a token. Replaces the hardcoded
   `(70,70)` drop point entirely. The drag preview should show the token's real
   footprint (see 2/4) so you can see what you are about to occupy.
2. **Snap to cell centres, with size-aware footprints.** Medium sits in one square;
   Large occupies a true 2×2, Huge 3×3, snapping to the correct block.
3. **Remember zoom/pan per scene** so reopening prep lands where you left it.
4. **Size drives real footprint**, not just visual scale.

**Implications:** 2 and 4 together make footprint load-bearing rather than cosmetic —
it now affects placement, snapping, and whether a template covers a creature, which
lands squarely in Round 6. `token.size` already exists as a number in the schema, so
this is geometry work, not a migration.

Open sub-question for implementation: per-scene zoom/pan is GM view state. Client-side
`localStorage` is simplest and correct while the map is GM-only, but the table screen
(Round 7) has its own view, so the two must not share a key.

---

### Original questions (for reference)

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

## Round 3 — Tokens: identity, art, visibility — **ANSWERED 2026-08-11**

**Decisions:**

1. **Compendium art by default, with per-token upload as an override.** Monsters pull
   their bestiary image automatically; any token can have art uploaded over the top.
   (Combines two options — the GM asked for both.)
2. **PCs always named; NPCs unnamed until revealed.**
3. **Hidden tokens are ghosted for the GM, absent from the table screen.**
4. **Pickers must refresh live** — rewire the scene/token `<select>`s off the existing
   `encounter_update` SSE event instead of Jinja render-time snapshots.

**OPEN SUB-QUESTION raised by decision 2 — do not implement blind.** There is no
"visible but not yet named" state today. `visible_to_players` is binary (the player
filter drops hidden NPCs entirely), and `epithet` (`app.py:5143`, set at `app.py:6138`)
is a boss-reveal *title*, not a name-suppression flag. So "revealed" has to be built.
Three candidate shapes, to settle before coding:

- reuse `visible_to_players` (visible ⇒ named) — cheapest, but then you cannot put a
  creature on the table without naming it, which is exactly what was asked for;
- add a per-token/per-combatant `name_revealed` flag — explicit, one more piece of state;
- display `epithet` ("the Hooded Figure") until revealed, then the real name — uses what
  already exists and is the most evocative at the table.

Whichever is chosen, it must live on the **combatant/tracker** side, not be re-derived
in the map. Two separate copies of a player-visibility rule is precisely what caused the
NPC-HP leak and the `sanitize_for_player` incident.

---

### Original questions (for reference)

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

## Round 4 — Selection & the GM editing loop — **ANSWERED 2026-08-11**

**Decisions** (all four: the GM's in-progress edit is sacred):

1. **Hide the inspector when nothing is selected**, with a one-line "select a token"
   empty state so the sidebar doesn't just develop a hole. Needs the missing `[hidden]`
   rule on `.map-token-actions` *and* on `.map-btn` (the "Open linked sheet" anchor).
2. **Never repaint a field the GM is editing.** Skip repaint for the focused field and
   for anything dirty-but-unsaved; repaint everything else freely. The map stays live
   without eating the edit.
3. **Follow-turn scrolls but never changes selection.** Keep the viewport following;
   drop the `selectedId` reassignment in `focusActiveTurn` (`map.js:167`).
4. **One undo stack for everything** — walls, doors, lights, templates, fog and moves.
   Each action needs an inverse; today only movement has one (`movementHistory`, cap 30).

**Note:** 1 is two CSS lines and the single cheapest win in the whole audit. 2 and 3 are
small and land in the same repaint path (`applyScene` → `fillControls` /
`updateSelectionPanel`). 4 is the only large item in this round.

---

### Original questions (for reference)

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

## Round 5 — Walls, doors, fog & vision — **ANSWERED 2026-08-11**

**Decisions:**

1. **The table shows the union of all PCs' vision.** One screen, one answer — no
   per-player state, which is the natural fit for the settled audience.
2. **Fog is revealed by region/room**, not brushed.
3. **A "preview table view" toggle** flips the GM canvas to exactly what the table is
   showing, fog and hidden tokens included.
4. **Finish lights** — editable radius/colour/intensity, and they contribute to what
   the table reveals.

**Dependencies and risks, in rough order of danger:**

- **Region reveal presupposes walls that actually enclose.** Today walls are drawn one
  unconnected segment at a time with no snapping, so a one-pixel gap leaks the reveal
  into the next room. Region fog is only as good as wall completeness — so wall
  *drawing* (chaining + snap-to-grid + batching the per-segment round-trips) is a
  prerequisite for the fog model, not a separate nice-to-have.
- **The preview toggle forces a refactor**: `drawVisionOverlay` currently runs only when
  `!cfg.isGm` (`map.js:184`). Vision/fog rendering has to become a *view mode* rather
  than a role check. That is also what makes the table screen renderable at all, so it
  is on the critical path for Round 7 either way.
- **Union-of-vision plus contributing lights is the expensive path.** Raycasting is
  already `(160 + 6W)` angles × W walls per source per frame, uncached, inside
  `pointermove`. It now runs on the machine driving the TV rather than a phone, which
  buys headroom, but this is the decision that makes Round 7's performance question real
  rather than theoretical.

---

### Original questions (for reference)

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

## Round 6 — Templates, targeting & combat from the map — **ANSWERED 2026-08-11**

**Decisions:**

1. **Numeric size for every template; drag sets direction only** for cone and line.
   Burst and emanation become genuinely distinct — emanation radiates from the
   creature's whole footprint, burst from a point. (Today they are identical:
   `map.js:393`, `map.js:432`.)
2. **Auto-select targets, then adjust** — the template proposes, the GM edits the list
   before acting.
3. **Add:** conditions with durations, persistent damage, and live movement measurement
   against Speed while dragging. Condition timers (`condition_expiry`) and persistent
   damage are already modelled and persisted server-side, so those two are wiring, not
   new engines. Drag-measurement is genuinely new (a ruler exists; measuring a move
   against the creature's Speed does not).
4. **Area effects start on the map, everything else stays in the tracker.**

**Note on what was deliberately *not* chosen:** saving throws against a template were
left off the "missing" list because they are not missing — `/api/scenes/<id>/bulk-combat`
already takes a save kind + DC and reuses the existing `check_request` SSE. Read as
confirmation of the existing path, not a rejection of it.

**Dependency:** decision 2 combined with Round 2's real footprints means
`templateContainsToken` must test the token's **footprint**, not its centre point — a
burst clipping one corner of a Large creature has to catch it. That is the same geometry
change footprint touches everywhere else.

---

### Original questions (for reference)

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

## Round 7 — The shared table screen, and performance — **ANSWERED 2026-08-11**

**Decisions:**

1. **The table screen is a browser on a TV, driven by the GM's own machine** — a second
   window/tab dragged onto the TV. Rendering happens on the fastest hardware in the room,
   and it needs no independent auth.
2. **The table view shows:** fog applied and hidden tokens absent; no sidebar or tools,
   map only; larger nameplates and health for across-the-room legibility; and a prominent
   current-turn indicator (the tracker already broadcasts turn state).
3. **A GM-authenticated route** (e.g. `/map/table`), behind the existing gate. This is
   what keeps the decision to go GM-only intact — no player-facing auth surface is
   reintroduced, so the two leaks that motivated the gate stay unreachable.
4. **Performance work targeted at a laptop driving a TV**: `requestAnimationFrame`, stop
   allocating offscreen canvases per frame, and cache vision until walls or tokens
   actually move. Not the full tablet treatment.

**Consequence:** decision 1 + 3 together mean the table screen is a *view mode of the
existing page*, not a new app — which is why the vision refactor in Round 5 (render by
view mode rather than by `!cfg.isGm`) is the single piece of work that unlocks both the
preview toggle and the table screen.

---

### Original questions (for reference)

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

## The plan

All seven rounds answered 2026-08-11. Ordered by dependency, not by appeal. Each stage
is independently shippable; nothing here is one big PR.

### Stage 1 — Cheap correctness (hours, immediately felt)

Nothing here depends on anything else, and stage 1 is the single best value in the audit.

- `[hidden]` rules for `.map-token-actions` and `.map-btn`, plus a one-line empty state.
  **Two CSS lines fix the permanently-visible inspector and the always-on "Open linked
  sheet" button.** [R4.1]
- Follow-turn scrolls but no longer reassigns `selectedId` (`map.js:167`). [R4.3]
- Don't repaint the focused or dirty field in `fillControls` / `updateSelectionPanel`. [R4.2]
- Rebuild the scene/token pickers from `encounter_update` instead of Jinja snapshots. [R3.4]

### Stage 2 — Scene lifecycle (unblocks using real battlemaps)

- Image defines scene dimensions on upload; preserve aspect, cap for sanity. [R1.3]
- Drag-to-calibrate the grid over the image. [R1.4]
- Delete scene + background asset, with confirm, blocked while live. [R1.2]
- Split open from push: selecting opens locally, "Show on table" activates. **Audit
  every read of `active_scene_id`** — it stops meaning "what the GM sees". [R1.1]
- Remember zoom/pan per scene (client-side; must not share a key with the table view). [R2.3]

### Stage 3 — Tokens and geometry

- Drag-from-sidebar placement, with a footprint-accurate drag preview. [R2.1]
- Snap to cell centres; real 2×2 / 3×3 footprints for Large / Huge. [R2.2, R2.4]
- Compendium art by default, per-token upload as override. [R3.1]
- Hidden tokens ghosted for the GM. [R3.3]
- **Settle the name-reveal design first** (see Round 3's open sub-question) — there is no
  "visible but unnamed" state today, and it must live on the combatant side, not be
  re-derived in the map. [R3.2]

### Stage 4 — Walls, fog, vision, lights (the big one)

Strict internal order — each item is a prerequisite for the next:

1. **Wall drawing**: polyline chaining, snap-to-grid, batch the per-segment round-trips.
   Region fog is only as good as wall completeness; a one-pixel gap leaks a reveal.
2. **Vision as a view mode**, not `!cfg.isGm`. This one refactor unlocks both the preview
   toggle and the table screen.
3. **Region/room fog reveal.** [R5.2]
4. **Union of all PCs' vision.** [R5.1]
5. **Finish lights** — editable radius/colour/intensity, contributing to reveal. [R5.4]
6. **Preview-table-view toggle.** [R5.3]

Do stage 6's rendering work *alongside* this, not after: union vision plus contributing
lights is precisely what makes the current loop too slow.

### Stage 5 — Templates and combat

- Numeric size for all templates; drag sets direction only. Burst ≠ emanation. [R6.1]
- `templateContainsToken` tests the **footprint**, not the centre (depends on stage 3). [R6.2]
- Auto-select targets, then adjust. [R6.2]
- Conditions with durations and persistent damage from the map — both already modelled
  server-side, so this is wiring. [R6.3]
- Live movement measurement against Speed while dragging — genuinely new. [R6.3]

### Stage 6 — The table screen and performance

- `requestAnimationFrame`; stop allocating two full-scene offscreen canvases per frame;
  cache vision until walls or tokens move. Target: a laptop driving a TV. [R7.4]
- GM-authenticated `/map/table` as a view mode of the same page. [R7.1, R7.3]
- Table view: fog applied, hidden tokens absent, no chrome, large nameplates and health,
  prominent turn indicator. [R7.2]
- **Update `CLAUDE.md`** — a shared table screen revives the Campaign Hub Stage, which
  that file still records as cancelled and unowned.

### Corrections to earlier rounds (found while building)

**Name reveal: REVERSED 2026-08-11.** Round 3 chose "PCs always named, NPCs only once
revealed". When it came to build it, no such state existed — `visible_to_players` is
binary and `epithet` is a boss-reveal *title*, not a name suppressor — so the GM was
asked again with the real cost visible and chose **always show names**. Nameplates
already default to on, so this is now a no-op: nothing to build, and the per-token
`show_nameplate` toggle remains as a manual override. If reveal ever comes back, it was
decided it must live **on the combatant in the tracker**, not on the map token.

**Compendium token art: NOT BUILDABLE.** Round 3 chose "compendium art by default, with
per-token upload as an override", on my framing that the automatic half would cover most
tokens for free. That framing was wrong. Every monster JSON does carry an `img`, but
across 2497 bestiary files **2473 of 2475 entries are the same generic Foundry default**
(`systems/pf2e/icons/default-icons/npc.svg`), and the two exceptions are also
Foundry-internal paths this app cannot serve. There is no monster art in this dataset.

Wiring it would give every creature an identical grey silhouette — strictly worse than
the current coloured disc with initials, which at least tells two monsters apart. So the
automatic half is dropped. **Per-token upload is the only route to real token art**, and
whether to build it is an open question rather than a settled decision.

### Stage 7 — In-depth UI audit (requested 2026-08-11)

Once all six stages are built, the GM asked for a **separate in-depth UI audit** of the
map, in the same question-driven format as this one. Deliberately *after* the
functionality lands: several stages change what is on screen at all (the inspector's
empty state, footprint-accurate tokens, region fog, the table view's larger nameplates),
so auditing the current UI would be auditing something about to be replaced.

Scope it against `CLAUDE.md`'s design constraints — two typefaces only (Cinzel display,
Inter UI, via `--font-display` / `--font-ui`), and the note that standalone pages have
historically smuggled in extra faces by hardcoding families instead of using the tokens.
`map.html` is a standalone page and has never been through a design pass.

### Cross-cutting

- **Undo across everything** [R4.4] touches stages 3–5: every action needs an inverse.
  Add each inverse as its action lands rather than retrofitting one stack at the end.
- **Player projection stays untouched and tested** until stage 6 needs it. It is the
  GM↔table boundary, and rewriting a security boundary from scratch is what caused the
  `sanitize_for_player` incident.
