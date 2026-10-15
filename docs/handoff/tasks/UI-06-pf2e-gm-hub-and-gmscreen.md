# UI-06: PF2e GM dashboard (B) and GM Screen (B)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, medium effort)
- **Branch:** `codex/ui-06-pf2e-gm-hub-gmscreen` from current `origin/main`
- **Depends on:** none
- **Context:** approved directions in `tasks/UI-00.md` (table) and the bundled decision log.
  **Read "Conventions for every PF2e screen" below first.** It applies to UI-06 through UI-12.

## Conventions for every PF2e screen (UI-06 to UI-12)

**Where the designs are.** `docs/handoff/assets/UI-00-review.zip` (local only, gitignored; it is in
the `GM_pf2e_dnd5e` checkout). Extract it to a **new** folder (e.g.
`docs/handoff/assets/UI-00-review/`, also ignored). For screen `<s>`:
- `pf2e-<s>-review-offline.html`: open it in a browser to compare Current with A and B.
- `pf2e-<s>-revisions.css`: the design itself. Rules under `body.preview-refine` are Revision
  **A**, rules under `body.preview-session` are **B**. Some selectors carry both.
- `pf2e-<s>-preview.js`: the DOM moves for a revision (e.g. "move the Party tile after the combat
  banner"), plus a lot of demo-only code: blocked fetches, fake rolls, "Preview only" toasts,
  iframe sizing. **Port only the moves and the new markup, as real template changes.** Never ship
  runtime DOM shuffling, and never port the demo/blocking code.
- `ui-review-decisions.md`: what each screen must keep.

**How to port.**
1. The mockups were made from source snapshot `7e371500`. `main` has moved on (UI-03 fixed the GM
   screen script, the builder Review and the level-up rail, for example). Start from the
   **current** template, and follow the mockup's intent where the markup differs.
2. Copy the chosen revision's CSS without the `body.preview-*` prefix into the page's own style
   block, or `static/css/system.css` if more than one page uses it. Drop `!important` where the
   real cascade doesn't need it.
3. Keep **every** id, class used by JS, `onclick`/`onchange` handler, form `name`/`action`/`method`,
   link target and ARIA attribute. Add a test that renders the page and compares an inventory of
   ids, handlers, form fields and links against a list taken from `main` before your change.
   UI-02a's `tests/test_cosmere_generator_ui.py` is the pattern.
4. Project rules (CLAUDE.md): only `--font-display` / `--font-ui`; any class that sets `display`
   gets `.x[hidden]{display:none}`; JS-escape names in inline handlers; no new endpoints unless the
   brief says so; PF2e pages show PF2e mechanics only.
5. Gates: `python tools/check_templates.py`, the page's existing tests, your inventory test,
   `tests/test_inline_handler_escaping.py`, `tests/test_route_policy_inventory.py`. Paste numbers.
6. Claude checks every screen in the browser at 1440px and 375px (no sideways scroll at 375).
7. One task = one branch = one PR. After Claude accepts it, commit, PR and merge when every CI
   job is green (`PROTOCOL.md` step 6).

## UI-06 goal
- **GM dashboard, B** (`templates/gm_hub.html`, mockup `pf2e-dashboard-*`): party health sits right
  under the active-combat banner (the existing Party tile moves there, with its title and
  description as a heading); the three main Run tools in one row; the remaining tools as compact
  secondary grids. Keep all tiles, links and the hub's live repaint.
- **GM Screen, B** (`templates/gmscreen.html`, mockup `pf2e-gmscreen-*`): topic tabs plus an "All
  references" view, wider unboxed tables. Keep the original four groups, 14 cards, 12 tables and
  six drawer panes with identical text; keep search/detail, drawer shortcuts and the UI-03 pin /
  search / condition-link script working (pins stay in their own group).

## Acceptance
- [ ] Both pages match the B mockups at 1440px and 375px; all controls and text retained
      (inventory tests).
- [ ] GM Screen: tabs work by mouse and keyboard (arrow keys); "All references" shows everything;
      search reveals the right tab; pins and condition links still work (extend
      `tests/test_gmscreen_polish.py`).
- [ ] Gates from the conventions pass (numbers).

## Out of scope
- Any rules text or data. The Cosmere GM screen and dashboard (separate Cosmere batch).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- Ported dashboard Revision B into `templates/gm_hub.html`: Party View now follows the active-combat banner, the Run tools form the three-card primary row, and Prep and Manage use compact secondary grids. All original controls, links, live regions and repaint hooks remain.
- Ported GM Screen Revision B into `templates/gmscreen.html`: Checks, Conditions, Adversaries, Exploration and All topic controls use roving keyboard focus; single-topic searches reveal their topic, multi-topic searches show All, and clearing restores the prior topic; pins remain inside their original group; print still exposes every reference group. The header Checks button uses the existing drawer and now keeps `aria-expanded` synchronized.
- Added `tests/test_pf2e_gm_screens.py` with rendered inventories for IDs, links, handlers, JS selector classes, form and ARIA attributes, plus normalized-content fingerprints captured from `main` for all nine dashboard tiles, 14 reference cards and six drawer panes.
- Extended the Node controller harness to cover mouse and four-way keyboard topic navigation, Home/End, search reveal, condition links, drawer button and shortcuts, editable/copy-key guards, and interactive pin/unpin persistence and ordering.

### Verification

- `python tools/check_templates.py` — **107 templates parse**.
- `pytest -q tests/test_pf2e_gm_screens.py tests/test_gmscreen_polish.py tests/test_gm_xp_award.py tests/test_gm_system_repair.py tests/test_nav_cleanup.py tests/test_cosmere_gm_vitals.py tests/test_cosmere_world_skin.py tests/test_system_guards.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py` — **55 passed**.
- Claude completed the 1440px and 375px browser checks, including the populated-party and active-encounter dashboard states, GM Screen controls, print behavior and horizontal overflow. The required cross-topic search follow-up is covered by the runtime test.

## Review

**Claude, 2026-10-15: one required fix, then accepted.**

Read the diff (`gm_hub.html`, `gmscreen.html`, both test files) and checked live in a PF2e campaign
with an active encounter (Goblin Warrior + Kyle) at 1440px and 375px. Did not rerun the test
suite.

What works:
- **`/gm`:** the party band sits directly under the "Combat in progress" banner (226-280, band
  298-463), with Kyle's health row. The three Run tools form one row (Tracker / Map / GM Screen),
  and the remaining tools sit in compact grids. At 375px there is one column and no sideways
  scroll.
- **`/gmscreen` topics:**
  - 5 topic tabs; Checks shows by default;
  - "All references" shows all four groups;
  - Arrow keys move focus and the topic; Home and End work;
  - 14 cards and 12 tables are kept.
- **Search** for a term found only in a hidden topic ("Cover") switches to that topic.
- **Pins:** pin/unpin keeps the card in its own group (moves to the top of Adversaries and back).
- **Condition links:** clicking "Off-Guard" opens its detail.
- **Checks drawer:** the button, the C key and Escape all open/close it with `aria-expanded` in
  step.
- **Print:** the print rules hide the tabs and show every group.
- **375px:** no sideways scroll in a single topic or in All.

### Required fix
1. **Search hides most of its results when they span topics.** Measured from the Checks tab:
   - "damage" matches 6 cards in 3 topics, but only 1 is visible;
   - "DC" matches 8 cards in 4 topics, with 3 visible;
   - "hidden" matches 2 cards in 2 topics, with 1 visible.

   The search reveals only the first match's topic, so the GM sees a partial answer with no hint
   that more exist.

   **Fix:** when the matches span more than one topic, switch to "All references" (or the
   matching topics only). Then scroll to the first match. When the search is cleared, return to the
   topic that was selected before. Add a test: search a term present in two topics and check that
   every `.ref-match` card is visible.

### Note (not required)
- On a phone, the fixed "CHECKS" drawer handle on the right edge covers the last column of wide
  tables (for example the DC column of "DCs by level" in All). The handle predates this change.
  It's worth a small inset or a smaller handle at 375px in a later pass.
