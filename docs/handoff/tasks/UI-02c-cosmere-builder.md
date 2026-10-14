# UI-02c: Build the approved Cosmere builder / level-up (Revision B, level-up guide)

<!-- ============ BRIEF ============ -->

- **Owner:** codex (standard model, high effort). Claude verifies in the browser.
- **Branch:** `codex/ui-02c-cosmere-builder` from current `main`.
- **Depends on:** **PR #182 (MB-01) merged first.** #182 changes one line in this template
  (`doPreview` sends `{id:PID, build:collect()}`); start from the post-#182 `main` to avoid a
  conflict. If you must start before, rebase after #182 and keep its line.
- **Context:** decision in `tasks/UI-01.md` (builder **B**, plus "Checks done (builder)"). Local
  mockup `docs/handoff/assets/UI-01-review/builder-b.html` (`builder_layout.js/.css`; layout
  reference only). Files: `templates/cosmere_builder.html`, `templates/_character_draft_controls.html`
  (**shared** with `player_builder.html`, `character_import_preview.html`,
  `character_publication_recovery.html`).

## Goal
Less clutter above the steps, and a builder that tells you what's left to choose, especially after
raising the level. The numbers come from the engine's existing preview, not new logic.

## Approach
1. **Draft bar** (`_character_draft_controls.html`): replace the inline styles with a class
   (e.g. `.draft-bar`) in shared CSS (`static/css/system.css`): one compact row: title, status
   (`#draft-status`), buttons right-aligned, "Resume drafts" link. Keep every id
   (`draft-status`, `draft-save`, `draft-copy`, `draft-reload`, `draft-discard`), `hidden`
   attributes (add `.draft-bar button[hidden]{display:none}`) and the script include. Check all
   four pages that include it, PF2e builder included.
2. **Intro**: the title (~line 193, `{{ 'Edit' if pid else 'New' }} Cosmere Character`) becomes
   "Edit or level up" when `pid` is set, unchanged for new characters; move the `p.muted` intro paragraph into the `details.cb-glossary` box and rename its
   summary "How this builder works, and key terms".
3. **Sticky**: `#wiz-rail` sticks under the site nav; `.wiz-side` sticks beside the steps on desktop
   (static under 760px; rail scrolls horizontally on phones).
4. **Still to choose** (top of `.wiz-side`): render from `paintPreview(p)` using `p.budgets`
   (`attr`, `skills`, `expertises`, `talents`, each `[spent, available]`), one row per item with
   remaining > 0, e.g. "2 talents" + a **Go** button calling the existing `goStep(i)`:
   attributes -> step 1, skill ranks and expertises -> step 2, talents -> step 3. When nothing is
   left: "Nothing left to choose." Hook into `paintPreview`; don't wrap `fetch`.
5. **Step badges**: in the rail, "N left" next to steps 1-3 from the same numbers. The existing
   check mark (`.wiz-chk.done`) must only show when that step has nothing left (today Skills shows
   a check with ranks unspent). Keep `.wiz-chk.over` (red, over budget) as is.
6. **Level-up badge**: remember the level the page loaded with; when `#f-level` is higher, show
   "Leveling up: 5 -> 6" next to the level control (hidden otherwise; mind the `[hidden]` rule).
7. No change to `collect()`, saving, validation, budgets or the preview route.

## Acceptance
- [ ] New `tests/test_cosmere_builder_guide.py`: render the builder for an existing PC (edit mode)
      and a new one; assert the to-do container, the level badge element, the renamed title in edit
      mode only, and the draft-bar ids. Unit-test the budget -> rows mapping if you put it in a
      small pure function (recommended).
- [ ] `pytest -q tests/test_cosmere_builder*.py tests/test_cosmere_creator_ux.py tests/character_workflows tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` pass (paste numbers). The draft-bar change touches PF2e
      pages, so also run `pytest -q tests/browser -m browser` in CI.
- [ ] Browser (Claude verifies): edit a level-5 character, press + once: badge "5 -> 6", the list
      grows by the engine's numbers (UI-01 saw +1 attribute point, +2 skill ranks, +2 talents), Go
      opens the right step; a fully spent step shows a check and no "left"; phone 375px has no
      sideways scroll; the PF2e builder's draft bar still works.

## Out of scope
- Rules or budgets, new steps, PF2e builder layout beyond the shared draft bar.
