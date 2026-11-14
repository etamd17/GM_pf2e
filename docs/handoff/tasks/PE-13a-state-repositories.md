# PE-13a: Campaign-scoped state repositories, revisions and lock order

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready
- **Branch:** `claude/pe-13a-state-repositories` from current main. Record the exact starting SHA.
- **Depends on:** [PE-12](PE-12-game-state-schema.md) (merged), using the accepted [PE-01 contract](../../player-release/state-command-contract.md).
- **Followed by:** [PE-13b](PE-13b-check-order-conflicts.md) (check runner and conflict body), [PE-13c](PE-13c-reconciliation-hook.md) (max-HP and pool reconciliation) and [PE-14a](PE-14a-streams-receipts-events-schema.md) (binds `lock_stream`); these three may run in parallel after this task. "PE-13" in older documents means 13a+13b+13c. PE-14b's executor uses `CommandLocks` for the stream row and for each command type's `lock_plan(ctx)` (steps 2-5). PE-16 needs only this task: it uses the db-authority reads, and under file authority it reads files, never these rows.
- **Context:**
  - Contract (C = `docs/player-release/state-command-contract.md`): section 1 ids and pins (C:22-58, including content and edition mismatch C:41-50), section 2 records (C:62-85, clocks C:68), schema versions (C:153-154), lock order (C:156-166), section 4 step 4 "never from a `flask.g` or request cache" (C:429-436), the round-trip re-import rule (C:680), "no silent fallback" (C:784-785), single-worker and PostgreSQL-only concurrency (C:819-828), PE-13 row (C:1089).
  - Models: `core/persistence/models.py` `CharacterState` :601-640 (revision default 0 at :610), `EncounterState` :643-702 (one-active index :695-701), `EncounterCombatant` :705-747, `CampaignClock` :750-775, `CampaignSession` :778-814, `CampaignStateAuthority` :817-840 (authority values `file`, `migrating`, `db`, `exporting` at :835-837), `RULES_PROFILES` :597, `Campaign.settings` :133, header comment :588-595.
  - Repository style: `core/persistence/repositories.py` `CampaignRepository.get(for_update=...)` :106-114, `CharacterRepository.get` :176-184; compare-and-set pattern in `MigrationRunRepository._finish_campaign_run` (:457-510).
  - Transactions: `core/persistence/database.py` `session()` :110-121, `transaction()` :123-128 (`expire_on_commit=False` :106).
  - Locks that sit outside the command lock order: the account-creation advisory lock 73420401 and SQLite `BEGIN IMMEDIATE` (`core/persistence/identity.py:32-34`); the process lock `SqlWorkflowStore.transaction` takes outside its transaction (`core/persistence/workflow_store.py:27`); `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`); `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`).
  - PostgreSQL test conventions: `tests/persistence/test_postgresql_concurrency.py` (`pytestmark` :35, isolated schema and its own `DATABASE_URL` fixture :40-67, `_race` :181-193); CI job `.github/workflows/ci.yml:208-272` runs `pytest -q tests/persistence -m postgresql --strict-markers`.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 500 product lines and 550 test lines (the campaign-wide reads, the authority write guard and the cross-call lock-order rules add to the first estimate; `replace_from_files` is gone). If product code passes about 650, stop and report. (A planning estimate, not a contract rule.)

**Why PE-13 was split.** The contract row (C:1089) covers four separable things: the repositories and lock order (this task), the fixed check order and conflict body (PE-13b), and the max-HP/pool reconciliation hook (PE-13c). Together they estimate at about 1,800 changed lines, more than three times the 500-line target (`docs/player-release/task-map.md:5`), and PE-12 already overran its budget by about 50%. PE-16 needs only the read side, so it can start after this task.

## Goal

Give later command tasks one campaign-scoped way to read, lock and write the PE-12 state rows, with exact revision bumps, schema-version handling, authority rules, the profile-equals-campaign invariant and the contract's lock order. Nothing in the product calls it yet; no route, authority or worker setting changes.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Revisions.** An inserted row starts at revision 1 (the model allows 0, `models.py:610`, but 1 means "never written" cannot be confused with "written once"). Every write sets `revision = revision + 1` exactly, through a compare-and-set `UPDATE ... WHERE <key> AND revision = :read` with `rowcount == 1`, and sets `updated_at = utc_now()` explicitly (the PE-12 downgrade check compares `MAX(updated_at)`, `migrations/versions/20261110_0003_game_state_schema.py:397-412`; ORM `onupdate` does not fire for an in-place JSON mutation). A write with an unchanged payload still bumps; callers decide whether to write. A combatant insert, update or delete bumps its encounter's revision in the same call (C:70 "via encounter rev"); a PC's vitals never touch the encounter row (C:72-74).
2. **Revision is never set or lowered.** The repositories expose no way to set or lower a revision, and there is no `replace_from_files`. The stale-row replace write the re-import needs (revision = stale revision + 1, keeping `hp_floor_debt` and `origin: reconciliation` prompts, C:680) belongs to PE-20 (C:672-674, C:1093), which adds it when it needs it. Recorded as a PE-20 follow-up.
3. **Lock order** (C:156-162) is enforced by one object, `CommandLocks`, which refuses an acquisition that goes backwards (`LockOrderError`).
   - Step 1, "the campaign stream row", is a caller-supplied callable: `campaign_streams` is PE-14a's table (C:1090). Until PE-14a binds it, tests pass a stub that locks the `campaign_state_authority` row. Step 1 is taken once, before any other step.
   - Step 2 is the encounter, step 3 action rows (PE-30 follow-ons), step 4 `character_states` in ascending `(character_id, domain)` order.
   - Step 5 locks tables in a fixed order: `campaign_clocks` first, then later tables register a position (`item_instances`, `wallets`, shop, stash, project: PE-40+). Ids are locked ascending within each table. Locking an earlier table after a later one raises `LockOrderError`.
   - Across calls: a repeated call for a step whose smallest key is not greater than the largest key already held at that step raises `LockOrderError` (C:160-161 orders the whole step, not one call).
   - Locking an id that does not exist returns nothing and raises nothing, so PE-14b's `lock_plan` can lock hidden or missing ids without revealing them.
   - The wider rule, documented in the module docstring for PE-14b/14d: every writer takes the `campaign_streams` row before the campaign row and before any state or authorization row. Only locks that no command transaction takes may come before it: the account-creation advisory lock 73420401 and SQLite `BEGIN IMMEDIATE` (`identity.py:32-34`), the invitation row lock, and process locks taken outside the transaction (`workflow_store.py:27`). No process lock may be taken while a stream row is held. The one declared exception, `create_campaign` (campaign row first, then `lock_stream` in the same transaction), is PE-14d's.
4. **Authority.** A campaign with no `campaign_state_authority` row is treated as `file` (PE-12 seeds none, `models.py:588-595`).
   - `db` or `exporting`: rows are authoritative. A `character_states` read whose character exists in this campaign but whose row is missing raises `StateRowMissing`, and a missing `world` or `downtime` clock row does too (judgment: both clocks are fixed keys, C:68; record it in the report). Files are never read (C:784-785). An id that does not belong to the campaign returns nothing.
   - `file` (or no authority row) or `migrating`: every read requires `allow_stale=True`, otherwise it raises `AuthorityMismatch`. Only the PE-20/PE-24 re-import and the populated-downgrade check use it. PE-16 reads files under file authority, never these rows.
   - Writes (`insert`, `write`, combatant changes, `delete`) refuse with `AuthorityMismatch`, before any SQL write, unless authority is `db`. PE-14b's fence (check 2) answers first in a command; this guard is the backstop against a silent stale-baseline write.
5. **Pins.** The campaign pin is read from `campaigns.settings["rules"]` (`{profile, content}`, C:31-33). No campaign has one today; a missing or malformed pin raises `PinUnavailable`. Writing the pin is a follow-up (see Approach 6). The repository-only invariant (C:1089): inserting or writing a `character_states` row whose `rules_profile` differs from the campaign pin's `profile` raises `ProfileMismatch` before any SQL write. `encounter_states` get the same guard (C:32 "characters and encounters copy it"). Because of this guard the `rules_profile` column can never differ from the pin; PE-13b's `422 edition_mismatch` keys on the build payload's edition instead (C:48-50).
6. **Schema versions** (C:153-154). A per-record-kind registry maps `from_version -> upgrade(payload)`; the current version is a constant per kind (1 for every kind today, so the registry starts empty). Reads upgrade in memory and never write; `write(...)` stores the current version. A stored version above the current one raises `UnknownSchemaVersion` (C:154: fails closed with `503 authority_unavailable`).
7. **No request caches.** Repositories take the caller's `Session` and never call `core.auth._load_users`, `core.campaigns.get_campaign` or anything memoized on `flask.g` (C:429-430).

## Approach

Allowed files:
- new `core/persistence/state_repositories.py`
- new `core/persistence/command_locks.py`
- `core/persistence/__init__.py` (exports only)
- new `tests/persistence/test_state_repositories.py` (SQLite)
- new `tests/persistence/test_state_repositories_postgresql.py` (`pytestmark = pytest.mark.postgresql`, its own `DATABASE_URL` fixture as in `test_postgresql_concurrency.py`)

1. `state_repositories.py`: `CharacterStateRepository`, `EncounterStateRepository` (owns combatants), `CampaignClockRepository`, `StateAuthorityRepository` (read-only here: `authority(campaign_id)`, `stream_epoch(campaign_id)`), `CampaignPinRepository` (`pin(campaign_id)`). Each takes `(session)`; every method takes `campaign_id` and filters on it, so no lookup can cross campaigns. Reads return a frozen record with `kind`, `id`, `revision`, `schema_version`, `rules_profile`, `content`, `payload` (C:81-85 shape). Keep `revision` and `seq` distinct (C:28): nothing here allocates seq.
2. Campaign-wide reads, all under the same authority rule:
   - `EncounterStateRepository.active(campaign_id)` returns the one active encounter or `None` (`models.py:695-701`); `combatants(campaign_id, encounter_id)`; `is_combatant(campaign_id, character_id)`, true only for a PC combatant of the active encounter (PE-13c's in-encounter refusal and PE-14c's `is_live` use these).
   - Every repository has `list(campaign_id)` returning frozen records in key order (`(character_id, domain)`, `encounter_id`, `(encounter_id, combatant_id)`, `clock`), so PE-31's snapshot can collect rows and revisions from a caller-supplied `REPEATABLE READ` transaction.
3. Revision kind strings used in errors and records: `character.build`, `character.play` (C:92, C:149, C:390), `encounter`, `clock`. PE-13b owns the full `expected` vocabulary; these names must match it.
4. `command_locks.py`: `CommandLocks(session, lock_stream)` with `stream()`, `encounter(encounter_id)`, `actions(table, ids)`, `character_states([(character_id, domain), ...])`, `records(table, ids)`. Each call sorts its keys ascending before `SELECT ... FOR UPDATE` and applies the step, cross-call and step-5 table-order rules of Decision 3. `records` knows `campaign_clocks` only; later tables register their position in a module-level ordered list. On SQLite, `FOR UPDATE` is a no-op; the order checks still run.
5. Do not touch file-authority locking (`_pc_spell_lock`, `ENCOUNTER_LOCK`, `systems/dnd5e/store.py` locks; C:165-166), `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`), or `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`). The module docstring states the wider lock rule from Decision 3.
6. Record follow-ups for PE-13b/c, PE-14a (bind `lock_stream`), PE-14b (`lock_plan` through `CommandLocks`; map `StateRowMissing` to `503 authority_unavailable` with nothing written, `ProfileMismatch` and `LockOrderError` to `500 server_error`, and `UnknownSchemaVersion` to `503 authority_unavailable` per C:154), PE-20 (the stale-replace write, Decision 2) and PE-19/24 (who writes `settings.rules` and when; `campaign.repin_content`, C:43-44).

## Acceptance

- [ ] Insert creates revision 1; each write bumps by exactly 1 and moves `updated_at` (explicit UTC); a stale compare-and-set (another write in between) raises and writes nothing.
- [ ] No public method sets or lowers a revision (test inspects the repository API and tries a write with a lower `:read`).
- [ ] `build` and `play` revisions are independent: writing `play` leaves `build` unchanged and vice versa.
- [ ] Adding, changing or removing a combatant bumps the encounter revision once per call; writing a PC's `play` row never changes the encounter row or its revision.
- [ ] Every read and write is scoped by `campaign_id`; a lookup with campaign B's id for campaign A's row returns nothing (no exception that reveals existence).
- [ ] `active`, `combatants`, `is_combatant` (false for a combatant of an ended encounter) and `list` on every repository return frozen records in key order.
- [ ] `ProfileMismatch` for a `character_states` or `encounter_states` write whose profile differs from the pin, before any SQL write; `PinUnavailable` for a missing or malformed pin.
- [ ] An older `schema_version` upgrades on read without a write (registry test with a synthetic kind); a newer one raises `UnknownSchemaVersion`.
- [ ] Authority: in `db` and in `exporting` a missing `character_states` row of an existing character raises `StateRowMissing`; with no authority row the campaign reads as `file`; in `file` and `migrating`, reads without `allow_stale=True` raise `AuthorityMismatch` and succeed with it; writes in `file`, `migrating` and `exporting` raise `AuthorityMismatch` with nothing written.
- [ ] `CommandLocks` acquires `character_states` in ascending `(character_id, domain)` order whatever order the caller passes, and raises `LockOrderError` for a backward step; locking (B, play) then (A, play) in two calls raises; `records('campaign_clocks', ...)` after a later step-5 table raises (test registers a synthetic later table); locking a missing id raises nothing.
- [ ] PostgreSQL (CI): two transactions locking `{(A, play), (B, play)}` in opposite caller order both complete (no deadlock); a concurrent compare-and-set on one row yields exactly one winner.
- [ ] `python -m pytest -q tests/persistence/test_state_repositories.py tests/persistence/test_game_state_schema.py tests/persistence/test_database_and_models.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_state_repositories_postgresql.py -m postgresql --strict-markers` executes and passes in the CI `postgresql` job; zero collected or skipped cases are not acceptance. (Local Docker is retired; this runs only in CI.)
- [ ] `python -m alembic heads` is unchanged (no migration in this task); `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: check runner and conflict body (PE-13b); reconciliation (PE-13c); `campaign_streams`, seq, receipts, events, fence, auth re-read, `lock_plan` and the exception-to-code mapping (PE-14a-f); the stale-replace write (PE-20); writing the campaign pin and re-import in practice (PE-19/24); action and item tables (PE-30 follow-ons, PE-40).
- No migration, no route, no change to `app.py`, `systems/`, `core/campaigns.py` or any live writer. Product routes must not read these tables as authoritative.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
