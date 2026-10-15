# UI-07: PF2e generators (A) and loot ledger (B)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, medium effort)
- **Branch:** `codex/ui-07-pf2e-generators-loot` from current `origin/main`
- **Depends on:** none (independent of UI-06)
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first.** Decision log entries: "PF2e generators" and "PF2e loot management".

## Goal
- **Generators, A** (`templates/generator.html`, mockup `pf2e-generator-*`): row-order cards with
  labelled actions, and the quick tools always visible. Keep all 19 generators, 10 quick tools,
  six filters, nine biomes, the level/context URL updates, copy card/table/quick, pin/unpin,
  collapse, single and all rerolls, and quick dismiss/clear. Add one line of help near the
  context controls saying that changing the context rerolls everything and pins keep their saved
  result. The Cosmere twin (UI-02a, `cosmere_generator.html`) used the same pattern; match its
  button style.
- **Loot ledger, B** (`templates/loot_ledger.html`, mockup `pf2e-loot-*`): award form beside the
  history, expandable per-character balances, flatter history rows, clearer wording on award and
  delete. Keep all four coin denominations, item quantity add/remove, recipient and note, sheet
  deposits, older-entry deposit, award badges, the delete confirmation and the empty state.
  Wording fix (decision log): the total currently sums **coins only**, but the page calls it "Total
  Party Wealth" and colours it against a level guideline. Label it as coins ("Party coins") and
  drop the deficiency colouring. Don't change the calculation.

## Acceptance
- [ ] Both pages match their chosen mockups at 1440px and 375px (one column on phones, buttons at
      least 32px tall, no sideways scroll).
- [ ] Inventory tests for both pages: counts of generators, quick tools, filters and biomes; loot
      form fields/options and history controls unchanged.
- [ ] Loot: award and delete still post the same payloads (existing loot tests, plus one for the
      new label).
- [ ] Gates from the conventions pass (numbers).

## Out of scope
- Generator content or rules; loot valuation or wealth-by-level guidance (needs its own
  rules-backed task); Cosmere loot (UI-05 and the Cosmere batch).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
