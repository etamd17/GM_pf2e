# UI-02a: Build the approved Cosmere generators design (Revision A, labelled controls)

<!-- ============ BRIEF ============ -->

- **Owner:** codex (standard model, medium effort). Claude verifies in the browser after.
- **Branch:** `codex/ui-02a-cosmere-generators` from current `main` (after PR #181 merges, so
  `tasks/UI-01.md` is present).
- **Depends on:** none in code. Independent of PR #182 (#182 doesn't touch this page).
- **Context:** decision in `tasks/UI-01.md` > Decisions (generators **A**); approved PF2e twin in
  `tasks/UI-00.md` (PF2e generators A). Local mockup: `docs/handoff/assets/UI-01-review/`
  (`a.html`, built by `build.py`; not in git, ask the user if you need it). One file:
  `templates/cosmere_generator.html`. Route `app.py::cosmere_generator` (unchanged).

## Goal
Same page, same grid, same features; every icon-only button becomes a labelled button, cards get
flatter, and the header explains pins/back in one line. Bigger tap targets on phones.

## Approach (template + its own CSS block only)
1. Card header buttons, in this order: **Back** (keep `&#8592;` glyph + text), **Copy**, **Pin**
   (keep the bookmark SVG + text), **Roll 5** (only `name`/`npc`, was "x5"), **Reroll** (accent
   outline, last). Keep every class (`cg-back`, `cg-copy`, `cg-pin`, `cg-batch`, `cg-reroll`),
   `onclick`, `title`, `aria-label`, `aria-pressed` and the `disabled` on Back exactly as they are.
   Buttons get `width:auto; padding:0 .55rem; font:600 11.5px var(--font-ui)`.
2. `cgCopyText` swaps the clicked button's `innerHTML` to a check mark for 900 ms and restores it:
   it still works with text labels. No JS change needed. The other functions locate cards by
   `.cg-card[data-type]` and are untouched.
3. Cards: transparent background, 1px `rgba(255,255,255,.07)` border, a hairline under the card
   head; label in display font at 13.5px, not uppercase.
4. Header: title, then one line: "**Reroll all** refreshes every card except the ones you pin;
   **Back** returns a card to its previous result." Reroll all gets the accent-tinted background.
   Remove the "Rosharan prep, one tap at a time." subtitle (it's wrong under the Mistborn skin;
   MB-03 handles generator content).
5. `@media (max-width:640px)`: one column, buttons 32px tall, action row wraps under the label.
6. Any class you add that sets `display` must also get `.x[hidden]{display:none}` (CLAUDE.md rule).

## Acceptance
- [x] Same controls as today: 9 Reroll, 9 Pin, 9 Back, 9 Copy, 2 Roll 5, Reroll all, Copy all
      (count in the rendered page).
- [x] New test `tests/test_cosmere_generator_ui.py`: GET `/cosmere/generator` as GM in a Cosmere
      campaign; assert the counts above, that each card button has a visible text label, and that
      `cgTogglePin`/`cgBack`/`cgCopy`/`cgReroll`/`cgBatch` calls are unchanged.
- [x] `pytest -q tests/test_cosmere_generator_ui.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      and `python tools/check_templates.py` pass (paste numbers).
- [ ] Browser check (Claude does this if you can't): desktop 1440px and phone 375px, both
      **Stormlight and Mistborn** skins (`cosmere_world` campaign config); no sideways scroll at
      375px; Pin keeps a card through Reroll all; Back steps to the previous result; Copy shows the
      check mark.

## Out of scope
- Generator content (MB-03), grouping/filter (that was Revision B, not chosen), any route change.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- **Changed:** `templates/cosmere_generator.html` now uses the approved Back, Copy, Pin, optional
  Roll 5, Reroll order with visible labels. Existing classes, handlers, titles, ARIA attributes and
  Back disabled state remain intact. Cards use the flatter treatment, the header explains pin/back
  behavior without Stormlight-only wording, and the phone rule uses one column, wrapped actions and
  32px controls. The generator functions and route are unchanged.
- **Regression coverage:** `tests/test_cosmere_generator_ui.py` renders the real GM route in
  Cosmere mode and checks all nine card types, exact control counts and order, visible labels,
  header actions, per-card handlers, titles, ARIA labels, pin state and Back disabled state.
- **TDD evidence:** before the template edit, the new test produced **1 failed, 1 passed in
  106.52s** because the rendered controls still began with Pin rather than the approved Back / Copy
  / Pin order. After the edit, the new file passed with **2 passed in 11.22s**.
- **Tests run:** `python -m pytest -q tests/test_cosmere_generator_ui.py
  tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py` -> **24 passed in
  13.63s**. `python tools/check_templates.py` -> **all 106 templates parse**.
- **Not verified here:** per the brief, Claude still needs to check desktop 1440px and phone 375px
  in both skins, including overflow, Pin plus Reroll all, Back history, and Copy's temporary check
  mark. No browser, push, PR, merge, deployment or generator-content change was performed.
