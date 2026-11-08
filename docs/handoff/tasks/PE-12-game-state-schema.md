# PE-12: Add transactional character and encounter state schema

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked on PE-01 acceptance and merge
- **Branch:** `claude/pe-12-game-state-schema` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** [PE-01](PE-01-state-command-contract.md). Coordinate migration merge order with PE-10; rebase onto its accepted revision if it lands first.
- **Context:** [foundation baseline](../../player-release/foundation-baseline.md), [ADR-0002](../../adr/0002-transactional-persistence.md), `core/persistence/models.py`, `core/persistence/database.py::Database`, existing migration conventions.
- **Change budget:** roughly 500 changed lines including tests; schema only.

## Goal

Provide additive tables capable of holding the agreed character and encounter
state in the same database transaction. Existing live routes continue using their
current backend; adding the schema does not activate it or import player data.

## Approach

Allowed files: `core/persistence/models.py`, one new `migrations/versions/` revision,
`tests/persistence/test_game_state_schema.py`, and focused adjustments to existing
model/migration tests. Use PE-01's accepted names and field contract.

1. Add character-state records keyed to existing campaign/character identity, with
   validated payload/schema and rules-version metadata and independent revisions
   for the state domains agreed in PE-01. Preserve D&D's separate build revision.
2. Add encounter-state records scoped by campaign/encounter ID, including roster,
   enemy state, turn/round state, effect references, rules pins and revision. Ensure
   a character from another campaign cannot be referenced as an owned PC through
   the schema boundary; document any invariant enforced later by repositories.
3. Define nonnegative revisions, uniqueness and referential constraints, timestamp
   behavior and deletion/retention semantics from PE-01. Avoid mutable JSON defaults
   shared across instances. Do not precompute game mechanics in SQL.
4. Supply additive upgrade and safe downgrade behavior. A nonempty state table must
   not be silently dropped; require the verified export/removal precondition named
   by the contract. Serialize migration-head integration with PE-10.
5. Test SQLite and PostgreSQL schema behavior using isolated synthetic records.
   Record repositories, receipt/outbox tables and runtime adapters as follow-ups.

## Acceptance

- [ ] Migration preserves all existing identity/assignment/draft/receipt rows and creates only the new state structures.
- [ ] Models round-trip independent build/play state and encounter/enemy/turn state with their agreed rules metadata and revisions.
- [ ] Invalid revisions, duplicate state keys and cross-campaign identity links fail as specified; repository-only invariants are explicitly documented rather than falsely claimed as database checks.
- [ ] Two model instances do not share mutable payload defaults.
- [ ] Empty migration downgrade succeeds; populated-state downgrade refuses destructive loss under the documented contract.
- [ ] `python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_game_state_schema.py` passes.
- [ ] `python -m pytest tests/persistence/test_game_state_schema.py -q -m postgresql --strict-markers` executes and passes the new PostgreSQL-marked cases against an isolated test database; zero collected/skipped cases are not acceptance.
- [ ] `python -m alembic heads` reports one integrated head and `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Runtime repositories, mutation APIs, backend cutover, migration of real data,
  operation/outbox schema, rules engines, access-policy changes, UI or SSE.
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
