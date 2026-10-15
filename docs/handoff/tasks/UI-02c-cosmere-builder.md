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
- [x] New `tests/test_cosmere_builder_guide.py`: render the builder for an existing PC (edit mode)
      and a new one; assert the to-do container, the level badge element, the renamed title in edit
      mode only, and the draft-bar ids. Unit-test the budget -> rows mapping if you put it in a
      small pure function (recommended).
- [x] `pytest -q tests/test_cosmere_builder*.py tests/test_cosmere_creator_ux.py tests/character_workflows tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` pass (paste numbers). The draft-bar change touches PF2e
      pages, so also run `pytest -q tests/browser -m browser` in CI.
- [ ] Browser (Claude verifies): edit a level-5 character, press + once: badge "5 -> 6", the list
      grows by the engine's numbers (UI-01 saw +1 attribute point, +2 skill ranks, +2 talents), Go
      opens the right step; a fully spent step shows a check and no "left"; phone 375px has no
      sideways scroll; the PF2e builder's draft bar still works.

## Out of scope
- Rules or budgets, new steps, PF2e builder layout beyond the shared draft bar.

<!-- ============ REPORT (written by Codex) ============ -->

## Report

- **Branch/base:** `codex/ui-02c-cosmere-builder`, based on post-#182/#183 main
  `98114c862a0a5ee8df75378cbb09c8e3bdbc57bf`. Local work only; no push, merge or deployment.
- **Changed:**
  - `templates/_character_draft_controls.html`: reusable compact draft bar; all five IDs,
    original title branches, ARIA attributes, hidden buttons and script include preserved.
  - `static/css/system.css`: shared draft bar typography, wrapping and right-aligned actions;
    explicit `.draft-bar button[hidden] { display: none; }`.
  - `templates/cosmere_builder.html`: exact new/edit titles and glossary summary; engine-backed
    remaining-choice list, step badges, over-budget precedence and initial-level badge.
    The budget mapping uses only preview `[spent, available]` arrays. Skills and expertises
    share step 2; attributes use step 1 and talents step 3. Empty text is exactly
    `Nothing left to choose.`.
  - `tests/test_cosmere_builder_guide.py`: 16 regression cases for rendering, all four shared
    pages, hidden CSS, singular/plural budgets, navigation destinations, actual preview
    painting, completion/over-budget precedence, level badge, scroll context and response ordering.
  - This report and `docs/handoff/QUEUE.md` record the handoff.
- **Review corrections:** a page-scoped viewport flex shell makes the base template's existing
  content wrapper the real scroll container. The desktop topbar stays sticky; a ResizeObserver
  measures topbar and rail heights so the rail and sidebar stack without overlap. At 760px and
  below the topbar and sidebar are static and the horizontal rail sticks directly under the nav.
  Step navigation scrolls this content wrapper. A request generation guard prevents an older
  preview response overwriting newer guidance. The preview endpoint, POST body (including `PID`),
  debounce, and fetch error handling are preserved; fetch is not wrapped. `collect()` and the
  entire save function were compared with the base and are unchanged. No validation, budget,
  route or engine code changed; UI-02b is untouched.
- **Independent follow-up review:** both corrections were accepted without new findings:
  viewport shell/nav/scroller, measured sticky offsets, phone fallback and step scrolling;
  request generation ordering with endpoint, identity payload, JSON handling and error behavior
  preserved. Claude's live browser review below remains the next handoff.

### Validation

The configured interpreter is `..\.venv_cosmere_rules\Scripts\python.exe` (Python 3.11.9).
PowerShell expands the brief's `tests/test_cosmere_builder*.py` with
`(Get-ChildItem tests/test_cosmere_builder*.py).FullName`. Repo-local `--basetemp` directories
keep disposable Windows test data in the authorized workspace.

- **Red before implementation:**
  `python -m pytest -vv -x -s tests/test_cosmere_builder_guide.py --basetemp=.pytest_cache/ui02c-red`
  -> **14 collected, 1 failed**, stopped on the expected missing `still-to-choose` container
  in the new-character render (61.84s). The initial quiet attempt was interrupted during
  slow app import before reporting a result.
- **First green:**
  `python -m pytest -q tests/test_cosmere_builder_guide.py --basetemp=.pytest_cache/ui02c-green`
  -> **14 passed** (17.56s), before the two review regressions were added.
- **Review red/green:** the actual new scroll-context and request-order test functions were
  executed directly from the test module's AST, without another app import. Both failed before
  their fixes; both passed afterward. The ordering test resolves level 6 before a stale level 5
  and asserts only level 6 is painted, with both request bodies preserved.
- **Final focused acceptance gate:**
  `..\.venv_cosmere_rules\Scripts\python.exe -m pytest -q (Get-ChildItem tests/test_cosmere_builder*.py).FullName tests/test_cosmere_creator_ux.py tests/character_workflows tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py --basetemp=.pytest_cache/ui02c-final`
  -> **399 passed, 14 skipped** (1066.32s / 17m46s), exit 0. The skips are existing
  backend-specific JSON/SQL boundaries and unavailable Windows symlink cases. No test was
  given a new skip, and no browser test was skipped.
- **Templates:** `python tools/check_templates.py` -> **all 107 templates parse**, exit 0,
  rerun after the review corrections.
- **Exact CI browser command:**
  `python -m pytest -q tests/browser -m browser --strict-markers --basetemp=.pytest_cache/ui02c-browser-final`
  -> **32 passed** (595.99s / 9m55s), outside the Windows sandbox. `python -m pip check`
  -> **No broken requirements found.** These exercise PF2e and Cosmere, JSON and SQL stores,
  mobile and desktop, builder/import publication, save/reload/restart, conflict-copy/retry,
  keyboard/status controls and ownership capabilities.
- **Shared-page inspection:**
  `..\.venv_cosmere_rules\Scripts\python.exe .pytest_cache/ui02c_inspect.py`
  -> **8 rendered-page/viewport checks passed**, outside the sandbox. This one-off ignored
  helper rendered the real templates, inlined the local stylesheets and removed scripts to
  isolate CSS. Screenshots were also inspected. In every case, copy/reload computed to
  `display: none`, and all visible buttons/links stayed inside the draft bar:

  | Page | Bar width at 1440px | Bar width at 375px | Result |
  |---|---:|---:|---|
  | Cosmere builder | 1264px | 327px | Hidden controls respected; visible controls fit |
  | PF2e builder | 239px (narrow sidebar) | 359px | Hidden controls respected; actions wrap and fit |
  | Import preview | 712px | 295px | Hidden controls respected; actions wrap and fit |
  | Publication recovery | 712px | 295px | Recovery title and controls wrap and fit |

  The Cosmere level badge computed to `display: none` initially at both sizes. The sidebar
  computed to `sticky` at 1440px and `static` at 375px; phone rail horizontal overflow was
  `auto`. Functional save/conflict/retry behavior is covered by the 32-browser-test gate;
  these isolated CSS checks do not replace Claude's live level-up and scrolling review.
- **Whitespace:** `git diff --check` -> exit 0, no output.

### Browser environment and remaining review

Playwright was absent from the configured environment. The parent agent installed the repository's
locked `requirements-browser.txt` with `--require-hashes`, then Playwright 1.63.0's pinned Chromium.
Chromium launch hangs inside this Windows sandbox: the initial browser test command and optional
rendered-page inspection were interrupted before any browser test result. An escalated standalone
Chromium launch succeeded in 2.7s; the required browser gate and CSS inspection then passed
outside the sandbox.
The first focused gate was interrupted when review corrections made its source obsolete; the
complete final-source run passed as recorded above. These interrupted commands are not counted as
passing gates or skipped tests.

Claude still owns the brief's final browser review: existing level-5 PC, + to level 6, exact badge
and engine delta, all Go destinations, fully spent ticks, red over-budget markers, actual sticky
scrolling, 375px overflow, and PF2e draft controls. Check both Cosmere skins. No production
verification or deployment was requested for this branch.
