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

- Ported generator Revision A into `templates/generator.html`: the original 19 cards now use a
  stable 3/2/1-column row-order grid, visible Copy/Pin/Reroll actions, keyboard-operable card and
  pinned-result toggles, an always-visible ten-tool Rapid Fire strip, and the exact context-change
  help. All generator IDs, categories, biomes, filters, handlers, URL updates, pins and rerolls are
  preserved.
- Ported loot-ledger Revision B into `templates/loot_ledger.html`: the coin summary uses accurate
  wording and neutral styling, Award Loot sits beside Award History on desktop, character coin
  balances live in a closed disclosure, and the award/delete effects are explained. All four coin
  fields, item controls, recipient behavior, endpoints, payloads, older-entry deposits, award
  badges, delete confirmation, calculations and empty/error states are preserved.
- Kept PF2e mechanics isolated: Cosmere `/gm/loot` redirects to `/cosmere/loot`; D&D requests for
  `/generator` and `/gm/loot` return to `/dnd5e/gm`; PF2e continues to render all three shared
  pages normally. The nine PF2e generator/loot API routes now reject Cosmere and D&D requests
  before generation, ledger, pin or inventory mutations. The loot summary API also reads the
  live PF2e generator wealth table instead of importing a nonexistent class.
- Fixed two latent magical-puzzle formatter crashes exposed by the rendered generator-page test:
  every magical template now receives its easy DC, and mirror HP is calculated before formatting.
- Added `tests/test_pf2e_generator_loot_ui.py` with rendered inventories for generator order,
  categories, titles, actions, tools, filters, biomes and keyboard hooks, plus the loot layout,
  fields, wording, client endpoints, payload shapes and history controls. It also exercises every
  magical puzzle template. Extended the system guard inventory for the page and API boundaries.

### Verification

- `python tools/check_templates.py` — **107 templates parse**.
- `pytest -q --basetemp=.pytest_cache/ui07-final tests/test_pf2e_generator_loot_ui.py tests/test_loot_award.py tests/test_system_guards.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py tests/test_typography_system.py` — **257 passed**.
- `git diff --check` — passed.
- Claude's required 1440px and 375px browser checks remain for review.

## Review

**Claude, 2026-10-15: accepted. No required fixes; one cleanup before committing.**

Read the diff (`app.py` guards and redirects, the `pf2e_generator.py` puzzle fix, both templates,
tests). Also confirmed that UI-06's search fix is in `main` (#199). Checked live in a PF2e
campaign (level-10 Kyle) at 1440px and 375px. Did not rerun the test suite.

**Generators:**
- 19 cards in 3 columns on desktop and 1 on a phone. No sideways scroll; 32px buttons on a phone.
- Copy / Pin / Reroll are visible on every card. Reroll changes the card.
- Enter and Space on a card title collapse and expand it (`aria-expanded` follows).
- Pin adds a server pin and Unpin removes it (0 -> 1 -> 0).
- Rapid Fire: 10 tools; two results added, Dismiss removes one, Clear removes the rest.
- Filter chips (6) narrow to 5 cards and back to 19.
- 9 biomes. Changing the biome and pressing Reroll all updates the URL (`?level=10&biome=Arctic`)
  and refreshes 19 of 20 result blocks. No console errors.
- Not tested: Copy and Copy Table. The browser pane blocks the clipboard, and those handlers are
  unchanged.

**Loot:**
- Desktop shows the award form beside the history; a phone has no sideways scroll.
- The header reads "Party coins 115 gp" with a neutral reference line; the old "Total Party
  Wealth" wording is gone. Character balances start closed.
- An award to Kyle through the form deposits to his sheet (108 -> 111 gp).
- An older, not-yet-awarded entry for Kyle shows "Award to sheet". Clicking it deposits (+4 gp),
  marks the entry Awarded and shows the badge (`POST /api/loot_ledger/<id>/award`).
- "Delete log" removes only the log entry (`POST /api/loot_ledger/delete`); Kyle's gold stays at
  115.

**Before committing:** delete the scratch folder `.pytest_tmp_ui07_final_reaudit/` in the
worktree root. It holds test output, it isn't git-ignored, and a broad `git add` would commit it.

Notes (not required):
- The recipient list offers only party characters, so the non-character path
  (`/api/loot_ledger/add` from the form) can't be reached from the page. `main` behaves the same.
  A "The Party (log only)" option would make it usable.
- Changing the biome alone only updates the URL; the cards refresh on Reroll all. The help line
  says that, so it's fine.
