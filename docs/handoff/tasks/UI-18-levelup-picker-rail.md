# UI-18: Level-up gains rail covers the open picker

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Branch:** `claude/ui-18-levelup-locked-reason` from `main` (after DS-02 and DS-03 merged)
- **Depends on:** DS-02 (PR #238), DS-03 (PR #242)
- **Context:** `templates/player_levelup.html:186-196` (rail stacking), `templates/_choice_picker.html:337-366` (locked rows), `docs/design/DESIGN_DIRECTION.md` 1.2-1.3

## Goal
On `/player/levelup/<pc>`, a player looking at an open choice picker can read why a row is locked, and can see keyboard focus land on it.

## The bug
`#gains-rail` is raised to `z-index: 55`, above the picker overlay (`#uni-modal`, z-50), so the GM override switch in its header stays usable while a picker is open. The whole rail box is opaque, though, and it painted over the left column of the picker: the search field and the first rows of the list. `pointer-events: none` stopped it from taking clicks, but not from covering pixels.

Measured on the real page, with one locked row in the list (so it is the first row):
- 1440x900: rail at x250-490, y111-321. It covered the reason chip completely, two thirds of the row, and 23% of the search field.
- 390x844: rail across the top of the list. It covered the reason chip completely and 20% of the search field.

Since DS-02, reduced motion collapses `cp-shake` and `cp-chip-flash`, so the chip is the only feedback a locked row gives. With the chip covered, a click or Enter on that row changed nothing visible. DS-03 already gives locked rows a keyboard focus ring (`outline-none` and the missing `focus:bg-gray-800` no longer matter), but the rail covered that ring too.

## Approach
CSS only, in the `body.picker-open` rules in `player_levelup.html`:
- Fade the rail out with the overlay, and hide it once the fade ends. It was already inert while a picker is open (`pointer-events: none`, body dimmed to .45), so nothing usable is lost.
- The fade keys off the overlay's own `opacity-0` class (`body:has(#uni-modal:not(.hidden):not(.opacity-0))`), not `body.picker-open`. `picker-open` is set 10ms before the overlay starts to fade in and stays until 200ms after it has faded out, so keying off it would leave a hole in the page, and a floating switch, while the overlay is still see-through.
- Keep the GM override switch visible and move it with `position: fixed` into a 48px band reserved at the top of the overlay (`body.picker-open #uni-modal { padding-top: 48px }`), so it never overlaps `#uni-window`. Fixed pixels, not `vh`: on phones `vh` is the large viewport while the fixed overlay is the visible one, so a `vh` margin could be smaller than it looks. The band follows `picker-open`, so the window doesn't jump during the fade.
- Give the moved switch the card fill, but only when it isn't armed, so the armed amber fill still shows.
- Reduced motion: no transitions on the rail; the switch's 0.2s fade-in is collapsed by the global DS-02 rule.
- No id, handler, class, ARIA or markup changes. The rail returns to normal when the picker closes.

## Acceptance
- [x] With the picker open at 390x844, 390x600 and 1440x900, the reason chip, the locked row and the search field are each the topmost painted element at every sampled point.
- [x] The override switch is visible, inside the viewport, does not overlap `#uni-window`, and its real handler still arms the override (the locked row becomes `cp-opt-override`) and disarms it. It still looks armed when armed.
- [x] Tabbing from the search field reaches the locked row, which matches `:focus-visible`, shows a ring and is not covered. Enter does not select it.
- [x] Closing the picker brings the rail back, with the switch inside it again.
- [x] While the overlay is still transparent (the instant after opening, and as closing starts), the rail is still in place with the switch inside it, with motion allowed and reduced.
- [x] `pytest -q tests/browser/test_levelup_picker_rail_browser.py tests/test_player_levelup_picker_layer.py` passes.
- [x] `python tools/check_templates.py`; inventory diff against `main` for `/player/levelup/Kyle` and `/player/builder`: 0 failures, 0 additions.
- [x] Windows full suite: no failures beyond the known ones.

## Out of scope / do not touch
- The picker partial (`_choice_picker.html`), its handlers and the builder page.
- Any reduced-motion replacement for the shake and flash. Codex's DS-02 ruling was that the visible reason chip is the feedback; this task makes that true again.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
  - `templates/player_levelup.html`: the picker-open rules. The rail fades out with the overlay and is hidden after; the switch moves into a 48px band at the top of the overlay with its own fill (only when unarmed). The old `.45` dim of `#gains-rail-body` and its reduced-motion twin are gone, since the rail is hidden.
  - `tests/browser/test_levelup_picker_rail_browser.py`: new, on the real page through the page's own renderer and handlers. 4 tests x 3 viewports (390x844, 390x600, 1440x900), plus a fade test with motion allowed and reduced.
  - `tests/browser/test_ds_focus_browser.py`: one stale comment that said the rail overlap still existed.
  - `docs/handoff/QUEUE.md`: this row, and DS-03 marked done (PR #242 has merged).
- **Tests run (Windows):**
  - New file before the fix: the coverage, switch and keyboard tests failed and the close test passed. The fade test failed against my first version (rail already hidden and switch already floating while the overlay was still transparent).
  - After: `pytest -q tests/browser/test_levelup_picker_rail_browser.py tests/test_player_levelup_picker_layer.py` -> **15 passed**.
  - Existing picker browser tests: `test_ds_focus_browser.py -k picker` -> **6 passed**; `test_ds_motion_browser.py -k "picker or locked_row"` -> **2 passed**.
  - `python tools/ui_screens.py motion --profile pf2e --routes /player/levelup/Kyle /player/builder` -> **4 pages checked, 0 problems**.
  - `tools/ui_inventory.py` diff, `main` against the branch, both routes -> **0 failures, 0 additions**.
  - `python tools/check_templates.py` -> all 111 parse.
  - Screenshots at 1440x900 and 390x844 with a filtered list: no rail over the window; the switch is in the band above it.
  - Full suite (Windows): **5488 passed, 239 skipped, 1 xfailed, 0 failed**; 11 errors, all PostgreSQL tests in `tests/persistence/` (no local database).
- **Self-review (adversarial, before handing over):** findings and what was done:
  1. My first version hid the rail and floated the switch as soon as `picker-open` was set, 10ms before the overlay fades in, and kept it until 200ms after the overlay had faded out. That left a hole in the page while it was still visible. Fixed by keying the fade off the overlay's `opacity-0`; covered by the new fade test.
  2. That version also put the switch in the overlay's `vh` margin. On phones with toolbars showing, `vh` is taller than the visible area, so the switch could overlap the window. Replaced with a fixed 48px band.
  3. It painted over the armed amber fill. A test failed first (`rgb(28, 24, 19)` instead of the amber) and passes after the fix.
  4. The report ticked the full-suite box before the run finished. Unticked until it has.
  5. The unarmed fill wasn't asserted. It is now.
- **Skipped / not verified:** PostgreSQL (no local database; CI runs it). Linux CI fonts: the switch is one line and the test measures it rather than assuming a width, but I haven't run it under Linux's fallback font. Not checked on Railway.
- **Known wrong / limitations:**
  - The rail's progress list isn't visible while a picker is open (it was dimmed and inert before).
  - The picker window is 48px shorter than before while open, to make room for the switch.
- **Follow-ups (not done here):**
  - Pre-existing: the switch's inline `color` and `border` beat `#gm-override-switch.armed`, so armed only changes the fill, never the text or border colour, anywhere on the page.
  - Pre-existing: on a 390px phone, a long reason chip pushes the row's `Lv` badge partly out of the list (seen with "Requires Stalwart Defender Dedication").
  - Pre-existing: at about 400px tall, the picker's own preview panel covers its list. Not the rail; that's why the short-phone test uses 390x600.

## Review
<!-- reviewer appends numbered findings, or "accepted" -->
