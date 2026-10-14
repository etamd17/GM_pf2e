# UI-02b: Build the approved Cosmere character sheet (Revision A, play-first + tabs)

<!-- ============ BRIEF ============ -->

- **Owner:** codex (strongest model, high effort: this is the highest-risk page of the three).
  Claude reviews the diff and verifies in the browser.
- **Branch:** `codex/ui-02b-cosmere-sheet` from current `main` (after PR #181 merges).
- **Depends on:** none in code; PR #182 doesn't touch `cosmere_sheet.html`. If #182 merges first,
  rebase and rerun the tests below.
- **Context:** decision in `tasks/UI-01.md` (sheet **A**, plus the "Checks done (sheet)" notes).
  Local mockup bundle `docs/handoff/assets/UI-01-review/` (`sheet-raw.html` plus
  `sheet_layout.js` / `sheet_layout.css`; the old `sheet-a.html` name is stale), which
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
- [x] New `tests/test_cosmere_sheet_tabs.py`: render a Radiant PC and a non-Radiant PC (fixtures like
      `tests/test_cosmere_fabrial_crafting.py::crafter_pc`); assert the tablist exists, Play is the
      default, Radiant tab present only for the Radiant PC, and a fixed list of required ids is
      present (copy the list from `main`'s render before you start: every `id="..."` in the page).
      Also record before/after `id="` and `onclick="` counts for the same character in your report.
- [x] Existing sheet tests pass: `pytest -q tests/test_cosmere*.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` (paste numbers; ~20 test files read this template's text).
- [ ] Browser (Claude verifies): desktop 1440 and phone 375, Stormlight + Mistborn skins; switch every
      tab; damage the PC from the GM tracker and see Health update with the Gear tab open; Print
      preview shows all sections; no sideways scroll at 375.

## Out of scope
- Rules, numbers, routes, `_pc_state_payload`, the PF2e player sheet. No new features.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- **Changed:** implemented Revision A in `templates/cosmere_sheet.html`: desktop vitals band,
  conditions/rest/derived strip, Play/Radiant/Gear/Character panels, 900px stacking and compact
  640px rules. The attribute heading alone was removed as requested. The read-only notice,
  header, build notes, quick-vitals bar and combat strip retain their existing markup and order.
- **Tabs:** Play is the default; only `pc and radiant` gets Radiant. Clicks, Left/Right arrows,
  Home/End, roving tabindex, selected state, panel visibility and per-character session storage
  are covered. Denied storage access and a stale Radiant selection both fall back safely.
  Print CSS reveals every panel and hides the tablist.
- **Preservation:** the fixture is Kal, a level-12 Fourth-Ideal Windrunner with Take Squire,
  Wound Regeneration and Bridge Four connections, rendered from `origin/main` at `3b021789`
  before the template edit. `tests/test_cosmere_sheet_tabs.py` freezes the complete ordered
  baseline ID list and every inline click handler; the final render may reorder IDs but must
  preserve their exact multiset and uniqueness. Counts before / after:

  | Measure | Before | After |
  |---|---:|---:|
  | Actual parsed ID attributes | 122 | 122 |
  | Actual parsed onclick attributes | 86 | 86 |
  | Literal `id="` occurrences in rendered HTML | 124 | 124 |
  | Literal `onclick="` occurrences in rendered HTML | 63 | 63 |

  Literal counts include JavaScript strings and omit handlers written with single HTML quotes;
  the parsed counts and exact handler values are the stronger preservation check. New tab/panel
  accessibility IDs are assigned by the new controller before its handlers initialize, preserving
  the brief's exact server-rendered ID count while providing `aria-controls` / `aria-labelledby`
  in the running page. A Radiant page gains eight new runtime IDs (four tabs and four panels).
- **Live behavior:** every original support-script body is unchanged. Resource painting,
  conditions, goals, wallet, notes, crafting and the vbar observer still use document-wide IDs
  and classes or local `closest` relationships inside intact blocks. Vitals and conditions stay
  outside hidden panels; tabs neither recreate nodes nor unregister SSE handlers. Each foldable
  reference section has a wrapper so its existing sibling-walking script cannot absorb Goals
  or another panel.

### Inventory of original `.cs-wrap` children

This accounts for conditional children too, including those absent from the representative render.

| Original children (headings travel with their content) | Destination |
|---|---|
| Read-only notice; header; build notes; `#cs-vbar`; `#cos-combat` | Original top region |
| `.cs-orbs`; `.cs-facets`; attribute grid | Band (orbs left; facets then attributes right) |
| Attributes heading | Removed, as requested |
| Conditions container and effects; `.cs-rest`; both `.cs-derived` rows (statistics and Connections) | Strip |
| Skills heading, explainer, roll tools, hint, `#cs-skills-grid`; intact `.cs-2col` | Play |
| Radiant heading and both `.cs-rad-plate` blocks | Radiant, PCs with Radiant content only |
| Infected Arts heading and ability blocks | Radiant for Radiants; Character for non-Radiant PCs |
| Inventory heading and grid/empty state; Spheres & Goods heading and wallet; Fabrials heading, each fabrial, crafted list and workshop | Gear |
| Talents heading and chips/empty state; Expertises heading and chips/empty state; Goals; Session Notes heading, textarea and status | Character |
| Notes autosave script | Character, with Notes and its original permission conditions |
| Fold script; live controls/SSE script | Nonvisual infrastructure after the panels, under the original PC/interactive gates |

**Unnamed Character blocks:** none. No additional unnamed visual top-level section exists.
The notes script travels with Notes; the other two support scripts remain after the panels,
matching the approved mockup mover's handling of nonvisual infrastructure. Traits and the Shardblade
strike remain inside the whole Play `.cs-2col`; singer form is an existing header badge, and Shardplate controls/status remain inside
the Radiant actions and band facets. There were no separate singer-form or Shardplate top-level
blocks to move. Infected Arts is defined once as a template macro and emitted in exactly one panel.

### Verification

- Strict TDD: `pytest -q tests/test_cosmere_sheet_tabs.py` before any template change ->
  **6 failed, 1 passed in 20.37s** (missing tablist/panels and hidden/print safeguards).
- Initial implementation: the same command -> **7 passed in 13.52s**.
- Final focused coverage: **16 tests passed within the acceptance run**, including six Node
  executions of the actual rendered tab controller, read-only/interactive PCs, non-PC rendering,
  Infected placement, section boundaries, hidden/print safeguards and the frozen render contract.
- Acceptance: `pytest -q tests/test_cosmere*.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
  -> **441 passed in 257.86s (0:04:17)**, exit 0. Run under Git Bash to expand the wildcard on Windows, with
  `..\.venv_cosmere_rules\Scripts` on PATH. System Python lacks Flask; the venv is the test runtime.
- `python tools/check_templates.py` -> **`OK: all 107 templates parse`**, exit 0.
- `git diff --check` -> **no output**, exit 0. The final diff was inspected; the three legacy
  inline script bodies compare byte-for-byte equal to the baseline, and no form names changed.

### Follow-ups / not verified here

- Claude's specified browser acceptance remains open: 1440px and 375px, Stormlight and Mistborn,
  every tab, GM-tracker damage while Gear is open, print preview completeness and no phone overflow.
  Server-rendered contracts and Node controller tests do not replace that visual/live-browser check.
- Non-PC actors render successfully with Play/Gear/Character and no Radiant tab. Their Gear and
  Character panels are empty because those original sections were PC-only; the brief only requests
  conditional omission for Radiant, so the other tab names are retained for review.
- No rule, route, payload, or PF2e-sheet changes; no push, merge or deployment. UI-02c was not started.
