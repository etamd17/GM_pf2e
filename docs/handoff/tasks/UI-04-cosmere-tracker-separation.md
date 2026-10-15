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

### Implementation

- The Cosmere row and inspector now omit PF2e MAP columns, quick-condition menus,
  Elite/Normal/Weak adjustments, persistent damage, death/recovery controls, Recall Knowledge,
  PF2e spell/feat sections, and PF2e condition remnants. Strikes and the shared action/reaction
  controls remain available.
- I chose the smaller quick-condition option from the brief: the row `+` menu is absent for
  Cosmere combatants. The inspector and Multi bar use the existing Cosmere condition vocabulary
  and `/api/cosmere/combatant/<id>/condition` route.
- The Cosmere Multi bar omits Basic saves and PF2e valued/timed condition inputs. Its damage type
  and condition lists now follow the campaign even before a combatant has been staged.
- Page-level PF2e tools are not emitted in a Cosmere campaign: Send Loot, Request Check, and the
  custom PF2e monster form. Targeted handouts use the Cosmere roster. Tracker labels now use
  Health, adversary, Tier, and the correct d20+Speed initiative description where applicable.
- PF2e Treat Wounds and Hero Point inbox events are ignored by the Cosmere tracker. The stat
  panel and condition chips also filter legacy PF2e fields from Cosmere combatants.
- The Full stats endpoint now returns a Cosmere-specific payload for both heroes and adversaries,
  so a Cosmere hero no longer dereferences PF2e class fields. It normalizes Cosmere strikes and
  unlocked skills and carries the Cosmere tracker block for the modal.
- PF2e-only server routes reject Cosmere targets with a clear 400 before mutation: quick
  conditions, persistent damage, Elite/Weak, Basic saves, recovery checks, and both Recall
  Knowledge endpoints. Missing legacy `system` values continue to mean PF2e.

### Sweep decisions

- Focus, action/reaction tracking, and Perception remain because each exists in the Cosmere
  implementation. The generic Secret Roll tool remains for the same reason.
- D&D 5e files and routes were not changed.

### Tests

- `pytest -q tests/test_tracker_cosmere_render.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py`
  -> **28 passed in 78.24s**.
- `pytest -q tests/test_stale_combatant_guard.py tests/test_save_damage_resolver.py tests/test_recovery_check.py tests/test_tracker_sheet_sync.py tests/test_map_combat_extras.py`
  -> **38 passed, 21 skipped in 55.24s**. The skips are the existing optional-data skips.
- `python tools/check_templates.py` -> **all 107 templates parse**.
- Browser verification remains for Claude: one Cosmere fight and one PF2e fight at 1440px.

## Review

**Claude, 2026-10-15: accepted. No required fixes.**

Read the full diff: `app.py` (the `_pf2e_only_combatant_error` guard on 7 routes, and the Cosmere
branch in `_combatant_detail_payload`), `tracker.html` and the new tests. Checked live at 1440px on
a local server on this tree. Did not rerun the test suite.

**Cosmere fight** (Teft Varenn, the bestiary Archer, and a custom "UI04 Spearman" with a Spear
strike):
- **Rows:** no quick-condition "+" menu and no Elite/Weak; the column reads "Health" and the
  adversaries are labelled ADVERSARY / Tier.
- **Inspector:** no MAP, agile, persistent damage, DC 15, Recall Knowledge, dying/recovery,
  Hit Points, Fortitude/Reflex, Off-Guard or Frightened. That includes the Spearman's strike
  area. Its condition picker lists the 14 Cosmere conditions.
- **Multi bar:** damage types impact/keen/energy/spirit/vital, the Cosmere condition list, and
  no basic save. Applying Prone to the hero and the adversary through Multi worked: both show
  `prone: true` through the Cosmere condition endpoint.
- **Full stats:** for the hero, the Spearman and the Archer it returns `system: cosmere` and
  renders the Cosmere block (Phys/Cog/Spir, Deflect, Focus, Investiture, attributes, Health,
  attacks and skills) with no PF2e terms and no console errors. Before this fix the hero
  path would have dereferenced PF2e class fields.
- **Routes:** POSTs to `toggle_elite_weak`, `toggle_condition` and `set_persistent_damage` for the
  Spearman each return 400 with a plain message.
- The only "Elite" text on the page is the bestiary adversary "Elite Shardbearer" in the add
  list.

**PF2e fight** (Goblin Warrior and the imported level-10 Kyle):
- Rows keep both quick-condition menus and the Elite/Normal/Weak buttons.
- The inspector keeps MAP, agile, Recall Knowledge and persistent damage.
- The Multi bar keeps the PF2e conditions and damage types and the basic save.
- The loot panel is present, and Full stats shows the PF2e block (AC, Fort/Ref/Will, both
  strikes).

Notes (not required):
- The 400 messages read "Elite and weak adjustments is a PF2e-only mechanic...". "Are" would read
  better for plural feature names; it's cosmetic.
- The bestiary Archer has no strikes in its tracker or Full stats payload (`strikes: []`). That
  comes from its source data, not from this change. Worth a look when the Cosmere tracker gets its
  own layout pass (UI-13).

Codex applied the optional wording polish after review: the response now reads
`PF2e <feature> cannot be used...`, which works for singular and plural feature names.
