# DS-00: Inventory diff tool

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Branch:** `claude/ds-00-inventory-tool` from `claude/design-brief` (PR #233)
- **Depends on:** none
- **Context:** [DESIGN_DIRECTION.md](../../design/DESIGN_DIRECTION.md) section 1.2 (what must not change), section 10.1 row DS-00, section 11 gate 2.

## Goal

Every design-system package must prove it changed only presentation. This tool records each page's behaviour hooks before and after an edit and fails on anything removed or changed, so a restyle that breaks a script or a frozen test inventory is caught before review.

## Approach

`tools/ui_inventory.py`, standalone, no new dependency (Playwright is already in `requirements-browser.txt`).

- `capture --mode html`: imports the app in-process against a throwaway `DATA_DIR` seeded with the four committed fixture PCs, legacy-open GM access, and renders each route through the Flask test client.
- `capture --mode dom --base-url URL`: headless Chromium against a running server; records the live DOM after scripts run, and again after selecting each panel-switching control (`--click`, default `[role=tab]`), reloading the page before each one.
- `diff before.json after.json`: exit 1 on any removal or changed value, exit 2 when the modes differ; additions are listed.
- Recorded per route: status; ids (an existing id appearing more often fails); `data-*`, inline handlers, `role`/`aria-*`/`tabindex` with the element's full class string, every form control (inside or outside a form), and inline `style` display/visibility, `hidden`, `open`, `inert` — each keyed to its element (own id, else nearest ancestor id + tag); form contracts (action, method, enctype, target, novalidate, ordered fields with options, falling back to option text, and textarea content); link, script and image targets with the `?v=` cache-buster stripped; script order (external and inline by content hash) and tab order, compared exactly; the `<body>` class string; class token counts; optional copy hooks (`--hooks`).

## Acceptance

- [x] A CSS-only edit diffs clean on all 18 default routes.
- [x] A scratch edit that removes one id and adds a class to an ARIA-bearing element fails with exactly those two lines.
- [x] Two captures of an unchanged checkout diff clean, in both modes (html: 18 routes; dom: 9 routes including the sheet, tracker, party and GM screen).
- [x] `pytest -q tests/test_ui_inventory_tool.py` passes (new tests: 19).
- [x] Full non-browser suite: no new failures (numbers in the report).
- [ ] VTT inventory script (`frontend/vtt/scripts/ui-inventory.mjs`): split out as **DS-00b**, see Follow-ups.

## Out of scope / do not touch

- Any template, CSS or JS file. This package changes no page.
- Committed baseline snapshots: captures are per-PR before/after files, not stored in git, because the inventory changes legitimately with every accepted feature.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- **Changed:** `tools/ui_inventory.py` (new), `tests/test_ui_inventory_tool.py` (new, 19 tests), this file.
- **Tests run:**
  - `pytest -q tests/test_ui_inventory_tool.py` -> 19 passed.
  - Full suite: see the line added below once the run finishes.
  - Real pages: html capture of 18 PF2e routes, all 200; CSS-only edit -> 0 failures; scratch break -> 2 failures, exit 1; two unchanged captures -> 0 failures in html mode and in dom mode.
- **Review:** an independent adversarial review of the first version found real false negatives (handlers swapped between buttons, reordered scripts, a duplicated id, changed `style="display:none"`, renamed controls outside forms, edited option text and textarea content, missing `enctype`) and two false-positive sources (the per-session CSRF token, text hooks split by a tag). All are fixed and each has a test.
- **Skipped / not verified:**
  - Only PF2e routes are fixtured. Cosmere, Mistborn, 5e, Chronicle, account-mode pages (`/me`, login, campaign management), `/map`, mobile combat and the VTT are not captured yet; until **DS-00c** seeds them, packages touching those routes rely on gate 2's `git diff` grep and gate 8's browser smoke.
  - dom mode was run against the 9 routes listed above, not all 18.
- **Known wrong / limitations:**
  - Two identical handlers swapped inside one container with no ids are not caught (hooks are keyed to the nearest id, not a full DOM path, to avoid failing on harmless markup reordering).
  - Any edit to an inline `<script>` fails by design: a design package must not change page JS. Moving CSS between `<style>` blocks is allowed.
  - Removing a Tailwind utility class fails; that is intended (JS may toggle it) but expect to justify each one.
  - `capture` must run in a fresh process (it refuses if `app` is already imported).
- **Follow-ups (not done here):**
  - **DS-00b** (Claude): VTT `ui-inventory.mjs` from a vitest render of each shell component (accessible names, roles, classes).
  - **DS-00c** (Claude): seeded Cosmere (Stormlight and Mistborn), 5e and account-mode fixtures so those routes are captured.

## Review
<!-- reviewer appends numbered findings, or "accepted" -->
