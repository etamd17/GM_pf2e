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
- [ ] `tools/ui_screens.py compare` against `main`: pixel-equal in all five profiles. **Not literally met; see Report, limits 1 and 2.**
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
1. **"Pixel-equal" is not literally met.** `/player/levelup/Kyle` on desktop (27 px) and `/cosmere/builder` on phone (12 px) differ from `main` by at most 1/255 at rounded corners. Over 5 runs each: main against main 0, branch against branch 0, main against branch 1. It is invisible, but it is repeatable and I did not find the cause; isolating `ds.css` or the curve gave inconsistent results.
2. **One difference is unattributed.** `/cosmere/player` on desktop in the cosmere profile varied branch to branch (delta 91, 360 px) and was intermittent. It was identical main to main in 3 runs and branch against main differs only when the branch differs from itself. With so few runs I can't say whether the branch causes it or it is noise.
3. **Two reduce rules touch existing pages, though the brief says no page changes.** Without them the block would end a looping cue and remove it, leaving a state the user can't see:
   - `body .gen-content.rerolling {animation: none; opacity: 0.55}`: the generator reroll cue.
   - `body .cp-chip-flash {animation: none; filter: brightness(1.6)}`: the locked-row cue.
   Both apply only under reduce. The reroll cue was checked on the real `/generator` with the response held back: 0.55 and no animation while waiting, class gone afterwards, and `gen-pulse` unchanged without reduce. The locked-row cue was tested only with copied markup, not on the real builder page.
4. **More animated-property exceptions than the brief listed.** Beyond the 6.2 list, `ds.css` also transitions `visibility` and `overflow` on `.ds-collapse`, plus `height` and `content-visibility` on `::details-content`, and keeps the shimmer gradient. `tests/test_ds_motion.py` lists each in `EXCEPTIONS`. The acceptance line now says so. The reviewer should confirm them, and decision 2 (shimmer).
5. **The motion audit only sees page load.** It records every animation from `animationstart` during the wait, including ones that finish, but not animations started later by a click or a live update. Those rely on the global block itself.
6. **Not fixed, not caused here:** `.hp-pin` at `system.css:5488` declares `var(--t-fast,160ms) ease`, which has two timing functions now and also had two before this change, because `--t-fast` already held `ease`. The declaration is invalid, so the pin never slides. The same applies to the unused `.notes-drawer`. Left for the screen package that owns them.

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
- Windows full suite: **5596 passed, 3 failed, 46 skipped, 1 xfailed, 11 errors**, 1:01:22. The failures are the known 3 (`test_inventory_seed.py::test_seed_authoring_recompiles_to_identical_bytes_without_local_paths`, `test_mistborn_adversaries.py::test_mistborn_adversary_statblock_and_tracker_add`, `test_dnd5e_render_and_db.py::test_db_matches_resolved_export`); the errors are the 11 PostgreSQL tests with no database. PostgreSQL has not run on this branch; CI will run it.
- `pytest -q tests/test_ds_motion.py tests/test_ds_tokens.py tests/test_ui_screens_tool.py`: 69 passed.
- `tests/browser/test_ds_motion_browser.py`: 33 passed, on four consecutive runs.
- `motion` exits 0 with 0 problems: pf2e 36, account 32, cosmere 24, mistborn 24, dnd5e 18 pages.
- The gate fails when it should: a looping page fails (test). With the block's duration line deleted, `/gmscreen` and `/player/levelup/Kyle` exit 1 with 6 problems; with the iteration line deleted, exit 1 with 2 problems.
- Inventory diff against `main`: 0 failures and 0 additions in all five profiles.
- Screenshots against `main`: dnd5e pixel-equal. Generators, `/admin/campaigns`, `/me`, `/join`, `/login` and the bestiary also differ main against main, so those are noise. See limits 1 and 2 for the rest.
- Contrast: 112 pairs, 0 enforced failures, 25 reported below 3:1. Templates: all 111 parse.
- Mutation check: 19 mutants, 18 caught at first; the survivor (inline chevron) led to a tighter test and now fails.
- The motion, inventory and screenshot gates ran before the last test-only edits. `static/css` and the gate code have not changed since.
- Not checked: a signed-in page on production, since this isn't merged.

### Cost
One independent review (31 agents, about 3.7M subagent tokens): 27 findings, 13 confirmed, 14 refuted. 12 fixed, including one high (the audit missed anything finishing within its wait); the 13th is limit 6.

### Design notes for later packages
Reviewers raised these and they were refuted as defects in DS-02 since no page uses the classes yet. The package that adopts each class should handle them:
- `.ds-press` also scales disabled buttons.
- `.ds-skeleton` is invisible in forced colours.
- `.ds-edge-fade` always fades 24px, even when the strip doesn't overflow.
- A closed `.ds-collapse` with a padded child isn't height 0.
- Check focus in nested collapses.

## Review
