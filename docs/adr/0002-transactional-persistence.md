# ADR-0002: Transactional persistence and ownership

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Project owner and maintainers

## Context

Accounts, invitations, campaign membership, character ownership, and most game
state are currently stored in JSON documents. Atomic replacement protects some
individual files, but a business operation often spans several documents. Invite
redemption, ownership transfer, last-GM protection, character saves, and event
publication therefore cannot be committed as one unit. Concurrent read-modify-
write operations can lose updates, and process-local event queues prevent safe
multi-worker deployment.

The application also stores large immutable rules data and binary campaign assets.
Those have different access and lifecycle requirements from transactional identity
and game state, so forcing every byte into one storage mechanism would add cost
without improving correctness.

## Decision

Use SQLAlchemy and Alembic with PostgreSQL as the production system of record for
transactional identity, authorization, and mutable game state. Use SQLite for fast
local/unit tests, while CI also runs PostgreSQL integration tests for concurrency
and database-specific behavior.

The first database cutover covers:

- users;
- campaigns and memberships;
- characters and owner/editor/viewer assignments;
- invites and redemption records;
- character drafts;
- audit events and migration runs.

Later vertical slices move character payloads, encounters, lightweight campaign
documents, and a domain-event outbox into PostgreSQL. Binary uploads and Chronicle
source documents remain behind an asset repository. Validated PF2e content remains
an immutable, versioned build artifact.

State changes and their externally visible events will be written in one database
transaction through an outbox. PostgreSQL `LISTEN/NOTIFY` may wake SSE workers,
while persisted outbox rows remain the replay and recovery source. Redis will not
be added until measured scale or delivery requirements justify another service.

Migrations use an expand/verify/contract process with additive schema changes,
dry-run reports, checksums, shadow comparison, feature-flagged reads, and verified
reverse export. Ambiguous ownership is reported and blocks cutover; it is never
guessed from a character name.

## Options considered

### A. Continue with JSON plus more file locks

| Dimension | Assessment |
|---|---|
| Operational cost | Low |
| Transactionality | Limited to one file |
| Multi-worker safety | Difficult and platform-dependent |
| Migration effort | Low initially |

This remains useful as a compatibility adapter but is rejected as the production
end state.

### B. Use SQLite as the production database

| Dimension | Assessment |
|---|---|
| Operational cost | Low |
| Transactionality | Good for a single instance |
| Concurrent writes | Limited |
| Railway scaling | Constrained by shared-volume semantics |

SQLite is appropriate for local tests and small standalone deployments, but not
as the recommended hosted multi-worker target.

### C. Use PostgreSQL with repository adapters

| Dimension | Assessment |
|---|---|
| Operational cost | Medium |
| Transactionality | Strong |
| Multi-worker safety | Strong |
| Incremental migration | Strong |

This is the selected option.

### D. Rewrite as independent services

| Dimension | Assessment |
|---|---|
| Delivery risk | Very high |
| Operational cost | High |
| Isolation potential | High |
| Current need | Unproven |

The present traffic and team constraints do not justify distributed-service
complexity. Domain boundaries will be made explicit inside the Flask application
first.

## Trade-off analysis

PostgreSQL adds deployment configuration, migration discipline, and integration
test cost. In return it provides the constraints, row locking, optimistic
revisions, and atomic multi-record changes required by ownership and multiplayer
state. Repository interfaces keep domain logic independent from SQLAlchemy and
allow legacy JSON reads during the transition.

Feature flags such as `OWNERSHIP_BACKEND=json|shadow|sql` and
`STATE_BACKEND=file|shadow|db` reduce cutover risk, but they also create temporary
dual-read complexity. Shadow mode must be time-bounded and backed by mismatch
metrics; it is not a permanent architecture.

## Consequences

- Ownership and invite redemption can enforce invariants transactionally.
- Character and encounter mutations can use revisions to reject stale writes.
- Event replay survives worker restarts and can support multiple workers.
- Production migrations require backups, preflight reports, and forward-fix plans.
- Existing JSON files remain useful for compatibility and recovery during the
  observation window, but stop being authorization sources after SQL cutover.
- Binary-asset durability remains a separate operational concern and must stay
  behind a repository abstraction.

## Acceptance criteria

- Concurrent redemption of a one-use invite yields exactly one success.
- Concurrent claims cannot create more than one character owner.
- Membership operations cannot leave a campaign without a GM.
- Authorization reads no legacy ownership fields after SQL cutover.
- Mutation and event publication commit or roll back together.
- Migration counts and checksums reconcile, with no unresolved ownership conflict.
- PostgreSQL integration tests run in CI before database-backed changes merge.

## Action items

1. Add SQLAlchemy, Alembic, and PostgreSQL test infrastructure.
2. Define identity, membership, assignment, invite, audit, and migration models.
3. Build an idempotent dry-run/import/verify/export toolchain.
4. Cut authorization reads over through `json`, `shadow`, and `sql` modes.
5. Migrate mutable game-state vertical slices behind repository interfaces.
6. Add the transactional outbox before enabling multiple web workers.
