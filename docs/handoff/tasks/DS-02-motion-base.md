# DS-02: Motion base and reduced motion

- **Owner:** claude
- **Branch:** `claude/ds-02-motion-base` from `main`
- **Depends on:** DS-00, DS-01 (both merged)
- **Context:** `docs/design/DESIGN_DIRECTION.md` 6.1 to 6.5, section 10 row DS-02

## Goal
Give later packages one motion vocabulary and make reduced motion hold site-wide. Nobody should see a change in normal use beyond a slightly different easing curve on existing hover and colour transitions.

## Approach
- `static/css/system.css`: `--t-fast`, `--t-base` and `--t-slow` become `<same duration> var(--ease-standard)`. Their 1ms reduced-motion collapse stays.
- `static/css/ds.css`: the global reduced-motion block from 6.3, and these new classes, used by no page yet:
  - `.ds-press`: press feedback, using `--motion-scale-from`.
  - `.ds-enter`: fade-rise entrance.
  - `.ds-panel`: panel swap.
  - `.ds-collapse` and `details.ds-details`: disclosure.
  - `.ds-skeleton`: loading placeholder.
  - `.ds-shimmer`: pending text.
  - `.ds-edge-fade`: edge fade on scrolling strips.
- `tools/ui_screens.py`: a new `motion` command. It opens each fixtured route with reduced motion emulated and fails if `document.getAnimations()` shows a CSS animation still running, looping or longer than the collapsed duration.
- `tests/test_ds_motion.py`: static rules for `ds.css`, plus a browser test of the classes and the reduce block.

### Decisions made in this brief
1. **The `:root` line in the 6.3 block is left out.** DS-01 already sets `--motion-distance` and `--motion-scale-from` under reduce in `system.css` and `ds-tokens.css`. A third copy could only drift.
2. **The shimmer keeps its gradient sweep.** Section 6.4 specifies it, but 6.2 limits which properties may animate and 6.5 forbids animated gradients. I read 6.4 as the named exception: small pending text only, never large areas. Under reduce and in forced colours it is plain muted text. The reviewer should confirm this reading.
3. **Closed `.ds-collapse` content uses `visibility: hidden`, with a delayed `visibility` transition.** Otherwise closed content could still take keyboard focus. `visibility` isn't on the 6.2 list, but it doesn't move or repaint anything gradually, so it's allowed here only.
4. **`.ds-skeleton` is defined here, as the DS-02 row says.** The DS-07 row lists it too; DS-07 builds on this class and should not redefine it.

### What the scan found
A read-only scan of `static/css`, `templates` and `static/js` before this brief:
- No `animation` declaration combines `infinite` with `forwards` or `both`, so collapsing to one iteration can't strand an element in a hidden end state.
- No `animation` declaration other than `animation: none` uses `!important`, so nothing outranks the block by that route.
- JS sets `style.animation` in two places (`player_sheet.html:4513`, `_player_nav.html:124`). Both are inline and not `!important`, so the block shortens them.
- Inline `animation-delay` is used at `player_sheet.html:6400` and `splash.html:156,161`. The 6.3 block doesn't reset delays, so these elements still wait their delay and then jump to the final state.
- `animationend` waiters are in `cosmere_sheet.html`, `gmscreen.html`, `player_levelup.html`, `player_sheet.html` and `tracker.html`. They fire sooner under reduce. An infinite animation that never ended before now ends, so its waiter can fire for the first time; the report must state whether any waiter is affected.

No duration changes, so no timer depends on this package. The report repeats the grep.

## Acceptance
- [ ] `--t-*` keep their durations and change curve only; the 1ms collapse is unchanged.
- [ ] `python tools/ui_screens.py motion --profile <p>` exits 0 for pf2e, account, cosmere, mistborn and dnd5e, covering at least the 10 key pages from the plan.
- [ ] A page with a deliberately looping animation makes that command fail (test).
- [ ] Inventory diff, `main` against the branch: 0 failures and 0 additions in all five profiles.
- [ ] `tools/ui_screens.py compare` against `main`: pixel-equal in all five profiles. **Not literally met; see Report, limits 6 and 7.**
- [ ] No `:active` rule on a legacy selector (`.btn`, `.tb`, `.act-btn`, `.d5-btn`, `.map-btn`), and no `.ds-*` motion class in any template (test).
- [ ] `ds.css` has no `transition: all`, no blur or `backdrop-filter`, no travel over 8px and no scale below 0.96, and animates only the 6.2 properties plus the listed `EXCEPTIONS`: the shimmer gradient, `visibility` and `overflow` on `.ds-collapse`, and `height` and `content-visibility` on `::details-content` (test).
- [ ] `pytest -q tests/test_ds_motion.py tests/test_ds_tokens.py tests/test_ui_screens_tool.py` passes.
- [ ] `python tools/check_contrast.py` shows 0 enforced failures.
- [ ] Windows full suite: no failures beyond the 3 known ones, plus the 11 PostgreSQL errors when no database is set.

## Out of scope / do not touch
- Applying motion to any existing page, including press on legacy selectors (screen packages do that).
- Any JS on paint or SSE paths; `dsFlash`, `dsTween`, the replay guard and `indicator.js` (adopted for later).
- The `.roll-toast` entrance and the keyframe kill at `system.css:228-235` (needs Evan).
- Existing per-feature reduced-motion blocks (they stay).
- The VTT and `frontend/vtt` (DS-08).

## Report

### Limits to read first
1. **Under reduced motion, clicking a locked row now shows nothing.** This follows Codex's finding: the static `brightness(1.6)` rule stayed on because the picker leaves `cp-chip-flash` attached, so the rule is gone. `main` under reduce played a 320ms shake and flash; now both collapse to 0.01ms and the row's pixels don't change. Only reduced-motion users lose it. The reason chip stays visible at rest on every locked row, with the exception in limit 2.
2. **The reason chip isn't always visible on `/player/levelup`.** The opaque gains rail (`player_levelup.html` about line 186, z-index 55, no pointer events while the picker is open) can cover the chip completely: desktop rail at x 250-490, y 111-321, and on phone too until the page scrolls 96px. The covering predates DS-02. What's new is that a click on a covered row shows nothing under reduce. A separate session is fixing the rail, and the fix is not part of this package.
3. **Keyboard users get no cue on a locked row.** Locked rows have no focus style (`_choice_picker.html:337-340`, not caused here), and under reduce Enter now shows nothing either.
4. **The new regression test covers the builder only.** The picker on `/player/levelup` was checked once by probe, not by a committed test.
5. **Other one-shot cues also collapse under reduce:** `.sheet-flash-*`, `.sustain-spell-highlight`, `.rapid-new`, `.cg-body.flash`, `.cp-shake`, `.sync-dot-glyph`. A sweep found none that leaves a wrong lasting look. That sweep covered 82 rule selectors in Chromium and real pages, but not live-combat states on real pages, not `/generator` element by element (node counts differed), not `/mobile` (it redirected), and no other browser.
6. **"Pixel-equal" is not literally met (accepted by Codex as a narrow exception).** `/player/levelup/Kyle` on desktop and `/cosmere/builder` on phone differ from `main` by at most 1/255 at rounded corners: 27 and 12 px earlier, 12 and 4 px in the latest repeat. The count varies between runs and I did not find the cause.
7. **`/cosmere/player` on desktop stays unattributed.** It varied branch to branch earlier (delta 91, 360 px). In the latest repeat the branch was identical 4 of 4 and `main` varied once (delta 28, 157 px). That doesn't establish a cause either way.
8. **One reduce rule touches an existing page, though the brief says no page changes.** `body .gen-content.rerolling {animation: none; opacity: 0.55}` is the generator reroll cue, accepted by Codex. It applies only under reduce, and the page's script removes the class on success and on error. Checked on the real `/generator` with the response held back: 0.55 and no animation while waiting, class gone afterwards, `gen-pulse` unchanged without reduce.
9. **More animated-property exceptions than the brief listed (accepted by Codex).** Beyond the 6.2 list, `ds.css` also transitions `visibility` and `overflow` on `.ds-collapse`, plus `height` and `content-visibility` on `::details-content`, and keeps the shimmer gradient. `tests/test_ds_motion.py` lists each in `EXCEPTIONS`.
10. **The motion audit only sees page load (accepted by Codex).** It records every animation from `animationstart` during the wait, including ones that finish, but not animations started later by a click or a live update. Those rely on the global block itself.
11. **Not fixed, not caused here:** `.hp-pin` at `system.css:5488` declares `var(--t-fast,160ms) ease`, which has two timing functions now and also had two before this change, because `--t-fast` already held `ease`. The declaration is invalid, so the pin never slides. The same applies to the unused `.notes-drawer`. Left for the screen package that owns them.

### What changed
- `static/css/system.css`: `--t-fast/base/slow` keep 120/180/280ms and now use `var(--ease-standard)`. The 1ms collapse is unchanged.
- `static/css/ds.css`: the global reduce block (duration 0.01ms, one iteration, `scroll-behavior: auto`, all `!important`) and `.ds-press`, `.ds-enter`, `.ds-panel`, `.ds-collapse`, `details.ds-details`, `.ds-chevron`, `.ds-skeleton`, `.ds-shimmer`, `.ds-edge-fade`, with `[hidden]` and forced-colours rules. No template or JS uses them.
- `.ds-chevron` is new and wasn't in the brief; `.ds-collapse` and `details.ds-details` need something to rotate.
- `.ds-enter` fills `backwards`, not `both`: a kept identity transform would trap `position: fixed` children. A static test guards it.
- `tools/ui_screens.py`: new `motion` command, and `shoot` now shares `_served` and `_pages` with it.
- New tests: `tests/test_ds_motion.py` and `tests/browser/test_ds_motion_browser.py`.

### Repeating the brief's scan
- `animation` combining `infinite` with `forwards` or `both`: still none.
- `animation` with `!important`: only `animation: none` in existing rules (`system.css:432,436,437`, `player_sheet.html:10358`), plus the new block.
- `animationend` waiters: none is on an infinite animation, so no waiter can fire for the first time. Each one removes a particle, pop or class. `checks-handle-glow` on `/gmscreen` is finite (3 iterations, now 1) and its class is removed sooner.
- `.trk-dmgpop` and `.trk-row-hit` already use `animation: none` under reduce, so their `animationend` never fires there. That is unchanged; the pop has a 2400ms backstop.
- Timers: durations did not change, so no timer depends on this package.

### Verification
All on the final tree unless a line says otherwise.
- Windows full suite: **5598 passed, 3 failed, 46 skipped, 1 xfailed, 11 errors**, 59:06. The failures are the known 3 (`test_inventory_seed.py::test_seed_authoring_recompiles_to_identical_bytes_without_local_paths`, `test_mistborn_adversaries.py::test_mistborn_adversary_statblock_and_tracker_add`, `test_dnd5e_render_and_db.py::test_db_matches_resolved_export`); the errors are the 11 PostgreSQL tests with no database. PostgreSQL has not run on this branch; CI will run it. Linux with gunicorn has not run either.
- An earlier full run was killed when a session ended. It had shown four failures in a row near the start, and it died before naming them. By position they fall in `tests/character_workflows/test_http.py`. That file then passed 3 times of 3 (17 passed each) and in the full run above. I did not reproduce them or find the cause.
- `pytest -q tests/test_ds_motion.py tests/test_ds_tokens.py tests/test_ui_screens_tool.py`: 69 passed.
- `tests/browser/test_ds_motion_browser.py`: 35 passed.
- The new real-builder test (no-preference and reduce) compares every computed property of the row and chip, bar `animation*`, before and after clicking rows 0, 1, 1. It passed 3 runs in a row. Adding `filter` or `box-shadow` to the chip, or `outline` to `.cp-shake`, each makes the reduce case fail. Codex's own reviewer test passes against the fix.
- A read-only recheck repeated the test about 70 times, including under CPU throttling, and all passed. That was before the test was strengthened. The exact CI browser command gave 67 passed in 6:49 on Windows, also before the strengthening; the full run above includes the final version.
- `motion` exits 0 with 0 problems: pf2e 36, account 32, cosmere 24, mistborn 24, dnd5e 18 pages. The gate fails when it should: a looping page fails (test). With the block's duration line deleted, `/gmscreen` and `/player/levelup/Kyle` exit 1 with 6 problems; with the iteration line deleted, exit 1 with 2 problems. Those two deletions ran before this fix.
- Inventory diff against `main`: 0 failures and 0 additions in all five profiles.
- Screenshots against `main`: the compare flagged five pages new to this run (phone player sheet, invites, Cosmere combat, Cosmere GM, 5e compendium). I repeated those and the other differing pages 4 times per side, alternating. For all of them the frames match `main`, or match one of `main`'s two alternating frames (desktop invites); the odd frames appear about evenly, on 4 pages for `main` only and 4 for the branch only. That fits noise but doesn't prove it. Only the two pages in limit 6 differ every time. Generators, `/admin/campaigns`, `/me`, `/join`, `/login` and the bestiary also differ main against main.
- Contrast: 112 pairs, 0 enforced failures, 25 reported below 3:1. Templates: all 111 parse.
- Mutation check on the first commit: 19 mutants, 18 caught at first; the survivor (inline chevron) led to a tighter test and now fails.
- Not checked: a signed-in page on production, since this isn't merged.

### Cost
One independent review (31 agents, about 3.7M subagent tokens): 27 findings, 13 confirmed, 14 refuted. 12 fixed, including one high (the audit missed anything finishing within its wait); the 13th is limit 11. Recheck of the locked-row fix: 10 read-only agents, about 1.38M subagent tokens. Limits 1 to 5 come from it.

### Design notes for later packages
Reviewers raised these and they were refuted as defects in DS-02 since no page uses the classes yet. The package that adopts each class should handle them:
- `.ds-press` also scales disabled buttons.
- `.ds-skeleton` is invisible in forced colours.
- `.ds-edge-fade` always fades 24px, even when the strip doesn't overflow.
- A closed `.ds-collapse` with a padded child isn't height 0.
- Check focus in nested collapses.

## Review

### Codex review (2026-10-30)

**Changes requested: one small required fix.** Reviewed `7af53175b177c254198a5ef204b3a09ca2114d8f` against the DS-02 brief and plan 6.1-6.5.

1. **[P2] The reduced-motion locked-row flash never returns to baseline** (`static/css/ds.css:182-185`). The real locked-row handler in `templates/_choice_picker.html:354-366` removes `cp-chip-flash` only to restart it on the next click, then leaves the class attached. Previously the finite 0.32s animation ended and the computed filter returned to `none`. With the new `animation: none; filter: brightness(1.6)`, each clicked reason chip remains highlighted until the list rerenders. This class is transient feedback, not a pending-state class like `rerolling`. Prefer dropping this special-case static filter: the locked reason text is already visible. If retaining feedback, its appearance must be bounded and reliably return to baseline without expanding the picker/paint workflow. Add a test using the real picker handler on the builder page and verify the appearance after the feedback interval; the copied, pre-classed span test cannot detect this lifecycle issue.

**Real builder reproduction:** a disposable server rendered `/player/builder` with the actual styles and picker code. Two deterministic level-99 options were supplied to the page's existing picker state, then clicked through the actual DOM/handler. After 600ms per click, normal mode returned both chips to `filter: none`. Under reduce, both remained `brightness(1.6)` with the flash class still attached. Reviewer regression: **1 failed in 13.79s**, exclusively on that reduced-motion appearance assertion. No character was published or saved.

The reviewer-only test is outside git at:
`C:/Users/Evan/.codex/visualizations/2026/10/13/01a1361b-2f40-7910-883c-5b656456edf7/gm_pf2e_mb04/.superpowers/review-ds02/test_review_picker.py`.
Set `DS_REVIEW_REPO` to the checkout being rechecked; it defaults to the reviewer's ignored exact-head export. It requires the existing Playwright browser dependency. This is the actual rendered page with fixture options, not a replacement click handler.

Requested rulings:

- **Pixels:** accept a narrow documented exception for the reported 27 desktop level-up pixels and 12 phone Cosmere-builder pixels, bounded to one channel level out of 255 at corners. Do not label that literal pixel equality or loosen the general compare gate. The intermittent Cosmere-player observation remains unattributed and must stay explicit in the report; this review does not establish it as either caused by DS-02 or harmless noise. It is not an additional required code finding on the available evidence. These numerical comparisons are the implementer's reported measurements, not fresh reviewer screenshot counts.
- **Existing-page reduce rules:** accept the generator's static 0.55 opacity: its real async handler removes `rerolling` on success and error, so the cue follows a pending operation and resets. Reject the locked-chip override until finding 1 is fixed.
- **Property/helper exceptions:** accept the shimmer sweep solely for small pending text, as explicitly named in 6.4; reduced motion and forced colours restore readable static text. Accept discrete visibility/overflow bookkeeping for disclosure focus/clipping. Accept native-details height/content-visibility because 6.4 explicitly calls for that interpolation with an immediate fallback. Accept `.ds-chevron` as the small helper for the rotation already specified in 6.4. No page adopts these new classes in this package.
- **Motion-audit limit:** the stated page-load-only scope is acceptable. It records animation starts during the load/wait and tests a finite animation finishing before the final read, plus current animations; it does not certify later clicks or live updates. Adopting packages still need interaction checks. The pre-existing invalid `.hp-pin` transition stays outside this fix.

Independent checks:

- `tests/test_ds_motion.py tests/test_ds_tokens.py tests/test_ui_screens_tool.py`: **69 passed in 9.09s**.
- `tests/browser/test_ds_motion_browser.py`, real Chromium: **33 passed in 23.69s**.
- The additional actual-builder regression above fails as described. Two earlier restricted-process browser attempts were interrupted without results and are not counted; the reported runs completed with browser-driver process access.
- Full suite and PostgreSQL were not repeated locally. The existing known failures remain documented; every current CI job, including PostgreSQL, must pass before any merge.

Only this task review and the queue are edited by Codex, left uncommitted. No implementation edits, commit or push. Return the one required fix to Claude for focused recheck before publication.
