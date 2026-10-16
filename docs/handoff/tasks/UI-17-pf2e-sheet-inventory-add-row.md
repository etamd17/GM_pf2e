# UI-17: PF2e sheet: make the Inventory "Add" row fit on phones

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, low effort: a small CSS fix)
- **Branch:** `codex/ui-17-inventory-add-row` from current `origin/main` (after UI-10 merges)
- **Depends on:** UI-10 (same stylesheet block)
- **Context:** found in the UI-10 review (`tasks/UI-10-pf2e-player-sheet.md` > Review). The user
  asked for this fix on 2026-10-15. It is a **layout bug only**. The inventory *scope* hold in
  CLAUDE.md ("Do NOT work on these") still applies: no bulk logic, no `invested` storage, no
  reorganising.

## The bug
On a 375px phone, the add-item row in the PF2e sheet's Inventory tab
(`templates/_pc_sheet/_tab_inventory.html`, the `div.flex.gap-2.mb-3` holding `#add-item-name`,
`#add-item-qty`, `#add-item-bulk` and the **Add** button) is wider than the screen:
- the Bulk field sits at 339-395px and the Add button at 403-463px;
- a parent has `overflow-x: hidden`, so the button is clipped and can't be seen or tapped.

Players can't add an item from a phone.

Cause: `#add-item-name` is `flex-1`, but a text input has an intrinsic minimum width, so it won't
shrink below about 150px. The fixed-width qty (`w-16`), bulk (`w-14`) and button then push past
the edge.

## Fix (CSS only, additive, in the sheet's inline `<style>` block next to UI-10's rules)
- Let the name field shrink: `#add-item-name { min-width: 0; }`.
- At `max-width: 480px`, let the row wrap so the button never leaves the screen. One option: give
  the name field the full first line and put qty, bulk and Add on the second. Pick what reads
  best, keeping the field order and the Add button's size at least 40px tall.
- **Do not edit the partial's markup, ids or handlers** (`addInvItem()`, the bulk lookup at
  `player_sheet.html` ~5333/5352). Use the existing ids as selectors.
- Check the Quick Add chips below still wrap, and the Consumables & Ammunition section's add
  controls, if they have the same pattern. Fix them the same way if they do; say so in the
  report.

## Acceptance
- [ ] At 375px and 320px every control in the add row is fully on screen and tappable. Test with
      `elementFromPoint` at the button's centre, or a browser test.
- [ ] At 1440px the row looks as it does today (one line).
- [ ] Adding an item from a phone works end to end (name, qty, bulk -> Add -> item appears).
- [ ] UI-10's inventory test (`tests/test_pf2e_player_sheet_ui.py`) still passes, plus a test
      asserting the new rule exists, and `python tools/check_templates.py` (numbers).

## Out of scope
- Anything about bulk values, encumbrance, `invested`, or how inventory is organised.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
