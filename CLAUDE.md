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
  - **`?audience=table` is NOT a server-side feature.** Its only use is client-side in `_sse_hub.html`: a passive table screen has no operator, so it self-reloads on a new deploy instead of showing the "New version" toast. There is no audience concept in `app.py` (grep: 0 hits) and no shared-table frame at all. (The Campaign Hub's Stage was going to build one; that project is cancelled, so a shared-table frame now has no owner.)
- **The Chronicle has TWO publishing lanes**, and they share storage with nothing.
  - **Vault lane** (original): `tools/chronicle_build.py` runs on the GM's machine, derives a spoiler-safe player vault from the Obsidian GM vault, hard-aborts on a surviving `[!danger]`/`[!secret]`/`[!gm]` marker, zips it and POSTs to `/api/chronicle/publish`. Whole-tree replace via `content/<hash>` + `current`/`previous` symlinks (`_chronicle_swap`).
  - **Doc lane** (`core/chronicle_docs.py`): the GM uploads a `.docx`/`.md`/`.txt` at `/chronicle/manage`, previews it, then toggles `published`. Stored at `chronicle/docs/` — a **sibling** of `content/`, never inside it, because `_chronicle_swap`'s prune deletes every content dir it doesn't point at. No symlinks, no rotation; the toggle *is* the rollback.
  - Trust models differ deliberately. The vault lane strips automatically and aborts; the doc lane trusts the GM's own preview and only *warns* on a marker (a GM writing in Word has no callout syntax, and a false positive that refuses the file defeats the feature). Nothing is player-visible until `published` is flipped.
  - **They union at read time in `_chronicle_visible_pages`**, and `_chronicle_fragment` dispatches by lane. Those two plus `_chronicle_render`'s manifest gate are the entire merge surface — every `/chronicle*` route funnels through them.
  - **`.docx` is parsed with the stdlib** (`zipfile` + `ElementTree` over OOXML) — no new production dependency on a service that auto-deploys `main`. Handles headings, emphasis, lists, tables and links; Word styling with no allowlisted equivalent is dropped, which the sanitizer would do anyway.
  - **Every converted byte goes through `_chronicle_sanitize_html` at WRITE time.** `_chronicle_fragment` hands fragments straight to `|safe` with no checks, so that call is the only thing between GM-pasted markup and the table. Sanitize before writing, never on read.
- **Multi-system**: `systems/` registry; `_active_system()` / `_active_campaign_id()` are request/session-scoped. Templates branch on `body.system-pf2e` / `body.system-cosmere`. Cosmere actors are `systems/cosmere/actor.py::CosmereActor` (reads the Foundry `cosmere-rpg` schema); Cosmere combat is flat-integer, defenses are static (phy/cog/spi), conditions are mostly advantage/disadvantage + Exhausted (a flat test penalty).
- **GM auth**: a `check_gm_access` before_request gates path-prefixes in `GM_API_PREFIXES` (don't re-flag prefix-gated routes as unauthenticated); `@gm_required` is a separate per-route gate. `_is_gm()` is true for the site admin, the active campaign's GM, or legacy-open mode (no `GM_PASSWORD`).
- Atomic JSON writes via `_atomic_write_json`; a global `/api/*` JSON error handler.
- **`DATA_DIR` fails silently, so `/health` carries evidence rather than inference.** `DATA_DIR = os.environ.get('DATA_DIR', BASE_DIR)` defaults to the checkout with no warning, and the worse case is quieter still: `DATA_DIR=/data` set correctly but the volume never mounted, so writes go to the container's own `/data`. `configured` / `separate_from_repo` / `writable` all read true there — nothing observable inside one process tells it apart from a healthy mount. So `_probe_storage` keeps a boot counter (`.storage_marker.json`, gitignored) in `DATA_DIR`: `boots_observed` > 1 is the only field that proves the directory survived a restart. **Diagnostic value: "players lost their sheets on deploy" now has three distinguishable causes** — the persistence thread not running, the save/reload race, and an unmounted volume — and `/health` separates the third from the other two. The probe runs once at import (`_autostart_storage_probe`, same pytest + reloader-parent exemptions as `_autostart_persistence`); `/health` itself does no disk I/O because Railway polls it. `/health` is public, so absolute paths are GM-only.

## High-risk areas — be careful

- **PB import + level-up correctness** is the highest-risk surface. `Character.__init__` parses Pathbuilder exports; `class_matrix.py` drives per-level proficiency timing. Guarded by ground-truth-vs-Pathbuilder + full-sheet snapshot tests (`tests/test_pc_snapshots.py`, `tests/snapshots/`, `tests/test_pb_import_correctness.py`). If you touch stat derivation, run these; regenerate snapshots by deleting `tests/snapshots/<dir>/` and running pytest twice.
- **Inline event-handler escaping (recurring bug class).** A user-controlled string (PC/spell/feat/item/combatant/compendium name) interpolated into an `onclick="..."` JS string **must** be JS-escaped: `.replace(/'/g, "\\'")` (or `.replace(/\\/g,'\\\\').replace(/'/g,"\\'")`). An apostrophe ("Go'el", "Thieves' Tools") otherwise closes the string → `SyntaxError` → dead button. **HTML escaping (`esc()`, `&#39;`) does NOT help** — the browser decodes the entity back to `'` before JS runs. Guarded by `tests/test_inline_handler_escaping.py`.
- The PC sheet repaints in place from the `pc_update` SSE `derived` block (saves/skills/strikes/conditions) — if you add a stat the sheet paints, make sure `_pc_state_payload` ships it, or the UI goes stale.
- **PC state persistence: anything that must survive a restart has to be started at IMPORT time.** Production is `gunicorn app:app` (`Procfile`), which **imports** this module — `__name__` is `'app'`, so `if __name__ == '__main__':` never runs. `_start_persistence_thread()` used to be called only from there, and because both the 2-second flush loop *and* `atexit.register(_flush_pending_persistence)` live inside it, production had **neither**. Nothing drained `_PC_PERSIST_DIRTY` / `_PERSIST_DIRTY`, so HP, conditions, focus, hero points, temp HP, shield, reaction, persistent damage and active effects were marked dirty and dropped on every deploy. `_autostart_persistence()` now runs at module scope; `tests/test_pc_state_persistence.py` guards it by importing the module in a **subprocess** (an in-process assertion cannot catch this — the module is already imported, and the suite takes a deliberate pytest exemption).
  - **Two write paths, and the difference is diagnostic.** Debounced (`_persist_pc_combat_state` → dirty flag → flush loop): HP, conditions, condition timers, focus, hero points, temp HP, shield, reaction, persistent damage, exploration activity, active effects, treat-wounds immunity. Immediate (route does read-modify-write + `save_and_reload_character`): spell slots, prepared spells, inventory, XP, level-ups, portraits. **If slots survive a restart but HP doesn't, the flush loop isn't running.**
  - **Live-tick writes pass `fsync=False`.** `_atomic_write_json`'s docstring (`app.py:178`) explains why: `os.fsync` is the one syscall gevent cannot yield around, so on the single worker each one stalls every player's SSE. `os.replace` stays atomic regardless.
  - **The read-modify-write race is FIXED, and the ordering is load-bearing.** Every route that reads a character file and then calls `save_and_reload_character` now calls `_flush_pc_dirty(pc_name)` **before its own read** — 28 call sites plus `require_pc_json`, which is the shared front door and flushes for its callers. The ordering cannot be relaxed: the flush must precede the read, so you *cannot* fix a new route by flushing inside `save_and_reload_character` (by then `pc_json` is already stale), and you must not stamp live state over `pc_json` after the reload either — daily prep deliberately pops `current_hp` so `Character.__init__` resets to max, and re-applying live state would hand the player back the damage they just slept off. `tests/test_pc_save_reload_race.py` walks `app.py`'s AST and fails any write-back route whose first flush comes after its first read, so a new route can't reintroduce it silently. Note the lock ordering this establishes: `_pc_spell_lock` → `ENCOUNTER_LOCK`, never the reverse.

## Rules reference

Engine fidelity is audited and documented — check these before claiming a rule
"isn't available": `PF2E_RULES_AUDIT.md`, `COSMERE_RULES_AUDIT.md`, `ROADMAP.md`,
`FOUNDRY_INTEROP.md`. The fullest Cosmere rules text is at `~/Downloads/Stormlight_Rules.txt`.

## Current work

**IN FLIGHT: the tactical-map feature audit. Start at `docs/map/AUDIT.md`.** The map is now on
trunk, GM-only (PR #111, `d139688f`), and the next step is a paused, question-driven audit of the
whole tool. That doc holds the settled decisions, ~23 findings verified against a running server,
and seven rounds of questions written ready to ask. It exists because Claude's per-project memory
does not travel between machines — the doc is the source of truth, not memory.

Three things are already decided and must not be re-litigated: the map is **GM-only** (no
player-facing route; `/api/scenes` is gated at the prefix), the eventual audience is **one shared
screen at the table** rather than per-player devices, and **GM workflow** is the first priority.
Note the shared table screen revives what this file records below as the cancelled Campaign Hub
Stage — correct that entry when the work starts.

**Do NOT work on these — deferred by the user:**
- Player-sheet **inventory reorg** ("hold on inventory scope"). The hold is on SCOPE, not on the
  work: a cosmetic restyle shipped (`952adb25`) and the structural questions behind it were never
  written down. Two real defects sit inside it if it ever reopens — `/api/add_item` appends
  `[name, qty]` with no bulk, and `Character.__init__` defaults a 2-element entry to `'0'`, so
  every item added in-app registers as **0 Bulk forever** and the encumbrance gauge is only truthful
  for a straight Pathbuilder import; and `invested` lives in `localStorage` only, so it does not
  follow the player to another device and the GM never sees it.
- A **sticky HP/conditions chip** on the player sheet. Note the record is weaker than "dropped":
  commit `71680180` **deferred** it from a deliberately CSS-only pass because it needs markup plus a
  JS paint hook. On desktop `.char-header` is already `position: sticky`, so HP never scrolls away;
  the gap is real only at **≤768px**, where the header is reset to `relative` and HP, conditions, AC
  and shield all scroll off together — i.e. on the phones the players actually use. `.header-hp-chip`
  is already styled (`system.css:3176`) and both mirrors already write `#header-hp-chip-cur`.

**Campaign Hub / the Stage was CANCELLED (2026-08-09)** — do not resurrect it, and do not treat
`api_stage_encounter()` in app.py as related (it is an unrelated pre-existing route for staging an
encounter).

**Player-vault ingestion is SHIPPED, not parked.** It used to be listed here, citing a plan file
that has never existed in git history. The scoping commit that deferred it (`686a2a76`, 14:18) was
followed by the implementation the same afternoon (`435a692d`..`5ba2eb98`, 15:54–20:04), all of it
ancestors of HEAD: `--mode player-vault` on `tools/chronicle_build.py` ingests an already
player-facing hand-authored vault instead of deriving one. The only thing outstanding is that it has
never been run against a real `--publish-url` — dry-run only.

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
