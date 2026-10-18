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

- Appended the selected Revision A rules to `templates/player_sheet.html` without moving or changing any markup, IDs, handlers, forms, links, ARIA, or JavaScript. The six abilities remain in the header as one flat divided strip, with larger modifiers and the approved compact spacing.
- Increased the interaction targets for tabs, skill rows, and strike buttons, tightened resource/action spacing, and retained the existing wide identity and skill rails. The later shared stylesheet already makes the live name and portrait smaller than the old mockup targets (18px/44px versus 26px/54px), so the additive block deliberately leaves those more compact current values authoritative.
- Scoped every new rule to this standalone PF2e sheet. No inventory layout, sticky HP behavior, read-only sheet, Cosmere sheet, D&D sheet, shared stylesheet, markup, or script changed.

### Verification

- `git diff --numstat origin/main -- templates/player_sheet.html` — **75 insertions, 0 deletions**; the template diff contains only the appended CSS block.
- `pytest -q tests/test_pf2e_player_sheet_ui.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py tests/test_pf2e_sheet_snappiness.py tests/test_player_sheet_inline_handlers.py tests/test_player_sheet_spell_detail.py tests/test_spell_slot_persistence.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py` — **81 passed, 4 skipped** (the four local-only `party_data` snapshots). The new rendered-sheet inventory freezes IDs, handlers, forms, links, and ARIA while the CSS contract excludes the other UI proposals.
- `python tools/check_templates.py` — **107 templates parse**.
- `git diff --check` — **passed**.
- Claude browser review remains: 1440px and 375px, no horizontal overflow, all six tabs, unchanged wide rails, and live HP, Hero Point, skill roll, spell-slot, and tracker `pc_update` behavior.

## Review

**Claude, 2026-10-15: accepted. No required fixes. One older phone bug found, for a separate
task.**

Read the diff: 75 lines added and none removed, all CSS in the sheet's inline style block, no
markup, ids, handlers or JS. Checked live on Kyle's sheet (`/player/sheet/Kyle`, level-10 Druid
in a live encounter) at 1440px and 375px. Did not rerun the test suite.

- **The Revision A styles apply:**
  - six attribute cells in one flat header strip (transparent background, 1px dividers, 22px
    modifiers);
  - tabs 40px tall.
- **Tabs:**
  - At 1440px, Combat, Magic, Feats, Inventory and Pets switch correctly. Core folds into the
    identity/skill rails at that width, which is existing behaviour noted in the decision log.
  - At 375px all six tabs, Core included, switch correctly.
- **Controls:**
  - HP damage 5 then heal 5: 138 -> 133 -> 138, with the header chip following.
  - Hero Points: 1 -> 2 -> 1.
  - A skill roll ran without error.
  - A spell slot toggles to `spent` / `aria-pressed=true` and back.
  - No console errors.
- **Live update from the tracker:** 12 damage through `/api/adjust_hp` repainted the sheet and
  the header chip (138 -> 126), and healing restored it.
- **Phone:** the page has no sideways scroll on any tab, and the attribute strip fits the width
  exactly.

**Older bug found, not caused by this change (needs its own task):** on a phone, Inventory's
add-item row runs past the screen. The bulk field sits at 339-395px and the **Add** button at
403-463px on a 375px screen, inside a parent with `overflow-x: hidden`. The button can't be seen
or tapped, so **a player can't add an item from a phone.** None of UI-10's rules touch that row.
The user has put inventory *scope* on hold, so this is raised as a layout bug for the user to
schedule.

Not verified: where the sheet shows an applied condition. I couldn't find the active-condition
display in the time available. The HP repaint shows the `pc_update` path works, and this change
is CSS only.
