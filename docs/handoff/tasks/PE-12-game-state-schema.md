# PE-12: Add transactional character and encounter state schema

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** review (implemented 2026-11-10 on `claude/pe-12-game-state-schema` from `aaec6f19`; draft PR #249 for CI evidence)
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

## Review
<!-- Codex appends numbered findings or acceptance. -->
