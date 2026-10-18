# UI-08: PF2e encounter tracker (B: combat controls first)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (strongest model, high effort: `tracker.html` is ~5,900 lines, shared with
  Cosmere, and is the most-used page in a fight)
- **Branch:** `codex/ui-08-pf2e-tracker` from current `origin/main` **after UI-04 merges**
  (UI-04 gates the PF2e-only controls by system in the same file)
- **Depends on:** UI-04
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first.** Mockup `pf2e-tracker-*`. This one has no `pf2e-tracker-preview.js`: the B moves are made by
  `render-pf2e-tracker-review.py` and are visible in `pf2e-tracker-session.html`, with styles in
  `pf2e-tracker-revisions.css`. `pf2e-tracker-preview-actions.js` / `-bootstrap.js` are demo-only. Decision log entry "PF2e encounter tracker".

## Goal
In a PF2e fight, the combat controls (round/turn, the initiative order, the active combatant)
come first. Encounter setup (adding monsters, party, saved encounters) folds into a disclosure that
is **open when the encounter is empty** and closed once combat has combatants. Keep every row
control, the inspector, multi-target tools, the XP bar and keyboard shortcuts.

## Approach / constraints
- **Speed matters here** (CLAUDE.md: tracker-sheet sync is the priority). Layout and CSS only; don't
  add work to the render or SSE paths. If you touch the row renderer, keep it one pass.
- **Cosmere shares this file.** Gate the new layout so a Cosmere fight is unchanged by this task
  (the Cosmere tracker's own approved B layout is a separate, later task). After UI-04, check
  that no PF2e control reappears in Cosmere.
- Small screens: the combat table scrolls horizontally inside a focusable container with a visible
  hint, rather than clipping right-hand controls (the mockup's approach).

## Acceptance
- [ ] PF2e tracker matches the B mockup at 1440px; at 375px the page has no sideways scroll and
      every row control can be reached.
- [ ] Setup disclosure: open with zero combatants, closed with combatants, toggles by keyboard.
- [ ] Inventory test over the rendered tracker (PF2e encounter): every handler, input id and option
      unchanged. Cosmere render test (`tests/test_tracker_cosmere_render.py`) unchanged.
- [ ] Existing tracker tests plus the convention gates pass (numbers); browser CI runs in the PR.

## Out of scope
- Combat rules, SSE payloads, the Cosmere tracker layout.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

- Ported Revision B into `templates/tracker.html` behind the strict PF2e system class: the compact
  hero band puts round, turn and combat actions first; initiative rows are flatter and denser; and
  the active-combatant inspector uses the approved narrower desktop column and unboxed stat band.
  Cosmere and D&D receive none of the new wrappers or styles.
- Wrapped the existing setup strip whole in a native disclosure. It renders open when the initial
  encounter is empty and closed when combatants exist, while preserving every existing input,
  option, ID and inline handler. Empty-to-populated and populated-to-empty live transitions update
  it once, while ordinary combat repaints preserve a manual choice. The global shortcut listener
  leaves native summary activation alone so Space toggles it instead of advancing the turn.
- Added a PF2e-only, labelled and focusable initiative region. At phone widths it contains the wide
  combat grid, shows a sideways-scroll hint and keeps page-level horizontal overflow contained so
  every right-hand row control remains reachable. Its focused arrow keys and control activation
  remain native, while N, Escape and the combat shortcuts continue to work from table controls.
  Desktop keeps the existing viewport-sticky column headings.
- Added `tests/test_pf2e_tracker_ui.py`, with a rendered inventory fingerprint captured from
  `origin/main` at `9c9e4c15`, empty/populated disclosure checks, keyboard and mobile-region
  contracts, live disclosure transitions, shortcut preservation, Cosmere isolation, and the
  Revision B style contract.

### Verification

- `python tools/check_templates.py` — **107 templates parse**.
- `pytest -q tests/test_pf2e_tracker_ui.py tests/test_tracker_cosmere_render.py tests/test_tracker_session_readiness.py tests/test_tracker_visual_payload.py tests/test_tracker_sheet_sync.py tests/test_tracker_error_attribution.py tests/test_combat_turn_advance.py tests/test_encounter_save_persistence.py tests/test_round_events.py tests/test_turn_timer.py tests/test_cosmere_initiative.py tests/test_cosmere_conditions_ui.py tests/test_system_guards.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py` — **134 passed, 21 skipped**.
- Final review-fix rerun, `pytest -q tests/test_pf2e_tracker_ui.py` — **7 passed**.
- `git diff --check` — passed.
- Claude's required 1440px and 375px browser checks remain for review.

## Review

**Claude, 2026-10-15: accepted. No required fixes.**

Read the diff: every new rule sits under `body.system-pf2e`, the setup `<details>` and the scroll
region render only when `active_system == 'pf2e'`, and `_syncPf2eEncounterSetup` and
`_trackerKeepsNativeKey` are new. Checked live in a PF2e campaign (Goblin Warrior + Kyle) at
1440px and 375px. Did not rerun the test suite.

- **Combat first:** the turn bar sits at the top (81px), the setup summary below it (234px), then
  the initiative grid (340px).
- **Setup panel transitions:**
  - Empty on load: open.
  - The first creature added: closes by itself.
  - Opened by hand during combat: stays open through an HP change and a second add.
  - Encounter cleared: opens by itself; refilled: closes again.
- **Keyboard (real key presses):**
  - Space on the focused setup summary toggles it and does **not** advance the turn.
  - With focus on the page, N still advances the turn.
  - On a phone, with the scroll region focused, Right arrow scrolls the table (0 -> 120 -> 200px)
    and leaves the turn and inspector unchanged.
- **Phone, 375px:**
  - The page has no sideways scroll; the table scrolls inside its own 343px region (800px of
    content).
  - The hint "Swipe or scroll sideways to reach every combat control." is visible.
  - The row's right-most control (Delay) starts at 803px and becomes reachable and clickable
    (`elementFromPoint` hits it) after scrolling.
- **Cosmere is unaffected:** the Cosmere tracker HTML has no setup disclosure and no scroll
  region; the PF2e tracker has both.
