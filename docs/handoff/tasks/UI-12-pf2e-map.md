# UI-12: PF2e map workspace (B: map first, right-side tabs)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (strongest model, high effort: the map is ~4,500 lines guarded by 421 tests
  and an audit, `docs/map/AUDIT.md`)
- **Branch:** `codex/ui-12-pf2e-map` from current `origin/main`
- **Depends on:** none in code. Schedule it **after the VTT plan is settled** (VTT-ALPHA in
  `QUEUE.md`): the VTT rebuild may replace this page, and the user should confirm this layout
  work is still wanted. **Status starts as `waiting on user`.**
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first,** then `docs/map/AUDIT.md` (settled decisions; don't reopen them) and the map notes in
  CLAUDE.md. Mockup `pf2e-map-*` (B = `*-session.html`, scenes `gm`, `table`, `empty`). Decision
  log entries "PF2e map and exploration workspace", "Map preview verification", "PF2e map selection".

## Goal
On the GM view (`templates/map.html`, `static/css/map.css`):
- The map stage takes the left/main column; the controls move to a **280px right sidebar**.
- A persistent **scene rail** at the top of the sidebar: the active-scene picker, table status and
  "Show on table".
- **Tokens / Scene / Terrain / Targets tabs** below it (arrow-key navigation, Tokens open by
  default). The panels move into these tabs; "Create another scene" folds into a disclosure.
- Flatter panels, 38px controls, wrapped header actions.
- Below 900px: one column, map first, sidebar under it (max 420px tall, scrolls).
- `/map/table` (the shared screen) and the no-scene state are **unchanged**: no sidebar,
  full-viewport map.

## Constraints
- **Move the original control nodes in the template; don't recreate them.** The mockup counted 101
  map buttons/inputs/selects/links with their ids, constraints and option values; your inventory
  test must match that list from current `main`. `map.js` looks controls up by id, so ids must
  not change.
- The Build tool group and the GM's table preview stay.
- Rendering rules from CLAUDE.md still apply (event-driven frames, no animated input to the mask
  cache, `uiFont()` on canvas). This task should not touch `map.js` beyond tab switching. If a
  layout change resizes the canvas, call the existing resize/fit path; don't add a new one.
- `[hidden]` vs `display`: `map.css` already has guarded rules; every new class you hide by
  attribute needs its own `.x[hidden]` rule (this has bitten the map five times).

## Acceptance
- [ ] GM view matches the B mockup at 1440px and 375px; table view and no-scene view unchanged
      (screenshot comparison in the report).
- [ ] Inventory test: the 101 map controls keep ids/types/constraints/options.
- [ ] All existing map tests (`pytest -q tests -k map`) plus the convention gates (numbers); browser
      CI in the PR.
- [ ] Claude's browser check: select a token, ruler, a wall, fog reveal, "Show on table", and the
      table screen receiving the scene and a ping.

## Out of scope
- Map engine, permissions, the VTT rebuild, player-facing map access (the map stays GM-only).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
