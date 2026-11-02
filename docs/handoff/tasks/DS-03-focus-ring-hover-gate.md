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
DS-03 gives locked picker rows a visible ring: calculated 3.66:1 on the builder and measured 3.74:1 on level-up at the row's 0.55 opacity. No markup or script change is needed. The rest stays with **DS-15c** (visible row focus restyle) and **DS-09** (ARIA): `aria-disabled` rather than `disabled`, all reasons as visible text (the chip shows `reasons[0]` only and the rest are in `title`), and text feedback on Enter under reduced motion. `.cp-opt-override` rows get the ring the same way. The hidden remove controls at `player_levelup.html:1821` and `player_builder.html:1356` can't be reached by keyboard, and a ring can't fix that; record them in appendix A with a `BUG-UI-nn` id.

## Acceptance
- [ ] Section 11 gates, with these specifics:
- [ ] `tools/ui_screens.py focus` exits 0 on all five profiles at both viewports; screenshots of each focused control type attached to the PR; a suppressed control makes it fail (test).
- [ ] Every suppression hit is mapped to an override or a named exclusion (grep for `outline`, `outline-none` and `focus:outline-none` across `static/css`, `templates` and `app.py`, listed in the report).
- [ ] Inventory diff against `main`: 0 failures on all five profiles.
- [ ] `test_map_signals.py`, `test_map_keyboard.py`, `test_map_gm_loop.py`, `test_ds_motion.py`, `test_ds_tokens.py`, `test_ui_inventory_tool.py` green; no tested CSS block edited.
- [ ] `motion` still exits 0 on all five profiles. No new animation or transition.
- [ ] Screenshots at rest against `main` differ only as listed above and by the accepted DS-02 corner pixels; any other difference is repeated and reported as attributed or not.
- [ ] Contrast tool: 0 enforced failures. Templates parse.
- [ ] Windows full suite: no failures beyond the known 3, plus the 11 PostgreSQL errors when no database is set.

## Out of scope / do not touch
- ARIA additions (DS-09), and `disabled` or tab order changes.
- `.ds-*` component styling (DS-04 onward), the VTT (DS-08).
- Picker scripts and any inline script (inventory hash).
- Restyling existing rings, or reconciling their colours.
- The level-up rail overlap (separate session).
- The missing touch mitigation on builder and level-up (`player_levelup.html:1927`, tooltips at `player_builder.html:1223`, `player_levelup.html:1424`): existing defect, record in appendix A.

## Report
