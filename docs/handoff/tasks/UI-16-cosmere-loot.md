# UI-16: Cosmere loot ledger (B: award form beside searchable history)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, medium effort)
- **Branch:** `codex/ui-16-cosmere-loot` from current `origin/main` **after UI-05 (gem values)
  merges**
- **Depends on:** UI-05
- **Context:** read the conventions in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md` and the
  Cosmere notes in `tasks/UI-13-cosmere-dashboard-and-gmscreen.md`. Mockups:
  `cosmere-loot-<skin>-<ledger|empty|failed>-session.html` with `cosmere-loot-revisions.css` /
  `-preview.js`. Decision log entries: "Cosmere loot management", "Cosmere loot preview checks",
  "Cosmere loot selection". UI-03 fixed the error handling and reversal; UI-05 added gem values.
  **The mockup predates both**, so where they differ, the current behaviour wins.

## Goal
`templates/cosmere_loot.html`:
- **Layout:**
  - the award form sits beside the history on desktop and stacks on phones;
  - the history gets text search and a recipient filter, with a zero-results state and a
    clear button;
  - flatter history rows.
- **An updating draft summary,** labelled "Not saved yet": recipient, gem, sphere counts, items
  and the draft's worth in marks.
  - **The worth comes from the server's table.** Add the value table to the `/api/cosmere/loot`
    response (e.g. `gem_values: {gem: {chip, mark, broam}}` in diamond chips, from
    `systems/cosmere/spheres.py`) and compute the draft from that.
  - **Never** put a second copy of the numbers in the template.
- **States:**
  - a failed history load reads as "Couldn't load the ledger" with a retry, not as an empty
    ledger;
  - the empty state stays as it is.
- **Help text** explains party (ledger-only) versus named-recipient (wallet credit) awards and
  "Remove" (reverses the wallet credit; refused if already spent).
- **Keep:** every form field, option and constraint; the item add/remove rows; and the UI-03
  behaviour (confirm before delete, errors shown, form kept on failure).

## Acceptance
- [ ] Matches the B mockup at 1440px and 375px in both skins, for a ledger, an empty state and a
      failed load.
- [ ] Draft summary test: changing gem/denominations updates the worth using the server table (an
      emerald broam shows 200 mk), and nothing is saved until Award.
- [ ] Search/filter tests including zero results and clear.
- [ ] Existing loot tests (`tests/test_cosmere_loot.py`, `tests/test_cosmere_loot_to_sheet.py`,
      `tests/test_cosmere_spheres.py`) plus the convention gates (numbers).

## Out of scope
- Mistborn currency (boxings/clips) under the Mistborn skin: separate task, needs a decision.
- Party-wide distribution or splitting an award.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
