# UI-11: PF2e level-up (B) and character builder (B): roadmap + live preview

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (strongest model, high effort: PF2e build and level-up are the highest-risk
  surface in CLAUDE.md, and both pages carry private drafts)
- **Branch:** `codex/ui-11-pf2e-levelup-builder` from current `origin/main`
- **Depends on:** none (UI-03 already fixed the builder Review and the level-up rail clicks)
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first.** Mockups `pf2e-levelup-*` and `pf2e-builder-*` (Revision B = `*-session.html`). Decision
  log entries "PF2e level-up flow", "PF2e character builder selection" and their follow-ups.

## Goal
- **Level-up, B** (`templates/player_levelup.html`): numbered roadmap labels ("1. Class features",
  ...; keyboard-activatable), readable step instructions, a flatter wizard (no card boxes,
  rows with dividers), and a **"Draft changes" panel in the gains rail** that updates as the player
  chooses: level old to new, max HP old to new, each boosted attribute old to new, boosts selected
  x/4, skill increases, picked feats, sections still outstanding.
- **Builder, B** (`templates/player_builder.html`): a sticky step header ("3 / 7 · Background" and
  "n / 6 required stages complete"), Back/Continue buttons at the bottom of each step, flatter
  choice cards, and a **collapsible "Character preview" column** (open by default at 900px+,
  collapsed on phones, where it sits above the step): name, level, ancestry/heritage,
  background, class, the six attributes, feat count, trained-skill count, spells, languages and
  equipment.

## Rules for the preview panels (most important)
- **No second rules engine.** Both panels only display numbers the page already computes:
  level-up uses `_ceremonyHpDelta()`, `getTempFinalStats()` and `state`; the builder uses
  `getTempFinalStats()`, `getTempFinalSkills()` and `state`. If a number isn't available from an
  existing function, leave it out and say so in the report.
- Repaint from the existing validation/render hooks (the mockups wrap `runValidation` /
  `navTo`). In production, call the paint function from those functions directly, not by
  monkey-patching.
- The panels never write to the draft, the server or `state`.
- Label them plainly ("Draft changes", "Character preview"). Don't port the mockups' "illustrative /
  not verified" notes, which were review-only.

## Other constraints
- Keep the GM override switch reachable during picks (UI-03 made the rest of the rail
  click-through). The mockup moved the switch into the picker header; do that only if it stays the
  **same element and handler** and returns after the picker closes. Otherwise keep UI-03's
  behaviour.
- Draft controls (`_character_draft_controls.html`, restyled in UI-02c) must keep working: save,
  copy, reload, discard, resume. The builder's legacy publish path stays untouched.
- Snapshot and ground-truth tests must not change (no stat derivation changes).

## Acceptance
- [ ] Both pages match their B mockups at 1440px and 375px.
- [ ] Inventory tests: every nav control, the four draft controls, picker handlers and form fields
      unchanged on both pages.
- [ ] Preview tests: pick an ancestry feat, boosts and a class in the builder; the preview shows
      them. Select boosts and a feat in level-up; "Draft changes" shows old to new values matching
      the existing review step.
- [ ] `tests/test_pc_snapshots.py`, `tests/test_pb_ground_truth.py`, `tests/test_levelup_chain.py`,
      `tests/character_workflows`, `tests/test_player_levelup_picker_layer.py` plus the convention gates
      (numbers). Browser CI in the PR.

## Out of scope
- Rules, feat eligibility, HP/boost math, caster paths beyond what the existing code does.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
