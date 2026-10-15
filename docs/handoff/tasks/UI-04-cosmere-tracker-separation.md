# UI-04: Keep PF2e mechanics out of the Cosmere tracker

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** codex (standard model, high effort: `tracker.html` is large and shared)
- **Branch:** `codex/ui-04-cosmere-tracker` from current `origin/main`
- **Depends on:** none
- **Context:** user decision 2026-10-15: "hide all PF2e in the Cosmere tracker. Each universe
  (D&D, PF2e, Cosmere) should be separate and shouldn't bleed over with mechanics." The bleed was
  found in `tasks/UI-00.md` and listed in `tasks/UI-03.md` > Out of scope. Template:
  `templates/tracker.html` (it already branches with `c.system === 'cosmere'`, `isCos`, and
  `active_system == 'cosmere'`). Line numbers below are from `main` at `4664c887`; re-find them.

## Goal
In a Cosmere fight the tracker shows only Cosmere mechanics. PF2e fights look and work exactly as
today. Nothing is deleted from PF2e; the PF2e-only parts are simply not rendered (or are inert)
for Cosmere combatants and in a Cosmere campaign.

## What to hide in Cosmere (all confirmed on `main`)
1. **Strikes header "MAP -5/-10"** and the M2/M3 / agile lines in the inspector (~3253-3268).
   Cosmere adversaries have `strikes`, so they show today. Show the strike itself without the
   multiple-attack-penalty columns.
2. **Row "+" quick conditions** (`QUICK_CONDS`: Off-Guard, Frightened..., ~2650, ~2877-2880, and
   `quickCond` ~2683, which posts PF2e conditions). For Cosmere rows, either offer Cosmere
   conditions through the existing `cosmereCondition(...)` path or hide the "+" quick menu. Pick
   whichever is smaller; say which in the report.
3. **Elite / Normal / Weak buttons** (~2808-2812). PF2e creature adjustments; hide for Cosmere.
4. **Persistent damage**: the inspector block with its "DC 15" flat check (~3182-3226), the row PD
   tag (~2793), and the end-of-turn persistent-damage and Frightened reminders (~3371-3372).
5. **Multi-target bar** (~2026-2040): the "Basic save" Reflex/Fortitude/Will choice, and the
   condition list filled from PF2e `CONDITIONS_LIST` (~3732). Use `COSMERE_CONDITIONS` for Cosmere
   (the helper at ~5584 already picks between them; reuse it) and hide the basic-save choice.
6. **Sweep for anything else.** Read the tracker top to bottom with a Cosmere encounter loaded and
   list every remaining PF2e-only term or control you find (hero points, focus spells, recall
   knowledge DCs, PF2e degrees of success wording, etc.). Fix the clear ones. Put anything you're
   unsure about in the report for the user rather than guessing.

## How to gate
- Gate on the **combatant's** system (`c.system === 'cosmere'`) for per-row and inspector parts,
  and on the campaign (`active_system`) for page-level parts. Don't add a third way to detect it.
- Server side: any route these controls call (e.g. quick condition, elite/weak, persistent damage)
  should refuse a Cosmere combatant with a clear 400 rather than silently applying PF2e rules.
  Check what each does today before changing it; if a route is shared and already
  system-aware, leave it.
- Every element you hide by attribute whose class sets `display` needs `.x[hidden]{display:none}`
  (CLAUDE.md rule). Inline handlers keep the JS-escaping rule.

## Acceptance
- [ ] Render the tracker with a Cosmere encounter (a PC plus an adversary with strikes): none of
      items 1-5 appear in the HTML or the rendered rows/inspector. With a PF2e encounter they all
      still appear. Write both as tests (extend `tests/test_tracker_cosmere_render.py`).
- [ ] The routes behind items 2-4 refuse Cosmere combatants (tests).
- [ ] `pytest -q tests/test_tracker_cosmere_render.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
      plus any PF2e tracker tests you touched, and `python tools/check_templates.py` (numbers).
- [ ] Browser (Claude does this if you can't): a Cosmere fight and a PF2e fight, 1440px.

## Out of scope / do not touch
- D&D 5e has its own tracker; nothing here changes it.
- Cosmere rules themselves (no new Cosmere mechanics beyond reusing existing Cosmere conditions).
- Map, player sheets.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

## Review
