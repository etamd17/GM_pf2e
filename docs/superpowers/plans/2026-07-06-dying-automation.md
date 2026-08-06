# Dying Automation Implementation Plan

> **For agentic workers:** small arc (most of the feature pre-exists — read
> the spec's "What already exists" section FIRST). Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** guided recovery checks from the player's own sheet: server-backed
roll (or physical-d20 entry) that auto-applies all four degrees with the
dying/wounded/doomed math, through the SAME core the GM tracker widget uses.

**Spec:** `docs/superpowers/specs/2026-07-06-dying-automation-design.md`
(the contract — includes the verified inventory of what already exists).

## Global Constraints

- Branch `feat/dying-automation`; commit per task; push/PR only at the end
  through the standing finish pipeline. Trailer exactly:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. No emojis.
- Extract-and-share: ONE recovery-resolution core, two thin routes. Never
  duplicate the degree ladder (the sheet's divergent client-side copy is
  being deleted for exactly that sin).
- `python3 -m pytest -q` full suite before each commit;
  `python3 tools/check_templates.py` after template edits.
- Declare NO new module globals near feature code (boot-order trap — see
  arch_app_boot_order memory); this feature shouldn't need any.

---

### Task 1: Backend — shared core + player route (TDD)

**Files:** `app.py` (extract `_resolve_recovery_check` from
`recovery_check` ~10662; add `POST /api/pc/<pc_name>/recovery_check` with
`@require_pc_self_or_gm` next to `update_pc_condition`);
`tests/test_recovery_check.py` (create).

**Tests first (RED), covering the previously-untested ladder through BOTH
routes:** DC = 10 + dying; success/failure ±1; `>= DC+10` / `<= DC-10` ±2;
nat 20 steps degree up one; nat 1 steps down one; recovery to dying 0 adds
wounded +1 and logs; death at `4 - doomed` (incl. doomed clamp); dying
stays clamped at threshold; 400 when not dying; provided d20 honored +
bounds-clamped; server roll within 1..20. New route: owner rolls own PC ok;
`session player_name` mismatch 403 (legacy mode with GM_PASSWORD set —
follow test_dying_state.py's client/session pattern); GM ok; in-encounter
path mutates the live combatant AND mirrors to PARTY_LIBRARY; no-encounter
path mutates the library PC + persists + broadcasts sheet state.

- [ ] RED → implement → GREEN → full suite → commit:
  `Dying automation: shared recovery core + player self-serve route (TDD)`

### Task 2: Player sheet — real roll wiring

**Files:** `templates/player_sheet.html` (`rollRecoveryCheck` →
`fetch('/api/pc/' + pcNameEncoded + '/recovery_check')`, render server
result via existing `broadcastToGM`/`showRollToast`; delete client math);
`templates/_pc_sheet/_header.html` (DC text in the widget title, live-
updated in `_refreshDyingWidget`; physical-d20 input + Apply row).

- [ ] Implement → check_templates → full suite → commit:
  `Player sheet: recovery check rolls server-side and auto-applies`

### Task 3: Verification (controller, live browser)

- [ ] Seed a PC to dying on the repro server; from the SHEET: Roll →
  server toast (degree + new dying) + widget values repaint via SSE +
  combat log entry on the tracker; physical-d20 entry applies; recovery
  to 0 adds Wounded; death at threshold shows DEAD state; tracker widget
  still works (regression); non-owner session gets 403; Cosmere sheet
  untouched. Then: finish pipeline (final review → PR → CI → merge →
  Railway verify → memory/ledger updates).
