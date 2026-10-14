# UI-02b: Build the approved Cosmere character sheet (Revision A, play-first + tabs)

<!-- ============ BRIEF ============ -->

- **Owner:** codex (strongest model, high effort: this is the highest-risk page of the three).
  Claude reviews the diff and verifies in the browser.
- **Branch:** `codex/ui-02b-cosmere-sheet` from current `main` (after PR #181 merges).
- **Depends on:** none in code; PR #182 doesn't touch `cosmere_sheet.html`. If #182 merges first,
  rebase and rerun the tests below.
- **Context:** decision in `tasks/UI-01.md` (sheet **A**, plus the "Checks done (sheet)" notes).
  Local mockup `docs/handoff/assets/UI-01-review/sheet-a.html`, made by `sheet_layout.js`, which
  MOVES the real sections. Use it as the layout reference only; the product change goes in the
  template markup, not a client-side mover. One file: `templates/cosmere_sheet.html` (~1,570 lines;
  sections inside `.cs-wrap` start ~line 401). Routes `cosmere_pc_sheet` (PCs) and `cosmere_sheet`
  (`/cosmere/sheet/<actor_id>`, non-PC actors) both render it: the layout must work for both.

## Goal
Everything used every turn sits in one top band; the rest is one tap away in tabs. Nothing is
removed, renamed or re-wired.

## Target layout (top to bottom)
1. `.cs-readonly` notice and `.cs-head`: unchanged. `.cs-buildnotes`, `#cs-vbar` and `#cos-combat`
   stay where they are and stay as they are. Keep `#cs-vbar`: its existing scroll gate shows it
   once the rings scroll away, which matters when a tab is long.
2. **Band** (new wrapper, two columns on desktop, stacked under 900px): left `.cs-orbs`; right
   `.cs-facets` then the six-attribute `.cs-attrs` grid (drop its "Attributes" `.cs-section-h`
   heading; the grid labels already say Strength, Speed...).
3. **Strip** (new wrapper): the Conditions block (~line 478), `.cs-rest`, both `.cs-derived` rows.
4. **Tabs**: a `role="tablist"` of buttons, then one `role="tabpanel"` per tab:
   - **Play** (default): Skills heading, the explainer line, `.cs-rolltools`, `.cs-hint`,
     `#cs-skills-grid`, `.cs-2col` (strikes + actions).
   - **Radiant**: the Radiant heading and both `.cs-rad-plate`s (`#cos-sl-actions`), plus singer-form
     / Shardblade / Shardplate / Infected blocks if present. **Render this tab only when it has
     content** (non-Radiant characters and non-PC actors get no Radiant tab).
   - **Gear**: Inventory, Spheres & Goods (`#cos-wallet`), Fabrials (incl. `#cos-crafted-list`,
     `#cos-workshop`).
   - **Character**: Talents, Expertises, Goals (`.cs-goals-wrap`, moved out of the top), Session
     Notes (`#pc-notes-*`).
   - **Inventory every top-level child of `.cs-wrap` before you start** (many are inside `{% if %}`).
     Anything this list doesn't name goes in Character; list it in your report.
5. Tabs: plain buttons, arrow-key navigation, `aria-selected`/`aria-controls`, the selected tab
   remembered in `sessionStorage` per character (wrap in try/catch). Panels use the `hidden`
   attribute, so any panel class that sets `display` needs `[hidden]{display:none}`.
6. `@media print`: show **all** panels and hide the tablist (the print sheet must stay complete).
7. Phones (`max-width:640px`): the three rings on one row (the mockup used `zoom:.82` on `.orb-w`;
   any method is fine), attributes 3 per row, compact boxes.

## Hard rules (CLAUDE.md, sheet edition)
- **No id, class used by JS, `onclick`, or form name may change or disappear.** Move markup blocks
  whole. Before/after counts of `id="` and `onclick="` in the rendered page must be equal.
- Every JS function that looks things up by id or class must still find them (check
  `document.getElementById(...)` / `querySelector(...)` targets: vbar gate ~line 1150, goals,
  wallet, notes, conditions, crafting).
- The live repaint from SSE (`cosmere_player_state` events) must still update Health / Focus /
  Investiture / conditions while any tab is open.

## Acceptance
- [ ] New `tests/test_cosmere_sheet_tabs.py`: render a Radiant PC and a non-Radiant PC (fixtures like
      `tests/test_cosmere_fabrial_crafting.py::crafter_pc`); assert the tablist exists, Play is the
      default, Radiant tab present only for the Radiant PC, and a fixed list of required ids is
      present (copy the list from `main`'s render before you start: every `id="..."` in the page).
      Also record before/after `id="` and `onclick="` counts for the same character in your report.
- [ ] Existing sheet tests pass: `pytest -q tests/test_cosmere*.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` (paste numbers; ~20 test files read this template's text).
- [ ] Browser (Claude verifies): desktop 1440 and phone 375, Stormlight + Mistborn skins; switch every
      tab; damage the PC from the GM tracker and see Health update with the Gear tab open; Print
      preview shows all sections; no sideways scroll at 375.

## Out of scope
- Rules, numbers, routes, `_pc_state_payload`, the PF2e player sheet. No new features.
