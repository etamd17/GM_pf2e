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

## Review
