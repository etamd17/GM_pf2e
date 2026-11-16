# PE-13c: Max-HP and pool reconciliation hook

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** changes requested (Codex implementation review 1, 2026-11-15; R1-1; built from main `737555a2`)
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
  - New `core/commands/reconciliation.py`: `reconcile_hp`, `debt_after_heal`, `reconcile(adapter, *, before, after, play, hp, used, reservations, user_initiated) -> Reconciliation`, `apply` and `preview` (both call `reconcile`), `refuse_publish_in_encounter`, `Reservation(pool, action_id, declare_seq, amount, targets)`, `Snapshot(build, items)`, `ReservationBlocked` and `InEncounter`. Imports only `core.persistence.state_repositories` (for `is_combatant`), nothing from PE-13b or PE-14.
  - `core/commands/__init__.py` already existed (PE-13b) with the agreed bytes; left as is.
  - New `tests/persistence/test_reconciliation.py`: 77 tests.
- **Interface for PE-15, PE-16-18, PE-18b and PE-31** (Decision 4 left the keys to this task):
  - Adapter methods: `effective_max(build, play, items)`, `is_dead(play)`, `pools(build, play, items) -> {pool: max}`, `invalid_choices(build, play) -> [{"id", ...}]`, `orphaned_effects(build, play, items, removed_pools) -> [{"id", "pool"?, ...}]`. `orphaned_effects` receives the removed pool names so the adapter can end effects that depended on them; `pool` on an effect ties it to that pool's entry.
  - The caller passes the current `hp` and pool `used` counts, read with its adapter, because both live under system-specific play keys. `used` must name only pools the adapter reports for the before state (anything else is `ValueError`, a caller bug): PE-15 must filter orphaned `item:*` resource keys (C:680) out of it.
  - `play` keys this hook owns: `hp_floor_debt` (default 0) and `choice_prompts` (a list; each reconciliation prompt is `{"kind": "choice", "origin": "reconciliation", "choice": {...}}`). `Reconciliation.play_updates(play)` returns both, existing prompts kept; the caller writes them with `hp` and `used` in one `play` write. A choice that already has a stored reconciliation prompt is not prompted again.
  - Entries, in commit order: `hp` (`hp_before`, `hp_after`, `debt_before`, `debt_after`, `effmax_before`, `effmax_after`), then per pool in name order its `reservation_released` entries newest first (`pool`, `action_id`, `declare_seq`, `amount`, `targets`, `reason: "reservation_invalidated"`) and then its `pool_clamped` (`pool`, `max_before`, `max_after`, `used_before`, `used_after`) or `pool_removed` (`pool`, `used`, `effects`), then `effect_ended` (`effect_id`, `pool`, `effect`), then `choice_prompt` (`prompt`). Newest-first is per pool, as the contract words it.
  - `Reconciliation.released` has one item per reservation; `released_actions` lists each action once, for PE-30's follow-on to hold its targets.
- **Judgments:**
  - The HP rule runs only when the effective maximum moves (C:168-170). With the maximum unchanged, HP and debt are left exactly as they are, so a choice-only publish or a pool-only change never charges an outstanding debt as damage. A dead character gets no `hp` entry.
  - `ReservationBlocked.action_ids` names each blocking action once, in `declare_seq` order, across every pool that blocks; actions on reserved pools that still fit are not named.
  - `refuse_publish_in_encounter` treats any non-`None` `override` as set; whether the principal may send one is PE-13b check 4.
  - Counts (HP, debt, maxima, used, amounts) must be non-negative integers; booleans are refused.
- **Tests run:**
  - `tests/persistence/test_reconciliation.py`: 77 passed. Brief acceptance command with `test_state_repositories.py`: 143 passed.
  - The never-heals property test runs 40 seeded random sequences of maximum changes and checks `hp - debt - max` is conserved and the start state is restored exactly (no `hypothesis` in the lock file, so it uses `random.Random(seed)`).
  - Mutation checks: 14 hand mutations (debt charged at an unchanged maximum, preview forcing automatic, every action named, all effects on a removed pool, no de-duplication, oldest-first release, no clamp, no 0-HP clear, no `min`, partial heal clearing debt, duplicate prompts, override ignored, and others). All are killed except one equivalent mutant (`break` to `continue` in the release loop: once a pool fits, later reservations still fit).
  - `git diff --check` clean. CI numbers for the head are in the PR.
- **Self-review.** A three-lens adversarial review (contract, pool edge cases, mutation) with two verifiers per finding confirmed two product defects, both fixed: (1) debt was charged as damage whenever the hook ran with an unchanged maximum (37/37 with debt 6 became 31/37 on a choice-only publish); (2) an action holding reservations in two pools was named twice in `ReservationBlocked`. It also confirmed three weak tests, now strengthened: preview was never compared on the refusal path, no refusal case had a reserved pool that still fits, and the `pool_removed` effect list was not asserted exactly. Rejected: unknown `used` keys raising (documented as a caller precondition above), releases ordered per pool rather than globally (the contract orders per pool), and starting states where `used + reserved` already exceeds the maximum (the contract never allows them).
- **Skipped / not verified:** no real adapter (out of scope); no PostgreSQL case (the hook takes no lock and writes nothing; `is_combatant` is PE-13a's and already covered).
- **Known wrong / limitations:**
  - Over the estimate: 320 raw product lines against about 270 (under the 370 stop), mostly the module docstring that fixes the interface above; 429 test lines against about 380.
  - A reserved pool already over its maximum before the change (not reachable under the contract) is refused or released like any other: the rule compares only the new maximum with `used + reserved`.
- **Follow-ups (not done here):** PE-15/16-18 implement the adapters and filter `used`; PE-30's follow-on applies `released_actions`; PE-20's stale-replace write keeps `hp_floor_debt` and `choice_prompts` across a round trip (C:680).

## Review

### Brief review 1 — accepted (Codex, 2026-11-14)

Reviewed PR #259 at `4b21f404` against `791936f1`. **No findings in this brief.**
HP floor debt, non-healing equipment round trips, reservation refusal and
newest-first release, explicit exception attributes and the read-only adapter
boundary agree with the accepted contract. The shared initializer bytes and
separate module permit the proposed parallel window. The estimate and stop
threshold are reasonable for the pure hook and its test matrix.

This accepts the instructions, not an implementation. It remains blocked on
accepted and merged PE-13a, whose brief needs corrections elsewhere in this
review. PR #259 as a whole is not accepted yet. Docs only; no product tests
ran. Review edits are uncommitted; nothing was merged.

### Brief review 2 - acceptance carried forward (Codex, 2026-11-14)

Rechecked compatibility with the revised PE-13a/13b/14b interfaces at `51ac0e51` against `791936f1`. **The round-1 acceptance stands; no new findings.** The pure reconciliation hook, exception attributes and docstring-only package initializer remain compatible. Scope, estimate and stop line are unchanged. Implementation remains blocked on accepted and merged PE-13a. Docs-only review; no product tests ran.

### Implementation review 1 - changes requested (Codex, 2026-11-15)

Reviewed PR #262 at `a3940cc1ccab70240f4568f3b4b4a34df162fc77` against `737555a2`, including the Report's interface judgments and every Acceptance item. **One interface fix is required before implementation acceptance.**

1. **R1-1 [P2] Give effect changes distinct before/after play inputs.** `Snapshot` at `core/commands/reconciliation.py:107-111` holds only build/items, and `reconcile` at `:254-260` passes the same `play` object to both effective-max and pool calculations. Owned effects live in play (C:65), and an effect change/expiry is an explicit reconciliation trigger (C:168-175, C:205-211). With unchanged build/items and an HP-bonus effect removed from play, either possible `play` argument makes the hook see identical maxima, so it silently omits the HP adjustment. For an effect-provided pool, supplying post-expiry play makes its legitimate pre-change used count or reservation look like an unknown pool and raises `ValueError` instead of performing the required automatic release/removal. This cannot be implemented by a stateless adapter using the documented inputs; it would need to hide play state in build/items or depend on call order.

   Provide distinct play views for the before/after derivations, or an explicit interface for derived before/after maxima and pools. Keep only the fields with their own HP rules constant as C:172-175 requires, rather than freezing all of play. Document which inputs supply current HP/debt/used counts, death state, prompts and effect inspection so later adapters can apply the returned changes without losing the underlying effect update. Add a stateless adapter regression where only `play.effects` changes: an HP bonus expires; a resource pool disappears with a used count and with a reservation; and preview equals apply without mutating either input. Retain the unknown-pool refusal for keys genuinely absent from the before state.

**Reproduction:** `../output/pe13c-review/play_effect_probe.py`, outside the tracked worktree, uses only synthetic in-memory inputs. Run from this worktree with `..\GM_pf2e\venv\Scripts\python.exe ..\output\pe13c-review\play_effect_probe.py .`. On this head:

```text
actual effect transition: 18 -> 10
contract HP result: (1, 3)
before-expiry play => HP/debt (6, 0) entries ()
after-expiry play => HP/debt (6, 0) entries ()
removed pool with used count => refused: used count for unknown pool effect_charge
removed reserved pool => refused: reservation action-1 holds unknown pool effect_charge
```

**Accepted judgments and coverage:** The HP floor/debt arithmetic, cap, round-trip cases, partial-heal/full-rest behavior and seeded conservation property agree with the contract. Running the HP rule only when its effective maximum moves is correct under C:168-170; charging debt during an unrelated pool/choice change would be wrong. Omitting an HP entry for a dead character is also correct. For valid before/after pool inputs, user refusals, deduplicated blocking action IDs, newest-first releases within each pool, clamps/removals, exact effect lists and stored prompts match the brief. Caller-supplied HP/used counts, `hp_floor_debt` / `choice_prompts`, passing removed pool names to the effect adapter, and the documented entry shapes/order are otherwise reasonable. Genuine orphaned `item:*` used keys must still be filtered by PE-15. The 320 product lines remain below the 370 stop; the 429 test lines are justified. No route, live writer, authority mode or worker setting changes.

**Non-blocking clarification:** The Report's blanket invalid-count refusal claim exceeds the implementation: before-pool maxima and newly added after-pool maxima are not validated, an existing pool with maximum `None` is treated as removed, and `debt_after_heal` may clear debt without validating it. Either validate both pool maps/count paths consistently or describe these as trusted-caller preconditions rather than guaranteed refusals. This is not an additional acceptance blocker for valid adapter inputs. PE-30 must also use the exact per-reservation targets in `released`/entries when holding affected targets; `released_actions` alone is only the deduplicated action list.

**Verification:**

- Repository-venv `python -m pytest -q tests/persistence/test_reconciliation.py tests/persistence/test_state_repositories.py`: **143 passed** in 1.82s.
- The separate effect-change probe reproduced R1-1 on this exact head.
- `git diff --check 737555a2..a3940cc1`: clean. `python -m alembic heads`: unchanged at `20261111_0004 (head)`.
- GitHub CI run `43782713712` on this head: **changes, browser, postgresql and test all passed**. No new PostgreSQL test is needed for this pure hook; no real adapter or live campaign was exercised. The implementer's mutation run and full local suite were not repeated.

For players: saving and action-validation foundations are already merged. The HP/resource consistency layer works for build and equipment changes, but needs this correction for expiring effects before acceptance. No new player screen or live storage change is active. Next: Claude, high reasoning, fixes R1-1, updates the consumer interface in the Report, adds the effect-transition tests and returns for re-review. PE-14a remains independently ready. Review notes are uncommitted; Codex has not merged or started another task.
