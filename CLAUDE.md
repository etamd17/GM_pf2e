# GM_pf2e — project guide for Claude Code

A Flask web app: a GM's table tool for **Pathfinder 2e** and the **Cosmere RPG**
(Stormlight). Character builder + leveler, bestiary, live combat tracker, player
sheets, GM screen — server-rendered, real-time over SSE, run in person at the table.

## Run / test / deploy

```bash
# Local dev server (Flask). Legacy-open mode = no auth when GM_PASSWORD is empty.
DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5001 FLASK_DEBUG=true python app.py

pytest -q                      # full test suite (CI runs this)
python tools/check_templates.py   # Jinja parse check (CI runs this) — run after editing any .html
```

- **Port 5001**, not 5057 — `app.py`'s `PORT` default, `.claude/launch.json`, and
  `start.command` all agree on 5001.
- **Set `DATA_DIR` when you run locally.** `.claude/launch.json` does not, so
  `core/storage.py` falls back to `BASE_DIR` and the app writes runtime state
  (including `scenes/` and uploaded map backgrounds) into the repo root. Those
  paths are gitignored, but a stray `DATA_DIR` still mixes runtime data with
  source.
- **Legacy-open mode makes everyone a GM.** With no `GM_PASSWORD` and no
  accounts, `_is_gm()` returns True for every request — so `/player/*` pages
  render the *GM* payload. Any test of player-facing filtering or hidden-token
  leakage must set `GM_PASSWORD` or run in account mode, or it proves nothing.

- **Production runs on Railway and auto-deploys `main`.** Keep `main` green; a push to `main` ships to players. Persistent data lives on a Railway volume (`/data`); features must write to the volume, never the local FS or symlinks. See `DEPLOY.md`.
- Prod uses **gunicorn with exactly one gevent worker** (`Procfile`). This is mandatory for SSE (see below) — do not raise `--workers`.
- `FLASK_DEBUG=true` auto-reloads Jinja templates (no restart for `.html` edits). Static CSS/JS is still browser-cached — cache-bust with `?cb=` when testing CSS.
- Tests that need live party data skip when `party_data/` is absent (it's gitignored). Committed ground-truth lives in `tests/fixtures/*.json`.
- **The suite runs on Windows as well as Linux** (it didn't used to; ~50 failures were platform artifacts, not real ones). Two rules keep it that way:
  - **Always pass `encoding='utf-8'` when a test reads a repo file.** Bare `read_text()` / `open()` use the platform codec — cp1252 on Windows — and every template, `system.css` and `app.py` contains non-ASCII. CI is Linux and will not catch it.
  - **`os.replace` cannot overwrite an existing directory symlink on Windows** (`MoveFileEx` refuses `MOVEFILE_REPLACE_EXISTING` for anything with `FILE_ATTRIBUTE_DIRECTORY` → `WinError 5`). Replacing a *missing* one is fine, so this only bites on the second write. `_chronicle_repoint` (`app.py:844`) is the one place that does this and it handles it; if you add another symlink swap, do the same. `os.remove` on a directory symlink *does* work — that part needs no special casing.

## Working agreements

- **No emojis** in code, UI strings, comments, or commit messages unless explicitly asked.
- **Commit/push only when the user asks.** Never commit directly on `main` — branch off it; the user decides when to merge/push. Co-author trailer on commits:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Verify prod-facing fixes on Railway**, not just locally — local-green has missed prod-only failures before.
- This is a **single-GM, in-person** tool (4 players + 1 GM). Snappiness with that table + tracker↔sheet sync is the priority.
- **Removed, do not rebuild:** the in-app notes/Obsidian vault (the GM authors in real Obsidian; the site only keeps a read-only story-thread view + a manual session recap).
- **The tactical map is BACK IN SCOPE for this repo** (reopened 2026-08-06). An
  earlier line here said the VTT was removed and owned by a separate parallel
  effort — that is no longer true; see *Tactical map* under Architecture. Anything
  claiming the map is gone is stale.

## Architecture

- **`app.py` is a ~20k-line monolith** — all Flask routes plus the `Character` (PF2e) and `Monster` classes. Live combat state is held in **process globals** (`ACTIVE_ENCOUNTER`, `ROUND_NUMBER`, `TURN_INDEX`, `PARTY_LIBRARY`, …), flushed to `server_state.json` (`_persist_encounter_state`) and re-hydrated on boot. There is **one live campaign slot** at a time; `load_campaign(cid)` rebinds the globals.
- **Server-rendered Jinja + vanilla JS.** No build step, no SPA framework.
- **SSE** (`/api/events`): every page subscribes through the shared hub `window.appSSE(eventName, handler)` in `templates/_sse_hub.html` — **never** `new EventSource('/api/events')` directly (one socket per tab; the hub multiplexes + reconnects). Broadcast from the server with `sse_broadcast(event, data, player_filter=...)`: `data` goes to GMs, and `player_filter(copy)` returns the player-facing payload (or `None` to drop it for players entirely) — computed once and shared by all player subscribers.
  - **`?audience=table` is NOT a server-side feature.** Its only use is client-side in `_sse_hub.html`: a passive table screen has no operator, so it self-reloads on a new deploy instead of showing the "New version" toast. There is no audience concept in `app.py` (grep: 0 hits) and no shared-table frame yet — the Campaign Hub's Stage has to build one.
- **The Chronicle has TWO publishing lanes**, and they share storage with nothing.
  - **Vault lane** (original): `tools/chronicle_build.py` runs on the GM's machine, derives a spoiler-safe player vault from the Obsidian GM vault, hard-aborts on a surviving `[!danger]`/`[!secret]`/`[!gm]` marker, zips it and POSTs to `/api/chronicle/publish`. Whole-tree replace via `content/<hash>` + `current`/`previous` symlinks (`_chronicle_swap`).
  - **Doc lane** (`core/chronicle_docs.py`): the GM uploads a `.docx`/`.md`/`.txt` at `/chronicle/manage`, previews it, then toggles `published`. Stored at `chronicle/docs/` — a **sibling** of `content/`, never inside it, because `_chronicle_swap`'s prune deletes every content dir it doesn't point at. No symlinks, no rotation; the toggle *is* the rollback.
  - Trust models differ deliberately. The vault lane strips automatically and aborts; the doc lane trusts the GM's own preview and only *warns* on a marker (a GM writing in Word has no callout syntax, and a false positive that refuses the file defeats the feature). Nothing is player-visible until `published` is flipped.
  - **They union at read time in `_chronicle_visible_pages`**, and `_chronicle_fragment` dispatches by lane. Those two plus `_chronicle_render`'s manifest gate are the entire merge surface — every `/chronicle*` route funnels through them.
  - **`.docx` is parsed with the stdlib** (`zipfile` + `ElementTree` over OOXML) — no new production dependency on a service that auto-deploys `main`. Handles headings, emphasis, lists, tables and links; Word styling with no allowlisted equivalent is dropped, which the sanitizer would do anyway.
  - **Every converted byte goes through `_chronicle_sanitize_html` at WRITE time.** `_chronicle_fragment` hands fragments straight to `|safe` with no checks, so that call is the only thing between GM-pasted markup and the table. Sanitize before writing, never on read.
- **Multi-system**: `systems/` registry; `_active_system()` / `_active_campaign_id()` are request/session-scoped. Templates branch on `body.system-pf2e` / `body.system-cosmere`. Cosmere actors are `systems/cosmere/actor.py::CosmereActor` (reads the Foundry `cosmere-rpg` schema); Cosmere combat is flat-integer, defenses are static (phy/cog/spi), conditions are mostly advantage/disadvantage + Exhausted (a flat test penalty).
- **Not everything is in `app.py`.** `core/` holds standalone modules app.py imports without a circular dependency: `storage.py` (campaign-scoped path resolution + atomic JSON I/O, and the `^[0-9a-f]{32}$` id validation that makes traversal impossible), `auth.py`, `campaigns.py`, `backups.py`, `scenes.py`. `services/` holds `active_effects.py` and `scene_sync.py`.
- **GM auth — there are THREE patterns, not two.** (1) a `check_gm_access` before_request gates path-prefixes in `GM_API_PREFIXES` (don't re-flag prefix-gated routes as unauthenticated); (2) `@gm_required` is a per-route decorator; (3) **inline `if not _is_gm(): 403` inside a shared route** — used where one URL serves both roles, e.g. `GET/PATCH /api/scenes/<id>` reads for any member but writes for the GM only. No scene path appears in `GM_API_PREFIXES`, so pattern 3 is the only thing protecting several of them. When auditing, grep the handler body, not just the decorator. `_is_gm()` is true for the site admin, the active campaign's GM, or legacy-open mode (no `GM_PASSWORD`).
- Atomic JSON writes via `_atomic_write_json`; a global `/api/*` JSON error handler.
- **Tactical map (VTT).** Pages `/map`, `/map/<scene_id>` (GM) and `/player/map`; API under `/api/scenes/*`. Routes and helpers are one contiguous block, `app.py:6452-7261`. Supporting code: `core/scenes.py` (scene schema, token helpers), `services/scene_sync.py` (projection + player sanitization), `templates/map.html`, `static/js/map.js` (canvas renderer, tools, vision raycasting), `static/css/map.css`. Tests in `tests/test_scenes.py`.
  - **Scene JSON stores presentation state ONLY** — background, grid, token placement, visibility, walls, lights, fog, templates. HP, conditions, initiative, resistances, dying and healing stay authoritative in `ACTIVE_ENCOUNTER` / the character sheets, and are projected onto tokens at read time by `_scene_live_indexes` + `project_scene`. Never persist combat state into a scene file.
  - **Every player-facing byte goes through `services/scene_sync.py::project_scene(..., player=True)`.** That one function is the whole player/GM boundary: it drops hidden tokens, strips `controller_user_id`, filters GM-only lights and templates, and masks closed secret doors. If you add a field to a token, decide there whether players may see it.
  - There is exactly **one** player sanitizer. A second, `core/scenes.py::sanitize_for_player`, was deleted — it had zero callers and had already drifted (it never filtered GM-only templates, and it masked secret doors the fingerprintable way). Don't reintroduce a parallel one.
  - Scenes live at `DATA_DIR/campaigns/<cid>/scenes/` (or `DATA_DIR/scenes/` with no campaign), gitignored.

## High-risk areas — be careful

- **PB import + level-up correctness** is the highest-risk surface. `Character.__init__` parses Pathbuilder exports; `class_matrix.py` drives per-level proficiency timing. Guarded by ground-truth-vs-Pathbuilder + full-sheet snapshot tests (`tests/test_pc_snapshots.py`, `tests/snapshots/`, `tests/test_pb_import_correctness.py`). If you touch stat derivation, run these; regenerate snapshots by deleting `tests/snapshots/<dir>/` and running pytest twice.
- **Inline event-handler escaping (recurring bug class).** A user-controlled string (PC/spell/feat/item/combatant/compendium name) interpolated into an `onclick="..."` JS string **must** be JS-escaped: `.replace(/'/g, "\\'")` (or `.replace(/\\/g,'\\\\').replace(/'/g,"\\'")`). An apostrophe ("Go'el", "Thieves' Tools") otherwise closes the string → `SyntaxError` → dead button. **HTML escaping (`esc()`, `&#39;`) does NOT help** — the browser decodes the entity back to `'` before JS runs. Guarded by `tests/test_inline_handler_escaping.py`.
- The PC sheet repaints in place from the `pc_update` SSE `derived` block (saves/skills/strikes/conditions) — if you add a stat the sheet paints, make sure `_pc_state_payload` ships it, or the UI goes stale.
- **Hidden-NPC information leaks are a repeat offender.** The tracker deliberately coarsens non-PC health for players — `hp_status` of `""` / `"Wounded"` / `"Dead"` and never a number (`app.py:14931`) — and masks a hidden combatant's whole identity to `"???"`. This was fixed once already (`ROADMAP.md`, session-critical item 1) and the tactical map reintroduced it: `_scene_live_indexes` puts raw `current_hp`/`max_hp` on every combatant and `project_scene` ships it to players untouched. **Any new player-facing payload must match the tracker's policy, not invent its own.** See *Known defects* below.
- **`tests/test_inline_handler_escaping.py` only globs `templates/**/*.html`** (line 99). Files under `static/js/` are outside the guard entirely. `map.js` happens to be safe (canvas-driven; no `onclick`/`innerHTML` anywhere), but do not read a green suite as proof that a `.js` file is clean.

## Rules reference

Engine fidelity is audited and documented — check these before claiming a rule
"isn't available": `PF2E_RULES_AUDIT.md`, `COSMERE_RULES_AUDIT.md`, `ROADMAP.md`,
`FOUNDRY_INTEROP.md`. The fullest Cosmere rules text is at `~/Downloads/Stormlight_Rules.txt`.

## Current work

**The tactical map is in flight** (reopened 2026-08-06). A complete VTT layer landed in this
working copy — scenes, tokens, encounter sync, map-native combat, fog, walls/doors, lights,
templates, ruler, and client-side vision. It is wired into the GM hub (`gm_hub.html:148`) and the
player nav (`_player_nav.html:27`), and `tests/test_scenes.py` passes 19/19.

It has **not shipped**, and it is **not ready to**. Before it can:

1. **Fix the player-payload leaks** (see *Known defects*). Highest priority.
2. **Full fog is the agreed target**: server-authoritative vision *plus* background tiling, so
   unexplored map art never reaches the client. Today fog is cosmetic — the browser downloads the
   whole unfogged image and paints darkness over it, and receives every wall and every fog
   operation. This needs a design spec before code.
3. Land regression tests that actually exercise auth (see *Known defects*, item 5).

The app-wide UI/UX + minimal-design arc finished and shipped (see below).

### Known defects in the tactical map (confirmed by reading code, 2026-08-06)

Numbering is stable — fixed items keep their number so earlier references still resolve.

1. ~~**Players receive exact monster HP.**~~ **FIXED.** The policy now lives in one place,
   `app.py::_npc_hp_status`, called by the encounter SSE frame, `/api/player_state`, and
   `_scene_live_indexes`. `project_scene`'s player branch drops `current_hp`/`max_hp` for non-PCs
   via `_coarsen_live_for_player` and leaves the coarse `hp_status` behind; `map.js` paints a
   two-state bar from it. **Add a fourth caller, never a fourth copy** — this policy had already
   been duplicated once and the map's copy drifted straight back to shipping raw HP.
2. ~~**Secret doors are fingerprintable.**~~ **FIXED.** The mask now sets `secret=False` instead of
   popping the key, so a masked door is byte-identical in shape to a real wall.
3. ~~**PF2e cones are rendered at 60°, not 90°.**~~ **FIXED.** `spread` is `Math.PI / 4` in both the
   renderer and `templateContainsToken`'s hit test; a regression test pins both.
4. **Visibility polygon sorts across two angle ranges.** Base rays span `[0, 2π)` (`map.js:257`);
   wall-endpoint rays come from `Math.atan2`, range `(-π, π]` (`:260`). The single ascending sort at
   `:264` puts upper-half endpoint vertices at the head of the array instead of interleaving them.
   Normalize before pushing.
5. **Auth is mostly untested.** *Partly addressed.* The `scene_client` fixture still monkeypatches
   `_is_gm` to always-True, and `gm_required` is just `if _is_gm()`, so that fixture neuters the
   decorator on every route it touches — **do not add auth assertions to tests using it.** The new
   `real_auth_player_client` fixture stubs only the mode switches (legacy mode + a `GM_PASSWORD`)
   so `_is_gm()` and `_scene_member_allowed()` run their real branches; use it for anything
   player-facing. It now covers the 403 on eight GM map routes plus the player payload.
   Still uncovered: `POST .../activate` and `GET/POST .../background` success paths,
   `_scene_player_can_move`'s real branches, and — because there is no JS harness at all — every
   geometry and coordinate-transform function.
6. **Six full `party_data/` directory scans per token drag.** `_scene_payload` scans twice
   (`_scene_live_indexes` + `_scene_character_records`); `_broadcast_scene` builds two payloads and
   the PATCH response builds a third. Blocking disk I/O on the single gevent worker, contending with
   every open SSE stream.
7. **`core/storage.atomic_write_json` fsyncs unconditionally** with no opt-out, and every scene
   mutation routes through it. `app.py:178` documents that `os.fsync` is exactly the blocking
   syscall gevent cannot yield around, which is why `_atomic_write_json` exposes `fsync=False` for
   high-frequency writes. Token drags and fog strokes are high-frequency writes.
8. **A no-op token PATCH still writes to disk and broadcasts.** `save_scene` + `_broadcast_scene`
   run unconditionally at `app.py:7211-7212`, outside any did-anything-change check, bumping
   `revision` every call. Any campaign member can trigger it in a loop, unthrottled.
9. **`revision` is advisory.** Bumped on every save, but there is no `If-Match`, no expected-revision
   parameter and no client-side conflict detection. Concurrent edits are silently last-write-wins.
10. **`core/scenes.py::create_scene` and `set_active_scene` are not lock-protected**, unlike every
    mutating route. They check-then-write `scenes/index.json` with no `_path_lock`.
11. **Four GM routes 500 instead of 404 on a malformed scene id** — `sync-encounter` (`app.py:6836`),
    `combatants` (`:6913`), `elements` (`:6974`), `bulk-combat` (`:7078`) don't catch the `ValueError`
    from `scene_file()`. The other routes do.
12. **Background upload buffers before it checks size.** `request.files.get('image')` (`app.py:7236`)
    parses the whole multipart body before the 25 MB `content_length` check at `:7239`, and
    `MAX_CONTENT_LENGTH` is 64 MB. It also bypasses `_save_image_compressed` (`app.py:11050`). There
    is no delete-scene route anywhere, so volume growth is unbounded.
13. ~~**`GET /api/load_stage/<name>` is unauthenticated**~~ **FIXED.** `/api/load_stage/` joined
    `/api/save_stage` in `GM_API_PREFIXES`, so both halves of the stage round-trip are gated.

**Do NOT work on these — explicitly owned elsewhere or deferred by the user:**
- **Campaign Hub / the Stage** (`docs/superpowers/specs/2026-07-21-campaign-hub-design.md`). The user
  is building this **separately, with its own multi-agent setup**, to see how that approach performs.
  It is **out of scope for this repo's live site for now** and will be integrated much later. Do not
  implement it, and do not start prep work for it, unless the user explicitly reopens it. (No Stage
  code was ever written here — `api_stage_encounter()` in app.py is an unrelated pre-existing route
  for staging an encounter.)
- Player-sheet **inventory reorg** ("hold on inventory scope").
- A **sticky HP/conditions chip** on the player sheet — considered and dropped by the user.
- **Player-vault ingestion** (plan: `docs/superpowers/plans/2026-07-21-chronicle-player-vault-ingest.md`).

### Recently shipped (do NOT re-suggest as new work)
- **App-wide UI/UX + minimal-design arc — COMPLETE**, live on Railway in three merges: `3c4cffba`
  (Cosmere page-by-page audit), `7ad84d46` (shared-CSS minimal pass), `d0655aa3` (PF2e GM screen,
  builder + level-up, landing/account chrome, player sheet).
- **Two typefaces only: Cinzel (display) + Inter (UI)** — via `--font-display` / `--font-ui`.
  Root cause of the old "mixed fonts" sprawl: standalone pages (builder, splash, `_account_base`,
  campaign_intro, player_sheet) **hardcode font families instead of using the tokens**, so app-wide
  sweeps miss them — each had smuggled in an extra face (Crimson Text, EB Garamond). If you add a
  standalone page, use the tokens. Deliberate exception: **Alegreya** (`--font-flavor`) on the player
  sheet, the reading serif for long-form rules prose.
- Round-events, dying automation, ten-minute rest, and the Chronicle are all **shipped**; the old
  `feat/table-view-vtt-program` branch is gone.

### Editing conventions learned the hard way
- **The player sheet is the highest-risk UI file.** Prefer *strictly additive* CSS appended at the
  end of its stylesheet (the minimal pass was 81 insertions / 0 deletions) so no markup, id, handler
  or JS moves — that is what keeps the `pc_update` repaint and spell preparation safe.
- To flatten Tailwind-utility boxing (`bg-gray-800 … shadow-xl`), add a **scoped CSS override**
  (e.g. `.step-panel .bg-gray-800 { … }`) rather than editing markup across many lines.
- When a page is served from a **git worktree**, the dev server serves that worktree's files —
  edit there, not in the main checkout, or your change silently won't appear.
