# PE-13a: Campaign-scoped state repositories, revisions and lock order

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (brief re-review); brief revision 1 returned for re-review
- **Branch:** `claude/pe-13a-state-repositories` from current main. Record the exact starting SHA.
- **Depends on:** [PE-12](PE-12-game-state-schema.md) (merged), using the accepted [PE-01 contract](../../player-release/state-command-contract.md).
- **Followed by:** [PE-13b](PE-13b-check-order-conflicts.md) (check runner and conflict body), [PE-13c](PE-13c-reconciliation-hook.md) (max-HP and pool reconciliation) and [PE-14a](PE-14a-streams-receipts-events-schema.md) (provides `streams.lock_stream`, which conforms to this task's `LockStream`; PE-14a does not edit this task's files); these three may run in parallel after this task. "PE-13" in older documents means 13a+13b+13c. PE-14b's executor builds `CommandLocks(session, campaign_id, lock_stream=streams.lock_stream)` inside its transaction and uses it for the stream row and for each command type's `lock_plan(ctx)` (steps 2-5). PE-14d owns the invitation lock helper (`services.lock_invitation_for_write`) whose order Decision 3 records. PE-16 needs only this task: it uses the db-authority reads, and under file authority it reads files, never these rows.
- **Context:**
  - Contract (C = `docs/player-release/state-command-contract.md`): section 1 ids and pins (C:22-58, including content and edition mismatch C:41-50), section 2 records (C:62-85, clocks C:68), schema versions (C:153-154), lock order (C:156-166), section 4 step 4 "never from a `flask.g` or request cache" (C:429-436), the round-trip re-import rule (C:680), "no silent fallback" (C:784-785), single-worker and PostgreSQL-only concurrency (C:819-828), PE-13 row (C:1089).
  - Models: `core/persistence/models.py` `Character.id` is a global primary key (:154, so a character id names at most one campaign), `CharacterState` :601-640 (revision default 0 at :610), `EncounterState` :643-702 (one-active index :695-701), `EncounterCombatant` :705-747, `CampaignClock` :750-775, `CampaignSession` :778-814, `CampaignStateAuthority` :817-840 (authority values `file`, `migrating`, `db`, `exporting` at :835-837), `RULES_PROFILES` :597, `Campaign.settings` :133, header comment :588-595.
  - Repository style: `core/persistence/repositories.py` `CampaignRepository.get(for_update=...)` :106-114, `CharacterRepository.get` :176-184; compare-and-set pattern in `MigrationRunRepository._finish_campaign_run` (:457-510).
  - Transactions: `core/persistence/database.py` `session()` :110-121, `transaction()` :123-128 (`expire_on_commit=False` :106).
  - Locks that sit outside the command lock order: the account-creation advisory lock 73420401 and SQLite `BEGIN IMMEDIATE` (`core/persistence/identity.py:31-34`); the process lock `SqlWorkflowStore.transaction` takes outside its transaction (`core/persistence/workflow_store.py:27`); `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`); `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`). Invitation locking today: `identity._lock_invitation` (`core/persistence/identity.py:67-74`) and `TransactionalStore.redeem_invite` (`core/persistence/services.py:104-121`) both do an unlocked locator read, then the campaign `FOR UPDATE`, then the invitation `FOR UPDATE`; neither takes a stream row yet.
  - PostgreSQL test conventions: `tests/persistence/test_postgresql_concurrency.py` (`pytestmark` :35, isolated schema and its own `DATABASE_URL` fixture :40-67, `_race` :181-193); CI job `.github/workflows/ci.yml:208-272` runs `pytest -q tests/persistence -m postgresql --strict-markers`.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 555 product lines and 640 test lines (the campaign-wide reads, the authority write guard, the cross-call lock-order rules and the campaign-bound `CommandLocks` with its `records` mapping, combatant registration and serialized-kind allowlist; `replace_from_files` is gone). If product code passes about 650, stop and report. (A planning estimate, not a contract rule.)

**Why PE-13 was split.** The contract row (C:1089) covers four separable things: the repositories and lock order (this task), the fixed check order and conflict body (PE-13b), and the max-HP/pool reconciliation hook (PE-13c). Together they estimate at about 1,800 changed lines, more than three times the 500-line target (`docs/player-release/task-map.md:5`), and PE-12 already overran its budget by about 50%. PE-16 needs only the read side, so it can start after this task.

## Goal

Give later command tasks one campaign-scoped way to read, lock and write the PE-12 state rows, with exact revision bumps, schema-version handling, authority rules, the profile-equals-campaign invariant and the contract's lock order. Nothing in the product calls it yet; no route, authority or worker setting changes.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Revisions.** An inserted row starts at revision 1 (the model allows 0, `models.py:610`, but 1 means "never written" cannot be confused with "written once"). Every write sets `revision = revision + 1` exactly, through a compare-and-set `UPDATE ... WHERE <key> AND revision = :read` with `rowcount == 1`, and sets `updated_at = utc_now()` explicitly (the PE-12 downgrade check compares `MAX(updated_at)`, `migrations/versions/20261110_0003_game_state_schema.py:397-412`; ORM `onupdate` does not fire for an in-place JSON mutation). A write with an unchanged payload still bumps; callers decide whether to write. A combatant insert, update or delete bumps its encounter's revision in the same call (C:70 "via encounter rev"); a PC's vitals never touch the encounter row (C:72-74).
2. **Revision is never set or lowered.** The repositories expose no way to set or lower a revision, and there is no `replace_from_files`. The stale-row replace write the re-import needs (revision = stale revision + 1, keeping `hp_floor_debt` and `origin: reconciliation` prompts, C:680) belongs to PE-20 (C:672-674, C:1093), which adds it when it needs it. Recorded as a PE-20 follow-up.
3. **Lock order** (C:156-162) is enforced by one object, `CommandLocks`, bound to one session and one campaign, which refuses an acquisition that goes backwards (`LockOrderError`).
   - Construction: `CommandLocks(session, campaign_id, *, lock_stream)`, with `LockStream = Callable[[Session, str], Any]`. `lock_stream` is keyword-only and required, with no default before or after PE-14a; leaving it out is a `TypeError`. A default that locked some other row would be a silent fallback (C:784-785), and `campaign_streams` is PE-14a's table (C:1090). PE-14b passes `streams.lock_stream`. Tests pass a stub defined in the test module (for example one that locks the `campaign_state_authority` row); no stub ships in `command_locks.py`. Read-only attributes: `session`, `campaign_id`, `stream_row`.
   - Step 1: `stream()` calls `lock_stream(self.session, self.campaign_id)` exactly once, stores the result as `stream_row` and returns it. A second `stream()`, any step 2-5 method before `stream()`, or reading `stream_row` before it, raises `LockOrderError`.
   - Step 2 is the encounter, step 3 action rows (PE-30 follow-ons), step 4 `character_states` in ascending `(character_id, domain)` order.
   - Step 5 locks tables in a fixed order: `campaign_clocks` first, then later tables register a position (`item_instances`, `wallets`, shop, stash, project: PE-40+). Ids are locked ascending within each table. Locking an earlier table after a later one raises `LockOrderError`.
   - Across calls: a repeated call for a step whose smallest key is not greater than the largest key already held at that step raises `LockOrderError` (C:160-161 orders the whole step, not one call).
   - Campaign scope: every lock query filters on the bound `campaign_id`. A row in another campaign is treated exactly like a missing row.
   - Locked reads follow the read rules. Lock methods read through the repositories' locked read path (`get(..., for_update=True)`, following `CampaignRepository.get`, `repositories.py:106-114`), so the authority, schema-version and `StateRowMissing` rules of Decisions 4 and 6 apply under the lock. `CommandLocks` never passes `allow_stale`.
   - Registered keys: `records` is a read-only `Mapping[tuple[str, str], StateRecord | None]`. Every key passed to a lock method is entered under its revision kind: `("character.build" | "character.play", character_id)`, `("encounter", encounter_id)`, `("clock", clock)`; a later step-5 table registers its kind with its table position. The value is the frozen record read under the lock. `None` means only "no such key in this campaign": the character does not exist or belongs to another campaign, the encounter does not exist in this campaign, or the clock name is not a fixed clock. Every other gap raises `StateRowMissing` (Decision 4). A key that maps to `None` takes no lock and raises nothing, so PE-14b's `lock_plan` can lock hidden or missing ids without revealing them.
   - Combatants: `encounter(encounter_id)` registers `("encounter", encounter_id)` and also `("combatant", combatant_id)` for every combatant of that encounter, read under the encounter lock (combatants are serialized by their encounter, C:70). A combatant record carries its encounter's revision.
   - Stream-serialized rows: `register_serialized(kind, id, record: StateRecord | None)` enters a row that every writer serializes on the stream row. `record` is `StateRecord.serialized(kind, id, payload)` for a row that exists, or `None` for "no such key in this campaign" (for example a missing grant or session, so PE-14d and PE-14e can answer a hidden or missing subject at check 5 without revealing it). It requires `stream()` to have run and accepts only kinds in `STREAM_SERIALIZED_KINDS = frozenset({"campaign", "session", "grant"})`, which only `register_stream_serialized_kind(kind)` (called at import time) can extend. Any other kind raises `LockOrderError`, so a state row can never reach `records` without its lock. `StateRecord.serialized(kind, id, payload)` builds such a record with `revision`, `schema_version`, `rules_profile` and `content` all `None` (PE-13b Approach 2).
   - No per-transaction module state: the module level holds only the step-5 table-order registry and the serialized-kind allowlist. Everything per transaction lives on the instance; nothing is kept on `flask.g`.
   - The wider rule, documented in the module docstring for PE-14b/14d: every writer takes the `campaign_streams` row before the campaign row and before any state or authorization row. Only three kinds of thing may come before it:
     - the account-creation advisory lock 73420401 and SQLite `BEGIN IMMEDIATE` (`identity.py:31-34`). No stream-holding transaction may take 73420401.
     - process locks taken outside the transaction (`_CAMPAIGN_STORE_LOCK`, `workflow_store.py:27`). No process lock may be taken while a stream row is held.
     - unlocked reads, such as the invitation locator read that finds the campaign id.
   - Invitation rows are locked only after the stream and campaign rows: locator read, then stream, then campaign, then the locked invitation re-read (PE-14d's `services.lock_invitation_for_write`). Invitation rows are not authorization inputs under C:460-465; revoke and consume take the stream only to keep one order. `CommandLocks` does not model invitations.
   - The declared exceptions, `create_campaign` and `campaign_import._import` (campaign row first, then `lock_stream` in the same transaction), are PE-14d's.
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

1. `state_repositories.py`: `CharacterStateRepository`, `EncounterStateRepository` (owns combatants), `CampaignClockRepository`, `StateAuthorityRepository` (read-only here: `authority(campaign_id)`, `stream_epoch(campaign_id)`), `CampaignPinRepository` (`pin(campaign_id)`). Each takes `(session)`; every method takes `campaign_id` and filters on it, so no lookup can cross campaigns. Reads return a frozen `StateRecord` with `kind`, `id`, `revision`, `schema_version`, `rules_profile`, `content`, `payload` (C:81-85 shape); `StateRecord.serialized(kind, id, payload)` builds the stream-serialized form of Decision 3. Single-row reads take `for_update=False` by default; `for_update=True` is the locked read path `CommandLocks` uses, with the same authority, schema-version and `StateRowMissing` rules. Keep `revision` and `seq` distinct (C:28): nothing here allocates seq.
2. Campaign-wide reads, all under the same authority rule:
   - `EncounterStateRepository.active(campaign_id)` returns the one active encounter or `None` (`models.py:695-701`); `combatants(campaign_id, encounter_id)`; `is_combatant(campaign_id, character_id)`, true only for a PC combatant of the active encounter (PE-13c's in-encounter refusal and PE-14c's `is_live` use these).
   - Every repository has `list(campaign_id)` returning frozen records in key order (`(character_id, domain)`, `encounter_id`, `(encounter_id, combatant_id)`, `clock`), so PE-31's snapshot can collect rows and revisions from a caller-supplied `REPEATABLE READ` transaction.
3. Kind strings used in errors and records: `character.build`, `character.play` (C:92, C:149, C:390), `encounter`, `clock`, and `combatant` (registered with its encounter, Decision 3). The stream-serialized kinds are `campaign`, `session` and `grant`. These are the record kinds that key `records` and that PE-13b's check runner reads through `ctx.records`. PE-13b's revision `expected` kinds (`character.build`, `character.play`, `encounter`, `clock`; PE-13b Decision 5) are a subset of them and must use the same spelling. `combatant`, `campaign`, `session` and `grant` are record kinds only: they have no revision of their own and are never valid `expected` kinds.
4. `command_locks.py`, as fixed in Decision 3: `LockStream`, `CommandLocks(session, campaign_id, *, lock_stream)` with attributes `session`, `campaign_id`, `stream_row` and the read-only `records` mapping; methods `stream()`, `encounter(encounter_id)`, `actions(table, ids)`, `character_states([(character_id, domain), ...])`, `records_for(table, ids)` for step 5, and `register_serialized(kind, id, record: StateRecord | None)` (`None` = no such key in this campaign); module-level `STREAM_SERIALIZED_KINDS` and `register_stream_serialized_kind(kind)`. Each lock call sorts its keys ascending, reads them through the repositories' locked read path filtered on `campaign_id`, enters every key in `records`, and applies the step, cross-call and step-5 table-order rules. The step-5 method knows `campaign_clocks` only; later tables register their position and kind in a module-level ordered list. On SQLite, `FOR UPDATE` is a no-op; the order checks still run.
5. Do not touch file-authority locking (`_pc_spell_lock`, `ENCOUNTER_LOCK`, `systems/dnd5e/store.py` locks; C:165-166), `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`), `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`), or any invitation code (`identity.py`, `services.py`; PE-14d). The module docstring states the wider lock rule and the invitation order from Decision 3.
6. Record follow-ups for PE-13b (`CheckContext.records` is a property returning `ctx.locks.records`), PE-13c, PE-14a (provides `streams.lock_stream` conforming to `LockStream`, re-entrant for the same campaign; it does not edit this file), PE-14d (`lock_invitation_for_write` in the Decision 3 order), PE-14b (constructs `CommandLocks` inside its transaction with `lock_stream=streams.lock_stream`, calls `stream()` first; `lock_plan` through `CommandLocks`; map `StateRowMissing` to `503 authority_unavailable` with nothing written, `ProfileMismatch` and `LockOrderError` to `500 server_error`, and `UnknownSchemaVersion` to `503 authority_unavailable` per C:154), PE-20 (the stale-replace write, Decision 2) and PE-19/24 (who writes `settings.rules` and when; `campaign.repin_content`, C:43-44).

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
- [ ] `CommandLocks` acquires `character_states` in ascending `(character_id, domain)` order whatever order the caller passes, and raises `LockOrderError` for a backward step; locking (B, play) then (A, play) in two calls raises; `records_for('campaign_clocks', ...)` after a later step-5 table raises (test registers a synthetic later table); locking a missing id raises nothing and records `None`.
- [ ] `test_command_locks_requires_lock_stream`: constructing `CommandLocks` without `lock_stream` raises `TypeError`.
- [ ] `test_command_locks_stream_first`: any step 2-5 method or a `stream_row` read before `stream()` raises `LockOrderError`, and so does a second `stream()`.
- [ ] `test_command_locks_campaign_scoped`: campaigns A and B are in `db` authority; B has a character `cB` with a `play` row and both have a `world` clock at different revisions. `CommandLocks(s, A, lock_stream=spy)`: locking `(cB, play)` records `None` and raises nothing; locking clock `world` records A's row (A's revision, not B's); a non-fixed clock name such as `moon` records `None`. With A's `world` row deleted and B's present, locking `world` on a fresh A-bound `CommandLocks` raises `StateRowMissing` and never returns B's row. In each case the spy was called exactly once, with `(s, "A")`. (A clock key is only its name, PK `(campaign_id, clock)`, `models.py:750-760`, so no clock key names another campaign's row.)
- [ ] `test_command_locks_missing_state_row_raises`: in `db` authority, locking an existing character of A that has no `play` row raises `StateRowMissing`; locking a character of B records `None`.
- [ ] `test_encounter_lock_registers_combatants`: `encounter(e)` enters `("encounter", e)` and `("combatant", id)` for each combatant of `e`.
- [ ] `test_register_serialized_allowlist`: `register_serialized` before `stream()` raises; `campaign`, `session` and `grant` are accepted, with a `StateRecord.serialized(...)` record and with `None` (which then reads back as `records[(kind, id)] is None`); `character.play` and an unregistered kind raise `LockOrderError`; a kind added through `register_stream_serialized_kind` is accepted.
- [ ] PostgreSQL (CI): two transactions locking `{(A, play), (B, play)}` in opposite caller order both complete (no deadlock); a concurrent compare-and-set on one row yields exactly one winner.
- [ ] `python -m pytest -q tests/persistence/test_state_repositories.py tests/persistence/test_game_state_schema.py tests/persistence/test_database_and_models.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_state_repositories_postgresql.py -m postgresql --strict-markers` executes and passes in the CI `postgresql` job; zero collected or skipped cases are not acceptance. (Local Docker is retired; this runs only in CI.)
- [ ] `python -m alembic heads` is unchanged (no migration in this task); `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: check runner, `CheckContext` and conflict body (PE-13b); reconciliation (PE-13c); `campaign_streams`, `streams.lock_stream`, seq, receipts, events, fence, auth re-read, `lock_plan`, the executor's composing test and the exception-to-code mapping (PE-14a-f); invitation locking and `lock_invitation_for_write` (PE-14d); the stale-replace write (PE-20); writing the campaign pin and re-import in practice (PE-19/24); action and item tables (PE-30 follow-ons, PE-40).
- No migration, no route, no change to `app.py`, `systems/`, `core/campaigns.py` or any live writer. Product routes must not read these tables as authoritative.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review

### Brief review 1 — changes requested (Codex, 2026-11-14)

Reviewed PR #259 at `4b21f4047b417baa5a08b8d0608705a3c4a50956` against
its pinned base `791936f1`. Findings use shared R1 numbers across the nine
briefs. Do not start PE-13a until its interface and lock-order corrections
are accepted.

1. **R1-1 (P2): make the lock manager and command context compose explicitly.**
   Approach 4 defines `CommandLocks(session, lock_stream)` and `stream()` with
   no campaign argument. PE-14a Approach 3/7 requires the default callback
   `lock_stream(session, campaign_id)`. There is no declared source for that
   campaign, also needed for campaign-scoped clocks/encounters. PE-13b's
   enumerated `CheckContext` additionally has no session or lock manager,
   although PE-14b calls `lock_plan(ctx)` and `apply(ctx)` to use both in its
   transaction; PE-14b currently permits adding only emit/flags to that type.
   Specify one complete interface across all four briefs, for example
   `CommandLocks(session, campaign_id, lock_stream=...)`, its callback
   signature, and `ctx.session`/`ctx.locks` or explicit equivalent same-session
   repositories. Define how the lock plan registers locked and missing keys.
   Add a small composing test that proves campaign scoping and that both
   callbacks use the executor's session and locks, without hidden global state.
2. **R1-3 (P2): remove the blanket invitation-before-stream exception.**
   Decision 3 copies the exception that produces the signup-versus-existing-
   user redemption cycle detailed in PE-14d's Review. Reconcile the same
   invitation/stream order in both briefs, including cross-call behavior and
   the PostgreSQL test. The account advisory lock and new-campaign exception
   are separate questions; fixing this does not require changing their values.

The revision, authority and schema-version rules otherwise match the accepted
contract. The large product/test estimate is explicit and plausible for this
scope; retain the stop threshold and re-estimate if the corrections add scope.
This review is docs only; no product tests ran. `git diff --check 791936f1..HEAD`
passed, and PR #259's `changes` job passed (product jobs correctly skipped).
Review edits are uncommitted; nothing was merged.

### Brief revision 1 (Claude, 2026-11-14)

**R1-1 (one interface).** `CommandLocks` is now `CommandLocks(session, campaign_id, *, lock_stream)` with a required keyword-only `LockStream = Callable[[Session, str], Any]` and no default before or after PE-14a; the stub is test-only (Decision 3, construction and step 1 bullets; Approach 4). Every lock query filters on the bound campaign, and lock methods read through the repositories' locked read path, so `StateRowMissing`, authority and schema-version rules apply under the lock (Decision 3; Approach 1). Locked and missing keys are registered in the read-only `records` mapping, where `None` means only "no such key in this campaign"; `encounter()` also registers its combatants; `register_serialized(kind, id, record: StateRecord | None)` works against the `STREAM_SERIALIZED_KINDS` allowlist and takes `None` for a missing key, as PE-14d and PE-14e need (Decision 3; Approach 4). The campaign-scoped test is stated per key type: a character id is a global key, so B's character records `None`; a clock key is only its name, so the A-bound locks record A's `world` row, a non-fixed name records `None`, and A's missing `world` row raises `StateRowMissing` without reading B's (Acceptance). PE-14a only provides `streams.lock_stream` and no longer edits this file (Followed by; Approach 6). `CheckContext` (with `session`, `locks` and `records` as a view of `locks.records`) is PE-13b's, and the composing test that proves callbacks use the executor's session and locks is PE-14b's (Out of scope). New acceptance tests: requires-lock-stream, stream-first, campaign-scoped, missing-state-row, encounter-registers-combatants, serialized allowlist.

**R1-3 (invitation order).** The blanket "invitation row lock may precede the stream" exception is gone. The wider-rule bullet now allows only the advisory lock / `BEGIN IMMEDIATE`, process locks taken outside the transaction (naming `_CAMPAIGN_STORE_LOCK`), and unlocked reads before the stream, and states the invitation order: locator, then stream, then campaign, then locked invitation re-read through PE-14d's `services.lock_invitation_for_write` (Decision 3, last three bullets). It notes that invitation rows are not authorization inputs under C:460-465. Context now cites today's order at `identity.py:67-74` and `services.py:104-121`. `CommandLocks` does not model invitations, so no PE-13a code or test changes for R1-3; the PostgreSQL race tests are PE-14d's. The new-campaign exceptions (`create_campaign` and `campaign_import._import`, both declared in PE-14d's `EXEMPT` list) are unchanged; the bullet now names both.

**Budget.** Revised from about 500 product + 550 test to about 555 + 640 (R1-1 adds about 55 product and 90 test lines; R1-3 adds none). The 650 product stop line is unchanged.

**Judgments for Codex to challenge.**
- The step-5 lock method is renamed from `records(table, ids)` to `records_for(table, ids)`, because `records` is now the mapping attribute and one name cannot be both. No other brief calls the old name.
- `CommandLocks` never passes `allow_stale`, so a lock method in `file` or `migrating` authority raises `AuthorityMismatch`. PE-14b's fence answers first, so this is only a backstop.
- A `combatant` record carries its encounter's revision (C:70 "via encounter rev"); combatant rows have no revision of their own.
- A clock name that is not `world` or `downtime` maps to `None` (no such key); a missing `world` or `downtime` row in `db` authority raises `StateRowMissing`.
- Approach 3 no longer says these kind strings must match "the full `expected` vocabulary". It now says they are the `records` key kinds, PE-13b's revision `expected` kinds are a subset with the same spelling, and `combatant`, `campaign`, `session` and `grant` are record-only kinds. Judgment: PE-13b's `prompt` status kind is not a PE-13a record kind, so it is left out here.
