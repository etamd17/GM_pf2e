# PE-14a: Campaign stream, receipt and event tables; envelope, digest and type registry

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-13a)
- **Branch:** `claude/pe-14a-stream-schema` from current main after PE-13a merges. Record the exact starting SHA.
- **Depends on:** [PE-13a](PE-13a-state-repositories.md) (lock order, `CommandLocks`). PE-01 and PE-12 are done. PE-13b and PE-13c are not needed; this task may run in parallel with them.
- **Followed by:** PE-14b (executor; wires check 1), PE-14c (grant and token records, step-4 authorizer), PE-14d (stream-locked authorization writes), PE-14e (sessions, folder form, history backfill) and PE-14f (status read, abandon, their routes). "PE-14 done" means all six.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: ids (C:25-29), content mismatch only for commands that derive rules (C:41-42), dependency sets and payload id fields (C:87-95), check 1 (C:97-100), override (C:137-138), lock order step 1 (C:156-157), envelope (C:224-235), digest (C:236-241), receipt (C:242-245), abandon receipt (C:299-302), code table (C:352-362), refusal re-check order (C:373-375), event row (C:394-417), event table is history and outbox (C:445-446), snapshot watermark and replay pages (C:580-590), round-trip class "not exported; frozen; preserved" (C:682), populated downgrade (C:831-837), greenlet test and PostgreSQL-only concurrency (C:826-828), "events keep their ids and have no FK" (C:1088), PE-14 row (C:1090).
  - Digest reference: commit `00b334b4`, `vtt/commands/service.py` `_canonical` (:125) and `command_digest` (:136). Not on main: port, do not import. Main's `tools/migrate_transactional_store.py:131-142` lacks integral-float normalization; do not reuse it.
  - Principals: `core/request_context.py` `PrincipalKind` :25-33, `Principal.integration_token` :149.
  - Migrations: head `20261111_0004` (`migrations/versions/20261111_0004_dnd5e_identity.py:21-22`); conventions in `20261110_0003_game_state_schema.py` (SQLite FK guard :50-65, single SQLite transaction :75-95, offline downgrade refused :442-443); `core/persistence/runtime.py:32` `_REQUIRED_REVISION` (stale comment at :31).
  - Tests that pin the current head literally and break when `_0005` lands: `tests/persistence/test_runtime_backend.py:117` (parametrized revision list) and :130 (`if revision == "20261111_0004"`); `tests/persistence/test_dnd5e_identity_schema.py:458-474` (downgrade to `PREVIOUS` from `head`, then asserts version `== HEAD`), :483 (offline `HEAD:PREVIOUS` expects 0004's message) and :492 (`heads == [HEAD] == ["20261111_0004"]`). Precedent for the fix: `tests/persistence/test_game_state_schema.py:48-50` pins `PE12 = "20261110_0003"` so downgrade cases stop at their own revision. (`test_game_state_schema.py:47` is `HEAD = runtime._REQUIRED_REVISION`; it needs no edit.)
  - `create_campaign` (`core/persistence/campaign_runtime.py:145-165`) upserts the campaign row inside its transaction (:152-156). The stream row's FK therefore lets PE-14d call `lock_stream` there only after that insert (its declared lock-order exception).
  - Existing tables that must stay separate: `CharacterWorkflowReceipt` (`models.py:455-483`), `AuditEvent` (`models.py:486-510`).
  - Runtime: single gevent worker (`Procfile:1`, `railway.toml:6`); psycopg 3.3.6, gevent 26.9.0, SQLAlchemy 2.1.1 (`requirements.txt:633, :295, :796`). Pool: SQLAlchemy `QueuePool` defaults (pool_size 5, max_overflow 10); `core/persistence/database.py:79-81` passes only `pool_pre_ping`.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 600 product lines (including the migration) and 650 test lines. Stop and report past about 750 product lines. (Planning estimate; raised from 550 for `applied_status`, the receipt CHECKs, the two read helpers and the head-pin test edits.)

**Why PE-14 was split.** The PE-14 row (C:1090) covers four tables plus a token store, the envelope and digest, the transaction runner and refusal path, the status read and abandon, five kinds of authorization writer, two session commands with the legacy-folder collision rule, the history backfill and about fifteen named acceptance cases. A read-only estimate is 3,500-4,500 changed lines, against the 500-line target (`task-map.md:5`); PE-12 ran 50% over a 1,300-line budget. Only PE-14a (`_0005`) and PE-14c (`_0006`) migrate, and they are sequential through PE-14b, so there is always one Alembic head (`task-map.md:76`).

## Goal

Create the per-campaign stream, receipt and event tables and the pure pieces every command needs before it runs: the envelope parser, the digest, the command-type registry with check 1, and plain stream reads for PE-31. No command executes yet.

## Decisions this brief fixes (reviewer: challenge these first)

1. **The stream row.** `campaign_streams(campaign_id PK, FK campaigns ON DELETE CASCADE, last_seq BIGINT NOT NULL DEFAULT 0 CHECK >= 0)`. It is lock-order step 1 (C:156-157) and the only row PE-13a's `lock_stream` binds to. Every writer takes it before the campaign row and before any state or authorization row (PE-14d enforces this for the existing writers). `stream_epoch` stays on `campaign_state_authority` (C:29) and is not copied. The row is created lazily under the lock: `INSERT ... ON CONFLICT DO NOTHING`, then `SELECT ... FOR UPDATE`, so this task changes no campaign creator. Because of the FK, the lazy insert works only once the campaign row exists in the transaction; `create_campaign` therefore inserts its campaign row first and then calls `lock_stream` (PE-14d's declared exception). A migration backfill is unnecessary and not done.
2. **Seq.** `allocate(stream, n)` returns `last_seq+1 .. last_seq+n` and stores the new `last_seq`, under the held lock (C:28, C:426). PE-14b's `ctx.emit(EventDraft) -> (event_id, seq)` calls it at emit time, so a seq is known while `apply` runs; the receipt's seq range comes from the emitted events. Gaps are impossible because allocation and insert share the transaction.
3. **No FK on receipts and events.** `game_events` has no FK (C:1088); `command_receipts` also has none, matching `CharacterWorkflowReceipt` (`models.py:455-477`); both are kept for the campaign's life across epochs (C:244-245). Names stay distinct from `character_workflow_receipts` and `audit_events`.
4. **Receipt columns:** `campaign_id`, `principal_kind` (`user` | `integration_token`), `principal_id`, `operation_id`, `digest`, `status` (`committed` | `rejected`), `applied_status`, `code`, `revisions` JSON, `event_ids` JSON, `seq_first`/`seq_last` (nullable for refusals), `stream_epoch`, `created_at`; unique `(campaign_id, principal_kind, principal_id, operation_id)` (C:242-244). No response body (C:244).
   - `applied_status` is `committed` or `pending` on a committed receipt and NULL on a rejected one (CHECK). A replay answers HTTP 202 when it is `pending` and 200 otherwise, so the "recorded status" of C:354-355 survives; the status read (PE-14f) reports `state: committed` for both. This column is a judgment (C:243 lists only `committed`/`rejected`); record it in the report.
   - `code` is NULL on a committed receipt and NOT NULL on a rejected one (CHECK).
   - `digest` is 64 lowercase hex, nullable, with a CHECK that `digest IS NULL` if and only if `code = 'abandoned'`: an abandon receipt (`status = rejected`, `code = abandoned`, C:299-302) has no envelope to hash.
   - Lookup order is PE-14b's, recorded here so the columns support it: `code = abandoned` → `409 abandoned`; else same digest → replay; else `409 operation_id_reused` (C:357-360, C:373-375).
5. **Event columns** follow C:396-407 one-for-one: scalar columns for `event_id` (PK, 32 hex), `campaign_id`, `seq`, `stream_epoch`, `type`, `occurred_at`, `session_id`, `encounter_id`, `operation_id`, `action_id`, `causation_event_id`, `origin`, `principal_kind`, `principal_id`, `rules_profile`; JSON for `actor`, `subjects`, `changes`, `dice`, `explain`, `visibility`. Unique `(campaign_id, seq)`; index `(campaign_id, stream_epoch, seq)` for PE-31's cursor (C:582, C:588).
6. **Registry.** A `CommandType` declares `type`, subject kind, dependency set (`expected` kinds and each one's relation to the subject), payload id fields, `requires_expected`, `derives_rules` (only these types can fail `503 content_mismatch` at check 8, C:41-42), allowed principals, terminal predicate (C:87-95, C:130-135) and `lock_plan(ctx)`, the callable PE-14b calls after the receipt check to take lock-order steps 2-5. It also stores `apply(ctx)`, the callable PE-14b runs to make the change, and `rules_adapter`, an object with `check(ctx, override)` and `build_edition_matches(subject_record, pin)` from which PE-14b builds PE-13b's check runner. This task only stores `lock_plan`, `apply` and `rules_adapter`; it never calls them. Check 1 validates against the registry with no database access (C:97-100). Unknown `schema`/`type`, malformed `operation_id` (C:27), or a campaign that differs from the route's give `400 bad_envelope` (C:356). An `expected` kind outside the dependency set also gives `400 bad_envelope` (C:99). A missing required `expected` gives `400 bad_envelope` too: this is a judgment (a static-schema failure under check 1, C:99-100; C:356 does not list it); record it in the report.
7. **Check 1 is a standalone function.** `check_envelope(envelope, route_campaign_id)` is pure and runs before any transaction. PE-14b wires it; PE-13b's runner covers checks 4-9 only and does not call it. This task depends on neither.
8. **Stream reads for PE-31.** `stream_watermark(session, campaign_id) -> int` is a plain read of `last_seq` (0 when there is no row), taking no lock. `events_after(session, campaign_id, after_seq, limit) -> list[EventRow]` returns events ordered by seq. Both are raw reads; projection and the 200-per-page limit (C:588-590) belong to PE-31.
9. **Greenlet contention** (C:826-828) is proved here, on the stream lock itself, because it is a property of the lock primitive. If the test shows psycopg blocks the gevent hub, stop and report before PE-14b: the fix is a design change. (PE-14b sets `lock_timeout` from its `STREAM_LOCK_TIMEOUT_MS` constant; this task sets none.)

## Approach

Allowed files:
- `core/persistence/models.py` (three new models only), `core/persistence/__init__.py` (exports)
- new `migrations/versions/20261115_0005_command_stream.py` (date part may change; keep `_0005`), `down_revision = "20261111_0004"`
- `core/persistence/runtime.py` (`_REQUIRED_REVISION` only; also correct the stale comment at :31)
- new `core/persistence/streams.py` (lock, allocate, watermark, events_after)
- `core/commands/__init__.py` (create if absent, no re-exports; whichever of PE-13b, PE-13c and PE-14a lands first creates it; callers import submodules). Its exact content is this one line followed by a newline, identical in every brief so parallel branches do not conflict: `"""Command pipeline. Import submodules directly; this package re-exports nothing."""`, new `core/commands/envelope.py`, `core/commands/digest.py`, `core/commands/registry.py`
- `core/persistence/command_locks.py` (bind the default `lock_stream` only)
- `tests/persistence/test_runtime_backend.py` (:117 and :130 only: add the new head as the accepted revision and keep `20261111_0004` in the list as a refused older revision)
- `tests/persistence/test_dnd5e_identity_schema.py` (add `PE10 = "20261111_0004"`; use it instead of `head`/`HEAD` in the downgrade-scoped cases at :458-474 and :483; move the single-head assertion at :492 to the new schema module)
- `tests/persistence/test_game_state_schema.py` (only if a downgrade case fails; pin it to `PE12`)
- new `tests/persistence/test_command_stream_schema.py` (includes the single-head test), `tests/persistence/test_command_envelope.py`, `tests/persistence/test_command_stream_postgresql.py`

1. Models and migration per decisions 1, 4 and 5, including the receipt CHECKs. Follow 0003's conventions. Populated downgrade (C:831-837): refuse while any `command_receipts` or `game_events` row exists, or any campaign's authority is `db`, `migrating` or `exporting`; refuse offline. Empty `campaign_streams` rows may be dropped (they hold only a counter; with no events, `last_seq` carries nothing). Upgrade then downgrade then upgrade round trip on SQLite and PostgreSQL.
2. Update the head pins listed in Context so the existing suites still test 0003 and 0004's own guards.
3. `streams.py`: `lock_stream(session, campaign_id) -> StreamRow`, `allocate(stream, n) -> range`, `stream_watermark(session, campaign_id) -> int`, `events_after(session, campaign_id, after_seq, limit) -> list[EventRow]`. `lock_stream` requires an active transaction and raises if called twice with different campaigns in one transaction.
4. `envelope.py`: parse the body of C:226-231, take the principal only from the authenticated `Principal` (C:233), accept only `USER` and `INTEGRATION_TOKEN` (others `403 not_permitted`, C:234-235), require integer `epoch`, validate `operation_id` (C:27), and parse `override` as optional (C:137). Whether the principal may send `override` is check 4 (PE-13b with PE-14c's `override_allowed`), not this task.
5. `digest.py`: SHA-256 of canonical JSON over `schema, campaign_id, type, subject, expected, rules, override, payload`; sorted keys, separators `(",", ":")`, `ensure_ascii=False`, `allow_nan=False`, integral floats as ints, absent `override` as `null` (C:236-241). Golden vectors: at least six, generated by running `00b334b4`'s `_canonical` on the same inputs where the key sets overlap, checked in as literals.
6. `registry.py`: `register(CommandType)`, `get(type)`, `check_envelope(envelope, route_campaign_id)` implementing check 1 per decisions 6 and 7. Register one test-only type from the tests, never in product code.
7. Bind PE-13a's `CommandLocks` default `lock_stream` to `streams.lock_stream`.

## Acceptance

- [ ] Migration adds exactly three tables; `python -m alembic heads` shows one head, `_REQUIRED_REVISION` names it, and `alembic check` is clean in CI. The single-head test lives in `test_command_stream_schema.py` and names `_0005`.
- [ ] Unique receipt key refused on duplicate; two events with the same `(campaign_id, seq)` refused; receipts and events survive deleting a character (no FK).
- [ ] Receipt CHECKs: a committed receipt with `applied_status` `pending` and with `committed` both insert; a committed receipt with NULL `applied_status` or a non-NULL `code` is refused; a rejected receipt with non-NULL `applied_status` or NULL `code` is refused; an `abandoned` receipt with NULL digest inserts; a NULL digest with any other code, and an `abandoned` receipt with a digest, are refused.
- [ ] Populated downgrade refused with one receipt, with one event, and offline; empty downgrade succeeds; round trip on SQLite (and PostgreSQL in CI).
- [ ] `test_runtime_backend.py` accepts the new head and refuses `20261111_0004`; `test_dnd5e_identity_schema.py` downgrade and offline cases still assert 0004's own refusal messages.
- [ ] `lock_stream` creates the row on first use; `allocate(3)` twice gives 1-3 then 4-6. `lock_stream` inside a transaction that has just inserted a new campaign row succeeds.
- [ ] `stream_watermark` returns 0 with no row and `last_seq` otherwise, without locking; `events_after` returns events after the given seq in seq order, at most `limit`. PostgreSQL (CI): inside one `REPEATABLE READ` transaction the watermark equals the max seq that `events_after(…, 0, …)` reads, while a concurrent transaction commits more events.
- [ ] Digest golden vectors pass; changing only `operation_id`, `epoch` or `client` keeps the digest; adding, removing or rewording `override` changes it; `1.0` and `1` hash equally; `NaN` is refused.
- [ ] Check 1: unknown type, unknown schema, malformed `operation_id`, `campaign_id` differing from `route_campaign_id`, an `expected` kind outside the dependency set, and a missing required `expected` each give `400 bad_envelope` without touching the database; `LEGACY_GM`, `LEGACY_PLAYER`, `PUBLISH_TOKEN` and `ANONYMOUS` principals give `403 not_permitted`. A registered type exposes `derives_rules`, `lock_plan`, `apply`, `rules_adapter`.
- [ ] PostgreSQL (CI): two first-time `lock_stream` calls for one campaign race and both succeed with one row; two contending transactions allocate disjoint contiguous ranges.
- [ ] PostgreSQL (CI), greenlet contention: under `gevent.monkey.patch_all()` in a subprocess, greenlet A holds the stream lock and sleeps; greenlet B blocks on the same lock; a third greenlet keeps ticking (heartbeat at least every 100 ms) while B waits, and B proceeds after A commits. Also run with more waiters than the pool allows (QueuePool defaults 5 + 10) and record the outcome.
- [ ] `python -m pytest -q tests/persistence/test_command_stream_schema.py tests/persistence/test_command_envelope.py tests/persistence/test_game_state_schema.py tests/persistence/test_dnd5e_identity_schema.py tests/persistence/test_state_repositories.py tests/persistence/test_database_and_models.py tests/persistence/test_runtime_backend.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_command_stream_postgresql.py tests/persistence/test_game_state_schema.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: wiring check 1, the transaction runner, fence, receipt lookup and replay, `lock_plan` calls, `ctx.emit`, `lock_timeout`, refusal transaction, event writer and post-commit hook (PE-14b); checks 4-9 and the override check (PE-13b); grant and token records, the authorizer and `override_allowed` (PE-14c); authorization writes and the `create_campaign` lock order (PE-14d); sessions, folder form and backfill (PE-14e); status read and abandon (PE-14f); projected snapshots, cursors and paged replay (PE-31).
- No route, no `app.py`, no SSE hub (`app.py:2638-2958`, PE-31b), no Obsidian code, no change to `character_workflow_receipts` or `audit_events`.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
