# DS-03: Global focus ring and hover gate

- **Owner:** codex
- **Branch:** `codex/ds-03-focus-ring` from current `origin/main`
- **Depends on:** DS-01, DS-02 (both merged)
- **Context:** `docs/design/DESIGN_DIRECTION.md` 5.0, 5.7, 1.2, section 10 row DS-03, section 11 gates
- **Before you start:** follow the "UI/UX work" design-resources rule from the user's global instructions: review https://beui.dev/, https://bencho.dev/, https://github.com/ui-layouts/uilayouts, https://github.com/superdesigndev/superdesign, https://github.com/marvkr/better-design, https://github.com/madebymustafa/design-taste and https://github.com/ibelick/motion-primitives, and take the look only. Run the full suite before and after.

## Goal
Every keyboard focus stop on the site shows a visible ring, and touch users stop seeing stuck tile and card hovers. Nothing else changes.

## Where this brief corrects the plan
Section 5.7 has been corrected in this same commit. These are the reasons:
1. **The base rule is not zero specificity.** `:focus-visible` sits outside `:where()`, so the rule is (0,1,0), and `ds.css` loads later than Tailwind. Measured in Chromium: it beats `.outline-none`, and loses to `.focus\:outline-none:focus` (0,2,0), inline styles and `!important`.
2. **"Fails on `outline-style: none`" passes on invisible rings.** Tailwind's utilities compile to `outline: 2px solid transparent`, so they compute as `solid`. A button at `opacity: 0` also passes. The check below replaces it.
3. **`.map-btn` has no suppression.** `map.css` has outline rules only at :126, :129 and :168. It needs no override, and DS-30e's map focus ring should be reconciled when that package is briefed.
4. **The `.tab-btn` citation moved:** `system.css:549-553` is now `:747-751`. Other stale citations outside 5.7 (the plan's lines 355, 363, 991, 1306) are left for the packages that use them; re-check any line number before citing it.
5. **"Zero `outline:none` survivors" can't be a literal grep,** because overrides leave the suppressed rule in place. It now means: every hit below maps to an override or a named exclusion.

## User amendment (2026-11-02)

The first real-page audit found that Quick Search does not contain keyboard focus: after its controls, Tab reaches controls behind the still-open dimming overlay. The outline is painted there, but the overlay lowers its measured contrast to about 1.3:1. This is a pre-existing behavior defect, not fixable through the permitted ring CSS without changing the overlay/focus workflow.

The user chose: **keep DS-03 CSS-only, document the focus-escape blocker and report the strict audit failures explicitly.** The command must continue to exit nonzero and list these stops; do not filter them, alter tab order or claim a zero-problem focus gate. The report must distinguish this approved scope limitation from in-scope ring defects. Focus containment remains a separate Claude task. The other acceptance gates remain required.

## Approach

### 1. Base rule in `ds.css`
The 5.7 rule, plus `:where(.ds-inset-focus):focus-visible`. Keep existing border and shadow cues; the ring adds to them.

### 2. Overrides in `ds.css`
Each carries a comment citing the line it beats. Re-check every line number first.

**Needs `!important`, because it beats `!important` in `system.css`:**
| Beats | Override |
|---|---|
| `system.css:750` `.tab-btn:focus-visible` | `body .tab-btn:focus-visible` |
| `system.css:576` `.panel-card input, select, textarea`, `.hp-input`, `.tinput`, `.tselect` on `:focus` | The same selectors with `body` and `:focus-visible`. This also covers `party_view.html:227`, `player_sheet.html:437`, `tracker.html:311,314`, and the panel-card rows at `_pc_sheet/_tab_inventory.html:24,28,32,36,118,119,123,158,176`. |

**Plain, no `!important`.** Use the page's own selector, prefixed so it wins:
- `dnd5e.css:52` (loads after `ds.css`, so specificity has to win)
- `_account_base.html:41` as `body input:focus-visible, body select:focus-visible`. `campaign_invites.html:9-12` already wins there and keeps its ring.
- `campaign_intro.html:198`, `cosmere_bestiary.html:20`, `generator.html:228`, `gmscreen.html:125`, `loot_ledger.html:202`, `system.css:5542`
- `notes.html:13`: needs the id, `#nt-area`
- `player_sheet.html:2264,2274,2430,2543`. :2430 `.class-cheatsheet summary` has no focus cue at all today.
- `tracker.html:186,203,526,644,780,790,799,851,863,1438`; :780 is (0,4,0)
- `tracker.html:927` `.trk .pd-row input`: **must stay non-important.** `tracker.html:4081` writes an inline red outline for invalid input, and `!important` would hide that error on focus.
- `player_sheet.html:2094` hi-contrast theme: `html[data-theme="hi-contrast"] button` beats the base rule with a faint outline. Add a `:focus-visible` rule at higher specificity.

**Tailwind, one rule:** `body .focus\:outline-none:focus-visible`. This covers `base.html:213`, `encounter_builder.html:32,38,48,53,56,58,212`, `player_view.html:355`, `_pc_sheet/_modals.html:12,46,78` and `_pc_sheet/_tab_magic.html:5,6,452`. Plain `outline-none` needs no override, because the base rule already wins: `player_builder.html` and `_choice_picker.html:31,337`.

**Inline, three markup edits:** delete `outline:none` from the `style` attributes at `base.html:561` (`#qs-input`), `:998` (`#chat-input`) and `:1161` (`#journal-input`). This avoids `!important` against an inline style. `tools/ui_inventory.py` records only `display` and `visibility` from `style`, so it should not flag them; confirm that, and that no test pins those strings.

**Controls that hide the ring through opacity.** An outline paints inside its element's opacity, so a ring on a hidden or dim control is invisible or too faint. Add `:focus-visible { opacity: 1 }` for:
- Hover-revealed buttons: `_pc_sheet/_tab_combat.html:154`, `_tab_feats.html:92`, `_tab_inventory.html:110,188`, `player_levelup.html:1927`.
- Dim at rest: `.gm-pin-btn` (`gmscreen.html:335`), `_pc_sheet/_tab_pets.html:22`.
- `player_view.html:233,235` set opacity inline, and the second needs it most (0.3, about 1.97:1). Use `!important` here, citing the inline style, or leave them and record why.

**Exclusions, with reasons:**
- `system.css:3290` `#sr-hp-input`: no element uses it.
- `app.py:10607,10621`: the legacy `/gm/login` pages load no `ds.css` and exist only in legacy mode.
- `static/mockups/encounter_d.html:130`: not a served page.
- Existing visible rings stay as they are (`system.css:628-631`, `:3642`, `:3762`, `:5314-5319`, `:5500`, `map.css:168`, `tracker.html:1556,1713`, the level-up `focus-visible:outline-amber-500` rows, and page rings on `account_home`, `campaign_invites`, `cosmere_*`, `generator`, `gmscreen`, `loot_ledger`, `party_view`, `tracker`). Their colours and offsets differ, but all are visible, so unifying them is outside this package. Change one only if the tab-through measures it below 3:1.

### 3. Hover gate
Wrap these in `@media (hover: hover) and (pointer: fine)` **where they already live**. `test_ds_motion.py` would refuse a literal lift in `ds.css`, and moving them risks the cascade.
- `system.css:3526`, `:3540`, `:1874` (`!important`, so wrap in place)
- `cosmere_gm.html:26,28`, `player_view.html:110`, `cosmere_pcs.html:14`, `cosmere_bestiary.html:13`
- `account_home.html:20,39`. Their lift is already neutralised at :66-67; leave that rule ungated, and keep `.account-home .cc-cover{height:34px;` byte-identical (`test_pf2e_party_campaign_ui.py:942`).

**Never gate anything that reveals a control or information:** `group-hover:opacity-100`, `group-hover:inline`, `player_sheet.html:2644-2645`, `.gm-pin-btn` (`gmscreen.html:336-337`, where the selector also carries `.pinned`), `.pc-tile:hover .pc-cta`.

**Left alone:** button and FAB lifts, colour-only card hovers, and `tw-*.css` (build output). `generator.html`, `gmscreen.html` and `notes.html` drop the first rule of `extra_head` because of a nested `<style>` (DS-10d), so don't put a gate first in those blocks.

### 4. Tooling and tests
- **`tools/ui_screens.py focus`:** a new command, reusing `_served` and `_pages`. On each route and viewport, press Tab through the page and for each stop record the control, outline width, colour and effective opacity (multiplying up its ancestors). Screenshot each control type. Exit 1 when any of these is true: width 0 or style `none`; a transparent colour; effective opacity below 1 unless the ring still reaches 3:1; contrast with the surface behind it below 3:1. Include a test where a suppressed control makes it fail. Tab order must not be changed by the tool or the CSS.
- **Beyond page load:** add steps for the choice picker (`#uni-modal`) on `/player/builder` and `/player/levelup`, `#qs-input`, and the sheet's hidden tabs and modals. Roving-tabindex widgets are reached one tab at a time; use arrow keys for the rest (`test_cosmere_sheet_tabs.py:352`, `test_gmscreen_polish.py:314`).
- **`tests/test_ds_motion.py` must change.** `test_important_only_in_the_global_reduced_motion_rule` (:79-82) and `test_no_rule_reaches_an_existing_page` (:85-99) reject this package's rules, and the second splits `:where(a, button, ...)` on its commas. Keep their purpose: an explicit allowlist of focus selectors, a separate raw-file check that every `!important` focus rule cites the line it beats, and no new animation or transition.
- **New browser tests** on real pages: a focused locked row and an override row show a ring on `/player/builder`, with `.cp-opt-locked` still at opacity 0.55; a hover-revealed button shows at full opacity and its ring on focus; the invalid persistent-damage input stays red on focus; a gated tile doesn't lift under `hover: none` and does with a mouse.

### Traps
- **The class freeze blocks `.ds-inset-focus` on most existing elements.** Anything with `role`, `aria-*` or `tabindex` keeps its full class string in the inventory, so a new class counts as a removal. Inline-script markup can't take one either, because the script's hash changes. Use selector rules.
- **Inset only where clipping is measured.** Candidates: `.nav-links` (`base.html:45`), `.tab-bar-scroll` (`player_sheet.html:2041,3127`), `overflow-hidden` panels (`_tab_magic.html:18`, `_tab_feats.html:33,88`). An inset ring on a filled control can drop to about 1:1 (`map.css:66`, `tracker.html:222-226`, `generator.html:274`).
- **Tracker scroll regions** have rings at `tracker.html:1556,1713` (0,4,1). They stay as they are unless clipped.
- **Tested CSS blocks stay untouched:** `map.css:8,9,64,67,93,126,129,168`. `test_map_signals.py` finds the first `.map-tool {` and `.map-btn {`, and `map.css` bans gilt and px radii file-wide, so put new rules in `ds.css`.
- **Parchment theme:** static reading says cream doesn't reach body descendants (`system.css:369`, `:1324-1328`). Check it in the tab-through; on any cream surface use `--ruby-300` or ink.
- **Firefox and Safari** aren't covered by these tools. Say so in the report.

## Visible changes to expect
- Text inputs match `:focus-visible` on a mouse click as well, so clicking into one now shows the ring.
- `join`, `login`, `new_campaign`, `register` and `setup` autofocus an input, so the ring shows on load and `compare` will flag those pages. Those are expected; anything else that differs at rest needs an explanation.
- `.tab-btn` and `.hp-input` etc. keep their existing cues as well as the ring.

## Keyboard-focus follow-up from DS-02
DS-03 gives locked picker rows a visible ring: calculated at 3.66:1 on the builder and 3.74:1 on level-up at the row's 0.55 opacity; the ring itself was seen in a scratch-page screenshot, not on the live app. No markup or script change is needed. The rest stays with **DS-15c** (visible row focus restyle) and **DS-09** (ARIA): `aria-disabled` rather than `disabled`, all reasons as visible text (the chip shows `reasons[0]` only and the rest are in `title`), and text feedback on Enter under reduced motion. `.cp-opt-override` rows get the ring the same way. The hidden remove controls at `player_levelup.html:1821` and `player_builder.html:1356` can't be reached by keyboard, and a ring can't fix that; record them in appendix A with a `BUG-UI-nn` id.

## Acceptance
- [ ] Section 11 gates, with these specifics:
- [ ] `tools/ui_screens.py focus` exits 0 on all five profiles at both viewports; screenshots of each focused control type attached to the PR; a suppressed control makes it fail (test).
- [x] Every suppression hit is mapped to an override or a named exclusion (grep for `outline`, `outline-none` and `focus:outline-none` across `static/css`, `templates` and `app.py`, listed in the report).
- [x] Inventory diff against `main`: 0 failures on all five profiles.
- [x] `test_map_signals.py`, `test_map_keyboard.py`, `test_map_gm_loop.py`, `test_ds_motion.py`, `test_ds_tokens.py`, `test_ui_inventory_tool.py` green; no tested CSS block edited.
- [x] `motion` still exits 0 on all five profiles. No new animation or transition.
- [x] Screenshots at rest against `main` differ only as listed above and by the accepted DS-02 corner pixels; any other difference is repeated and reported as attributed or not.
- [x] Contrast tool: 0 enforced failures. Templates parse.
- [x] Windows full suite: no failures beyond the known 3, plus the 11 PostgreSQL errors when no database is set.

The strict focus gate remains open under the user's CSS-only scope decision; other failures are reported separately and are not blanket-waived. The screenshot wording is restored verbatim; the retained `437f3d…` `/me` repeat, supported by the later invariance proofs, and its unattributed tablet raster variation are recorded below. The final corrected-source Windows run is complete, with its exact result and error-ID comparison below. Claude completed native 200% browser zoom during review: 44 GM views in PF2e, account, Stormlight and D&D, with no sideways page scroll; Mistborn and player views were not covered. That prior check is not a new native capture of these corrected rings. This checklist does not assert full site accessibility conformance.

## Out of scope / do not touch
- ARIA additions (DS-09), and `disabled` or tab order changes.
- `.ds-*` component styling (DS-04 onward), the VTT (DS-08).
- Picker scripts and any inline script (inventory hash).
- Restyling existing rings, or reconciling their colours.
- The level-up rail overlap (separate session).
- The missing touch mitigation on builder and level-up (`player_levelup.html:1927`, tooltips at `player_builder.html:1223`, `player_levelup.html:1424`): existing defect, record in appendix A.

## Report

Accepted by Claude on 2026-11-02, including the Chronicle focus geometry and documented measurement limits. The three required test-only follow-ups are complete: both changed test files passed 76 tests, and every accepted runtime file plus all fourteen stylesheets stayed byte-identical. The branch is `codex/ds-03-focus-ring`, based on merged brief commit `12467c9d4f9f2c5750622cfb54a65b24129210f8`. The accepted stylesheet remains `e13b9f5d…`. Earlier verification records 588 focused passes and a Windows full run with 5,625 passed, 139 skipped, one xfailed, zero failed tests and the same eleven missing-PostgreSQL setup errors. Those historical fifteen-file hashes stayed unchanged during that full run; only the two test files were subsequently strengthened as required. The implementation/review history is retained below. The strict focus audit remains red with all other failures and coverage gaps reported; the full-stroke tool follow-up is DS-03t. Publication and merge follow PROTOCOL step 6, with every current CI job required to succeed.

### Changes

- Added the shared 2px focus ring, the future inset variant, and the brief's source-cited cascade overrides in `ds.css`. Existing borders/shadows remain. Invalid persistent-damage input keeps its inline red outline. Only the explicitly required overrides use `!important`.
- Removed only the three inline `outline:none` declarations from Quick Search, chat and journal. Hover-revealed and dim controls become opaque on keyboard focus. The hover gates wrap the existing tile/card rules in place.
- Real-page paint checks also found geometry cases beyond the initial suppression list: empty stretched GM tile links paint their indicator on `::after`; roster card links, sheet tabs, picker options and the Cosmere builder glossary need an inset inside clipping containers; sheet pins, Whisper and Cosmere cross-references need offsets that avoid neighboring text. Active sheet tabs need additional clearance from their existing gold underline. Segmented World/Rules buttons need an inset and selected buttons use `--c-on-accent` so the ring contrasts with their filled accent. Those initial rules change the focus indicator only; the later phone Chronicle geometry exception is declared separately below for review.
- Added `ui_screens.py focus` and a separate `tools/ui_focus.py`: real Tab traversal, explicit view-only picker/search/tab/modal states, arrow navigation for genuine roving widgets, effective-opacity metadata, and paired focused/outline-hidden screenshot measurements. Incomplete traversal, missing required states and measurement errors remain failures. It neither changes tab order nor silently filters the reported stops.
- Replaced the DS-02 test's blanket selector assumptions with an exact focus-selector allowlist and a separate source-citation check for important rules. New tests exercise actual rendered pages and the audit's failure cases.

No product JavaScript, IDs, classes, handlers, form payloads, ARIA, tab indices, rules, routes or persistence changed. `map.css` and generated Tailwind outputs are untouched. This is not a VTT package.

New files remain untracked for review: `tools/ui_focus.py`, `tests/test_ds_focus.py`, `tests/test_ui_focus_tool.py`, `tests/browser/test_ds_focus_browser.py` and `tests/browser/test_ui_focus_audit_browser.py`. Read these as well as the tracked diff.

### Initial verification (before review corrections)

Evidence root (local and ignored): `gm_pf2e_mb04/.superpowers/ds03/`. The disposable fixtures use reviewed private Cosmere release 1.1.2 and the existing read-only D&D data. No private data, screenshots or test dependency installs are in the public diff. No production configuration or data was changed.

| Check | Actual result |
|---|---|
| Full untouched baseline | 5,508 passed, 139 skipped, 1 xfailed, 11 PostgreSQL errors (no database), 11 warnings in 8,164.43s; no test failures. The brief's three previously known failures did not reproduce in this environment |
| Full after implementation | 5,572 passed, 139 skipped, 1 xfailed, 11 warnings, the same 11 PostgreSQL errors, no test failures in 4,044.42s. This run precedes the final three focus-offset corrections; see the validation boundary below |
| HTML inventory before/after | PF2e 18 routes, account 16, Stormlight 12, Mistborn 12, D&D 9; every profile has 0 failures, 0 additions, 0 unchecked routes |
| Reduced-motion audit | PF2e 36 page views, account 32, Stormlight 24, Mistborn 24, D&D 18; 0 problems in every profile |
| Contrast tool | 112 pairs; 0 enforced failures; 25 informational below-3:1 pairs remain reported |
| Template parsing | 111 templates parsed |
| Focused selection at the initial implementation | 543 passed in 166.86s: the required map files, focus/motion/tokens, inventory and screenshot tools, both new browser files, the existing DS-02 browser file, inline-handler escaping, route policy, typography and asset versioning |
| At-rest captures | 335 unique route/role/viewport states per tree, two captures each: 670 before and 670 after; 375, 390, 768, 1024 and 1440 CSS px widths |
| Axe | 0 new nodes on account, Stormlight, Mistborn and D&D; the initial unseeded PF2e generator had 1 differing contrast node, and a repeat with identical seed produced 0 new nodes |
| Representative reflow/default text/forced colors | 15 captures per tree across all five profiles; 320px viewport has no horizontal page scroll on the sampled GM/login routes; a real 32px default-font preference was confirmed in an unstyled iframe; forced-color focus uses a visible 2px system-color outline |
| Live repaint | Two independent disposable browser contexts; real damage/heal route changed the other context's Kyle sheet HP 138 → 137 → 138 without reloading |
| Keyboard-only navigation | 12 real Tab presses reached the primary GM Screen tile link, its painted ring measured 9.24:1, and Enter opened the actual GM Screen |

Commands used (run from each named checkout, with the isolated Python environment and private-data paths described above):

```text
python -m pytest -q -p no:cacheprovider --durations=15
python -m pytest -q -p no:cacheprovider tests/test_asset_versioning.py tests/test_ds_focus.py tests/test_ds_motion.py tests/test_ds_tokens.py tests/test_inline_handler_escaping.py tests/test_map_gm_loop.py tests/test_map_keyboard.py tests/test_map_signals.py tests/test_route_policy_inventory.py tests/test_typography_system.py tests/test_ui_focus_tool.py tests/test_ui_inventory_tool.py tests/test_ui_screens_tool.py tests/browser/test_ds_focus_browser.py tests/browser/test_ds_motion_browser.py tests/browser/test_ui_focus_audit_browser.py
python tools/check_templates.py
python tools/ui_inventory.py capture --profile PROFILE --out CAPTURE.json --allow-status /gm/rest=302
python tools/ui_inventory.py diff BEFORE.json AFTER.json
python tools/ui_screens.py motion --profile PROFILE
python tools/ui_screens.py focus --profile PROFILE --out OUTPUT
python tools/ui_screens.py focus --profile pf2e --routes /player/sheet/Kyle --out OUTPUT
git diff --check
```

`PROFILE` is each of `pf2e`, `account`, `cosmere`, `mistborn`, `dnd5e`. The full command produced the two completed full-suite results above; the second command produced the final 543 passes. Browser-file arguments are grouped deliberately to preserve this repository's fixture discovery. Screenshot, axe and representative usability helpers, exact invocations and raw logs are retained with the local evidence index; they are not added to the public repository.

The default-font check does not double existing explicitly pixel-sized text. Native 200% browser zoom was initially unverified because the temporary full-Chromium profile failed to launch (`spawn UNKNOWN`); Claude subsequently completed it in installed Chrome (44 GM views; see the retained review). Firefox and Safari remain uncovered. The representative reflow captures do not establish that every existing page is free of layout defects.

### Initial strict focus gate and follow-ups

The strict focus command is **not green**. The user amendment above keeps Quick Search containment outside this CSS-only package and requires the command to retain and report its failures. Appendix A records hidden remove controls (BUG-UI-17), touch discovery gaps (18), Quick Search focus escape (19), off-screen focusable closed drawers/HP pin (20), overlapping fixed controls (21), other overlay focus escape (22), the existing gains-rail task in another chat (23), and tracker dropdown/phone-panel overlap (24). The Quick Search exception is not a blanket approval of the other failures. None is concealed by a CSS z-index or tab-order change.

The authoritative final evidence is a **route-level composite**, not another atomic five-profile run: the two sheet views come from `evidence/focus-final-sheet/` after the Whisper correction, the other 34 PF2e views from `evidence/focus-final-pf2e/`, and the four other profiles from `evidence/focus-complete/`. The later PF2e selectors do not match those other profiles' captured controls. All raw results and historical captures remain unchanged; the evidence index links every source and its hash.

| Profile | Page views | Focus observations | Failed observations | Diagnostics | Quick Search underlay | Other failed observations |
|---|---:|---:|---:|---:|---:|---:|
| PF2e | 36 | 5,126 | 1,305 | 1,306 | 788 | 517 |
| Account | 32 | 682 | 234 | 234 | 234 | 0 |
| Stormlight | 24 | 2,116 | 992 | 992 | 986 | 6 |
| Mistborn | 24 | 1,804 | 837 | 837 | 830 | 7 |
| D&D | 18 | 1,364 | 630 | 630 | 630 | 0 |
| **Total** | **134** | **11,092** | **3,998** | **3,999** | **3,468** | **530** |

Every profile retains exit 1; there are zero extra required-state/recipe errors. These are repeated observations across states and viewports, not unique defect counts. Quick Search's own input and pills have zero failures; its 3,468 failures are underlay controls. The other 530 remain reported for review, not waived by that decision. Across all states, 93 observations are wholly outside the viewport (84 closed Checks controls, nine HP pins). One covered phone level-up locked row retains its partial-opacity uncertainty and unmeasured-ring diagnostic.

All 63 remaining load-state failures were individually classified: 16 wholly outside the viewport, two partly outside closed Checks bodies, seven Checks-handle overlaps, 36 known fixed-widget intersections, one monster dropdown over the next control and one neighboring encounter-panel overlap. Zero remain unclassified. Rectangle intersections support the overlap diagnosis but do not prove every obstructed pixel; paired images remain available. No unresolved pure ring-CSS load defect was identified in that inspected set. This does **not** mean zero accessibility failures or blanket acceptance of the expanded-state failures. The full per-record classification is in local `AUTHORITATIVE_FINAL_FOCUS.md` and `.json`.

The audit is a sparse painted-core check, not a pixel-complete WCAG conformance proof. `missingSamples` is retained as a coverage diagnostic. It covers the fixture/default Tab cycles plus Quick Search, the two pickers, visible sheet tabs, equipment/dice/conditions, and genuine roving widgets. It does not activate every ordinary party panel, every native disclosure, daily preparation, whisper, or every action-dependent dialog.

The full runs used identical source/test bytes throughout each run (hashes retained). Three narrow focus-offset corrections—active sheet tabs, picker options and Whisper—were applied afterwards. The first two's strengthened 12-case rendered selection failed on the old CSS, then passed (12 passed, 17 deselected in 25.35s). Whisper has two additional native-Tab regressions. The final 543-test selection passed after all three corrections, and affected-route strict captures completed afterwards. A complete full-suite run was **not** repeated after those three focus-only corrections. Final source hashes and the before/after validation boundaries are retained.

PostgreSQL and Linux gunicorn are not verified locally. Every current-head CI job, including PostgreSQL, must pass after review and publication before merge.

### Focused correction evidence

- The real builder's locked row initially had Tailwind's transparent `solid 2px` outline. Locked and override rows now have the ring; the locked row remains at opacity 0.55.
- Actual stretched tile, roster-card, compact-pin and phone-tab regressions failed before their corresponding geometry corrections. Phone tab clipping reproduced in all four tested theme states. Selected and inactive Cosmere World/Rules controls, the glossary and the Opportunity cross-reference now have paired painted-ring checks as well as unchanged markup/glyph/layout checks.
- The selected segmented option needs on-accent ink: simply insetting the light ring onto the accent fill measured 1.58:1. The corrected controls measured at least 6.31:1 in Stormlight and 12.31:1 in Mistborn. Glossary measurements were 11.36:1; Opportunity measurements were 11.53:1. These are targeted checks, not whole-site minimums.
- Opportunity's original 2.75:1 failure depends on the real Inter font's neighboring punctuation geometry. That actual-font red proof is retained. Portable CI tests block the font CDN, check actual paint and unchanged glyph/layout, and enforce the zero-offset clearance policy; they do not require external font delivery.
- An independent probe caught a false pass in the new tool: it discarded unchanged samples where the ring and surface were the same RGB. The corrected tool reports that known portion as 1:1; ambiguous partial-opacity paint fails with uncertainty. Small circles, stretched pseudo indicators, finite-transition settlement, genuine roving navigation, hidden-tab recipes and the standalone CLI entrypoint also have regressions. Final tool-only checks: 20 unit/tool tests and 27 browser tests passed.
- Full collection exposed a defect in the first new browser fixture: it reused a process in which the app was already imported. A focused pre-import reproduction gave 11 setup errors; the isolated subprocess fixture fixed it. Before the late offsets, the portable CSS browser cohort gave 23 passed, 13 deselected, and the corresponding real-font cohort also gave 23 passed. The final portable cohort gives 31 passed, 13 deselected.
- The final capture then exposed two states those earlier tests missed: active tab rings crossed their existing gold underline, and option rings at the nested picker scroll edge were clipped. The strengthened tests exercise actual selected and inactive tabs in four theme states on phone/desktop, plus eligible/locked/override renderer classes on both picker hosts. They prove the row is flush with the real scroll edge and preserve eligibility classes, opacity, glyphs, dimensions and the tab underline. Active tabs use a -4px inset; picker rows use -2px. The known gains-rail cover remains separate and unfixed.
- During that test upgrade, three initial failures were fixture alignment during the modal's opening scale transition. Waiting for the existing finite transition fixed setup; the corrected red run then gave 12 genuine painted-ring failures in 19.05s. Both initial and corrected logs remain available. The CSS was not weakened to accommodate a test race.
- The last sheet capture reproduced a desktop Whisper ring crossing the normal Staff damage text: 1.82:1 before the correction. A -2px inset now measures 8.733:1 on desktop and 8.871:1 on phone, with zero missing/uncertain samples. Two native-Tab regressions preserve the button rectangle, classes, opacity and pen glyph. The portable tests do not depend on external fonts; the actual-font failing pair is retained separately.

The first larger focused invocation recorded 2 Mistborn failures before any UI assertion because the OS assigned Chromium-blocked port 6566, plus 31 DS-02 setup errors. Exact collection-only A/B checks reproduced the latter when browser file arguments were separated by root-file arguments; grouping the browser arguments restored fixture discovery. The grouped 535-test recheck passed, without product changes or unsafe-port browser flags. Initial failure logs are retained, not counted as passes. The interrupted early full runs have no success summary and are not counted as completed gates.

### At-rest screenshot attribution

The strict first-pair comparison exits 1 for PF2e, account, Stormlight and Mistborn, and 0 for D&D. Allowing an exact match to either retained before frame gives PF2e 83/90, account 51/80, Stormlight 54/60, Mistborn 52/60 and D&D 45/45 matching states. No comparison tolerance was widened.

| Difference | Evidence and disposition |
|---|---|
| Account login, join, register and new-campaign forms at all five viewports | Expected autofocus rings; 20 states. Setup is not in this owner-already-exists fixture and is not claimed as captured. |
| Generators at all five viewports in PF2e, Stormlight and Mistborn | Fresh random card contents on every GET, varying within both unchanged trees; 15 states. There was no fixed-seed screenshot equality run. The fixed-seed axe recheck is separate evidence. |
| Account `/me` and admin campaigns | Backup timestamps and random campaign IDs differ. Original crops and source identify the data changes; a stable-fixture admin repeat gave 16 identical frames across both stylesheet modes. At this initial matrix stage no controlled `/me` repeat had yet been completed; the later stable repeats are recorded below. |
| Cosmere GM Screen at 768px; Mistborn builder at 375, 390 and 1440px; PF2e sheet at 390px | Each disputed case had 16 controlled frames using baseline/current CSS. Every case had an exact cross-style match, with identical sampled geometry/styles and `BODY` unfocused. Both stylesheet modes reproduced the original variants. A persistent branch-CSS difference did not reproduce; the underlying Chromium raster mechanisms remain unknown. |
| PF2e sheet at 375px | The best original cross-pair differs by 2 pixels, max channel delta 5, and both same-tree repeats vary. An additional controlled 16-frame repeat gave identical old/new stylesheet frames, reproducing original baseline frame 2 in all 16. The original branch variant was not reproduced; its renderer mechanism remains unknown. The original strict difference is retained and is **not** called the DS-02 1/255 corner waiver. |

The at-rest matrix preceded the late focus-only inset corrections. These selectors do not match the unfocused captured controls; the later targeted captures exercise the focused appearance. Six controlled renderer cases now have exact cross-style repeats, 16 frames per case. Detailed filenames, hashes and limitations are in ignored `screenshot-comparison.json` and `visual-investigation/FINDINGS.md`. None of the unexplained raster observations is presented as a proved rendering cause or literal original-image equality.


### Complete baseline suppression mapping

Generated from the untouched `12467c9d` tree. Line numbers below refer to that tree. Every grep hit is listed; none is treated as removed merely because an override was added.

| Baseline source | Disposition |
|---|---|
| `app.py:10607` | excluded: legacy-open /gm/login standalone HTML, no ds.css |
| `app.py:10621` | excluded: password legacy /gm/login standalone HTML, no ds.css |
| `static/css/dnd5e.css:52` | ds.css: body .d5-input/.d5-select/.d5-textarea:focus-visible |
| `static/css/system.css:576` | ds.css: body .panel-card input/select/textarea, .hp-input, .tinput, .tselect :focus-visible, important |
| `static/css/system.css:750` | ds.css: body .tab-btn:focus-visible, important |
| `static/css/system.css:3290` | excluded: unused #sr-hp-input (no element creates it) |
| `static/css/system.css:5542` | ds.css: body .chron-doc-row__rename:focus-visible |
| `static/css/tw-base.css:2092` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-base.css:2357` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-campaign_intro.css:2066` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-campaign_intro.css:2319` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-mobile_combat.css:1951` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-mobile_combat.css:2139` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_builder.css:1951` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_builder.css:2139` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_levelup.css:1951` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_levelup.css:2139` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_sheet.css:1951` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_sheet.css:2139` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_view.css:2073` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `static/css/tw-player_view.css:2326` | generated Tailwind utility; actual consumer class mapped below; build output unchanged |
| `templates/_account_base.html:41` | ds.css: body input/select:focus-visible; campaign_invites visible ring remains more specific |
| `templates/_choice_picker.html:31` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_choice_picker.html:337` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_modals.html:12` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_modals.html:46` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_modals.html:78` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_tab_inventory.html:24` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:28` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:32` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:36` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:118` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:119` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:123` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:158` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/_pc_sheet/_tab_inventory.html:176` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_tab_magic.html:5` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_tab_magic.html:6` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/_pc_sheet/_tab_magic.html:452` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/base.html:213` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/base.html:561` | inline outline:none removed; shared base rule |
| `templates/base.html:998` | inline outline:none removed; shared base rule |
| `templates/base.html:1161` | inline outline:none removed; shared base rule |
| `templates/campaign_intro.html:198` | ds.css: body .field-input:focus-visible |
| `templates/cosmere_bestiary.html:20` | ds.css: body .cb-search:focus-visible |
| `templates/encounter_builder.html:32` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:38` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:48` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:53` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:56` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:58` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/encounter_builder.html:212` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/generator.html:228` | ds.css: body .gen-toolbar-input:focus-visible |
| `templates/gmscreen.html:125` | ds.css: body .gm-search-input:focus-visible |
| `templates/loot_ledger.html:202` | ds.css: body .form-group input/select:focus-visible |
| `templates/notes.html:13` | ds.css: body #nt-area:focus-visible |
| `templates/party_view.html:227` | ds.css: body .hp-input:focus-visible, important |
| `templates/player_builder.html:174` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:185` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:198` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:231` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:341` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:390` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:407` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:422` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:429` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:1017` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:1025` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_builder.html:1043` | shared :where(...):focus-visible wins plain outline-none by equal specificity and later load |
| `templates/player_sheet.html:437` | ds.css: body .hp-input:focus-visible, important |
| `templates/player_sheet.html:2264` | ds.css: body .feat-note-input:focus-visible |
| `templates/player_sheet.html:2274` | ds.css: body .inv-filter:focus-visible |
| `templates/player_sheet.html:2430` | ds.css: body .class-cheatsheet summary:focus-visible |
| `templates/player_sheet.html:2543` | ds.css: body .whisper-panel textarea:focus-visible |
| `templates/player_view.html:355` | ds.css: body .focus\:outline-none:focus-visible |
| `templates/tracker.html:186` | ds.css: body .trk .ts-search input:focus-visible |
| `templates/tracker.html:203` | ds.css: body .trk .ts-select:focus-visible |
| `templates/tracker.html:311` | ds.css: body .tinput:focus-visible (important) |
| `templates/tracker.html:314` | ds.css: body .tselect:focus-visible (important) |
| `templates/tracker.html:526` | ds.css: body .trk .c-init input:focus-visible |
| `templates/tracker.html:644` | ds.css: body .trk .epithet-in:focus-visible |
| `templates/tracker.html:780` | ds.css: body .trk .c-hp-quick .qhp-in:focus-visible |
| `templates/tracker.html:790` | ds.css: body .trk .c-hp-ctrl input:focus-visible |
| `templates/tracker.html:799` | ds.css: body .trk .c-hp-ctrl select:focus-visible |
| `templates/tracker.html:851` | ds.css: body .trk .cp-add select:focus-visible |
| `templates/tracker.html:863` | ds.css: body .trk .cp-add input[type="number"]:focus-visible |
| `templates/tracker.html:927` | ds.css: body .trk .pd-row input (nonimportant, inline invalid-red wins):focus-visible |
| `templates/tracker.html:1438` | ds.css: body .trk .tactics-area:focus-visible |

Additional exclusion: `static/mockups/encounter_d.html:130` is not served and is outside the required static/css/templates/app.py grep.
Existing positive-outline rules are not suppressions; their original declarations remain in source. Measured clipping repairs and the Chronicle exception override only the named focus paint/geometry as reported. The invalid-PD inline red still wins unchanged.

88 matching source lines; 0 unmapped.

New intentional suppression in the corrected tree: `static/css/ds.css:94` removes the empty `.tile__link` anchor's duplicate outline in favor of the stretched `::after` indicator immediately below it. This is a named replacement cue, not a missing ring; the real tile regression checks its perimeter and unchanged interior.

## Review (Claude, 2026-11-02): back to in-progress

Reviewed the tracked diff, the five new files and this report, with five read-only review agents plus an adversarial check of each finding, and ran the native 200% zoom check myself. The cascade work is sound: in a 20-row sample every cited override exists and outranks its target, and no markup, handler, ID or ARIA change was found. The findings below are required unless marked optional.

### Native 200% browser zoom (done)

Ran it in installed Google Chrome, headed, with the profile's real default page zoom set to 200% (`partition.default_zoom_level` = log 2 / log 1.2). Every page reported `devicePixelRatio` 2 and a 640 CSS px viewport, so the zoom was really applied. Bundled Playwright Chromium gave the same `spawn UNKNOWN` you hit when headed, and it ignores the zoom preference when headless.

- Covered 44 page views: PF2e 11 routes, account 12, Stormlight 12, D&D 9, all as GM. Up to 60 Tab stops per page; screenshots at rest and at stops 2, 8 and 20. Mistborn and the player role were not run.
- No page scrolls sideways at 200% (`scrollWidth - clientWidth` = 0 everywhere).
- Rings are drawn at 200% exactly as at 100%. The one real defect found is finding 1, and it shows at both zoom levels.
- Two stop-20 screenshots came out blank (`/gm` and `/gm/loot`). `/gm` was re-run on the untouched baseline tree and came out blank there too, so DS-03 did not cause it.
- My script's "clipped by an overflow ancestor" check ignores inset rings, so its other hits (`.wiz-tab`, `.cw-btn`, the builder `.nav-item`, the roster card links) were not confirmed and are not findings.

### Findings

1. **Required. The top-nav ring is clipped on every page that extends `base.html`.** `.nav-links { overflow-x:auto }` (`base.html:45`) makes the vertical overflow `auto` as well, so the outward 2px ring on the nav links, the search button and the shortcut chip loses its top and bottom edges. It appears as two gold brackets. Seen at 100% and 200% on every profile; it is the first thing a keyboard user tabs to. Fix: an inset ring scoped to `.nav-links` (for example `body .nav-links :where(a, button):focus-visible { outline-offset: -2px; }`), the same treatment as the sheet tabs. Add a painted-ring regression that requires all four edges.
2. **Required. False pass in `tools/ui_focus.py:203-214`.** A sample at 1.05:1 or below counts as 1:1 only when both frames are exactly the outline RGB. Anything else lands in `missing`, which `problems()` never reads. So a ring on a surface one RGB step from the ring colour passes on its remaining edges. Reproduced: ring `#e0b65a` on a gradient half `#dfb65a`, half `#111` gives contrast 9.90, 5 missing samples, 0 same-colour samples and no problems. Make any low-contrast sample that the control's geometry does not prove clipped a failure or an uncertainty, and add the near-match case beside `test_ring_matching_half_the_surface_is_not_a_clean_contrast_pass`. Finding 1 is the kind of defect this lets through, so re-run the strict audit afterwards and report the new numbers.
3. **Required. Stray second ring on static tile links.** On tiles where `.tile__link` stays `position: static` (the main-run "Open GM Screen" tile, `gm_hub.html:250`; Cosmere hub `cosmere_gm.html:196,235`), the base rule still outlines the empty inline anchor. A gold box of about 6x24px appears over the glyph, beside the intended `::after` ring. Confirmed on the real `/gm`: 132 changed pixels inside the tile, away from the edge band. Fix: `body .tile__link:focus-visible { outline: none; }` beside the `::after` rule, and a test that samples inside the tile as well as its edge.
4. **Required. The screenshot acceptance box was reworded and then ticked.** The brief's gate says any other difference is "repeated and reported as attributed or not"; the report says the account `/me` difference was not repeated. Restore the brief's wording, and either repeat `/me` under a stable fixture or leave the box unticked with that reason.
5. **Required. Run the Windows full suite again on the final CSS.** The last full run predates the three inset corrections, and findings 1-3 change the CSS again. Report the real numbers.
6. **Small fixes.**
   - (a) `tools/ui_focus.py:90`: a zero-size control with no radius divides 0 by 0, and `focus.json` gets bare `NaN` tokens, which `JSON.parse` and `jq` reject. Guard the division and write with `allow_nan=False`.
   - (b) `tests/browser/test_ds_focus_browser.py:397`: the sheet-pin test says "without touching neighbouring text" but checks neither `missingSamples == 0` nor the offset. Add both, as the Whisper test does.
7. **Report text.**
   - (a) About 40 rows of the suppression table say "panel-card fields also get important override" for templates that contain no `.panel-card`. Drop that clause where it does not apply.
   - (b) The `tracker.html:311` row names `body .trk .tinput`, but `ds.css` ships `body .tinput:focus-visible`. Correct it.
   - (c) BUG-UI-21 in `DESIGN_DIRECTION.md` cites `base.html:179-187` for the sheet-pin and Dice overlap. `player_sheet.html` does not extend `base.html`: its Dice widget is `_pc_sheet/_modals.html:25`, placed on phones by `player_sheet.html:2053`.

### Not findings

Each of these was raised by a review agent and rejected on checking:

- CSS-versus-device pixel sampling: the clip handles it.
- The `out` path and `chdir` order.
- Linux fallback fonts: the tests check geometry and paint, not exact glyphs.
- Network dependence: Sortable failing to load does not affect the asserted rings.
- `str.index` in the hover-gate test: adequate for the current files.
- The full suite not being rerun: the report says so plainly, and it is now finding 5.
- Owner names in the appendix that are not yet queue rows: they are follow-ups, not claims.

The approved Quick Search scope exception stands; nothing here asks for a z-index or tab-order change.

## Review corrections (Codex, 2026-11-02)

The original review corrections and additional measured clipping fixes are complete. Expanded-state review prompted stopping an earlier full run without a summary; it is not counted as a completed gate. The final corrected-source Windows run is now complete and compared below. All changes remain uncommitted and unpushed. The source was reopened only for narrow ring CSS and associated tests, then frozen before the final gates. Earlier evidence is retained as history, not substituted for these final-source runs.

The pre-expanded snapshot is preserved across 15 source/test files (`ds.css` SHA-256 `444a3b21510fcffe0cfcf310c8e29221a9b2fa6e1f3d0320ec141e5b82a23415`). Its combined selection passed **576 tests in 217.88s**, with every hash unchanged. Owner selections on that snapshot: CSS browser **55 passed, 13 deselected in 127.75s**; static focus/motion **32 passed in 1.84s**; tool unit/browser **43 passed in 29.66s** after the method-label update. Its incomplete full run reached 38% with no summary and is not a completed gate. Logs and source hashes are retained under `recheck/pre-expanded-incomplete/` and `recheck/pre-expanded/`. Expanded-panel findings must be resolved and rechecked before the final full suite restarts.

### Findings addressed

1. Added only the scoped `.nav-links` inset. The real `/gm` regression independently checks paint on all four edges of a nav link and the search button at phone and desktop widths, plus unchanged control geometry/markup. Both viewport cases failed on the old CSS's top edge.
2. The audit now treats in-image low-change samples without clipping proof as uncertainty regardless of opacity. Exact same-colour paint remains a known 1:1 failure. Both one-RGB-step half-ring cases reproduce the old false pass and now fail; no near-match sample is silently discarded. Positive inset-ring controls pass at opacity 1 and 0.55 with zero missing/uncertain samples. Old strict counts must be replaced by the fresh five-profile run below.
3. Suppressed the empty `.tile__link` anchor's own outline next to its unchanged `::after` rule. The real tile test checks its entire interior as well as the perimeter. It reproduced the extra 8×29px interior box before the correction.
4. Restored the screenshot acceptance wording verbatim and completed the stable `/me` repeat described below.
5. The fresh Windows full suite completed after the expanded-panel corrections: 5,625 passed, 139 skipped, one xfailed, eleven warnings and the eleven exact baseline PostgreSQL setup errors; zero test failures. The command added only a 600-second traceback diagnostic to the earlier full-suite flags, without skipping any tests; none fired.
6. Guarded zero-radius fitting against 0/0 and required `allow_nan=False` when writing focus JSON. The real zero-size report passes Chromium `JSON.parse`; synthetic NaN and either infinity are refused. Sheet-pin tests now require both zero missing samples and offset -2px.
7. Removed the unrelated panel-card boilerplate from 38 suppression-table rows, corrected the actual `body .tinput:focus-visible`/`.tselect` selectors, and cited the sheet's own Dice partial/phone placement in BUG-UI-21. Claude's original review text is retained unchanged.

### Correction tests

- CSS regressions before the fix: 3 failed, 2 passed, 28 deselected in 155.97s (cold fixture startup included). The new nav cases and tile-interior case fail; strengthened existing pin cases pass.
- Corrected CSS browser cohort: 33 passed, 13 deselected in 99.88s. Static focus/motion: 31 passed in 4.40s.
- Tool regressions before the fix: 6 failed in 5.96s. Final owned unit/browser selection: 43 passed in 31.22s.
- Logs, exact commands and hashes are under local ignored `recheck/`. These early owned selections are not represented as the broader gates; the final combined selection, full suite and strict audit are recorded in the final sections below.

### Additional clipping exposed by the corrected audit

A combined selection on the first corrected snapshot passed **553 tests in 213.63s**, with all 15 source/test hashes unchanged. The fresh account audit then exposed 14 phone Chronicle tab clips. The preserved partial PF2e capture also identified builder-navigation scroll-edge clipping, Rapid Fire edge controls, setup-summary bottom strokes and calendar Prev's stroke under the neighboring month selector. These narrow indicator-paint cases were corrected before the final-source gates restarted. The early full run was stopped after 16 progress dots with no summary; no pass or completed-suite count is claimed. Raw completed/partial evidence is retained under the local `pre-chronicle*` directories.

The same account capture distinguishes 14 partly off-screen top-nav observations and one wrapped Register link whose fragmented outline cannot be certified by the single-rectangle sampler. Those remain reported layout/measurement limits, not assumed CSS defects. Quick Search's own controls have no failure there; its approved underlay exception does not cover the other observations.

### Explicit review decisions: Chronicle geometry and measurement coverage

The phone Chronicle strip has no horizontal tab padding. Insets cross label ink; a lighter-token experiment appeared to pass the sparse audit at 4.64:1 but independently checked stroke-core pixels over the glyphs reached only 1.51:1. That alternative was rejected. Expanding only the scroller's paint box also failed because it moved the existing generic nav shadow into the ring. A specificity-correct shadow-removal diagnostic proved that cause; removing the existing decoration is not the proposed fix.

The implemented compensation keeps the original accent and desktop ring. At widths up to 480px, and only while a tab matches `:focus-visible`, it adds compensating 4px inline padding/negative margins to the strip and tabs and uses an inset of -4px. The current-tab underline is compensated back to its original position and width. **The phone tab hit boxes widen by 8px and move 4px left while the strip has keyboard focus; the existing nav stripe extends 4px at each end.** Glyph positions, underline ink, header/main/following-content positions, page scroll widths and non-style hooks remain exact. These are real focus-time geometry/decoration exceptions, not an "indicator only" claim; Claude should explicitly accept or reject them before any commit. The static allowlist names only those selectors and property values.

The ignored 12-state candidate probe (375/390/1440, Home and Journal on both routes) gives 8.3617:1 sampled contrast and a minimum of 8.3036:1 over the actual straight 2px stroke core, with zero missing/uncertain samples. Desktop pixels are unchanged. Native six-tab testing at the 320px floor is clean; an extra 280px check exposes the same last-tab visibility problem on old and new CSS (original four uncertainty samples, new two), so no JavaScript or below-floor scrolling fix was added. The frozen-source regression and audit results are recorded below.

The audit's broader limit is also explicit: its `contrast` value is the **minimum of 12 retained best-in-3×3 samples**, not a minimum over every ring pixel. It can miss localized low contrast within or between those windows, as the rejected lighter-token example demonstrates. The original one-RGB-step half-ring false pass is fixed; this separate sparse-coverage limit is not claimed fixed by that correction. Module documentation, exported JSON method metadata and human diagnostics now name the method and exclude whole-perimeter/WCAG certification. Final named-control tests use independent painted/full-core evidence. A general geometry-derived full-stroke mask would be a separate tool change; the remaining coverage limit needs Claude's explicit review rather than an implied clean accessibility certificate.

The product regressions now cover the compensated strip with all six real published-document links, native first/last Tab navigation at 320/375/390/1440, non-overlapping targets and exact blur restoration. The final eight route/viewport cases passed in 26.65s: minimum independently checked straight-core contrast 8.0779:1, minimum sparse result 8.3384:1, zero missing/uncertain samples. Final positive images/metrics are retained with SHA-256 manifests under `recheck/correction-tests-green/owner-complete/` and `chronicle-floor/`.

### Stable `/me` repeat

Captured the real account `/me` route under one fixed account, campaign ID and backup timestamp, alternating baseline/current template loaders and exact external stylesheet bytes. All fixture JSON hashes, non-style HTML, backup text, campaign links and sampled geometry/styles stayed unchanged. Five viewports (375, 390, 768, 1024, 1440), four independent contexts per stylesheet and two viewport-only captures per context gave **80 retained frames**.

- At 768, 1024 and 1440, all 16 frames per viewport are pixel-identical.
- Both phones have exact cross-style matches (best difference 0px). At 375, baseline has two raster hashes and current one; worst pair 720 pixels/max channel 96. At 390, baseline has one and current two; worst pair 811 pixels/max channel 96.
- The phone raster mechanism remains **unattributed**. No tolerance, masking or screenshot exclusions were introduced. This completes the previously missing repeat without claiming that all original frames are identical. The original `/me` backup-timestamp difference remains separately attributed to fixture data.

Evidence: local `visual-investigation/me-stable-repeat-20261102/summary.json`, `captures.json` and all 80 PNGs. Native 200% zoom remains Claude's completed 44-view review evidence above, not a claim that these headless captures performed native zoom.

**Controlled repeat after the Chronicle correction:** the same stable method was run against the exact `437f3d…` stylesheet, retaining another 80 frames under `visual-investigation/me-stable-final-20261102/`. Fixture bytes stayed unchanged and every viewport has an exact 0-pixel cross-tree match. All 16 frames at 375, 390, 1024 and 1440 are identical. At 768, baseline has two pixel hashes and current one; worst pair 990 pixels/max channel 96 is same-tree baseline variation, whose raster cause remains unattributed. Non-style HTML, backup value, campaign links and sampled geometry/styles each have one value per viewport. No masking or tolerance was added, and not all 80 frames are claimed identical. Summary SHA-256: `8464502125e9f56f16fbf93504033b32151a9e764f1b70866d4cf026713d071d`. The later two builder-only outline rules cannot match `/me`; the retained-selector proof below supports using this repeat with its actual captured hash, rather than claiming a redundant `444a3b…` capture.

### Last builder repairs and retained-capture provenance

The completed five-profile `437f3d…` audit exposed two remaining phone wizard edge clips in Cosmere and 104 clipped Mistborn stepper observations (52 per viewport). The last product delta contains only a phone (`max-width:760px`) `.bld .wiz-rail .wiz-tab` outline inset of -3px, and a `.mbb .stepper button` inset of -2px. A -2px wizard experiment appeared to pass sparse sampling but overlapped the selected border at 1:1 in the independent stroke-core check, so it was rejected. The chosen -3px inset clears that border without changing colour, glyphs or layout.

Four real builder cases passed in 36.90s after three genuine painted-ring failures and one desktop pass in the corrected red run (32.95s). The initial red also had a desktop fixture comparison against a viewport position changed by native focus scrolling; that setup issue was corrected using local offsets, with its original log retained. Mistborn tests preserve the entire form's values and assert that focusing buttons sends no actions. Named proof images and metrics have SHA-256 manifests.

Only the affected Cosmere and Mistborn captures are repeated after these last two rules. PF2e, account and D&D retain their actual `437f3d…` captures: 43 real route/role HTML responses contain no `.mbb`, `.wiz-rail` or `.wiz-tab` tag classes. Removing exactly the two added outline rules leaves every other nonempty CSS line identical with internal selector whitespace preserved; all other runtime/template hashes are unchanged. This invariance proof was independently checked. The final aggregate is therefore an explicit composite with per-profile source hashes, **not** five captures on identical `444a3b…` stylesheet bytes. No raw result is filtered, rewritten or converted to a pass. Local proof: `recheck/retained-invariance.json` and `retained-profile-selector-proof/`.

### Corrected strict audit before the expanded batch

| Profile | Views | Focus observations | Failed observations | Diagnostics | Quick Search underlay | Other failed observations |
|---|---:|---:|---:|---:|---:|---:|
| PF2e | 36 | 5,126 | 1,679 | 2,463 | 788 | 891 |
| Account | 32 | 682 | 249 | 292 | 234 | 15 |
| Stormlight | 24 | 2,116 | 1,032 | 1,202 | 986 | 46 |
| Mistborn | 24 | 1,804 | 901 | 1,111 | 830 | 71 |
| D&D | 18 | 1,364 | 653 | 795 | 630 | 23 |
| **Total** | **134** | **11,092** | **4,514** | **5,863** | **3,468** | **1,046** |

All five strict commands retain exit 1. There are **zero Quick Search input/pill failures** and **zero extra required-state/navigation errors**. Counts repeat controls across states and viewports; they are not unique defect counts. The corrected classifier catches uncertain paint that the old tool discarded, so the higher failure total is not itself evidence of a product regression.

The 1,046 non-Quick-Search observations consist of 233 load-state observations and 813 expanded-state observations. The user-approved Quick Search exception does **not** waive these. Each load record was reviewed against its image/geometry: PF2e 78 retain known drawer/HP-pin/offscreen geometry and existing handles/dropdown/Budget-panel covers; account 15 are 14 off-screen nav observations plus one wrapped inline-link measurement limit; Stormlight 46 are 25 off-screen observations and 21 fixed-handle overlaps; Mistborn 71 are 36 off-screen and 35 handle overlaps; D&D 23 are 16 off-screen and seven handle overlaps. Expanded-state records remain individually reported rather than being accepted from their state names.

The wizard edge clips are resolved. In Mistborn, 94 of the 104 old clipped stepper observations now pass (minimum sampled contrast 14.5527:1, zero missing samples). The remaining ten are phone controls visibly covered by existing chat/d20 handles; their old three-side clip is gone, and those covers are retained as failures. No newly failing load IDs or further pure ring-CSS defect was identified in the final reviewed set. One unrelated Stormlight pin overlap stopped failing after its captured vertical position changed; that incidental difference is not credited to the wizard rule.

Raw sources, their actual stylesheet/source hashes, independent reconciliation and method labels are linked from local `index.html`; the aggregate is `recheck/focus-summary.json` and `.md`. Every raw JSON parses strictly without non-finite numbers. These counts precede the expanded-panel corrections. The final Windows full suite used this command:

```text
python -m pytest -q -p no:cacheprovider --durations=15 -o faulthandler_timeout=600
```

Its completed final-source result and exact PostgreSQL setup-error comparison are recorded below. The incomplete earlier launches remain retained and are not counted as completed full-suite gates.

### Expanded-state review before the final rerun

All **813** expanded-state non-Quick-Search failed observations were reviewed using the retained metadata, actual source ownership and images. The 654 equipment/condition/picker/sheet-tab records are individually partitioned in `recheck/expanded-pairs-review/partition654.json`; the other 159 are in `recheck/DICE_ROVING_ASSURANCE.json`. Counts describe repeated observations, not unique defects, and categories do not waive failures.

| Family | Observations needing an own-control clipping check | Other failed observations |
|---|---:|---:|
| Equipment | 265 rows and two category buttons | 122 sheet controls behind the open browser |
| Conditions | One bottom toggle | 127 sheet controls behind the open panel |
| Choice picker | Eight category buttons | Ten own search/list controls covered by the existing gains rail; 36 background controls |
| Sheet tabs | Four Play/Prep buttons; four inventory invested/drop controls | 75 existing covers, viewport-edge or oversized-control observations |
| Dice panel | None: all 38 own controls pass, minimum sampled 7.416:1, no missing/uncertain samples | 54 sheet controls or existing widget overlaps/off-screen controls |
| Roving reference tabs | None: all eight selected tabs pass, minimum sampled 8.338:1, no missing/uncertain samples | 105 closed Checks controls or controls intersecting its existing handle |

The equipment-row records have two repeated geometries (99 desktop, 166 phone); the phone group is also partly outside the viewport. Five CSS groups were repaired together: equipment, picker categories, condition toggle, caster view buttons and inventory actions. Dim eligibility ink, existing selected borders, labels, handlers and geometry remain intact. Candidates require independent straight-stroke paint checks, because a sparse sample alone can pass a partially invisible ring. No z-index, focus containment or tab-order change was made. The stopped 38% full run predates this batch and cannot satisfy final-source verification.

### Frozen expanded-panel correction

The final stylesheet SHA-256 is **`e13b9f5dbe6b25e1ee1c156373fbb7420ae3e1ae1a6743be5d697a0667f747d2`**. All 15 source/test hashes are recorded in `recheck/source-freeze.json`. The eight additional selectors change outline paint/offset only: equipment rows/categories and picker categories use -3px; untrained equipment retains 0.4 opacity and uses the existing bright text token with -4px; condition toggles use -3px, overriding only the existing important offset at `system.css:628-631`; Play/Prep uses -3px; PF2e inventory invested buttons use -2px and the tiny Drop control uses 0px. The inventory selector includes `#tab-inventory`, so it cannot affect Cosmere's separate `#inv-list`. No new hitbox/geometry exception was added in this batch.

The exact final tests against archived `444a3b…` CSS gave **10 failed, 2 passed, 68 deselected in 44.54s**. The expected passes are the phone equipment geometry-only diagnostic and already-visible desktop equipment categories. The final CSS was restored byte-for-byte. On those same final bytes, the complete focus browser cohort gave **67 passed, 13 deselected in 164.77s** and focus/motion static checks gave **32 passed in 1.87s**. The combined selection then gave **588 passed in 253.15s**, with every source hash unchanged. Templates parse (**111**) and the diff check is clean.

| Named proof | Minimum independent straight-core contrast | Boundary |
|---|---:|---|
| Desktop equipment, untrained | 3.4805:1 | Original 0.4 eligibility opacity retained; opaque rows exceed 9.79:1 |
| Equipment categories | 6.5855:1 | Selected/inactive states on phone and desktop |
| Choice categories | 5.8165:1 | Selected/inactive states; existing border retained |
| Play/Prep | 7.5879:1 | Both selected modes and both controls |
| Inventory | 8.1537:1 | Invested inset; Drop through the actual invested → Tab → Drop path |
| Condition toggle | 7.4257:1 | Native Tab to the final real Off/On toggle, bottom flush within 0.5px |

The standard equipment fixture catalogue has no untrained observations, so the test feeds twenty bounded Advanced fixture records through the real renderer/category handler; it does not change rules or save data. The two phone row observations remain explicit geometry diagnostics (core minimum 1.0), not four-edge passes: native focus can horizontally scroll the list outside its frame, with row x=-41 or x=9 while the frame starts at 16. This is recorded separately as BUG-UI-25. Viewport containment alone is not treated as proof that an ancestor does not clip.

The public equipment case's sixth desktop row has 189px clearance and supplies eligibility/colour coverage. An additional ignored native row-15 proof supplies the actual desktop bottom-edge check at exactly 0px clearance: all four final straight cores pass (opaque minimum 9.79497:1; untrained minimum 3.49676:1). Counterfactual 2px-outset paint has a 1.0:1 bottom edge; old dim gold elsewhere reaches only 2.53652:1. The runner retained those completed desktop pairs, then stopped on the known phone's unavailable left-edge pixels; it is not claimed as a completed positive phone run. No source bytes changed.

Drop has exactly 2px clearance on the native same-row Tab sequence; its 0px offset clears the glyph and paints fully there. Artificially scrolling that 24px control flush **after focus** still clips its outward stroke and remains a saved negative. No claim covers every manual scroll position, and no larger hitbox was introduced to conceal that limit. Initial candidate-test logs are retained: their phone classification and forced Drop geometry were corrected to assert the real frame/native path, without lowering the positive stroke-core threshold. Network assertions exclude the sheet's existing spell-slot initialization POSTs; they establish no other POST during the tested interactions, not zero network traffic.

Positive proof retains **330 PNGs and 101 metric/frame JSON files**, plus SHA manifests, under `recheck/correction-tests-green/expanded-owner-final/`; the final red proof retains 60 files. `expanded-owner-results.json` gives every named metric and limit; `equipment-true-edge/` gives the independent row-15 proof.

### Final strict audit and independent reconciliation

The final PF2e capture completed in **743.66s**, retained exit 1, and matched every frozen source hash. Its **5,126** control identities and all per-state counts match the preceding capture. Account and D&D retain their actual `437f3d…` captures, and Stormlight/Mistborn retain their actual `444a3b…` captures. This is an explicit composite: `expanded-retained-invariance.json` verifies 49 hashed real HTML documents, absence of the necessary selector roots and dynamic condition markers, exactly eight added outline rules, and unchanged other runtime bytes. The earlier two-rule proof remains intact. No repeated capture on final bytes is implied for those four profiles, and no raw failure is filtered or rewritten.

| Profile | Views | Focus observations | Failed observations | Diagnostics | Quick Search underlay | Other failed observations |
|---|---:|---:|---:|---:|---:|---:|
| PF2e | 36 | 5,126 | 1,390 | 1,873 | 788 | 602 |
| Account | 32 | 682 | 249 | 292 | 234 | 15 |
| Stormlight | 24 | 2,116 | 1,032 | 1,202 | 986 | 46 |
| Mistborn | 24 | 1,804 | 901 | 1,111 | 830 | 71 |
| D&D | 18 | 1,364 | 653 | 795 | 630 | 23 |
| **Total** | **134** | **11,092** | **4,225** | **5,273** | **3,468** | **757** |

Every strict command still exits 1. Quick Search's own controls have zero failures; extra required-state/navigation errors are zero. The **757** other failed observations comprise 228 load-state and 529 expanded-state observations; they are not covered by the Quick Search scope exception. The expanded remainder reconciles exactly to equipment-background 122, condition-background 127, picker 46, sheet-tab limits 75, Dice 54 and roving/reference 105. Four newly failing generator controls sit under the existing Safety/Party Chat widgets at different captured y positions. Nine older generator failures no longer intersect them; their between-capture position variation is not attributed or credited to CSS.

All **284** identified overflow observations leave the strict failed set. This is a sampled-result statement, **not** proof that every edge is visible: all **500 phone equipment rows** still extend partly outside the viewport and retain missing samples. Across the composite, **626 observations with no `problems` still have missing samples** (PF2e 556, account 1, Stormlight 61, Mistborn 8, D&D 0): 601 control rectangles extend outside the viewport and 25 do not. These known clipped-coordinate coverage gaps are separate from the failed-observation totals. The near-match correction fails in-image samples without clipping proof; it does not turn geometrically unavailable paint into whole-ring evidence. `focus-coverage-gaps.json/.md` preserves all 626 records. No sparse pass is described as a complete accessibility certificate or a fix to the phone equipment layout.

Independent comparison and image/source review found no further own-panel CSS defect in this batch. Actual raw hashes, identity reconciliation, all remaining failures, new generator crops and coverage limitations are in `pf2e-expanded-final-comparison.json`, `pf2e-expanded-final-review.json`, `focus-summary.json/.md` and the coverage supplement. The source remains frozen at `e13b9f5d…`.

### Final Windows suite

The fresh full run completed on the final 15-file snapshot (retained session `7926`, now closed): **5,625 passed, 139 skipped, one xfailed, eleven warnings, eleven errors in 4,469.95s (1:14:29), with zero failed tests**. It used the same baseline environment and full selection, plus the 600-second diagnostic; none fired.

All eleven error IDs exactly match the baseline missing-PostgreSQL setup errors, with no new or missing IDs. The pytest exit is 1, so this is explicitly **not an all-green database run**; PostgreSQL must pass in CI before merge. No test failure occurred, and the three previously known failures did not reproduce. Before/after/current disk hashes match every entry in `source-freeze.json`; `source_changed` is empty. No full rerun is needed or claimed after this result.

Actual command, complete summary, durations and errors are in `recheck/full.log`, `full.running.json` and `full.result.json`. `full-comparison.json/.md` preserve the exact eleven-ID and fifteen-hash checks. All screenshots, private fixtures and raw captures remain local and ignored. The evidence index links the unfiltered audits, coverage gaps, manifests and both completed and incomplete run history. The task is ready for Claude's recheck; nothing has been committed, pushed or deployed.

## Recheck (Claude, 2026-11-02): accepted, with three small test fixes before commit

Rechecked the frozen snapshot: all 15 source hashes match `source-freeze.json`. I read the CSS and template diff myself; one read-only review agent read the five new files, and I checked each of its findings in the code.

### What I ran

- **Focused selection:** 588 passed in two parts: 584 passed and 4 skipped in 704s without the private Mistborn data, then those 4 passed with `COSMERE_DATA_DIR` set. This matches your 588. I did not rerun the full Windows suite; your final-source run stands.
- **Native zoom, installed Chrome:** real profile default zoom, not emulation. For each focused nav or Chronicle control I compared it focused and with its outline made transparent, then checked an edge band on all four sides.
  - Top nav (`/gm`, `/party`, `/chronicle`, `/chronicle/journal`; every nav link, the search button and the shortcut chip): all four edges painted at 100% (1280 CSS px) and 200% (640 CSS px, dpr 2). **Prior finding 1 is fixed.** No sideways page scroll.
  - Phone Chronicle tabs at 390 CSS px, at 100% and at 200% (dpr 2): the `:has()` rule applies (offset -4px), all four edges are painted, and no page scrolls sideways. In the zoomed crops the right stroke sits right against the last letter (Home "e", Journal "l") but does not cover it.
- Prior findings 2, 3 and 6 were checked in code: the near-match false pass is closed, the tile anchor's own outline is removed, and NaN is guarded with `allow_nan=False`.

### Ruling: Chronicle focus geometry — accepted

I accept the phone-only, keyboard-only geometry exception as built. Reasons:
- It applies only while a tab matches `:focus-visible`, and links get that only from the keyboard. Mouse and touch users never see it.
- The tabs have a 12px gap (`system.css:5461`), so growing each by 4px per side leaves 4px between targets. Nothing overlaps.
- Glyphs, the underline and the following content stay fixed, as your tests check and my crops show.
- Without `:has()` support (Firefox before 121), the rule is skipped and the old clipped outward ring shows. That is no worse than `main`.
- The alternatives you tried are worse: a plain inset crosses the label ink, and the lighter token measured 1.51:1 on the stroke core.

### Ruling: sampling limits — accepted as documented limits, not as proof

I accept the sparse 12-sample `contrast` method for DS-03 on these conditions, which are already met:
- The method is named in the JSON metadata and diagnostics.
- The tool claims no whole-perimeter or WCAG certification.
- Risky control classes have independent full-stroke tests.
- The 626 records that have missing samples are kept in `focus-coverage-gaps`.

One related gap: a sample whose whole 3x3 window lies outside the screenshot counts as missing, not as a problem (`tools/ui_focus.py:207-218`). That falls under the same documented coverage limit, so no change is needed here.

A geometry-derived full-stroke mask is a tool follow-up (**DS-03t**), not a DS-03 blocker. The strict audit stays red. The Quick Search exception covers only its underlay observations; the other 757 failures stay reported against the BUG-UI rows in appendix A.

### Required before commit (test strength only; no CSS change)

1. `tests/test_ds_focus.py:157`: the declaration regex needs a trailing `;`, so an extra final declaration with no semicolon is not seen. For example, `animation: x 1s` slips past both this check and `test_ds_motion`. Parse every declaration in the body, for example by splitting on `;` and dropping empty parts, so any extra property fails.
2. `tests/browser/test_ds_focus_browser.py:685-687` (tile) and `:714-716` (roster card) check only the top-middle pixel. Require paint on all four edges, using the same four-edge helper as the nav test. The tile's interior check stays.
3. `test_ds_focus_browser.py:217-219` (nav) and `_expanded_paint` (around `:854-863`) record the "before" shape after focusing. Record it before `_keyboard_focus`, as the pin and tab tests do, so the "unchanged geometry" claim holds.

Then run the two changed test files (a red run is not needed). If they pass and no CSS bytes changed, commit, open the PR and merge once every CI job is green, including PostgreSQL. You do not need another Claude review unless the CSS changes or a test fails.

## Accepted test-only follow-through (Codex, 2026-11-02)

Implemented the three required test-strength fixes without changing any application code or CSS:

- The focus declaration guard parses every nonempty semicolon-separated declaration with `partition(':')`, including an unterminated final declaration. Exact Chronicle declaration maps reject additions and duplicates. Two temporary-CSS cases exercise the actual guard with `animation: x 1s`, both with and without a trailing semicolon; neither writes the stylesheet.
- Tile and roster-card tests now use the nav test's four-edge painted helper with paired focused/outline-hidden captures. The tile's whole-interior comparison remains intact.
- The nav shape is captured before keyboard focus. `_expanded_paint` settles the existing hover transition, captures its baseline shape, then focuses the control and performs the painted checks.

Requested command: `python -m pytest -q -p no:cacheprovider tests/test_ds_focus.py tests/browser/test_ds_focus_browser.py` (the runner also supplied an isolated local `--basetemp`). **76 passed in 171.21s; no failures or skips.** All fourteen tracked CSS files match the before-run hashes, including `ds.css` SHA-256 `e13b9f5dbe6b25e1ee1c156373fbb7420ae3e1ae1a6743be5d697a0667f747d2`; every runtime file in the accepted snapshot also matches. The two test files were unchanged during their run. No additional full run, audit or Claude re-review is claimed or needed under the accepted instruction.

Local ignored evidence: `recheck/accepted-prepublish/changed-tests.log`, `changed-tests.result.json`, `css-before.json` and `changed-tests-temp/`. The accepted sampling/coverage caveats and DS-03t follow-up remain unchanged. Ready to commit and publish, with merge blocked until every job on the final PR head is successful.
