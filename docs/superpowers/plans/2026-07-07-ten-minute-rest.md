# Ten-Minute Rest Block Implementation Plan

> **For agentic workers:** read the spec's "What already exists" section
> FIRST — the Treat Wounds modal + log endpoint exist and are being made
> server-authoritative, not built from scratch. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** player-driven 10-minute activities panel (Treat Wounds
auto-applied with enforced-but-overridable immunity, Refocus, RAW shield
Repair), all server-rolled, results rolling up to the GM over SSE.

**Spec:** `docs/superpowers/specs/2026-07-07-ten-minute-rest-design.md`
(the contract — includes the locked fork decisions and RAW citations).

## Global Constraints

- Branch `feat/ten-minute-rest`; commit per task; standing finish
  pipeline at the end. Trailer exactly:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. No emojis.
- Server-authoritative rolls ONLY — this arc exists because two sheet
  buttons shipped as client-side Math.random fakes. Extract-and-share:
  one resolution core per activity; routes thin-wrap.
- Verify RAW text against ~/Downloads sources / AoN before pinning tests
  (esp. Repair's per-rank values and the level-based DC).
- HP mutations go through the EXISTING party-HP internals (the
  test_dying_state.py-guarded paths) — never a new HP write.
- Immunity state persists with the PC's combat state (survives restarts;
  volume-safe). No new module globals near feature code (boot-order trap
  — see arch_app_boot_order memory).
- `python3 -m pytest -q` full suite before each commit;
  `python3 tools/check_templates.py` after template edits.

---

### Task 1: Backend — three activity routes + Treat Wounds core (TDD)

**Files:** `app.py` (cores + routes near the existing
treat_wounds_dispatch ~13359; immunity field through the PC combat-state
persistence like current_hp/current_focus); `tests/test_ten_minute_rest.py`
(create; kyle/goel fixture pattern from test_recovery_check.py).

**Tests first (RED):** Treat Wounds ladder per tier (DC/heal incl. flat
bonuses, crit doubles dice, crit-fail 1d8 damage to target), nat 20/1
STEP the degree, healer's Medicine mod comes from the server-derived
skills, self-target works, healing bounded by max HP, immunity set on
any attempt + repeat inside the hour 409s with remaining minutes +
override bypasses + GM always may, refocus +1/cap-400, repair per-rank
restore + crit + 2d6 crit-fail + full/destroyed/no-shield 400s, auth
(owner ok / other player 403 / GM ok), immunity survives a persistence
round-trip.

- [ ] RED → implement → GREEN → full suite → commit:
  `Ten-minute rest: server-rolled Treat Wounds/Refocus/Repair cores + routes (TDD)`

### Task 2: Sheet panel

**Files:** `templates/player_sheet.html` (+ `_pc_sheet/` partial if the
panel lands in the header/actions region): the 10-Minute Activities
panel; rework the Treat Wounds modal to POST the new route (server
outcome rendering, physical-d20 input, per-target immunity display with
remaining minutes + amber override); move the Refocus button in; replace
the free full shield repair with the RAW Repair row (shield HP/BT +
Crafting mod shown). In-flight guards on all three (the double-click
lesson). Remove the dead client-side Treat Wounds math.

- [ ] Implement → check_templates → full suite → commit:
  `Player sheet: 10-minute activities panel (server-rolled, immunity-aware)`

### Task 3: Verification + finish

- [ ] Browser walk on the repro server (NOTE: /private/tmp scratch data
  is 3-day-reaped — re-seed or touch files first): Treat Wounds ally +
  self, immunity countdown + 409 + override, refocus pip, shield repair
  by rank, GM healing-log receives server numbers, auth 403. Then:
  adversarial final-review workflow → fixes → PR → CI → merge → Railway
  verify → memory/ledger updates.
