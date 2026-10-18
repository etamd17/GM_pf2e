# UI-13: Cosmere GM dashboard (hybrid) and GM Screen (B)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, medium effort)
- **Branch:** `codex/ui-13-cosmere-dashboard-gmscreen` from current `origin/main`
- **Depends on:** none
- **Context:** **read "Conventions for every PF2e screen" in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md`
  first. They apply to the Cosmere screens too,** with two differences:
  - mockups are named `cosmere-<screen>-<skin>-<state>-<variant>.html`;
  - every check runs in **both skins**, Stormlight and Mistborn (switch with `POST /api/cosmere/world`).

  Decision log entries: "Cosmere GM dashboard", "Cosmere hub source follow-ups",
  "Cosmere GM dashboard selection", "Cosmere GM Screen", "Cosmere GM Screen selection".

## Goal
- **GM dashboard, hybrid** (`templates/cosmere_gm.html`). The mockups are
  `cosmere-dashboard-*-hybrid.html` with `cosmere-dashboard-revisions.css` and `-preview.js`.
  - Take Revision B's party-status band and put the **same** Advancement tile beside it. The band
    shows a text caption for zero Health, injuries and condition alerts.
  - Keep A's familiar Run / Prep / Manage tool cards (the grid, not B's flattened lists).
  - Keep all 11 tiles, every link and inline hub handler, the skin switch, and the live hub
    repaint, including the band captions surviving a repaint.
- **GM Screen, B** (`templates/cosmere_gmscreen.html`). The mockups are
  `cosmere-gmscreen-*-session.html`.
  - Topic tabs: Combat / Conditions / Surges & Orders / Recovery & Stats / All references /
    Pinned.
  - Wider, unboxed tables. Combat leads with turn/damage references, then the injury tables, then
    the Plot Die.
  - Search covers every topic. Jump links open the right topic and focus its heading. Pins still
    move favourites first.
  - Keep all ten panels, eight tables, the rule text, cross-links and pin buttons, with identical
    text.

## Also fix (hub follow-ups from the decision log, confirmed on `main` 8f418f26)
- `cosStartCombat()` (~line 354) goes to the tracker even when adding the party failed. Check the
  response, and show the error instead of navigating.
- `cosWorld()` (~line 278) swallows a failed skin switch with `.catch(function(){})`. Show a
  toast.
- `cosMarkReady()` (~line 311) has no rejection handler and treats a non-JSON failure as nothing.
  Check `r.ok` and show "Failed" on a network error too.

## Acceptance
- [ ] Both pages match their chosen mockups at 1440px and 375px, in both skins, with no sideways
      scroll.
- [ ] Inventory tests on both pages: tiles, links, handlers and pins unchanged. The GM Screen also
      keeps 10 panels, 8 tables and identical rule text.
- [ ] GM Screen: tabs work by keyboard; search reveals the right topic; a cross-link (e.g.
      Deflect) opens and focuses its target.
- [ ] Hub: tests for the three error paths (failed add_party doesn't navigate; failed world
      switch and failed mark-ready show a message).
- [ ] Convention gates pass (numbers).

## Out of scope
- **Mistborn reference content.** The GM Screen still shows Stormlight rules under the Mistborn
  skin. That's a rules/content task (the Mistborn data is private and installed separately), not
  layout; leave it as is.
- The Bondsmith reference constant, the Plot Die logic, and the NPC generator content.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
