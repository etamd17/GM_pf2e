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

- Worktree: `C:\Users\Evan\Documents\_gm_pf2e_ops_v2_remnant\GM_pf2e_ui17`.
  Branch: `codex/ui-17-inventory-add-row`, cut from fetched `origin/main` at **d5719eea**
  (UI-10 already merged; UI-14 PR #205 also merged). UI-11's separate worktree was preserved.
- Appended **21 CSS lines / zero deletions** at the end of `player_sheet.html`'s inline style,
  next to UI-10. The name field can shrink. At 480px and below the existing row wraps, with
  name on the first line and quantity, Bulk and Add on the next; all four controls have at
  least 40px height and the Add button cannot shrink. Selectors stay inside the sheet host
  and identify the existing row through its existing name-field ID.
- The inventory partial, IDs, handlers, JavaScript, saved inventory, bulk rules and shared CSS
  are unchanged. The desktop row stays on one line.
- Checked the adjacent controls: Quick Add already uses `flex-wrap` and all **21 chips** fit at
  every tested width. Consumables & Ammunition has a generated list with quantity controls,
  not a second name/quantity/Bulk/add row, so no additional change was needed.

### Verification

- Using the existing project venv, with writable temporary DATA_DIR/TEMP/TMP outside the checkout:
  `python -m pytest -q -p no:cacheprovider --basetemp=<external-temp>
  tests/test_pf2e_player_sheet_ui.py tests/test_pf2e_inventory_add_row_ui.py
  tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
  — **25 passed** in 147.04s. UI-10's full rendered behavior inventory stays identical;
  the new test asserts the scoped shrinking/wrapping/touch-target rules exist.
- `python tools/check_templates.py` — **107 templates parse**.
- `git diff --numstat -- templates/player_sheet.html` — **21 insertions, 0 deletions**;
  `git diff --check` — clean.
- Standalone Playwright check against a real disposable local Flask app, using a renamed copy
  of the committed Kyle fixture: **3/3 viewport cases passed (320px, 375px, 1440px)**.
  For each width, all four control rectangles are within the viewport and `elementFromPoint`
  at each centre resolves to that control. Both phone widths have two rows and controls at
  least 40px tall; 1440px stays one line. Quick Add's 21 chips fit. At every width, entered a
  custom item, quantity **2**, Bulk **L**, clicked the actual Add button, followed the reload,
  and verified the item in both the rendered list and the saved disposable character JSON.
- Browser command (from this worktree):
  `..\GM_pf2e\venv\Scripts\python.exe C:\Users\Evan\.codex\visualizations\2026\10\13\01a1361b-2f40-7910-883c-5b656456edf7\ui17_browser_check.py`.
  Local screenshots/logs are in the sibling visualization folder `ui17-browser-b29f8581`.
  The first sandboxed attempt timed out on localhost readiness; the approved run outside the
  sandbox passed. No production data was used or changed, and the disposable server was stopped.

### Handoff

Ready for Claude's focused diff/visual review. Everything remains uncommitted and unpushed.
No full suite or PR CI was run for this small CSS-only change. After acceptance, Codex commits,
opens the PR and merges only when every CI job is green.

## Review

### Claude, 2026-10-16 -- ACCEPTED

Reviewed the 21-line diff (zero deletions; markup, IDs, handlers and JS untouched). The scoping is
sound:
- the inputs' inline `style` attributes set only colour and border;
- their widths come from utility classes (`flex-1`, `w-16`, `w-14`), which the ID selectors
  outrank;
- `system.css`, which loads after this block, has no rule for this row;
- `:has()` is supported by every current phone browser.

**Independent checks (this worktree, uncommitted):**
- `pytest tests/test_pf2e_inventory_add_row_ui.py tests/test_pf2e_player_sheet_ui.py
  tests/test_inline_handler_escaping.py` -> **7 passed**. `tools/check_templates.py` -> **107
  parse**. `git diff --check` is clean.
- Browser: a local server on this worktree with a copy of Codex's disposable "UI17 Review"
  character; geometry measured on the real page.
  - **320px:** name on row 1 (42-278px); quantity, Bulk and Add on row 2 (Add ends at 238px).
    All are 40px tall, and each control's centre hit-tests to itself.
  - **600px** (between the breakpoint and desktop): one line, everything inside the card, and all
    hit-tests pass.
  - **1280px:** one line. Removing the new `min-width: 0` in place shows that before this change
    the Add button ended at 947px, past the card's 855px edge, so it was clipped on **desktop**
    too in this column. The fix repairs that as well.

**Non-blocking notes:**
- At 1280px the name field is 104px wide, which is cramped for long item names. A later pass could
  wrap the row by container width rather than viewport (a container query). This is not a
  regression: before, the row overflowed.
- Outside UI-17's scope, seen while checking: at 320px the Campaign Notes header buttons
  ("Session Recap", "Save Notes") and the Wealth card's "Save Coins" button overflow their cards.
  These are candidates for a follow-up CSS-only task; the inventory scope hold still applies to
  inventory structure.

Verdict: **accepted**. Codex commits, opens the PR and merges once every CI job is green.
