# UI-10: PF2e player character sheet (A: familiar, compact header)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, high effort: highest-risk UI file in the repo)
- **Branch:** `codex/ui-10-pf2e-sheet` from current `origin/main`
- **Depends on:** none
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first.** Mockup `pf2e-sheet-*`; Revision **A** is `pf2e-sheet-refine.html`. Decision log entry
  "PF2e player character sheet". CLAUDE.md > "Editing conventions learned the hard way".

## Goal
Revision A keeps the sheet's familiar layout and tightens it. It is a **CSS-only** change in the mockup: every
rule in `pf2e-sheet-revisions.css` that applies to A sits under `[class*="sheet-preview-"]`
(shared by A and B), so A needs **no markup move**. (B's only move, putting the ability row into
the Core tab, is not chosen.)
- Smaller name and portrait; tighter resource chips.
- The ability row as one flat strip (no boxes, thin dividers) with larger modifiers, always
  visible in the header.
- Larger tap targets: tabs 40px min, skill rows 40px, strike buttons 70px.
- Phone rules at 700px from the same file.

## Constraints (this file breaks easily)
- **Strictly additive CSS appended at the end of the sheet's stylesheet**, scoped to the PF2e
  sheet, the way the earlier minimal pass did it (81 insertions / 0 deletions). No markup, id,
  handler or JS changes. That's what keeps the `pc_update` repaint, spell preparation and the
  save/reload race guard safe.
- Don't port `.preview-sheet-feedback` or any demo code.
- Don't touch the deferred items: inventory reorg and the sticky HP chip (CLAUDE.md "Do NOT work on
  these").
- Where to append: the sheet's own inline `<style>` block in `templates/player_sheet.html`
  (it starts at about line 32; `system.css` loads after it at about line 2919, so check that no
  `system.css` rule overrides yours). `player_sheet_readonly.html` is a separate page that
  extends `base.html`, so it is out of scope.

## Acceptance
- [ ] `git diff` on the template shows only appended CSS lines (say so with the numbers).
- [ ] Sheet matches A at 1440px and 375px; at wide widths the identity/skill rails behave as today.
- [ ] Full-sheet snapshot tests unchanged (`tests/test_pc_snapshots.py`, `tests/test_pb_ground_truth.py`)
      plus the convention gates (numbers). Browser CI in the PR.
- [ ] Claude's browser check: HP damage/heal, hero points, a skill roll, a spell slot toggle and
      a live `pc_update` from the tracker still repaint correctly.

## Out of scope
- Rules, data, sheet tabs' content, the Cosmere and 5e sheets.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
