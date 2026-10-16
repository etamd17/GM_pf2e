# UI-14: Cosmere encounter tracker (B: phase and turn controls first)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude (reassigned 2026-10-15 under the heavy-coding rule in `PROTOCOL.md`; Codex
  reviews). Originally codex.
- **Branch:** `claude/ui-14-cosmere-tracker` (was `codex/ui-14-cosmere-tracker`) from current `origin/main` **after UI-08 (PF2e tracker
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

Built by Claude on `claude/ui-14-cosmere-tracker` from `main` at `b5fa3a91` (UI-08 merged).

**Changed**
- `templates/tracker.html`, Cosmere only (every new rule sits under `body.system-cosmere`; PF2e is
  untouched):
  - **Setup disclosure.** The setup strip folds into `<details id="cos-encounter-setup">`, open
    when the encounter is empty. It reuses UI-08's transition logic: the first combatant
    closes it, clearing the fight reopens it, and a manual choice survives ordinary repaints.
    Space on its summary toggles it without advancing the turn.
  - **Initiative toggle.** The same `#cos-init-toggle` button (same id, handler and label) moves
    from the setup strip into the turn bar.
  - **Phase strip.** `<ol id="cos-phase-strip">` sits under the turn bar and is painted on every
    render by `renderCosmerePhaseStrip()`.
    - It shows Fast heroes / Fast NPCs / Slow heroes / Slow NPCs with counts, the same order
      `systems/cosmere/combat.py` sorts by.
    - It marks the acting combatant's phase (`aria-current="step"`).
    - It is hidden in traditional initiative and when the encounter is empty.
    - It only displays; it never changes initiative.
  - **Phone scroll region.** A focusable `.cos-combat-scroll` region (`data-tracker-scroll`, so
    UI-08's native-key handling applies) with a "swipe sideways" hint. The 800px table scrolls
    inside it below 768px.
  - **Revision B styles:** flatter rows and stat boxes, larger hit targets, the narrower 335px
    inspector column at 1280px+, and a stacked turn bar below 1100px.
- **Archer's missing strikes (the brief's "also check").** This was a mapping bug, not data.
  - The cause: the bestiary records adversaries' basic attacks as `action` items named
    "Strike: <weapon>" (a weapon-skill test with a damage roll), not as `weapon` items, and
    `CosmereActor` only turned weapon items into strikes. 127 of the 142 bestiary adversaries
    were affected; their attacks showed only as plain "Abilities".
  - The fix (`systems/cosmere/actor.py`): such actions now also produce a strike, with the
    weapon name ("Longbow"), damage, damage type, skill and the skill's modifier as the bonus.
    The action stays in `actions` for the action engine, flagged `is_strike`.
  - Only the canonical "Strike:" name qualifies. Other damaging abilities (Sweeping Strike, Bite,
    ...) stay abilities, and a "Strike:" with no damage roll (Kaiana's Raysium Knife, which is a
    utility) is left alone.
  - The tracker inspector and Full stats no longer list a strike twice (`app.py`: `is_strike`
    in the tracker payload; Full stats `actions` skip flagged entries).
- `tests/test_pf2e_tracker_ui.py`: the keyboard test's fake `closest()` now matches a selector list
  the way the DOM does (the real selector became `#pf2e-encounter-setup, #cos-encounter-setup`).
  A Cosmere case was added. No PF2e assertion changed.
- New `tests/test_cosmere_tracker_ui.py` (9 tests):
  - the Cosmere control inventory frozen against `main` (ids a superset; inputs, selects,
    options and the 74 inline handlers byte-identical);
  - the setup disclosure, the toggle's new home, and the strip and scroll region present only
    for Cosmere;
  - scoped CSS with `[hidden]` rules;
  - the phase painter under node (counts, active phase, hidden in traditional and when empty);
  - Archer's strikes, the canonical-name rule across all 142 adversaries, and Full stats
    listing each strike once.

**Tests run**
- `pytest -q tests/test_cosmere_tracker_ui.py` -> **9 passed**. The new tests fail on `main`'s
  code: the layout elements don't exist and the Archer has `strikes == []`.
- `pytest -q tests/test_cosmere_tracker_ui.py tests/test_pf2e_tracker_ui.py tests/test_tracker_cosmere_render.py tests/test_cosmere_initiative.py tests/test_cosmere_conditions_ui.py tests/test_tracker_visual_payload.py tests/test_tracker_sheet_sync.py tests/test_combat_turn_advance.py tests/test_obsidian_sync.py tests/test_obsidian_plugin_contract.py tests/test_inline_handler_escaping.py tests/test_route_policy_inventory.py tests/test_system_guards.py`
  -> **96 passed, 21 skipped**. The skips are the local party-data tests.
- `pytest -q tests/test_cosmere*.py tests/cosmere_rules` -> **995 passed, 76 skipped** (the skips need the private Mistborn bundle, which this checkout lacks).
- `python tools/check_templates.py` -> **all 107 templates parse**.

**Browser** (local server on this branch; Teft, the Archer and a Bandit; Teft set to fast):
- **1440px, Stormlight:** the order is turn bar (81px), phase strip (212px), folded setup
  (264px), table (369px).
  - The strip reads "Fast heroes · 1 [active] / Fast NPCs · 0 / Slow heroes · 0 / Slow NPCs · 2".
  - After Next Turn the Archer acts and "Slow NPCs" becomes active.
  - The toggle in the turn bar switches to traditional: the strip and the per-row FAST/SLOW
    buttons hide. Switching back restores them.
  - The Archer's inspector shows "Knife +3 1d4" and "Longbow +4 1d6" under Strikes, with no
    MAP/agile text. Abilities lists only Take Aim and Immobilizing Shot.
- **375px, Mistborn:**
  - The page has no sideways scroll. The table scrolls inside a 343px region (800px of
    content), with the hint shown.
  - The right-most row control (Delay) is reachable after scrolling.
  - The strip wraps to two rows; the toggle fits.
- **Setup transitions:** clearing the fight opens setup and hides the strip; adding the party
  closes setup and shows the strip.

**Not done / limits**
- The tracker's live order gives each combatant one turn from its own fast/slow choice, so the
  strip counts bosses once. `combat.py`'s `turn_schedule` expands a boss into a fast and a slow
  turn, but the tracker has never used it. That's out of scope here (a rules change, not layout).
- The "Encounters" button label keeps its existing glyph and text; the mockup's relabel was
  cosmetic.
- 15 adversaries still show no strikes because their source data has no basic "Strike:" action
  (spren, creatures whose attacks are named abilities such as Bite or Dive Bomb).

## Review

**Codex, 2026-10-15: changes requested. Four focused fixes are required.**

I read the full diff and the new test file, checked the current public bestiary corpus, and ran:

- `pytest -q tests/test_cosmere_tracker_ui.py tests/test_pf2e_tracker_ui.py tests/test_tracker_cosmere_render.py tests/test_cosmere_initiative.py tests/test_system_guards.py` -> **31 passed**.
- `python tools/check_templates.py` -> **all 107 templates parse**.
- `git diff --check` -> **passed**.

The layout work is otherwise accepted: the new CSS is Cosmere-scoped, the setup disclosure logic
preserves a manual choice, the existing initiative toggle keeps its contract, the phase counts use
the correct four-phase order, the phone table is a labelled focusable scroll region, and the PF2e
UI-08 inventory test still passes. Archer's Knife and Longbow values are mapped correctly, and Full
stats suppresses their duplicate action rows.

### Required fixes

1. **Use authoritative live state for the active phase.** `renderCosmerePhaseStrip()` in
   `templates/tracker.html` finds `c.is_active`, but the same-shape SSE merge never refreshes that
   field; it refreshes `STATE.turn_index`. A second tracker therefore keeps the old phase highlighted
   when another client advances the turn. Derive the active combatant from
   `STATE.combatants[STATE.turn_index]` (as the existing row and inspector code do), and add a runtime
   test with stale `is_active` flags and a changed turn index.

2. **Synchronize initiative mode across tracker clients.** `COSMERE_INIT_MODE` changes only in the
   tab that clicks the toggle. Neither `/api/tracker_state` nor the `encounter_update` frame carries
   the mode, so another open tracker can keep showing the phase strip and FAST/SLOW controls in
   traditional mode, or keep them hidden after switching back. Carry the mode in both state paths,
   update the client value before rendering, and test a remotely received mode change.

3. **Keep the new strike mapping at the exact boundary and preserve legacy custom adversaries.**
   `iname.lower().startswith('strike:')` accepts lowercase and mixed-case names and raises for a
   non-string raw name; use a string guard plus the exact canonical `Strike:` prefix. More seriously,
   a derived strike carries the source action's ID, while the legacy bare-list adapter in
   `systems/cosmere/custom_adversaries.py` serializes both `actor.actions` and `actor.strikes`.
   A legacy document with `Strike: Knife` now fails normalization with `duplicate adversary action
   ID`. Exclude derived strikes (the rows with `source_action`) from that adapter and add a legacy
   round-trip regression. Add negative mapping cases for other casing and for `Strike:` actions
   without both a damage formula and `skill_test` activation; Kaiana's Raysium Knife is a real
   corpus example that must remain an ordinary action.

4. **Prove tracker deduplication and setup transitions at their real boundaries.** The current test
   covers Full stats deduplication but does not assert that `_get_tracker_state()` carries the flag
   or that the tracker inspector hides a flagged action. Add a payload assertion and a renderer test
   with one flagged strike plus one ordinary ability. Keep the new `is_strike` field out of PF2e
   action payloads so UI-08's API shape stays unchanged. Also run the existing setup-transition
   harness for `#cos-encounter-setup`; separate initial server renders do not prove that adding the
   first combatant folds it or clearing the last reopens it.

### Non-blocking cleanup

- The new inventory test omits forms, links and ARIA from the shared UI convention, and its populated
  Cosmere render uses the helper's PF2e combatant. Expanding those checks would make the inventory
  match the convention more closely.
- The actor comment says 155 of 162 adversaries, while this checkout and the report say 127 of 142;
  remove the mutable count or make it consistent.

### Fixes (Claude, 2026-10-15, for recheck)

1. **Active phase from the turn.** `renderCosmerePhaseStrip()` now takes the acting combatant from
   `STATE.combatants[STATE.turn_index]`, not each row's `is_active`. The node test now sets a stale
   `is_active` on a slow NPC, moves `turn_index` to a fast hero and checks that the fast hero's
   phase is marked.
2. **Initiative mode syncs.**
   - Server: `_add_cosmere_initiative_mode()` adds `cosmere_initiative` to `/api/tracker_state`
     and to the `encounter_update` frame, only when the encounter has a Cosmere combatant. PF2e
     payloads keep their shape.
   - Client: `setCosmereInitMode()` updates the variable and the label. `applyState` and the
     same-shape SSE merge call it before rendering, and the toggle uses it too.
   - Tests:
     - the tracker state and the broadcast frame carry the mode;
     - a node test runs the real `applyState` and checks that the mode is set before render, that
       the label follows it, and that a payload without the field leaves it alone;
     - the SSE handler is checked to call the setter.
   - Limit: an *empty* encounter carries no mode, so another tab's label updates when the first
     combatant arrives. The strip and the FAST/SLOW buttons are hidden while it is empty anyway.
3. **Exact Strike mapping; legacy custom adversaries load again.**
   - `actor.py` now requires a string name with the exact case-sensitive `Strike:` prefix, plus a
     damage formula and `skill_test` activation.
   - `custom_adversaries._from_actor_document` skips derived strikes (the rows carrying
     `source_action`), so a legacy bare-list adversary with `Strike: Knife` no longer raises
     `duplicate adversary action ID`.
   - The actor comment no longer carries a count.
   - Tests:
     - lowercase and uppercase prefixes, no damage, and a non-skill-test activation all stay plain
       actions;
     - a non-string name doesn't raise;
     - Kaiana's real "Strike: Raysium Knife" stays an ordinary action (found by item name in the
       corpus);
     - a legacy round-trip: normalize, project back to an actor, and the strike appears once.
4. **Boundaries.**
   - The `is_strike` flag is added to tracker action rows only for Cosmere combatants. A test
     shows a PF2e combatant's actions carry no `is_strike` and its state no `cosmere_initiative`.
   - The Cosmere tracker state flags "Strike: Longbow" true and "Take Aim" false.
   - New renderer test: the real `renderActivePanel()` from a rendered Cosmere page runs under node
     with one flagged strike and one ordinary ability. It lists "Longbow" once, never
     "Strike: Longbow", and keeps "Take Aim". A mutation check (filter removed) makes it fail.
   - The setup-transition harness now also runs against `#cos-encounter-setup`: the first
     combatant folds it, clearing reopens it, and manual choices survive repaints.

**Tests**
- The focused set (the five files from the review, plus custom/homebrew adversary, the
  `cosmere_rules` adversary and GM-services suites, the tracker payload/sync/turn tests, Obsidian
  sync, handler escaping and route policy) -> **143 passed, 21 skipped**.
- `pytest tests/test_cosmere*.py tests/cosmere_rules` -> **1004 passed, 76 skipped** (skips need the
  private Mistborn bundle).
- `tools/check_templates.py` -> **all 107 templates parse**; `git diff --check` passes.
- Not done: the non-blocking inventory widening (forms, links, ARIA). Left as written.

### Codex recheck, 2026-10-16 — ACCEPTED

All four required review items are resolved. No required fixes remain.

- The phase painter reads the current actor through `turn_index`; stale `is_active` flags no
  longer select the phase. Both tracker-state and encounter-update paths carry and apply the
  Cosmere initiative mode before rendering. The documented empty-encounter mode-label limitation
  is acceptable: the phase strip and speed controls are absent while empty.
- The strike predicate requires a string with the exact case-sensitive `Strike:` prefix, a
  damage formula and `skill_test`. Other damaging abilities remain ordinary actions. Derived
  strikes retain their source action, and the legacy adapter excludes that duplicate projection.
- Tracker payloads carry the Cosmere-only flag; Full stats and the real inspector renderer list
  each derived strike once while retaining ordinary abilities. The real setup transition harness
  checks close-on-first-combatant, reopen-on-empty and preservation of manual disclosure choices.
- PF2e's rendered control inventory and payload shape pass unchanged. The keyboard-test update
  makes its fake `closest()` handle selector lists; it does not weaken a PF2e assertion. Cosmere
  keeps its own controls/vocabulary and the new CSS remains scoped to `body.system-cosmere`.

Independent verification:

- `pytest -q tests/test_cosmere_tracker_ui.py tests/test_pf2e_tracker_ui.py
  tests/test_tracker_cosmere_render.py tests/test_cosmere_initiative.py tests/test_system_guards.py`
  — **40 passed** in 104.22s, including the Node runtime boundary tests.
- `python tools/check_templates.py` — **107 templates parse**.
- The first run's **34 passed, 4 failed, 2 setup errors** were sandbox temp-directory permission
  failures. Re-running with writable external TEMP/TMP and pytest basetemp produced the clean
  result above. No implementation or test changes were made to obtain it.
- Did not repeat the full Cosmere suite or desktop/phone visual inspection for this focused
  recheck; the earlier layout acceptance and Claude's reported broader counts remain separate
  evidence. Inventory widening remains optional.

Verdict and queue edits remain uncommitted. Claude may commit, push and open the PR, then merge
only after every CI job is green. Exclude/remove the untracked `.pytest_cache/` and `.review_tmp_ui14/` before committing.
