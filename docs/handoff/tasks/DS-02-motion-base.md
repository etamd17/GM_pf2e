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
- [ ] `tools/ui_screens.py compare` against `main`: pixel-equal in all five profiles.
- [ ] No `:active` rule on a legacy selector (`.btn`, `.tb`, `.act-btn`, `.d5-btn`, `.map-btn`), and no `.ds-*` motion class in any template (test).
- [ ] `ds.css` has no `transition: all`, no blur or `backdrop-filter`, no travel over 8px and no scale below 0.96, and animates only the 6.2 properties plus the two exceptions above (test).
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

## Review
