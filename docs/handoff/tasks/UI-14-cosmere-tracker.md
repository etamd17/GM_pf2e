# UI-14: Cosmere encounter tracker (B: phase and turn controls first)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (strongest model, high effort: `tracker.html` is shared with PF2e and is the
  most-used page in a fight)
- **Branch:** `codex/ui-14-cosmere-tracker` from current `origin/main` **after UI-08 (PF2e tracker
  layout) merges**. Both change the same file and the same setup disclosure, so build on UI-08's
  version.
- **Depends on:** UI-04 (merged), UI-08
- **Context:** read the conventions in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md` and the
  Cosmere notes in `tasks/UI-13-cosmere-dashboard-and-gmscreen.md` (both skins, mockup naming).
  Mockups are `cosmere-tracker-<skin>-<phases|traditional|empty>-session.html` with
  `cosmere-tracker-revisions.css` / `-preview.js`. Decision log entries: "Cosmere encounter
  tracker", "Cosmere tracker preview checks", "Cosmere encounter tracker selection".
  UI-04 already removed every PF2e control from the Cosmere tracker; don't bring any back.

## Goal
In a Cosmere fight:
- **Phase and turn controls come first.** The **same** initiative-mode control moves into the
  persistent header.
- **A four-phase overview**, shown only in phased initiative: Fast heroes / Fast NPCs / Slow
  heroes / Slow NPCs. It follows `systems/cosmere/combat.py`'s phase order, counts the actors in
  each phase and highlights the current one. It only displays; it never calculates or changes
  initiative.
- **Hidden for traditional initiative,** the house-rule mode.
- **Encounter setup in a disclosure** that is open when the encounter is empty, as in UI-08.
- **Flatter rows and inspector stat boxes,** larger fast/slow choices and Health hit targets.
- **Phones:** the combat table scrolls sideways inside a focusable container with a visible hint,
  so no right-hand control is clipped.

PF2e fights stay exactly as UI-08 left them.

## Also check
- The bestiary adversary "Archer" (and possibly others) has `strikes: []` in the tracker and Full
  stats payloads (found in the UI-04 review). Find out whether its attacks live elsewhere in the
  source record (e.g. as actions) and are just not mapped. If it's a mapping bug in
  `systems/cosmere/actor.py`, fix it with a test. If the source has no attacks, say so in the
  report. Don't invent stats.

## Acceptance
- [ ] Cosmere tracker matches the B mockups at 1440px (phased, traditional and empty) in both
      skins; at 375px every row control is reachable and the page has no sideways scroll.
- [ ] The phase band counts and highlights correctly (test with a fixture of heroes and NPCs in
      both speeds) and is absent in traditional mode.
- [ ] Inventory test over a Cosmere encounter: handlers, input ids and options unchanged from
      `main`. PF2e tracker tests unchanged. `tests/test_tracker_cosmere_render.py` extended.
- [ ] No PF2e control or vocabulary in the Cosmere tracker (UI-04's tests still pass).
- [ ] Convention gates and browser CI pass (numbers).

## Out of scope
- Turn-order rules, action budgets, injury progression, initiative math.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
