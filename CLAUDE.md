# GM_pf2e — project guide for Claude Code

A Flask web app: a GM's table tool for **Pathfinder 2e** and the **Cosmere RPG**
(Stormlight). Character builder + leveler, bestiary, live combat tracker, player
sheets, GM screen — server-rendered, real-time over SSE, run in person at the table.

## Run / test / deploy

```bash
# Local dev server (Flask). Legacy-open mode = no auth when GM_PASSWORD is empty.
DATA_DIR=$(mktemp -d) GM_PASSWORD='' PORT=5057 FLASK_DEBUG=true python app.py

pytest -q                      # full test suite (CI runs this)
python tools/check_templates.py   # Jinja parse check (CI runs this) — run after editing any .html
```

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
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Verify prod-facing fixes on Railway**, not just locally — local-green has missed prod-only failures before.
- This is a **single-GM, in-person** tool (4 players + 1 GM). Snappiness with that table + tracker↔sheet sync is the priority.
- **Removed, do not rebuild:** the VTT map (no battle maps) and the in-app notes/Obsidian vault (the GM authors in real Obsidian; the site only keeps a read-only story-thread view + a manual session recap).

## Architecture

- **`app.py` is a ~17k-line monolith** — all Flask routes plus the `Character` (PF2e) and `Monster` classes. Live combat state is held in **process globals** (`ACTIVE_ENCOUNTER`, `ROUND_NUMBER`, `TURN_INDEX`, `PARTY_LIBRARY`, …), flushed to `server_state.json` (`_persist_encounter_state`) and re-hydrated on boot. There is **one live campaign slot** at a time; `load_campaign(cid)` rebinds the globals.
- **Server-rendered Jinja + vanilla JS.** No build step, no SPA framework.
- **SSE** (`/api/events`): every page subscribes through the shared hub `window.appSSE(eventName, handler)` in `templates/_sse_hub.html` — **never** `new EventSource('/api/events')` directly (one socket per tab; the hub multiplexes + reconnects). Broadcast from the server with `sse_broadcast(event, data, player_filter=...)`: `data` goes to GMs, and `player_filter(copy)` returns the player-facing payload (or `None` to drop it for players entirely) — computed once and shared by all player subscribers.
  - **`?audience=table` is NOT a server-side feature.** Its only use is client-side in `_sse_hub.html`: a passive table screen has no operator, so it self-reloads on a new deploy instead of showing the "New version" toast. There is no audience concept in `app.py` (grep: 0 hits) and no shared-table frame yet — the Campaign Hub's Stage has to build one.
- **Multi-system**: `systems/` registry; `_active_system()` / `_active_campaign_id()` are request/session-scoped. Templates branch on `body.system-pf2e` / `body.system-cosmere`. Cosmere actors are `systems/cosmere/actor.py::CosmereActor` (reads the Foundry `cosmere-rpg` schema); Cosmere combat is flat-integer, defenses are static (phy/cog/spi), conditions are mostly advantage/disadvantage + Exhausted (a flat test penalty).
- **GM auth**: a `check_gm_access` before_request gates path-prefixes in `GM_API_PREFIXES` (don't re-flag prefix-gated routes as unauthenticated); `@gm_required` is a separate per-route gate. `_is_gm()` is true for the site admin, the active campaign's GM, or legacy-open mode (no `GM_PASSWORD`).
- Atomic JSON writes via `_atomic_write_json`; a global `/api/*` JSON error handler.

## High-risk areas — be careful

- **PB import + level-up correctness** is the highest-risk surface. `Character.__init__` parses Pathbuilder exports; `class_matrix.py` drives per-level proficiency timing. Guarded by ground-truth-vs-Pathbuilder + full-sheet snapshot tests (`tests/test_pc_snapshots.py`, `tests/snapshots/`, `tests/test_pb_import_correctness.py`). If you touch stat derivation, run these; regenerate snapshots by deleting `tests/snapshots/<dir>/` and running pytest twice.
- **Inline event-handler escaping (recurring bug class).** A user-controlled string (PC/spell/feat/item/combatant/compendium name) interpolated into an `onclick="..."` JS string **must** be JS-escaped: `.replace(/'/g, "\\'")` (or `.replace(/\\/g,'\\\\').replace(/'/g,"\\'")`). An apostrophe ("Go'el", "Thieves' Tools") otherwise closes the string → `SyntaxError` → dead button. **HTML escaping (`esc()`, `&#39;`) does NOT help** — the browser decodes the entity back to `'` before JS runs. Guarded by `tests/test_inline_handler_escaping.py`.
- The PC sheet repaints in place from the `pc_update` SSE `derived` block (saves/skills/strikes/conditions) — if you add a stat the sheet paints, make sure `_pc_state_payload` ships it, or the UI goes stale.

## Rules reference

Engine fidelity is audited and documented — check these before claiming a rule
"isn't available": `PF2E_RULES_AUDIT.md`, `COSMERE_RULES_AUDIT.md`, `ROADMAP.md`,
`FOUNDRY_INTEROP.md`. The fullest Cosmere rules text is at `~/Downloads/Stormlight_Rules.txt`.

## Current work

**No feature work is currently in flight.** The app-wide UI/UX + minimal-design arc finished and
shipped (see below); pick up whatever the user asks for next.

**Do NOT work on these — explicitly owned elsewhere or deferred by the user:**
- **Campaign Hub / the Stage** (`docs/superpowers/specs/2026-07-21-campaign-hub-design.md`). The user
  is building this **separately, with its own multi-agent setup**, to see how that approach performs.
  It is **out of scope for this repo's live site for now** and will be integrated much later. Do not
  implement it, and do not start prep work for it, unless the user explicitly reopens it. (No Stage
  code was ever written here — `api_stage_encounter()` in app.py is an unrelated pre-existing route
  for staging an encounter.)
- The **battle-map/VTT** — a separate parallel effort.
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
