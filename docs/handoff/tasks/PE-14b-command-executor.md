# PE-14b: Command executor: fence, receipts, replay, refusal transaction and event writer

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-13b, PE-14a)
- **Branch:** `claude/pe-14b-command-executor` from current main after PE-13b and PE-14a merge. Record the exact starting SHA.
- **Depends on:** [PE-13b](PE-13b-check-order-conflicts.md) (check runner for checks 4-9, `CommandRefusal`, `Authorizer` protocol, conflict body), [PE-14a](PE-14a-streams-receipts-events-schema.md) (tables, `check_envelope`, digest, registry, `lock_stream`/`allocate`), and through them [PE-13a](PE-13a-state-repositories.md) (`CommandLocks`, repositories, `StateRowMissing`, `ProfileMismatch`, `LockOrderError`, `PinUnavailable`, `UnknownSchemaVersion`). PE-13c is not needed: its exceptions are answered by attribute, never imported (Decision 4).
- **Followed by:** PE-14c (real authorizer, `override_allowed`, grant and token records), then PE-14d; PE-14e and PE-14f both follow PE-14d.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: content mismatch only for rule-deriving commands (C:41-42); check order, fence and receipt (C:97-105); no revision compared before visibility passes (C:111-112); override (C:137-140); schema versions (C:153-154); lock order (C:156-164); receipt and retry rule (C:242-252); epoch (C:346-350); code table (C:352-366); refusal receipts (C:368-378); both transition races (C:379-382); success body (C:388-392); event row, visibility and notes rule (C:394-419); response fields pass through the caller's projection (C:420-421); transaction steps (C:425-443); after commit / never inside (C:448-459); PE-14 row (C:1090).
  - PE-31 expects `services/session_events.py` and PE-14 repository APIs (`docs/handoff/tasks/PE-31-snapshot-replay.md:45`; also `docs/player-release/implementation-plan.md:106`). That file does not exist.
  - Legacy crash case to guard: receipt save fails, retry applies HP twice (`docs/player-release/obsidian-reuse-review.md:165-175`); response-loss tests assigned "under PE-14/70" (:194).
  - Error classes: `core/persistence/runtime.py` `store_call` :80-93, `StoreUnavailable` :35.
  - Process locks that must never be taken while the stream row is held: `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`), `ENCOUNTER_LOCK` (`app.py:2312`), `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`), `campaign_lock` (`core/obsidian_sync.py:52`). `sse_broadcast` is `app.py:2833`; the SSE hub spans `app.py:2638-2958`.
  - Active session row: `CampaignSession` (`core/persistence/models.py:778-814`, `status in ('active','ended')`, one active per campaign).
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 550 product lines and 750 test lines. Stop and report past about 700 product lines. (Planning estimate; it grew from 500/650 after review added `lock_plan`, `ctx.emit`, pending and rejected replays, the full exception map and the lock timeout.)

## Goal

One Python entry point, `execute(envelope, principal, *, route_campaign_id, content_descriptor, project)`, that runs a registered command type through section 4's single transaction: check 1 before the transaction, then the stream lock, fence, receipt lookup, the type's lock plan, PE-13b's checks 4-9, `apply` and the receipt, all atomic, answering exactly per the code table, including refusal receipts written in a second transaction. Only test-only command types use it; no route calls it.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Who runs which check.** Check 1 is PE-14a's `check_envelope(envelope, route_campaign_id)`, called before any transaction opens; a failure is `400 bad_envelope` (or `403 not_permitted` for a disallowed principal kind) with nothing written. Checks 2 (fence) and 3 (receipt lookup) are this executor's, under the stream lock. PE-13b's `run_checks` runs checks 4-9 only: the executor sets the ctx flags `envelope_checked`, `fence_passed` and `receipt_absent` as each passes, and `run_checks` raises `CheckOrderError` if any is unset. An unfilled check-9 adapter slot fails at runner construction.
2. **Fence mapping.** Authority `migrating` or `exporting` gives `503 migration_in_progress`; `file` or no authority row gives `503 authority_unavailable`; `db` with `epoch != stream_epoch` gives `409 resync_required` (C:101-104). The fence reads `campaign_state_authority` while holding the stream row, so an authority flip (which also takes the stream lock, PE-24) cannot interleave.
3. **Receipt lookup order** (C:357-360, C:373-375): a receipt with `code = abandoned` answers `409 abandoned` whatever the digest (its digest is NULL, PE-14a); otherwise a matching digest replays; otherwise `409 operation_id_reused`. The refusal transaction's re-check uses the same order.
4. **Exceptions to codes.**
   - Any exception raised from `core.commands` that has attributes `code` and `http_status` is answered from those attributes; whether a receipt is written follows the code table (C:356-366) for that code. This covers PE-13b's `CommandRefusal` (which accepts every code in C:352-366) and PE-13c's `ReservationBlocked` (`rule_violation`, 422) and `InEncounter` (`in_encounter`, 422). The executor imports no PE-13c symbol.
   - PE-13a `StateRowMissing`, `PinUnavailable` and `UnknownSchemaVersion` give `503 authority_unavailable`, nothing written (C:153-154, C:784-785).
   - PE-13a `ProfileMismatch` and `LockOrderError` give `500 server_error`: they are programming errors (check 8 runs first; lock order is the type's responsibility). `AuthorityMismatch` after the fence has passed under the stream lock is also a programming error and gives 500 (judgment; record in the report).
   - A lock timeout or serialization failure (`OperationalError` with SQLSTATE `55P03`, `40001`, `40P01`) gives `503 temporarily_unavailable`; an exception raised by `COMMIT` itself gives `503 temporarily_unavailable` with outcome unknown; any other exception before commit gives `500 server_error` (C:364-366).
5. **Lock timeout.** On PostgreSQL the executor issues `SET LOCAL lock_timeout = '<STREAM_LOCK_TIMEOUT_MS>ms'` before `CommandLocks.stream()`, in both the command transaction and the refusal transaction. `STREAM_LOCK_TIMEOUT_MS` is a module constant in `core/commands/executor.py`, default 5000; PE-14d's writers and PE-14f's status read and abandon import the same constant. On SQLite nothing is issued.
6. **Lock plan.** After the receipt lookup, the executor calls the command type's `lock_plan(ctx)`, which acquires lock-order steps 2-5 through PE-13a's `CommandLocks` for the subject, every `expected` reference and the payload ids. Locking a hidden or missing id reveals nothing (the 404 comes later from check 5). `lock_plan` also reads stream-serialized subjects into `ctx.records` without a row lock. Checks 4-9 read only rows already locked; `apply(ctx)` may lock only rows it creates. Every writer takes the stream row before the campaign row and before any state or authorization row; no process lock is taken while the stream row is held.
7. **Seq at emit time.** `apply` emits events through `ctx.emit(EventDraft) -> (event_id, seq)`, which calls PE-14a's `allocate(stream, 1)` under the held stream lock at emit time, so a row `apply` writes can carry its own declare seq. The receipt's seq range and event ids come from the emitted events. Seqs are contiguous because nothing else allocates while the lock is held.
8. **Step 4 is a slot here.** The SQL re-read of authorization inputs (C:429-436) needs grants and SQL token records, which are PE-14c's. The executor builds PE-13b's `Authorizer` through a factory slot inside the same transaction, after the stream lock; tests use a stub implementing PE-13b's protocol (including `override_allowed(type)`) that reads `campaign_memberships` and `character_assignments` from the same session, never `flask.g`. PE-14c replaces the factory with `authorizer.load`.
9. **Projection is a callback** (`project`, as PE-13b). It covers `result`, `events[]`, `revisions`, `conflicts` and `current` (C:420-421): a revision key or event for a record the caller cannot see is omitted. A replay of a committed receipt returns `replayed: true`, the recorded status (HTTP 202 when the receipt's `applied_status` is `pending`, else 200) and a fresh projection. "Access lost" (C:355) means the authorizer, re-read in this transaction, now refuses the caller's campaign role for the type (check 4's predicate); the replay then answers `403` with no body (judgment on what "access lost" tests; record in the report). A replay of a rejected receipt answers that code's HTTP status with `{"ok": false, "code", "operation_id", "replayed": true}` and no `conflicts` or `current` (the receipt holds no body, C:244); clients refetch.
10. **Applied status.** `apply` reports whether an action is left open; the executor then answers HTTP 202 with `status: "pending"` and writes the receipt with `applied_status = pending`, else 200 `committed` with `applied_status = committed` (C:354; PE-14a column).
11. **Post-commit hook.** `services/session_events.py` (new) holds `after_commit(campaign_id, last_seq)` with no subscribers by default. It never calls `sse_broadcast` (`app.py:2833`, which takes `_LIVE_CAMPAIGN_DISPATCH_LOCK`) and is invoked only after `COMMIT` returns. PE-31b attaches the hub (C:449-451).
12. **Event visibility input.** Command types supply a visibility map per event; the writer fills unlisted projections with `none`, forces `gm` to `full` (C:411-412), rejects an unknown class, and rewrites any `changes[]` entry with path `notes` to `{"path": "notes", "changed": true}` (C:418-419). Override is stored in the event's `explain[]` as `{"override": {"reason": ...}}` (C:138); the event shape has no dedicated field, so record this choice in the report. Whether the caller may send `override` at all is PE-13b's check 4 (`override_allowed`), not the writer's.

## Approach

Allowed files:
- new `core/commands/executor.py`, `core/commands/events.py`
- new `services/session_events.py`
- `core/commands/checks.py`: add `emit` and the three check flags to the check context only if PE-13b's ctx lacks them; no change to check logic
- new `tests/persistence/test_command_executor.py`, `tests/persistence/test_command_executor_postgresql.py`

Do not edit `core/commands/__init__.py` (created by whichever of PE-13b, PE-13c and PE-14a lands first, containing only `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` and a newline; callers import submodules) or `core/persistence/__init__.py`.

1. `execute(envelope, principal, *, route_campaign_id, content_descriptor, project)`; every keyword is required (a missing one is a `TypeError`, never a default).
   1. `check_envelope(envelope, route_campaign_id)` (PE-14a), no transaction; set `envelope_checked`.
   2. Open one transaction. On PostgreSQL `SET LOCAL lock_timeout`; `CommandLocks.stream()` (PE-13a, bound to PE-14a's `lock_stream`).
   3. Fence (Decision 2); set `fence_passed`.
   4. Receipt lookup by `(campaign_id, principal_kind, principal_id, operation_id)` in the Decision 3 order; on a replay build the authorizer, check access and answer per Decision 9 with nothing written. Otherwise set `receipt_absent`.
   5. `lock_plan(ctx)` (Decision 6); build the authorizer through the factory slot.
   6. PE-13b `run_checks(ctx, spec, adapter)` for checks 4-9, with `adapter` taken from the command type's `CommandType.rules_adapter` (PE-14a); the runner is built from it. `content_descriptor` for check 8 is already in `ctx`.
   7. `apply(ctx)`: writes through PE-13a repositories, emits events via `ctx.emit` (Decision 7).
   8. Insert the receipt (`status = committed`, `applied_status`, `code` NULL, digest, revisions written, event ids, seq range, `stream_epoch`) and the event rows; commit (C:425-443).
   9. After commit: `after_commit(campaign_id, last_seq)`. Body per C:388-392 with `watermark` = last emitted seq, everything projected through `project`.
2. Refusal path (C:368-378): roll back; a second short transaction sets the lock timeout, takes the stream lock, re-runs the fence (changed: insert nothing, answer 503 or 409), re-checks the receipt key in the Decision 3 order (answer from it), otherwise inserts the `rejected` receipt (`applied_status` NULL, the refusal `code`, the digest, no seq range). Send the refusal only after that commits. Codes whose table row says "nothing" (`bad_envelope`, `operation_id_reused`, `not_permitted`, `resync_required`, every 503) skip the second transaction and write nothing (C:356-364). A `stale_revision` refusal body is PE-13b's `conflict_body`, projected.
3. `events.py`: `EventDraft`, and `ctx.emit` turning it into a row with `event_id = new_id()`, the allocated seq, the current `stream_epoch`, `occurred_at` UTC, origin from `client.origin`, principal kind and id, the visibility rules of Decision 12, and the `session_id` of the campaign's `status = 'active'` `campaign_sessions` row if any (read in the same transaction).
4. Never inside the transaction (C:455-459): loading rules content (the caller passes `content_descriptor` in), file writes, waiting, taking a process lock. The AST acceptance test below pins the import side of this.

## Acceptance

- [ ] Signature: calling `execute` without any one of `route_campaign_id`, `content_descriptor` or `project` raises `TypeError`.
- [ ] Order: check 1 runs before a transaction opens (a bad envelope opens no session); `run_checks` called without the executor's three flags raises `CheckOrderError`; a spy shows `lock_plan` runs after the receipt lookup and before check 4, and `apply` runs after check 9.
- [ ] Commit path: state row revision, receipt and events are written in one transaction; an exception injected after the state write and before the receipt insert leaves nothing (no state, no receipt, no event, seq unchanged).
- [ ] Seq: a test type whose `apply` emits three events gets contiguous seqs, and a row it writes after the first emit stores that event's seq (declare-seq case); the receipt's seq range and event ids match the emitted events.
- [ ] Same key and digest replays: `replayed: true`, recorded status, nothing written, fresh projection; with access lost, `403` with no body. Replaying a command that answered 202 returns 202 `pending`.
- [ ] Replay of a rejected receipt (for example `stale_revision`) answers 409 with exactly `{ok: false, code, operation_id, replayed: true}` and no `conflicts` or `current`.
- [ ] Same key, different digest: `409 operation_id_reused`; abandoned key (receipt inserted directly with NULL digest): `409 abandoned` for any digest, including one equal to a previous attempt's.
- [ ] Each code-table row (C:352-366) has a test asserting HTTP status, body code and whether a receipt was written.
- [ ] Exceptions: a fake `core.commands` exception with `code = "in_encounter"`, `http_status = 422` answers 422 with a receipt; one with `code = "rule_violation"` likewise; `StateRowMissing` answers `503 authority_unavailable` with nothing written; `ProfileMismatch` and `LockOrderError` answer `500 server_error` with nothing written.
- [ ] Projection: a committed command that writes a record hidden from the caller omits its revision key from `revisions` and its event from `events[]`.
- [ ] Refusal receipt commits before the answer; a fault between rollback and refusal insert that flips authority gives 503 and no receipt (race a, C:380); an old-epoch retry of an operation whose receipt exists gives `409 resync_required`, not a replay (race b, C:381-382).
- [ ] An AST test asserts no module under `core/commands/` imports `core.persistence.character_files`, a rules-content loader, `app`, `core.campaigns` or `core.obsidian_sync` (C:455-459; no process lock under the stream row).
- [ ] Crash case: the receipt insert fails once, the client retries the same key, the state is applied exactly once (`obsidian-reuse-review.md:165-175`).
- [ ] `COMMIT` raising answers `503 temporarily_unavailable`; a retry with the same key answers from the receipt if the first commit landed.
- [ ] Event rows: unlisted projections `none`, `gm` `full`, unknown class rejected, notes changes reduced, override recorded in `explain[]`, `session_id` set when a session is active and NULL otherwise.
- [ ] The post-commit hook runs once after commit and not on refusal or replay; nothing in the transaction calls `sse_broadcast`.
- [ ] PostgreSQL (CI): two commands on one campaign serialize on the stream lock and get contiguous seqs; with `STREAM_LOCK_TIMEOUT_MS` lowered by monkeypatch (the real constant, not a hand-set `SET`), a contended stream lock maps to `503 temporarily_unavailable` with nothing written; both transition races repeated with real contending sessions.
- [ ] `python -m pytest -q tests/persistence/test_command_executor.py tests/persistence/test_command_envelope.py tests/persistence/test_command_checks.py tests/persistence/test_state_repositories.py tests/persistence/test_command_stream_schema.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_command_executor_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: real authorizer, `override_allowed`, grant and token records (PE-14c); stream-locked authorization writes (PE-14d); session commands and history backfill (PE-14e); status read, abandon and their routes (PE-14f); the HTTP command route and the first real command types (PE-15 onwards); hub wake and SSE (PE-31b); projections (PE-31, adapters); event and watermark reads (PE-14a's `stream_watermark`/`events_after`).
- No route, no `app.py`, no `core/route_policy.py`, no migration, no change to PE-13c's module.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
