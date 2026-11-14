# PE-13c: Max-HP and pool reconciliation hook

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-13a)
- **Branch:** `claude/pe-13c-reconciliation` from current main after PE-13a merges. Record the exact starting SHA.
- **Depends on:** [PE-13a](PE-13a-state-repositories.md) (`CharacterStateRepository`, and `EncounterStateRepository.is_combatant`). Independent of PE-13b and PE-14a; may run in parallel with both. Imports nothing from PE-13b or PE-14.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: lock order (C:156-164), max-HP reconciliation (C:168-194), pools and reservations (C:196-212), removed pools, effects, choices, combatants, preview (C:213-220), worked example 2 (C:1006-1014), re-import keeps `hp_floor_debt` and reconciliation prompts (C:680), the round-trip debt example (C:768-782), PE-13 row (C:1089), PE-15/16-18 own the domain mapping and the reservation rule's use (C:1091), PE-41 supplies item changes (C:1098).
  - D&D effective max today: `systems/dnd5e/combat.py:60` `effective_max`.
  - One active encounter per campaign: `uq_encounter_states_one_active` (`core/persistence/models.py:695-701`).
  - Downstream mapping: PE-14b's executor answers any `core.commands` exception carrying `code` and `http_status` from those attributes, with receipt-or-not taken from the code table (C:352-366). It imports no PE-13c symbol, so the attribute names below are the interface.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 270 product lines and 380 test lines. Stop and report past about 370 product lines. (Planning estimate.)

## Goal

A system-neutral reconciliation hook that computes, from before/after effective maxima and pool maxima supplied by a rules adapter, exactly the HP, debt, pool, reservation and prompt changes the contract defines, as one ordered list that serves both preview and commit. No adapter or command uses it yet.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Pure core, adapter inputs.** The hook takes an adapter object per rules profile (`effective_max(build, play, items)`, `is_dead(play)`, `pools(build, play, items)`, `invalid_choices(build, play)`, `orphaned_effects(...)`). PE-15 (D&D, wrapping `combat.py:60`) and PE-16-18 (PF2e, drained included, C:170-171) implement them; this task ships only a synthetic test adapter. The not-trigger rule (C:172-175) is the adapter's: it holds drained / `max_hp_reduction` constant when computing both maxima.
2. **Reservations as input.** Action rows do not exist until PE-30's follow-ons, so the hook takes reservations as a list of `(pool, action_id, declare_seq, amount, targets)` and returns which to release; PE-30's follow-on applies the release to its rows and sets targets to `held` with reason `reservation_invalidated` (C:210-211). `declare_seq` is the seq of the action's declare event, which PE-14b's `ctx.emit` allocates under the stream lock at emit time; the hook only orders by it.
3. **Storage of debt and prompts.** `hp_floor_debt` (non-negative, default 0, C:177-178) and reconciliation `choice` prompts (`origin: reconciliation`, C:216-217) live in the `play` payload; the hook returns the new payload values and the caller writes them through PE-13a's `CharacterStateRepository` (one `play` revision bump; the repository refuses the write unless authority is `db`).
4. **Result shape.** A list of entries in commit order, returned as `result.reconciliation[]` (C:169-170). The entry field names are this task's to choose (the contract fixes the contents, not the keys); record them in the report for PE-15/18b and PE-31.
5. **Own refusals, mapped by attribute.** This task defines its two refusals in `core/commands/reconciliation.py` and does not import PE-13b's `CommandRefusal`:
   - `ReservationBlocked(action_ids)` with `code = 'rule_violation'`, `http_status = 422`, `action_ids` (the blocking actions, in `declare_seq` order);
   - `InEncounter(character_id)` with `code = 'in_encounter'`, `http_status = 422`, `character_id`.
6. **No locks, no writes.** The hook reads only rows the caller has already locked: the command type's `lock_plan(ctx)` (PE-14b) takes the encounter (lock-order step 2) and the `character_states` rows (step 4) before checks 4-9 run (C:156-164). The hook takes no row lock and writes nothing; the caller writes its returned values.

## Approach

Allowed files:
- new `core/commands/reconciliation.py`
- `core/commands/__init__.py`: created if absent, with exactly this content (one line plus a trailing newline, nothing else): `"""Command pipeline. Import submodules directly; this package re-exports nothing."""`. Whichever of PE-13b, PE-13c and PE-14a lands first creates it, and the others leave it as is. The identical bytes keep parallel branches free of add/add conflicts. Callers import submodules.
- new `tests/persistence/test_reconciliation.py`

1. `reconcile_hp(hp, debt, effmax_before, effmax_after, dead)`: dead -> no change (C:179); `hp == 0` -> HP 0, debt 0 (C:180-181); `hp > 0` -> `raw = hp + (after - before) - debt`, `raw >= 1` gives `hp' = min(raw, after)` and debt 0, else `hp' = 1`, debt `1 - raw` (C:182-188). Temp HP untouched (C:194).
2. `debt_after_heal(hp_after, debt, rest_to_max)`: debt cleared at HP 0 or a rest that sets HP to max; healing otherwise changes HP only (C:189-190). Exposed for PE-15/17's vitals and rest commands.
3. `reconcile_pools(pools_before, pools_after, used, reservations, user_initiated)`:
   - user-initiated, and the new max is below `used + reserved` on a pool holding a reservation, or a reserved pool is removed -> raise `ReservationBlocked(action_ids)` (Decision 5), naming the blocking actions (C:199-203); `override` is not consulted (C:203-204);
   - automatic: release reservations newest first by `declare_seq` until `used + reserved <= max'`, or all if the pool is gone, then clamp `used` (C:205-212);
   - unreserved pool lowered: clamp and list (C:197-198); removed unreserved pool: drop and list its used count, and list dependent effects to end (C:213-214).
4. Orphaned effects end with an entry (C:215); invalid choices become `choice` prompts, never blocking and never silently removed (C:216-217).
5. `refuse_publish_in_encounter(session, campaign_id, character_id, override)`: raise `InEncounter(character_id)` when PE-13a's `EncounterStateRepository.is_combatant(campaign_id, character_id)` (active encounter only) is true, unless `override` is set (C:218-219). Whether the principal may send `override` at all is PE-13b's check 4 (403 `not_permitted`, C:137); this function trusts the flag it is given.
6. `preview(...)` and `apply(...)` share one function; preview returns the same list (C:220).

## Acceptance

- [ ] HP table: 3/45 -> max 37 gives 1 HP debt 6; back to 45 gives 3 HP debt 0 (C:187-188); worked example 2 gives 31/45 from 24/38 (C:1010); 1/37 debt 6 healed to 37/37 then re-equipped gives 39/45, and 20/37 debt 0 re-equipped gives 28/45 (C:768-781); hp 0 clears debt; dead changes nothing; temp HP unchanged; unequip/re-equip never heals (property test over random sequences).
- [ ] Debt cleared by a rest to max and at 0 HP, not by a partial heal.
- [ ] User-initiated pool lowering below `used + reserved` raises `ReservationBlocked` with `code == 'rule_violation'`, `http_status == 422` and the blocking `action_ids`, with and without `override`.
- [ ] Automatic lowering releases reservations newest-first by `declare_seq` and lists released actions and their targets in that order; then clamps `used`.
- [ ] Removed reserved pool (automatic) releases all its reservations; removed unreserved pool is dropped and listed with its dependent effect.
- [ ] Invalid choices produce `origin: reconciliation` prompts; nothing is removed silently.
- [ ] `InEncounter` (`code == 'in_encounter'`, `http_status == 422`, `character_id`) for a combatant in the active encounter, read through `is_combatant` on a SQLite fixture written through PE-13a; no refusal with `override`; no refusal when the character's only encounter is not active.
- [ ] Preview list equals the applied list for every case above.
- [ ] `core/commands/reconciliation.py` imports nothing from PE-13b's or PE-14's modules (a test inspects its imports), and `core/commands/__init__.py` has no re-exports.
- [ ] `python -m pytest -q tests/persistence/test_reconciliation.py tests/persistence/test_state_repositories.py` passes; `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: real D&D and PF2e adapters and the commands that call the hook (PE-15, PE-16-18, PE-18b); item changes as triggers (PE-41); action rows and holding targets (PE-30 follow-ons); events for ended effects and `character.build_reconciled` (registered by PE-15/18b and emitted through PE-14b's `ctx.emit`); the `override` permission check (PE-13b check 4, PE-14c `override_allowed`); mapping the refusals to responses and receipts (PE-14b); the encounter and character row locks (the command type's `lock_plan`, PE-14b).
- Do not change `systems/dnd5e/combat.py` or any PF2e HP code: today's D&D cap-only behaviour stays in file authority (the behaviour change applies to db commands, C:191-193).
- Do not edit `core/persistence/__init__.py`.
- Keeping `hp_floor_debt` and `origin: reconciliation` prompts across a file-era round trip is PE-20's stale-replace write (revision = stale + 1, C:680); PE-13a exposes no such write and this task adds none.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
