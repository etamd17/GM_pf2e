# UI-15: Cosmere Party Vitals (B) and character roster (hybrid)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, high effort: both pages carry live updates added in UI-03)
- **Branch:** `codex/ui-15-cosmere-vitals-roster` from current `origin/main`
- **Depends on:** none (UI-03's live-update fixes are merged)
- **Context:** read the conventions in `tasks/UI-06-pf2e-gm-hub-and-gmscreen.md` and the
  Cosmere notes in `tasks/UI-13-cosmere-dashboard-and-gmscreen.md`. Mockups:
  - `cosmere-party-vitals-gm-<skin>-<state>-session.html` (Vitals B);
  - `cosmere-party-roster-<role>-<skin>-<state>-hybrid.html` (roster hybrid);
  - `cosmere-party-revisions.css` / `-preview.js`.

  Decision log entries: "Cosmere Party Vitals and character overview", "Cosmere party preview
  checks", "Cosmere party source follow-ups", "Cosmere party selections".

## Goal
- **Party Vitals, B** (`templates/cosmere_gm_vitals.html`):
  - each hero is one aligned row of identity, defenses, Health / Focus / Investiture and status;
    rows stack on phones;
  - a clickable attention strip on top (downed, critical, hurt, injuries, many conditions)
    jumps to the row;
  - when a hero has more than four conditions, the "+N" badge expands to the full list;
  - keep the zero-Investiture omission, the acting marker and the risk wording.
- **Character roster, hybrid** (`templates/cosmere_pcs.html`):
  - A's familiar cards, with the inline defenses and access labels;
  - B's attention summary above them;
  - the private-draft/import section moves, as the **same** markup, under a labelled
    disclosure;
  - keep every creation / import / invite / bestiary / sheet destination and the player-vs-GM
    branches (players have no invite or bulk-import controls).

## Must not regress (UI-03 work on these pages)
- Live updates come through `appSSE('cosmere_player_state', ...)`: Vitals cards are keyed by
  `data-pid` with maxima refreshed from the payload, and the roster repaints Health / Focus /
  Investiture in place.
- The attention strip must update from the same events. When a hero recovers, their alert
  disappears without a reload; when one goes down, it appears.
- The roster shows **current / max** Investiture.
- One Mistborn character without the Mistborn data must not break either page (the UI-03
  follow-up fallback).

## Acceptance
- [ ] Both pages match their chosen mockups at 1440px and 375px in both skins, populated and
      empty.
- [ ] Inventory tests: links, inputs and disabled-sheet states unchanged; the player view has no
      GM controls.
- [ ] Attention strip tests: a fixture with downed / hurt / injured heroes produces the right
      entries; an SSE update clears and adds entries; clicking an entry focuses its row.
- [ ] Existing `tests/test_cosmere_gm_vitals.py` and `tests/test_cosmere_status.py` pass, plus the
      convention gates (numbers).

## Out of scope
- Rules, sheet access, the PF2e party page (UI-09).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
