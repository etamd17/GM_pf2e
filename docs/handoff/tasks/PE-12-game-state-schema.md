# PE-12: Add transactional character and encounter state schema

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** done (Codex review 3 accepted 2026-11-11; merge blocked until browser CI passes)
- **Branch:** `claude/pe-12-game-state-schema` from current main (at or after `3b1c1f40`). Record the exact starting SHA.
- **Depends on:** [PE-01](PE-01-state-command-contract.md). Coordinate migration merge order with PE-10: both alter `characters`, so whichever lands second rebases onto the other and keeps its constraints intact (one Alembic head).
- **Context:** [PE-01 contract](../../player-release/state-command-contract.md) sections 1, 2, 4 (Sessions), 7 and 9, and the PE-12 row and reconciliation note in section 11; [foundation baseline](../../player-release/foundation-baseline.md), [ADR-0002](../../adr/0002-transactional-persistence.md), `core/persistence/models.py`, `core/persistence/database.py::Database`, `core/persistence/repositories.py::MigrationRunRepository`, `tools/migrate_transactional_store.py`, `tools/export_transactional_runtime.py::_envelopes`, existing migration conventions (including `20261001_0002`'s online empty-store downgrade check).
- **Change budget:** roughly 1,300 changed lines including tests. That covers the new state tables, the `migration_runs` and `characters` alterations with backfill and partial indexes, scoping of the legacy `migration_runs` readers, the populated-downgrade checks, and SQLite plus PostgreSQL tests. If the diff passes about 1,600 lines, stop and propose splitting it into (a) the new state tables and (b) the `migration_runs`/`characters` alterations, both landing before PE-13. (The budget is a planning estimate, not a contract rule.)

## Goal

Provide the tables that hold the agreed character, encounter, session, clock and
authority state in one database transaction, and alter the existing
`migration_runs` table (campaign-scoped import/export runs) and `characters` table
(`archived_at` with an active-row locator index) as PE-01 section 11 assigns.
Existing live routes continue using their current backend; adding the schema does
not activate it or import player data.

## Approach

Allowed files: `core/persistence/models.py`; `core/persistence/__init__.py` (exports
of the new models only); `core/persistence/repositories.py` (`MigrationRunRepository`
only); `tools/migrate_transactional_store.py` and
`tools/export_transactional_runtime.py` (their `migration_runs` readers only);
`core/persistence/runtime.py` (`_REQUIRED_REVISION` only); one new
`migrations/versions/` revision; `tests/persistence/test_game_state_schema.py`; and
focused adjustments to `tests/persistence/test_database_and_models.py`,
`test_migrate_transactional_store.py`, `test_export_runtime.py`,
`test_postgresql_concurrency.py` and `test_runtime_backend.py`, plus rebase-only
adjustments to PE-10's `test_dnd5e_identity_schema.py` if PE-10 merged first. Use PE-01's accepted names and field contract.

1. Add `character_states` keyed `(campaign_id, character_id, domain)` for the
   `build` and `play` domains (section 2), with validated payload/schema and
   rules-pin metadata and an independent revision per domain. The D&D `build`
   revision replaces `build_rev`. State rows cascade with their character.
2. Add `encounter_states` keyed `(campaign_id, encounter_id)` (section 2) with
   status, round/turn, initiative order, creature vitals and effects, rules pins,
   clock anchors and an encounter revision. The roster is `encounter_combatants`
   (step 3). Enforce one active encounter per campaign with a partial unique index
   on `(campaign_id) WHERE status = 'active'`.
3. Add `encounter_combatants` keyed `(campaign_id, encounter_id, combatant_id)`, with
   `kind`, a creature ref and a nullable `character_id` under a composite FK to
   `characters(campaign_id, id)`, so a character from another campaign cannot be
   referenced. Add campaign clocks keyed `(campaign_id, clock)` for `world` and
   `downtime`, each with a value and a revision. Add
   `campaign_sessions(campaign_id, session_id, status, started_at, started_by,
   origin, ended_at, ended_by)` with primary key `(campaign_id, session_id)` and a
   partial unique index on `(campaign_id) WHERE status = 'active'` (section 4). Add
   `campaign_state_authority(campaign_id, authority, stream_epoch, changed_at,
   source_checksum)`, one row per campaign, with authority in `file`, `migrating`,
   `db`, `exporting` and `stream_epoch >= 1` (sections 1 and 7).
   Partial indexes use `sqlite_where`/`postgresql_where`, as
   `uq_character_assignments_one_owner` does. Events keep their ids and have no FK.
4. Alter `migration_runs`:
   - add nullable `campaign_id` and `stream_epoch` (both NULL on legacy rows);
   - add `kind` (`import`/`export`), NOT NULL and backfilled to `import`;
   - allow the `exported` and `aborted` statuses;
   - replace the column-level unique `source_checksum` with a partial unique index
     on `source_checksum WHERE campaign_id IS NULL`, plus uniqueness of
     `(campaign_id, kind, source_checksum, stream_epoch)` among runs not `aborted`.

   Scope every legacy reader to `campaign_id IS NULL`:
   `MigrationRunRepository.get_by_source_checksum` and `start`,
   `tools/migrate_transactional_store.py` (including its any-run emptiness check,
   `select(MigrationRun).limit(1)`, which one campaign run would otherwise turn into
   a refusal), and `tools/export_transactional_runtime.py::_envelopes`. The runtime
   export's history sidecar (`select(MigrationRun).order_by(MigrationRun.id)`) stays
   unscoped on purpose: section 7 keeps full history there. Record whether
   `migration_runs.campaign_id` has a foreign key to `campaigns` and why (the
   contract does not say).

   PE-12 owns the repository support for campaign runs and its tests: a scoped
   start that takes `campaign_id`, `kind` and the `stream_epoch` the caller read
   under the migration gate; `finish` accepting `exported` and `aborted`; a retry
   that inserts a new run; and an `aborted` run not blocking that retry. PE-19, PE-20
   and PE-24 call it: they decide when a failure before promotion or the export swap
   marks a run `aborted`, read the epoch under the migration gate, and write the
   export manifest into `details`.
5. Alter `characters`: add nullable `archived_at`, and replace
   `uq_characters_legacy_locator` with a partial unique index on
   `(campaign_id, legacy_storage, legacy_file) WHERE archived_at IS NULL`
   (section 7). Archive never rewrites a locator. The archive command,
   archive-aware lookups and the export's archived-row filter belong to PE-41.
   On SQLite this needs a batch rebuild of `characters`. If PE-10 merged first, the
   rebuilt table keeps PE-10's system, storage and `legacy_file = id + '.json'`
   checks unchanged on upgrade and downgrade, and the migration test asserts this.
6. Define nonnegative revisions, uniqueness and referential constraints and
   timestamp behavior from PE-01. Avoid mutable JSON defaults shared across
   instances. Do not precompute game mechanics in SQL.
7. Supply additive upgrade and implement section 7's populated-downgrade rule:
   - refuse while any campaign's authority is `db`, `migrating` or `exporting`;
   - refuse while rows exist in a class the round-trip table does not export (of
     this revision's tables: `campaign_sessions` and campaign clocks; later
     revisions' tables are dropped by their own downgrades first);
   - refuse while character-held state rows lack a `migration_runs` row with
     status `exported` newer than their last committed command;
   - refuse offline (`as_sql`), as `20261001_0002` does (convention, not a
     contract rule).

   The contract's rule covers the new tables only. For the two existing tables this
   revision alters, take these defaults (brief judgment; confirm in review, and
   revise PE-01 if Codex reads them as rule changes):
   - refuse while any `characters.archived_at` is set, since dropping the column
     would silently return archived characters to lookups and name resolution;
   - once every check passes, delete `migration_runs` rows with `campaign_id` set
     (transition machinery for the tables being dropped) before restoring
     `uq_migration_runs_source_checksum`; test it with an aborted run and its
     retry sharing a `source_checksum`.
8. Set `core/persistence/runtime.py::_REQUIRED_REVISION` to this revision, or
   SQL and shadow mode refuse to start after `alembic upgrade head`. Whichever of
   PE-10 and PE-12 lands second sets it to the final single head.
9. Test SQLite and PostgreSQL schema behavior using isolated synthetic records.
   Record state repositories, receipt/event tables and runtime adapters as
   follow-ups for PE-13 and PE-14.

**Two points the contract leaves to this task's judgment.** Decide them in the
report and have Codex confirm them in review; if either needs a rule change,
revise PE-01 rather than this brief.
- **Stale marker.** Section 7 says db rows are "marked stale" while files are
  authoritative, and that marking stale is not a write. Either derive staleness from
  `campaign_state_authority` (authority `file`) or add a per-row marker; say which
  and why.
- **"Newer than their last committed command."** PE-14's receipts do not exist yet,
  so the downgrade check must compare the latest `exported` run with something
  PE-12 owns (for example the state rows' update time or revision). Say which.

## Acceptance

- [ ] Migration preserves all existing identity, assignment, draft, receipt and migration-run rows. Legacy `migration_runs` rows get `campaign_id` and `stream_epoch` NULL and `kind = 'import'`, and keep their `source_checksum` uniqueness through the partial index. Existing characters get `archived_at` NULL.
- [ ] Models round-trip independent build/play state and encounter/combatant/turn state with their agreed rules metadata and revisions.
- [ ] Invalid revisions, duplicate state keys and cross-campaign identity links fail as specified; repository-only invariants are explicitly documented rather than falsely claimed as database checks.
- [ ] At most one `active` `campaign_sessions` row and at most one active encounter per campaign (partial unique indexes, on SQLite and PostgreSQL). An `encounter_combatants` row cannot name a character from another campaign. Deleting a character deletes its state rows.
- [ ] Two non-aborted runs with the same `(campaign_id, kind, source_checksum, stream_epoch)` are refused. An `aborted` run does not block a new run with the same key. Two legacy rows with the same `source_checksum` are still refused.
- [ ] `MigrationRunRepository.get_by_source_checksum`/`start`, `tools/migrate_transactional_store.py` and `tools/export_transactional_runtime.py::_envelopes` ignore campaign runs, with tests that a campaign import run does not change `_envelopes`/`envelope_metadata` or the exported legacy tree (the history sidecar still lists it), and that the legacy import's any-run check ignores rows with `campaign_id` set.
- [ ] The locator index refuses two non-archived rows with the same `(campaign_id, legacy_storage, legacy_file)` and does not count rows with `archived_at` set.
- [ ] Two model instances do not share mutable payload defaults.
- [ ] Empty migration downgrade succeeds. Populated downgrade is refused while any campaign is `db`, `migrating` or `exporting`; while any `campaign_sessions` or campaign clock row exists; while character-held state rows lack an `exported` run newer than their last committed command, including when a second, unfinished export follows an older successful one; while any character is archived; and when run offline. A downgrade that passes removes campaign runs, including an aborted run and its retry with the same `source_checksum`, and restores the legacy unique constraint.
- [ ] `python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_game_state_schema.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py tests/persistence/test_postgresql_concurrency.py tests/persistence/test_runtime_backend.py` passes.
- [ ] `python -m pytest tests/persistence/test_game_state_schema.py -q -m postgresql --strict-markers` executes and passes the new PostgreSQL-marked cases against an isolated test database; zero collected/skipped cases are not acceptance.
- [ ] `python -m alembic heads` reports one integrated head, `_REQUIRED_REVISION` names it, and `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: state repositories and checks (PE-13); streams, receipts, events,
  grants and session commands (PE-14); item, wallet and ledger tables (PE-40); the
  archive command, archive-aware lookups and the runtime export's archived-row filter
  (PE-41); writing campaign import/export runs, the export manifest and the round
  trip (PE-19/20); authority transitions and the migration gate (PE-24). The only
  repository and tool changes allowed here are the `migration_runs` scoping, statuses
  and campaign-run support above.
- Mutation APIs, backend cutover, migration of real player data (backfilling `kind`
  on existing `migration_runs` rows is in scope), rules engines, access-policy
  changes, UI or SSE.
- Product routes must not begin reading empty new tables as authoritative state.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Branch / base:** `claude/pe-12-game-state-schema`, cut from main at `aaec6f19`.
- **Changed:**
  - `core/persistence/models.py`:
    - New tables: `CharacterState`, `EncounterState`, `EncounterCombatant`, `CampaignClock`, `CampaignSession` and `CampaignStateAuthority`.
    - `Character.archived_at` added. `uq_characters_legacy_locator` is replaced by the partial index `uq_characters_active_legacy_locator WHERE archived_at IS NULL`.
    - `MigrationRun` gains `campaign_id`, `kind` (NOT NULL, default `import`) and `stream_epoch`.
    - `MigrationRun` gains four checks:
      - `known_kind`;
      - `known_status`, which allows `exported` and `aborted`;
      - `scope`: legacy rows have no campaign, no epoch, kind `import` and a legacy status; campaign rows have both set, with epoch >= 1;
      - `exported_is_export`.
    - `MigrationRun` gets two partial unique indexes:
      - `uq_migration_runs_legacy_source_checksum WHERE campaign_id IS NULL`;
      - `uq_migration_runs_campaign_attempt (campaign_id, kind, source_checksum, stream_epoch) WHERE campaign_id IS NOT NULL AND status <> 'aborted'`.
  - Migration `20261110_0003`:
    - PostgreSQL uses plain ALTERs.
    - SQLite rebuilds `characters` and `migration_runs` with `copy_from` shapes, so CHECK names and indexes survive.
    - On SQLite the whole revision runs in one explicit transaction, after refusing enforced foreign keys and pre-existing FK violations. It runs `foreign_key_check` again after the rebuild.
    - Existing runs are backfilled `kind='import'`. An unknown legacy status is refused up front.
  - Populated downgrade:
    - Refused offline.
    - Refused while any campaign authority is `migrating`, `db` or `exporting`.
    - Refused while session rows, clock rows or archived characters exist.
    - Refused while any campaign's newest state write (character state, encounter or combatant rows) has no `exported` run completed at or after it.
    - When allowed, it drops the state tables and deletes the per-campaign runs before restoring both uniques.
  - `MigrationRunRepository`:
    - `get_by_source_checksum` and `start` are scoped to `campaign_id IS NULL`.
    - New `start_campaign_run`: it validates its inputs, rejoins the same non-aborted attempt, and inserts a new row after an abort.
    - `finish` accepts statuses by scope: legacy runs take succeeded/failed/verified; campaign imports take succeeded/aborted; campaign exports take exported/aborted. A campaign run finishes exactly once.
  - Tools: the any-run check in `migrate_transactional_store` and `export_transactional_runtime._envelopes` are scoped to legacy runs. The history sidecar still lists every run; new columns are appended last, so existing key order is unchanged.
  - `_REQUIRED_REVISION` is now `20261110_0003`. Package exports were added.
  - Tests:
    - New `tests/persistence/test_game_state_schema.py` (60 cases, 2 of them PostgreSQL).
    - One case each added to the migrate and export tests.
    - The table-set test and the runtime revision test were updated.
- **Decisions (brief judgment points):**
  - Stale marker: no column. Rows are stale when `campaign_state_authority.authority = 'file'`, so staleness cannot disagree with authority.
  - "Last committed command": compared per campaign as the maximum `updated_at` across `character_states`, `encounter_states` and `encounter_combatants`, against the newest `exported` run's `completed_at`. Encounter rows are included as well as character rows, which is the conservative reading.
  - `migration_runs.campaign_id` has no FK. A run is evidence and must outlive its campaign, as receipts and events do.
  - The downgrade refuses while any `archived_at` is set, and deletes campaign runs once every other check passes.
  - Enums: domain `build`/`play`; rules profile `pf2e`/`dnd5e-2014`/`dnd5e-2024`; encounter and session status `active`/`ended`; combatant kind `pc`/`creature`, where a `pc` row needs a character; clock `world`/`downtime`; authority `file`/`migrating`/`db`/`exporting`.
  - Session `started_at` is nullable so that file-era ids can be backfilled.
  - Combatant rows cascade with both their encounter and their character.
- **Tests run (Windows, repo venv):**
  - Brief acceptance set without the PostgreSQL marker: `pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_game_state_schema.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py tests/persistence/test_postgresql_concurrency.py tests/persistence/test_runtime_backend.py -m "not postgresql"` gave 163 passed, 8 deselected. That was before the review fixes; the full persistence run below covers the final code.
  - `pytest -q tests/persistence -m "not postgresql"` gave 301 passed, 15 deselected, after review round 1.
  - `pytest -q tests/persistence/test_game_state_schema.py -m "not postgresql"` gave 58 passed, 2 deselected after review round 2.
  - The two rollback tests fail if the explicit BEGIN is removed; checked by mutation.
  - `alembic heads` reports `20261110_0003 (head)`.
  - On a fresh SQLite file, `alembic upgrade head` followed by `alembic check` reports "No new upgrade operations detected". The migrated SQLite schema matches `create_all`: constraint names, indexes and columns.
  - `git diff --check aaec6f19` is clean.
  - Full non-PG suite: `pytest -q --ignore=tests/pf2e_rules -m "not postgresql and not browser"` -> 4418 passed, 233 skipped, 195 deselected, 1 xfailed (run on `e8acb2a6`, before the review fixes; those touched only persistence code and tests, re-run above).
- **Skipped / not verified:**
  - **The PostgreSQL cases were not run locally.** Docker and WSL cannot start on this machine (virtualization is disabled). They ran in CI's `postgresql` job instead: on draft PR #249 at `66516d17`, `alembic upgrade head` + `alembic check` -> "No new upgrade operations detected" on PostgreSQL, and `pytest -q tests/persistence -m postgresql` -> 15 passed (includes the 2 new PE-12 cases).
  - The offline PostgreSQL DDL is checked by the existing check-name test, which passes.
- **Review:** two adversarial rounds (workflow; 17 agents in all).
  - Confirmed and fixed:
    1. A failed SQLite downgrade dropped the state tables under a head stamp, and nothing could recover it.
    2. A failed SQLite upgrade left `_alembic_tmp_characters`, which blocked every retry.
    3. The same root cause as 1 and 2, reported again: SQLite DDL autocommitted before the first data change.
    4. The downgrade-refusal tests matched words that Alembic's own log line always prints.
    5. Hardening: re-finishing an exported run could have moved its `completed_at` past newer state.
  - Rejected with reasons:
    - The PostgreSQL downgrade check-then-drop takes no locks. A downgrade runs with the app quiesced.
    - Locator lookups ignore `archived_at`. That belongs to PE-41.
    - `start_campaign_run` is check-then-insert. Concurrency is serialized by the PE-24 migration gate.
    - Rejoining a terminal run is intended.
- **Known wrong / limitations:**
  - On SQLite the downgrade's timestamp comparison is a string comparison. It is correct for values the ORM writes (one fixed format, UTC). A hand-written value in a different format could compare wrongly.
  - The diff is about 1,950 lines, most of them tests (the brief's guideline is about 500). The migration alone is about 470 lines because of the SQLite rebuild shapes.
- **Follow-ups (not done here):**
  - PE-13: repositories must set `updated_at` on every state write. The downgrade's export check depends on it.
  - PE-14: session-id uniqueness against folder keys.
  - PE-19/24: staging rows for import and the migration gate that serializes `start_campaign_run`.
  - PE-41: archive-aware locator lookups (`core/persistence/ownership.py`) now that two rows can share a locator.
- **Response to Codex review 1 (2026-11-11):**
  1. **Finding 1 (P1), fixed.**
     - What changed: `MigrationRunRepository.finish` now hands campaign runs to `_finish_campaign_run`. That method flushes pending work, then runs one `UPDATE migration_runs ... WHERE id = :id AND status = 'running'`, and refuses with "already finished" unless exactly one row changed. The caller's in-memory status is no longer consulted. On PostgreSQL, a second concurrent finisher waits on the row lock, re-checks the predicate after the first commits, matches zero rows and is refused.
     - After a successful finish, the method expires the attributes it wrote, so the next read comes from the row. My first version copied the values onto the object with `set_committed_value`. The follow-up review showed that a SAVEPOINT rollback then left the object claiming a finish the database no longer had. That is fixed and has its own test.
     - It now refuses a run object that is not attached to the repository's session.
     - Legacy runs (`campaign_id IS NULL`) take the old path unchanged, so `succeeded -> verified` still works.
     - New tests:
       - `test_stale_campaign_run_object_cannot_finish_again` (SQLite file) and `test_postgresql_stale_campaign_run_object_cannot_finish_again` (CI) replay Codex's sequence with two separately loaded objects. The export finishes at 10:00, state is written at 11:00, and the stale object's finish at 12:00 is refused with both `exported` and `aborted`. Status, `completed_at`, `entity_counts` and `details` keep the first finish's values, nothing is left dirty, and the migration's real `_STATE_ROWS_WITHOUT_NEWER_EXPORT` query still reports c1 afterwards.
       - `test_campaign_finish_rolled_back_in_a_savepoint_can_finish_again`.
       - `test_campaign_finish_records_the_stored_values_on_the_caller`.
       - `test_legacy_run_still_moves_from_succeeded_to_verified`, which uses a separately loaded object.
  2. **Finding 2 (P2), fixed, and widened to every model refusal test.**
     - `_refused` now takes a required `match=` and searches the driver's own message (`IntegrityError.orig`), not the SQL text.
     - SQLite refusals are pinned to the CHECK name (`CHECK constraint failed: ck_...`) or to the exact unique-key column list. PostgreSQL refusals are pinned to the constraint or index name.
     - The clock test now writes the negative value and a negative revision to a campaign with no clocks. The session test writes active-with-`ended_at` and active-with-`ended_by` to a campaign with no sessions.
     - SQLite reports every foreign-key failure with the same text, so the three SQLite FK refusals are pinned by their fixtures, each of which breaks exactly one foreign key. The helper's docstring says so.
  - **Mutation checks (each run against the changed code, then restored):**
    - Dropping the `status = 'running'` predicate fails the stale-object regression.
    - Restoring the copy-onto-object version fails the savepoint test.
    - Removing `ck_campaign_clocks_value_nonnegative` fails the clock test.
    - Neutralising `ck_campaign_sessions_active_not_ended` fails the session test.
    - The follow-up review independently mutated each of the 6 clock and session constraints; all 6 were caught.
  - **Follow-up review:** a workflow of 8 agents, with a reviewer and a refuter per claim.
    - Confirmed and fixed:
      - the savepoint issue above;
      - the FK docstring overclaimed;
      - the PostgreSQL regression compared `completed_at` with `.replace(tzinfo=utc)`, which would fail on a server whose TimeZone is not UTC. It now normalises only naive values.
    - Refuted: "the pre-UPDATE flush writes a stale object's own pending edits". That write comes from the caller committing its own edits, and it lands the same way without `finish`. `finish` writes none of its own values when it refuses.
  - **Checks on the final code (Windows, repo venv):**
    - `pytest -q tests/persistence -m "not postgresql"`: 305 passed, 16 deselected.
    - `test_game_state_schema.py` alone: 62 passed, 3 deselected.
    - `alembic heads`: `20261110_0003 (head)`.
    - On a fresh SQLite file, `alembic upgrade head` then `alembic check` reports "No new upgrade operations detected".
    - `git diff --check` is clean.
    - PostgreSQL cases, including the new stale-object one, run only in CI. Docker still cannot start here.
- **Response to Codex review 2 (2026-11-11):**
  - **R2-1 (P2), fixed.**
    - Reproduced first with Codex's `finish_savepoint_probe.py` on `20a882b8`. When the result was read before rollback, the object and the rejoin showed `exported` while the row said `running`.
    - What changed: after the compare-and-set UPDATE wins, `_finish_campaign_run` writes the same values through the ORM (`setattr` plus `flag_modified`) and flushes. This emits a second, same-value `UPDATE ... WHERE id = :id`, and the flush registers the object with the current transaction or SAVEPOINT. Any rollback of that scope then expires it like any other ORM change. Reading the fields first no longer matters.
    - The persisted CAS is unchanged and still decides: a refused finish raises before any attribute is set. The second UPDATE cannot overwrite another finish. The CAS already holds the row lock on PostgreSQL (SQLite locks the whole database), and nothing runs between the two statements. The legacy path is untouched.
    - With the fix, the probe reports `running` for the object, the row and the rejoin in both cases, and the follow-up `aborted` finish succeeds.
  - **New regression:** `test_campaign_finish_read_then_rolled_back_in_a_savepoint` (SQLite) and `test_postgresql_campaign_finish_read_then_rolled_back` (CI) share one check, which runs these steps:
    1. Finish inside a SAVEPOINT, with counts and details.
    2. Read all four fields from the returned object.
    3. Roll back.
    4. Assert that the object, the stored row and a `start_campaign_run` rejoin (the same identity-map object) all show `running` with no completion.
    5. Finish again, and check the stored result.
  - **Mutation check:** putting back the expire-only version fails the new SQLite test at the post-rollback agreement assertion.
  - **Adversarial review (one agent, probes on SQLite):** no defect confirmed.
    - Checked and sound:
      - a released inner savepoint followed by an outer rollback (the old code got this wrong);
      - an inner-only rollback;
      - a full rollback;
      - commit, then a detached read;
      - a refused finish leaving nothing dirty;
      - an object already expired before `finish`;
      - `flag_modified` with an equal value still emitting and registering in SQLAlchemy 2.1.1;
      - no ORM flush listeners exist.
    - Accepted limitation: if `finish` runs in an inner SAVEPOINT and the outer one is rolled back explicitly while the inner one is still open, the object stays stale. SQLAlchemy 2.1.1 behaves the same for any ORM change in that pattern (the inner savepoint's change record is not merged into the outer one), and nothing in the codebase calls `begin_nested` outside tests. Nested `with` blocks unwinding on an exception roll back inner-first, which works.
    - In memory, `completed_at` is now the tz-aware UTC value that was written. On SQLite a reload gives naive UTC, which is the same instant.
  - **Browser CI:** the `20a882b8` failure was the apt mirror (`Temporary failure resolving 'azure.archive.ubuntu.com'`) while installing fonts, not a test failure. The new push reruns all four jobs.
  - **Checks on the final code (Windows, repo venv):**
    - `pytest -q tests/persistence -m "not postgresql"`: 306 passed, 17 deselected.
    - `alembic heads`: `20261110_0003 (head)`.
    - On a fresh SQLite file, `alembic upgrade head` then `alembic check` reports "No new upgrade operations detected".
    - `git diff --check` is clean.

## Review

**Changes requested** (Codex, 2026-11-10). Reviewed
`fa89abbcb13dd13d1cdd86f82778d641e75d1015` against base
`aaec6f19bc122f8b11e54a49a8b9531b4a3dc6fd`, matching the current head of
[draft PR #249](https://github.com/etamd17/GM_pf2e/pull/249). The worktree was
clean before review. No commit, push, merge or product-code edit was made.

### Numbered findings

1. **P1 — Enforce campaign-run finish-once against the persisted status.**
   `core/persistence/repositories.py:424–432` checks only the caller's ORM
   object's `run.status`. Two sessions can load the same running export. After
   session A finishes and commits, session B's retained object still says
   `running`; its later, sequential `finish()` succeeds and moves `completed_at`.
   The new test at `tests/persistence/test_game_state_schema.py:414–425` only
   reuses one object and therefore misses this case.

   Reproduced with synthetic records: the first finish records 10:00, encounter
   state is written at 11:00, and the stale object's second finish records 12:00.
   The migration's actual `_STATE_ROWS_WITHOUT_NEWER_EXPORT` query reports that
   campaign before the second finish and omits it afterwards, despite no fresh
   export. This can defeat the safeguard that prevents dropping unexported state
   (`20261110_0003_game_state_schema.py:408–409`).

   Make campaign completion conditional on the stored row still being `running`,
   for example with an atomic compare-and-set that refuses a zero-row update.
   A rejected second finish must leave status, completion time and other result
   fields unchanged. Preserve the legacy `succeeded -> verified` path. Add a
   regression using separately loaded ORM objects, including the export-freshness
   consequence. This is not the deferred `start_campaign_run` check-then-insert
   race: the reproduced finishes run sequentially, and the current caller contract
   does not require a fresh run object at completion.

2. **P2 — Isolate the intended CHECK violations in two model tests.**
   At `tests/persistence/test_game_state_schema.py:264–265`, the negative clock
   value also duplicates the existing `(c1, downtime)` primary key. At line 289,
   the active session with `ended_at` also conflicts with c2's existing active
   session. Both use the generic `_refused` helper, so unrelated uniqueness
   failures can make them pass.

   Independently removed `ck_campaign_clocks_value_nonnegative` and
   `ck_campaign_sessions_active_not_ended` from fresh in-memory metadata and ran
   the corresponding committed test functions unchanged: both still passed.
   Use otherwise valid, nonconflicting fixtures and check the intended constraint
   violation. Each test must fail if its intended CHECK is absent. The separate
   downgrade-refusal helper at lines 634–641 correctly checks the `RuntimeError:`
   line; that earlier Alembic-log issue is fixed.

### Verification

- Required local suite, using the repository venv:
  `python -m pytest -q tests/persistence -m "not postgresql"`:
  **300 passed, 1 failed, 15 deselected in 396.44s**. The failure was a
  120-second subprocess timeout in
  `test_runtime_http.py::test_database_outage_never_opens_legacy_gm_access[sql]`,
  not an assertion failure. Its targeted rerun passed: **1 passed in 11.24s**.
  This is not recorded as a new product finding or misreported as an entirely
  green first run.
- `python -m alembic heads`: exactly `20261110_0003 (head)`.
- `python -m alembic upgrade head` followed by `python -m alembic check` on a
  new, isolated SQLite file: passed; **No new upgrade operations detected**.
- Additional synthetic SQLite probes injected a late upgrade failure and a late
  downgrade failure. In both directions the complete schema, every row and the
  Alembic stamp were unchanged after failure. Removing the injected collision
  and retrying succeeded, preserved legacy values, and left a clean
  `foreign_key_check`. Migrated CHECK names and expressions matched the models.
- Inspected PostgreSQL plain ALTERs, `op.f` use for CHECK operations, model/index
  parity, composite FKs and cascades. Live PostgreSQL was verified through CI,
  not locally.
- Campaign-run scoping probes preserved all **4 runtime legacy files** and all
  **5 legacy reverse-export files** byte-for-byte before/after adding campaign
  runs. Runtime history retained all runs; the deliberately changing history
  sidecar and manifest were excluded from the runtime legacy-file comparison.
- `git diff --check aaec6f19..fa89abbc` passed. Changed paths are within the brief.

**CI checked on the reviewed head:**
[run 42925552222](https://github.com/etamd17/GM_pf2e/actions/runs/42925552222)
has all four jobs completed successfully: `changes`, `test`, `postgresql`, and
`browser`. The main `test` job's log records 950 PF2e package checks passed,
then **4520 passed, 136 skipped, 195 deselected, 1 xfailed** in its main pytest
run. The PostgreSQL log records successful migration/schema comparison and
**15 passed, 301 deselected**. PR #249 remains draft and unmerged.

Supplemental probes are local review evidence under the workspace's
`output/pe12-review/`, outside the product worktree:
`migration_run_probe.py`, `constraint_probes.py`, `migration_retry_probe.py`
and `migration-retry-result.json`. They use only synthetic data. The first two
reproduce the numbered findings; the third verifies rollback and retry behavior.

### Judgment calls and next step

Accepted within this schema task: deriving stale state from authority; comparing
the maximum state-row `updated_at` per campaign against completed exports;
retaining migration-run evidence without a campaign FK; blocking downgrade for
archived characters; and deleting campaign-run machinery only after the downgrade
guards pass. PE-13 must maintain UTC `updated_at` on every write, as the report
states. The known SQLite timestamp-format limit, diff size, CI-only PostgreSQL
verification, and PE-24 ownership of start serialization are not new findings.

Next: Claude addresses findings 1 and 2, adds the focused regressions, reruns the
required checks and returns the revised PR for review. Review and queue edits
are uncommitted. This verdict does not authorize a merge.

### Review 2 — changes requested (Codex, 2026-11-11)

Reviewed `20a882b894448ddb0b4c762042736036d38a80e0` against the previously
reviewed `fa89abbcb13dd13d1cdd86f82778d641e75d1015`. The local branch and draft
PR #249 matched that head, and the worktree was clean before review. No product
files were edited, and nothing was committed, pushed or merged.

**Both original findings are closed.**

- Finding 1: campaign completion now uses the persisted `status = 'running'`
  predicate and checks the affected-row count. The original stale-object probe
  is refused with `ValueError: campaign migration run is already finished`.
  The new SQLite/PostgreSQL regressions cover the unchanged completion fields
  and the actual downgrade freshness query. Legacy completion still supports
  `succeeded -> verified`.
- Finding 2: the helper checks `IntegrityError.orig`, and the revised fixtures
  isolate their intended constraints. Independently removing each of the two
  original CHECKs now makes its test fail with `DID NOT RAISE IntegrityError`.
  The widened refusal-message assertions and documented SQLite FK limitation
  are consistent with the fixtures.

#### Numbered finding

1. **R2-1 / P2 — Preserve rollback-aware object state after reading a finish result.**
   `core/persistence/repositories.py:467–479` performs the update with
   `synchronize_session=False`, then expires the written attributes. This works
   only while those attributes remain unread. An ordinary read of the returned
   result inside the savepoint loads the finished values and consumes that
   expiry. A subsequent savepoint rollback restores SQL but does not invalidate
   those loaded ORM values, since the ORM did not track this update.

   Reproduced with no manual row edits:
   1. Start a run and begin a savepoint.
   2. Finish it as `exported`, with counts and manifest details.
   3. Read `status`, `completed_at`, `entity_counts` and `details` from the result.
   4. Roll back the savepoint.
   5. SQL contains `running`, NULL completion time and the original metadata;
      the returned object still contains `exported` and the rolled-back result.
      `start_campaign_run()` for the same attempt returns that same cached
      object and also reports `exported`.

   The test at `tests/persistence/test_game_state_schema.py:564–576` rolls back
   before reading, so it misses this case. This can mislead PE-24's retry or
   completion handling. It does **not** bypass the corrected persisted CAS or
   downgrade query; the original P1 remains closed.

   Make synchronization or rollback invalidation aware of the ORM transaction,
   preserving the persisted CAS and legacy behavior. Extend the regression to
   read the returned fields before rollback, then assert agreement between the
   object, the stored row and a rejoined attempt, followed by a valid finish.
   Expiring the fields once is not sufficient for this sequence.

#### Verification and caller compatibility

- `python -m pytest -q tests/persistence -m "not postgresql"`:
  **305 passed, 16 deselected in 307.09s**.
- `python -m alembic heads`: exactly `20261110_0003 (head)`.
- Fresh isolated SQLite file: `python -m alembic upgrade head`, then
  `python -m alembic check`, both passed; no new upgrade operations detected.
- Both original probes were exercised. The migration-run probe now stops at
  the intended finish refusal. The constraint probe now fails at the missing
  clock CHECK; the session CHECK removal was also run independently and caught.
  These expected probe failures are evidence that the original findings closed.
- Independent mutation checks in isolated archived copies: the five selected
  normal-usage tests passed; deleting the persisted-status predicate failed the
  stale-object regression; restoring `set_committed_value` failed the current
  savepoint regression. Neither mutation is needed for the new read-before-
  rollback failure: it occurs on the unchanged candidate.
- New synthetic reproduction: workspace-local
  `output/pe12-review-r2/finish_savepoint_probe.py`, run with the repository venv
  and `GM_pf2e_pe01` as its argument. It contrasts unread/read-before-rollback
  cases and verifies both the stored row and `start_campaign_run()` rejoin.
  Mutation evidence is in `output/pe12-review-r2/mutation-result.json`.
- Requiring a campaign run to be attached to the repository's session is
  acceptable for PE-24 callers: load/rejoin and finish in the owning session.
  Flushing caller-owned pending work before the CAS is not an additional finding.
  The remaining issue is rollback coherence of the returned/rejoined object,
  not the attachment requirement.
- `git diff --check fa89abbc..20a882b8` and
  `git diff --check aaec6f19..20a882b8` passed. Only the repository, focused tests,
  task report and queue changed since review 1.

**CI checked on this head:**
[run 43037682794](https://github.com/etamd17/GM_pf2e/actions/runs/43037682794).
`changes` and `postgresql` passed. The PostgreSQL log confirms upgrade/schema
comparison and **16 passed, 305 deselected**, including the new stale-object
case. `browser` failed during dependency installation: the runner could not
resolve `azure.archive.ubuntu.com`, so the required Ubuntu packages could not
be fetched and browser tests were skipped. This is an infrastructure failure,
not evidence of a product regression. `test` was still in progress at the last
check. All four jobs are therefore **not green**; rerun the failed browser job
and confirm the main test job before returning for acceptance.

Next: Claude addresses R2-1, preserves both closed original findings, reruns the
focused regression and required checks, and returns the updated PR for re-review
with all four CI jobs green. Review and queue edits are uncommitted. No merge.

### Review 3 — accepted; merge blocked by browser CI (Codex, 2026-11-11)

Reviewed `6f8f821880e644ce74648933c0d59ab3bfe77b6b` against the previously
reviewed `20a882b894448ddb0b4c762042736036d38a80e0`, matching PR #249's head.
The worktree was clean before review. **R2-1 is closed. No remaining code
findings.** Both original review-1 findings remain closed. This accepts the
implementation; it does not waive the all-green CI requirement for merging.

The conditional update and affected-row check still decide whether a campaign
run may finish. After it wins, the same-value ORM update registers the object
with the current transaction, so a rollback invalidates fields even after the
caller has read them. The extra update is acceptable: it stays inside the same
transaction holding the winning write lock, uses the same completion timestamp,
and cannot be reached by a refused finish. Legacy completion is unchanged.

The new regression reads all four returned fields before rollback, then checks
the object, stored status/time and same-object rejoin, followed by a successful
finish. Independent probes also covered inner rollback, released-inner then
outer rollback, and full rollback. The reported outer-before-open-inner rollback
limitation reproduces with an ordinary ORM write as well; no non-test caller
uses `begin_nested`. This is acceptable for this task. Future PE-24 callers must
unwind nested scopes inner-first, as normal nested context managers do.

The in-memory aware-UTC timestamp and SQLite's reloaded naive-UTC timestamp
represent the same instant. The timestamp probe confirmed both updates use the
same value, rollback clears it, retry writes a new value, and a refused repeated
finish does not move it. The owning-session requirement remains acceptable.

Verification on the reviewed head:

- Repository venv, `python -m pytest -q tests/persistence -m "not postgresql"`:
  **306 passed, 17 deselected in 346.65s**.
- `python -m alembic heads`: exactly `20261110_0003 (head)`.
- Fresh isolated SQLite file: `python -m alembic upgrade head`, then
  `python -m alembic check`, passed with no new upgrade operations detected.
- `python ..\output\pe12-review-r2\finish_savepoint_probe.py .`: both cases
  show `running` for the returned object, stored row and same-object rejoin.
- Independent archived-copy mutation: six selected baseline tests passed;
  restoring only the expire-only method from `20a882b8` failed the new test at
  its post-rollback assertion (`test_game_state_schema.py:607`). Evidence:
  workspace `output/pe12-review-r3/mutation-result.json`. Additional probes:
  `nested_finish_probe.py` and `timestamp-result.json` in that directory.
- `git diff --check 20a882b8..6f8f8218` passed. Product changes in this round
  are confined to the finish correction and its regression tests.

CI checked through the GitHub connector on
[run 43053080292](https://github.com/etamd17/GM_pf2e/actions/runs/43053080292):
`changes`, `test` and `postgresql` passed. The main job reports 950 PF2e contract
tests passed, then 4525 passed, 136 skipped, 197 deselected and 1 xfailed in the
main suite. PostgreSQL reports **17 passed, 306 deselected**, including the new
rollback regression, and a successful upgrade/schema comparison.

`browser` is still failed at dependency installation: the runner could not
resolve `azure.archive.ubuntu.com`, and browser tests were skipped. The latest
observed run is completed/failed, not all green. This is an infrastructure
failure, not a new code finding. The requested `gh pr checks 249` CLI could not
connect to `api.github.com` locally; the connector supplied the job statuses and
logs above. A queued rerun has not been counted as a pass.

Next: Claude commits these review/queue edits and pushes them, obtains green
`changes`, `test`, `postgresql` and `browser` checks on the resulting PR head,
then merges under the handoff protocol. Do not bypass the browser check or use
auto-merge. Dependent work remains unready until PE-12 is integrated. This
review made no product edits, commits, pushes or merges.
