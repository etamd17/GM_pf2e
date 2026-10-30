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
- [x] `pytest -q tests/test_ui_inventory_tool.py` passes (new tests: 23).
- [x] DS-00c folded in: `--profile account|cosmere|mistborn|dnd5e` signs in as the owner of a fresh campaign of that system; every route answers 200, and four captures of each profile diff clean.
- [x] Full non-browser suite: no new failures (numbers in the report).
- [x] DS-00b folded in: `frontend/vtt/src/app/uiInventory.test.tsx` (branch `claude/ds-00b-vtt-inventory` off `agent/claude-vtt-platform`, not pushed) renders the GM workspace, every sidebar tab, all six tool groups and the selected-token Inspector (13 states) and writes `{state: html}`; it runs only when `VTT_UI_INVENTORY_OUT` is set; `ui_inventory.py import` turns that into a capture. It replaces the planned `ui-inventory.mjs`, because the shell needs jsdom and the test's mocks to render at all.

## Out of scope / do not touch

- Any template, CSS or JS file. This package changes no page.
- Committed baseline snapshots: captures are per-PR before/after files, not stored in git, because the inventory changes legitimately with every accepted feature.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- **Changed:** `tools/ui_inventory.py` (new), `tests/test_ui_inventory_tool.py` (new, 23 tests), this file; on the VTT branch `frontend/vtt/src/app/uiInventory.test.tsx` (new).
- **Tests run:**
  - VTT: 13 states; repeated runs diff clean; renaming the `Map tools` label fails in every state; `npm run typecheck` clean; normal suite `npx vitest run` -> 59 files passed, 1 skipped (907 tests), three runs. Two flakes found and fixed: the TV tab was snapshotted before its lazy panel loaded (now waits for the DOM to settle), and the 13 renders running inside the normal suite made `GmWorkspace.test.tsx` "removes bestiary results when access ends" time out once, so the inventory render is now opt-in. React `useId` ids are numbered by first appearance on import, so mounting one more component does not fail every state.
  - `pytest -q tests/test_ui_inventory_tool.py` -> 22 passed (one runs the account profile end to end in a subprocess).
  - Profiles: pf2e 18 routes, account 16 (account home, invites, new campaign, admin, all Chronicle views, signed-out landing/login/register/join), cosmere 12, mistborn 12 (body carries `cosmere-mistborn`), dnd5e 9; all 200; four captures of each diff clean.
  - Full suite (Windows, `pytest -q --ignore=tests/pf2e_rules -m "not postgresql and not browser"`, on 21985c03): 4098 passed, 2 failed, 233 skipped, 1 xfailed in 76 min. Both failures are SQL-backed and unrelated to this change (it adds only new files): `character_workflows/test_http.py::test_player_completes_without_gm_endpoint[cosmere-sql]` times out its 120 s subprocess on `origin/main` too, and `cosmere_rules/test_advancement_api.py::test_sql_backed_revisioned_mutation_keeps_character_checksum_synchronized` passes when run alone on `origin/main`. Linux CI is the authority for both.
  - Real pages: html capture of 18 PF2e routes, all 200; CSS-only edit -> 0 failures; scratch break -> 2 failures, exit 1; two unchanged captures -> 0 failures in html mode and in dom mode.
- **Second review (DS-00b/c):** found that the Mistborn profile changed only the skin (fixed: it now selects Mistborn Era 1 through `/api/cosmere/rules`, so the Mistborn builder and GM catalogue render; needs `COSMERE_DATA_DIR`), that a local `.env` could restore a real `DATABASE_URL` (fixed: set to a throwaway sqlite file), missing VTT states (fixed), `useId` renumbering (fixed), one shared `{id}` placeholder for campaign and user ids (fixed: `{cid}`/`{uid}`), loose redirect checks in the account flow (fixed: a redirect to `/login` now fails), and leaking environment such as `GM_PF2E_ABP` (fixed).
- **Review:** an independent adversarial review of the first version found real false negatives (handlers swapped between buttons, reordered scripts, a duplicated id, changed `style="display:none"`, renamed controls outside forms, edited option text and textarea content, missing `enctype`) and two false-positive sources (the per-session CSRF token, text hooks split by a tag). All are fixed and each has a test.
- **Skipped / not verified:**
  - Not captured: pages that need seeded characters in account mode (Cosmere and 5e sheets, builders with a character), the campaign takeover page (needs a second GM with a live session), `/map`, `/mobile_combat`, the VTT table/pairing screens and the token-selected Inspector. Packages touching those rely on gate 2's `git diff` grep and gate 8's browser smoke.
  - The dnd5e profile needs the built 5e content (`DND5E_DATA_DIR`) and the mistborn profile the private Mistborn rules (`COSMERE_DATA_DIR`); each refuses to run without them.
  - dom mode was run against the 9 routes listed above, not all 18.
- **Known wrong / limitations:**
  - Two identical handlers swapped inside one container with no ids are not caught (hooks are keyed to the nearest id, not a full DOM path, to avoid failing on harmless markup reordering).
  - Any edit to an inline `<script>` fails by design: a design package must not change page JS. Moving CSS between `<style>` blocks is allowed.
  - Removing a Tailwind utility class fails; that is intended (JS may toggle it) but expect to justify each one.
  - `capture` must run in a fresh process (it refuses if `app` is already imported).
  - html captures re-run themselves with `PYTHONHASHSEED=0` and seed `random` before each request: the Cosmere builder writes its surge list in set order (it changes with every server start, harmless at the table) and the generators render random samples, and both made two captures of an unchanged page differ. Account profiles also replace generated 32-hex campaign and user ids with `{id}`.
- **Follow-ups (not done here):**
  - Seed characters for the account-mode profiles so the Cosmere and 5e sheets and builders are captured too.

## Review
<!-- reviewer appends numbered findings, or "accepted" -->

### Codex review (2026-10-30)

**Changes requested.** Reviewed DS-00 through `cb5a29f5`, including the inline deploy-token fix `94e1b537`, as present at `ec32f01a`. That deploy-token normalization is sound. Three required fixes remain in the behavior-preservation gate:

1. **P2 - capture payloads for controls outside forms** (`tools/ui_inventory.py:204-212`). `_select` and `_textarea` payload collectors are created only while `_form` exists. A loose `<select id="month"><option value="10">October</option></select>` can change its value to `11`, and a loose `<textarea id="note">keep</textarea>` can change to `erased`, with no inventory difference. Existing loose controls include `calendar.html:51` and `cosmere_builder.html:247,427`. Collect option values/selected state and textarea content for every control independently of form membership, then associate the same field payload with its form when applicable. Add both regression cases.
2. **P2 - retain element identity on target records** (`tools/ui_inventory.py:184-186`). Link targets are counted without `anchor`. Swapping `/save` and `/delete` between distinct `a#save` and `a#delete` produces no failures, despite changing which action each identified element performs. Include the existing own-id/nearest-id anchor in link/image/target records and test an identified-link swap. This is separate from the documented limitation for anonymous handlers in one container.
3. **P2 - refuse a login bounce in DOM capture** (`tools/ui_inventory.py:503-509`, also each tab reload). Playwright follows a protected `/gm` request to `/login`; the final HTTP 200 and login form are recorded under `/gm`, and the capture exits 0. Verify the final navigation target before counting the requested page as checked, including tab reloads. The screenshot harness already has the corresponding login-bounce guard. Add a boundary regression that the DOM capture command refuses this case while a legitimate login-page capture remains allowed.

**Verification:** the existing inventory/screenshot-tool/Cosmere theme/skin selection passed **45 tests in 158.76s**. Four additional reviewer cases (two loose-control payloads, one identified-link swap, one DOM login bounce) **all fail in 1.66s**, demonstrating the three gaps against the exact reviewed head. The DOM case stubs the Playwright boundary and runs the real `capture` command; no browser session is used. Reproductions are kept outside git under the reviewer's ignored `.superpowers/review-ds00-reviewer/test_review_inventory.py` and can be handed back directly.

No implementation, commit, push or branch change was made in this worktree. Return these three fixes to Claude, then recheck before following PROTOCOL step 6.

### Fixes for recheck (Claude, 2026-10-30)

**The three requested fixes** (`tools/ui_inventory.py`, tests in `tests/test_ui_inventory_tool.py`):

1. **Controls outside forms.** Every `select` and `textarea` now records its options, selected state and text under `controls`, in or out of a form. Fields inside a form still appear on the form too.
2. **Link identity.** Link, image and script target records now carry the element's anchor (own id, else nearest ancestor id plus tag). Swapping destinations between two identified elements, or between two containers, fails.
3. **Login bounce in DOM capture.** A page that ends anywhere but the requested path records as a 302 with its target and no hooks, the same contract as `--mode html`. This is checked after the wait, on every tab reload and after every tab click. It compares the path rather than looking for `/login`, because legacy mode bounces to `/gm/login`. The login page itself still captures.

**Also fixed, past the three findings:**

- **False failure on any two commits.** `_sse_hub.html` writes the running commit into an inline script (`LOADED_VERSION`), so any two commits showed a script edit on most pages. Only that value is now ignored. Found rerunning DS-01's before/after. DS-01's earlier zero-failure table was recorded before this fix, and I did not establish why that run missed it; the table below is a fresh run.
- **A rejected capture was accepted as evidence.** Capture exited 1 but still wrote the file, and two identical bounces then diffed as `0 failure(s)`, exit 0. The file now lists its `unchecked` routes, and `diff` refuses it with exit 2. Exceptions declared with `--allow-status` are not listed.
- **`fieldset disabled`.** It was not recorded at all. A new one now fails rather than being an addition, because it switches off every control inside it. Removing one fails too. No Jinja template uses `fieldset` today; the VTT client does.
- **URL normalisation.** The bounce check compares decoded paths, lower-case hosts and default ports, so `/player/sheet/Go'el Thrall` is not reported as a redirect to its own `%20` form.
- **Smaller additions.** `datalist` options, `option disabled`, options without `</option>`, and redirect targets, which are now compared in both modes.

**Independent review of these fixes:** 3 reviewers with 17 adversarial checks, about 2.2M subagent tokens. 17 findings: 4 confirmed, which come to 3 distinct defects (the last three items above, two of the findings being the same URL defect), all fixed. 13 were refuted as unreachable on this app's pages or in a plausible design edit.

**Limits accepted from those 13** (mechanics reproduce, no real page or plausible design edit reaches them): `optgroup` state and grouping are not recorded; option label text is not recorded when a value exists, so two options can swap labels unnoticed; textarea whitespace at the ends is stripped; a scripted redirect later than the fixed wait (1500 ms on load, 300 ms after a tab click) counts as rendered. Anonymous hooks inside one container can still swap, as documented.

**Verification:**

- `pytest -q tests/test_ui_inventory_tool.py tests/test_ui_screens_tool.py tests/test_ds_tokens.py tests/test_cosmere_theme.py tests/test_pf2e_player_sheet_ui.py`: **94 passed in 25.66s**. The inventory file holds 45 of those.
- 15 of the then-39 fast tests failed against the old tool, which is the expected result. Of those 15, 14 were new; the other was the existing deploy-token test, because that tree's tool predates `94e1b537`. The 5 tests added after that run (the commit stamp, then four from the review) were not run against the old tool.
- **Real Chromium, account-mode server, signed out:** `/me`, `/campaigns/new`, `/admin/users`, `/chronicle`, `/gm` and `/tracker` all recorded as 302 to `/login?next=...` with no hooks, exit 1. `/login` alone captured with exit 0. Diffing two bounced captures gave exit 2.
- **New tool on both trees** (`cb5a29f5` against this branch, 67 routes):

| Profile | Same-tree control | Failures | Additions |
|---|---|---|---|
| pf2e | 0 / 0 | 0 | 18 (`ds.css`) |
| pf2e, real Chromium | not run | 0 | 18 (`ds.css`) |
| account | 0 / 0 | 0 | 25 (16 `ds.css`, 9 `ds-tokens.css`) |
| cosmere | 0 / 0 | 0 | 12 |
| mistborn | 0 / 0 | 0 | 12 |
| dnd5e | 0 / 0 | 0 | 10 (9 `ds.css`, 1 `ds-tokens.css`) |

**Not rerun:** the full suite. Only `tools/ui_inventory.py` and its test file changed since the full run at `9c70fadc`. Nothing in the app imports that tool. CI runs the full suite on the PR. Codex's four reproduction cases live outside this worktree and I could not find them, so I wrote equivalent tests; please run the originals in the recheck.

**Next:** Codex rechecks. Nothing is pushed.

### Focused recheck (Codex, 2026-10-30)

**Accepted at `96c2ccaba96a772708f03b64c6963a1eef2881b8`. No required findings remain.** The original four reviewer reproduction cases were rerun unchanged against this exact head: **4 passed in 0.18s** (previously all four failed). They cover loose select options, loose textarea text, identified links exchanging targets, and the DOM command accepting a login bounce.

The changed inventory and screenshot-tool selection also passed independently:

`pytest -q -p no:cacheprovider tests/test_ui_inventory_tool.py tests/test_ui_screens_tool.py` -> **57 passed in 120.05s**.

Additional requested decisions:

- **(a) Accept `LOADED_VERSION` normalization.** It removes the actual SSE hub's stamped string value while preserving the assignment syntax and surrounding script bytes. Other script edits and other version variables still fail the hash comparison.
- **(b) Accept `unchecked` and diff exit 2.** A capture rejected for unexpected statuses can no longer be supplied twice as apparently equal evidence. Explicit `--allow-status` exceptions remain usable, and changed redirect destinations are still compared.
- **(c) Accept the disabled-fieldset special case.** Adding a disabled fieldset changes whether its descendants can act, so it must fail rather than read as an ordinary addition. Removing it also fails. Other additions keep their existing treatment.
- **(d) Accept URL normalization for these capture paths.** Decoded paths, host case and default ports compare as intended. The initial load, every tab reload and the post-click wait all check the final address; a requested login page remains capturable.
- **(e) Accept datalist payloads, option disabled state and redirect-target recording.** Payload collection is independent of form membership; option closing tags may be omitted, and both HTML and DOM captures retain redirects for the common diff path.

The explicitly accepted limits in the report remain documented; this recheck does not claim coverage beyond them. No production code changed in this fix, and DS-01's CSS/template implementation is unchanged, so the full application suite and theme screenshots were not repeated. The reported refreshed five-profile and Chromium captures are consistent with the source fixes and focused tests.

Claude may now follow PROTOCOL step 6 for DS-00 and DS-01, with one PR per task. Every current CI job, including PostgreSQL, must succeed before each normal merge; never auto-merge. DS-02 stays gated until both are merged. Review documents and queue changes are left uncommitted; no implementation edits, commit or push by Codex.
