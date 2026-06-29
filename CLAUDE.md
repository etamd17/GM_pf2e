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
- **SSE** (`/api/events`): every page subscribes through the shared hub `window.appSSE(eventName, handler)` in `templates/_sse_hub.html` — **never** `new EventSource('/api/events')` directly (one socket per tab; the hub multiplexes + reconnects). Broadcast from the server with `sse_broadcast(event, data, player_filter=...)`, which pre-renders GM vs player frames. `?audience=table` forces the player frame (for a shared table screen).
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

- Branch **`feat/table-view-vtt-program`** — a "table view" program adding GM/table features: `audience=table` SSE, adversary HP-display modes (band/number/hidden), show-a-handout-on-the-table, and **auto-applied conditions** (PF2e monster AC/saves/Strikes + Cosmere Exhausted, with a "why" breakdown hint in the tracker inspector). Ships as **one PR at the end**.
- **Next up: feature 7 — round-events lane** (a GM-authored timeline of round-triggered events on the tracker: reminder + optional auto-apply payload, GM-only with per-event "show on table"). Design is locked; build pending. Then feature 8 (minions) and 9 (GM quick actions).
- Player-sheet interactivity fixes (1H/2H grip toggle; apostrophe-broken spell/item/handler buttons) are already merged to `main`.
