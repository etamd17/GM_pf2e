# Dying automation — guided recovery checks (design)

**Date:** 2026-07-06
**Status:** Approved scope (user-locked in the 2026-07-04 feature-queue round;
re-confirmed 2026-07-06), pending plan.

## Goal

When a PC is dying, the table gets a GUIDED recovery-check flow: the tracker
and the player's own sheet surface the check (DC 10 + dying), one button
rolls it, and the outcome math auto-applies — all four degrees, with the
dying/wounded interaction and the doomed-adjusted death threshold. The
player keeps the roll: it happens from their sheet, with the option to roll
physical dice and enter the d20.

## What already exists (verified in code, 2026-07-06)

- `/api/recovery_check/<instance_id>` (app.py ~10662): complete Remaster
  math — DC 10 + dying, degree ladder incl. `>= DC+10` crit success and
  nat 1 / nat 20 one-step shifts, ±1/±2 dying deltas, death threshold
  `4 - doomed`, wounded +1 on recovery to dying 0, PC mirror + SSE +
  combat log. Accepts optional `{"d20": n}` for physical rolls.
  **GM-only (`@gm_required`) and instance-scoped. Zero test coverage.**
- Tracker active-turn card (tracker.html ~3135): full guided widget —
  "Dying N — Recovery flat check DC X" + Roll button (server roll) +
  physical-d20 input + Apply button, both calling the endpoint, with a
  degree-colored toast. Start-of-turn placement per Player Core p.404.
- Player sheet dying widget (_pc_sheet/_header.html ~250): appears when
  hp 0 / dying > 0, shows Dying/Wounded values, has manual ±1 Dying and
  Call for Help. **Its "Recovery Check" button is a client-side fake**:
  `Math.random` roll, divergent math (no DC+10 crit success), broadcasts a
  toast, and applies NOTHING — the player must hand-adjust dying after.
- Dying entry/heal math (drop-to-zero, damage-at-zero, heal→wounded) is
  already correct and heavily tested (tests/test_dying_state.py).

## Design (the actual gap)

### Backend

- Extract the mutation core of `recovery_check` into
  `_resolve_recovery_check(target, d20)` returning the result dict; the
  GM tracker route thin-wraps it (extract-and-share, never duplicate).
- New player-facing route `POST /api/pc/<pc_name>/recovery_check`, guarded
  by the existing `@require_pc_self_or_gm` decorator (same auth as
  `update_pc_condition`, which the widget's ±1 buttons already use).
  Body: optional `{"d20": n}`. Resolution order: the PC's live combatant
  in `ACTIVE_ENCOUNTER` when present (inherits the tracker mirror +
  broadcast behavior); else the `PARTY_LIBRARY` PC directly (GM set dying
  outside an encounter) with sheet SSE + persistence.
- 400 when the PC isn't dying (parity with the GM route).

### Player sheet

- `rollRecoveryCheck()` POSTs the new route and renders the SERVER result
  (d20, dc, degree, new dying, died) through the existing
  `broadcastToGM` + `showRollToast` display — the toast now reports what
  actually applied. Client-side outcome math deleted.
- Physical-dice parity with the tracker: a d20 input + Apply in the dying
  widget posting `{"d20": n}`.
- The widget title surfaces the DC as text ("Recovery DC 12"), not just a
  tooltip, and updates live from the SSE-driven conditions repaint.
- Manual ±1 Dying and Call for Help stay unchanged.

## Constraints

- The shared mutation core must not fork: one function, two thin routes.
- Inline-handler rule: no GM/player free text enters handlers (all values
  here are server-generated ints/ids — keep it that way).
- The sheet repaints dying/wounded from the `pc_update` derived block —
  no new fields needed (`conditions` already ships); verify, don't assume.
- No emojis. Reduced-motion respected by reusing existing toast/widget
  styles (no new animation).
- High-risk surfaces untouched (derivation, PB import).

## Out of scope (noted, not built)

- Hero-point "Heroic Recovery" (spend all hero points → dying 0): natural
  follow-up, not in the locked scope.
- Turn-scoped emphasis on the sheet (the widget already shows whenever
  dying — RAW timing lives on the tracker's start-of-turn card).
- Cosmere: uses its own death-spiral model (Ch.9), explicitly untouched.

## Testing & verification

- TDD: backfill the untested degree ladder through the shared core (DC+10
  crit success, nat 1/nat 20 stepping, ±2/±1 deltas, wounded increment on
  recovery-to-0, death at `4 - doomed`, clamp at threshold, not-dying 400)
  plus the new route's auth (owner ok, other player 403, GM ok) and both
  resolution paths (in-encounter mirror, out-of-encounter library PC).
- Browser walk: sheet roll applies + toast shows server outcome; physical
  d20 entry; tracker widget parity; live repaint of Dying/Wounded values.
- Full suite + check_templates; verify on Railway post-merge.
