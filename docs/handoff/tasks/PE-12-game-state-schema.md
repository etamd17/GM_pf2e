# PE-12: Add transactional character and encounter state schema

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready (PE-01 accepted and merged in PR #247; brief reconciled with its section 11 on 2026-11-10)
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
`tools/export_transactional_runtime.py` (their `migration_runs` readers only); one new
`migrations/versions/` revision; `tests/persistence/test_game_state_schema.py`; and
focused adjustments to `tests/persistence/test_database_and_models.py`,
`test_migrate_transactional_store.py`, `test_export_runtime.py` and
`test_postgresql_concurrency.py`. Use PE-01's accepted names and field contract.

1. Add `character_states` keyed `(campaign_id, character_id, domain)` for the
   `build` and `play` domains (section 2), with validated payload/schema and
   rules-pin metadata and an independent revision per domain. The D&D `build`
   revision replaces `build_rev`. State rows cascade with their character.
2. Add `encounter_states` scoped by campaign/encounter id, with roster reference,
   turn/round state, effect references, rules pins and revision. Enforce one active
   encounter per campaign with a partial unique index.
3. Add `encounter_combatants` keyed `(campaign_id, encounter_id, combatant_id)`, with
   `kind`, a creature ref and a nullable `character_id` under a composite FK to
   `characters(campaign_id, id)`, so a character from another campaign cannot be
   referenced. Add campaign clocks keyed `(campaign_id, clock)` for `world` and
   `downtime`, each with a value and a revision. Add `campaign_sessions` with primary
   key `(campaign_id, session_id)` and a partial unique index allowing one `active`
   row per campaign (section 4). Add `campaign_state_authority` (one row per
   campaign: authority, `stream_epoch >= 1`, change metadata; section 1 and 7).
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
   a refusal), and `tools/export_transactional_runtime.py::_envelopes`.

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
8. Test SQLite and PostgreSQL schema behavior using isolated synthetic records.
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
- [ ] `MigrationRunRepository.get_by_source_checksum`/`start`, `tools/migrate_transactional_store.py` and `tools/export_transactional_runtime.py::_envelopes` ignore campaign runs, with a test that a campaign import run does not change the runtime export and does not make a legacy whole-store import refuse.
- [ ] The locator index refuses two non-archived rows with the same `(campaign_id, legacy_storage, legacy_file)` and does not count rows with `archived_at` set.
- [ ] Two model instances do not share mutable payload defaults.
- [ ] Empty migration downgrade succeeds. Populated downgrade is refused while any campaign is `db`, `migrating` or `exporting`; while any `campaign_sessions` or campaign clock row exists; while character-held state rows lack an `exported` run newer than their last committed command, including when a second, unfinished export follows an older successful one; and when run offline.
- [ ] `python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_game_state_schema.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py tests/persistence/test_postgresql_concurrency.py` passes.
- [ ] `python -m pytest tests/persistence/test_game_state_schema.py -q -m postgresql --strict-markers` executes and passes the new PostgreSQL-marked cases against an isolated test database; zero collected/skipped cases are not acceptance.
- [ ] `python -m alembic heads` reports one integrated head and `git diff --check` passes.
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
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
