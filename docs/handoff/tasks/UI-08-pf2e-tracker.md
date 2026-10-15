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

## Review
