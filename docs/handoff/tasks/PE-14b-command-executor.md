# PE-14b: Command executor: fence, receipts, replay, refusal transaction and event writer

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready (PE-13b and PE-14a merged); brief accepted (Codex brief review 2, 2026-11-14); PE-14a implementation review 1 requirements carried in (Decisions 15-17)
- **Branch:** `claude/pe-14b-command-executor` from current main after PE-13b and PE-14a merge. Record the exact starting SHA.
- **Depends on:** [PE-13b](PE-13b-check-order-conflicts.md) (check runner for checks 4-9, `CheckContext` with every field this task uses, `CommandRefusal`, `Authorizer` protocol, conflict body), [PE-14a](PE-14a-streams-receipts-events-schema.md) (tables, `check_envelope`, `CheckedEnvelope`, `EnvelopeRefusal`, digest, registry, `streams.lock_stream`/`allocate`), and through them [PE-13a](PE-13a-state-repositories.md) (`CommandLocks(session, campaign_id, *, lock_stream)` with `stream_row`, `records` and `register_serialized`, repositories, `StateRowMissing`, `ProfileMismatch`, `LockOrderError`, `PinUnavailable`, `UnknownSchemaVersion`). PE-13c is not needed: its exceptions are answered by attribute, never imported (Decision 4).
- **Followed by:** PE-14c (real authorizer, `override_allowed`, grant and token records), then PE-14d; PE-14e and PE-14f both follow PE-14d. PE-14e calls `ctx.bind_started_session` (Decision 13); PE-14f owns the event-ID filter for its status read (Decision 9).
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: content mismatch only for rule-deriving commands (C:41-42); check order, fence and receipt (C:97-105); no revision compared before visibility passes (C:111-112); override (C:137-140); schema versions (C:153-154); lock order (C:156-164); principal from the authenticated request only, accepted kinds (C:233-235); receipt and retry rule (C:242-252); epoch (C:346-350); code table (C:352-366); refusal receipts (C:368-378); both transition races (C:379-382); success body (C:388-392); event row, visibility and notes rule (C:394-419); response fields pass through the caller's projection (C:420-421); transaction steps, authorization inputs re-read in the same transaction (C:425-443, C:429-436); after commit / never inside (C:448-459); session start and end take the stream lock (C:466); mirroring keys on session identity (C:606-609); PE-14 row (C:1090).
  - PE-31 expects `services/session_events.py` and PE-14 repository APIs (`docs/handoff/tasks/PE-31-snapshot-replay.md:45`; also `docs/player-release/implementation-plan.md:106`). That file does not exist.
  - Legacy crash case to guard: receipt save fails, retry applies HP twice (`docs/player-release/obsidian-reuse-review.md:165-175`); response-loss tests assigned "under PE-14/70" (:194).
  - Error classes: `core/persistence/runtime.py` `store_call` :80-93, `StoreUnavailable` :35.
  - Transactions: `Database.session()` and `Database.transaction()` (`core/persistence/database.py:110-128`); `transaction()` yields one session per `begin()`, so a callback that calls `runtime.database()` would open a second transaction.
  - Principal: the frozen `Principal` (`core/request_context.py:58-126`, kinds at :25, `integration_token` factory at :149). The Obsidian bearer check sets only `g.obsidian_campaign_id` and `g.obsidian_token` (`services/obsidian_sync.py:537-538`); nothing builds a `Principal` from it today.
  - Process locks that must never be taken while the stream row is held: `_LIVE_CAMPAIGN_DISPATCH_LOCK` (`app.py:535`), `ENCOUNTER_LOCK` (`app.py:2312`), `_CAMPAIGN_STORE_LOCK` (`core/campaigns.py:42`), `campaign_lock` (`core/obsidian_sync.py:52`). `sse_broadcast` is `app.py:2833`; the SSE hub spans `app.py:2638-2958`.
  - Active session row: `CampaignSession` (`core/persistence/models.py:778-814`, `status in ('active','ended')`, one active per campaign). The legacy event path keeps tagging the ending session id after an end (`services/obsidian_sync.py:746-753`).
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 630 product lines and 1,000 test lines. Stop and report past about 730 product lines (raised by 30 for Decisions 15-17: client metadata validation in `envelope.py`, the digest re-check and the emit-before-receipt assertion). (Planning estimate; it grew from 550/750 in brief revision 1 for the campaign-bound locks and same-session context, the authenticated principal, the session snapshot and bind, and the pin read.)

## Goal

One Python entry point, `execute(envelope, principal, *, route_campaign_id, content_descriptor, project)`, that runs a registered command type through section 4's single transaction: check 1 before the transaction, then the stream lock, fence, receipt lookup, the authorizer re-read, the session snapshot, the type's lock plan, PE-13b's checks 4-9, `apply` and the receipt, all atomic, answering exactly per the code table, including refusal receipts written in a second transaction. `principal` is a verified `core.request_context.Principal` supplied by the caller and never derived from the body. Only test-only command types use it; no route calls it.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Who runs which check.** Check 1 is PE-14a's `checked = check_envelope(envelope, principal, route_campaign_id=route_campaign_id)`, called before any transaction opens. Its `EnvelopeRefusal` (attributes `code`, `http_status`) answers `400 bad_envelope` or `403 not_permitted` with nothing written. A non-`Principal` argument raises `TypeError`, which Decision 4 answers as `500 server_error`, nothing written. Everything after check 1 uses only `checked`: the receipt key and digest, the event principal fields, `ctx.principal = checked.principal` and `ctx.envelope = checked.envelope`. Checks 2 (fence) and 3 (receipt lookup) are this executor's, under the stream lock. PE-13b's `run_checks` runs checks 4-9 only: the executor records `envelope_checked`, `fence_passed` and `receipt_absent` as each passes and constructs the ctx with them, and `run_checks` raises `CheckOrderError` if any is unset. An unfilled check-9 adapter slot fails at runner construction.
2. **Fence mapping.** Authority `migrating` or `exporting` gives `503 migration_in_progress`; `file` or no authority row gives `503 authority_unavailable`; `db` with `epoch != stream_epoch` gives `409 resync_required` (C:101-104). The fence reads `campaign_state_authority` while holding the stream row, so an authority flip (which also takes the stream lock, PE-24) cannot interleave.
3. **Receipt lookup order** (C:357-360, C:373-375). The key is `(campaign_id, checked.principal_kind, checked.principal_id, operation_id)`. A receipt with `code = abandoned` answers `409 abandoned` whatever the digest (its digest is NULL, PE-14a); otherwise a receipt whose digest equals `checked.digest` replays; otherwise `409 operation_id_reused`. The refusal transaction's re-check uses the same order.
4. **Exceptions to codes.**
   - Any exception raised from `core.commands` that has attributes `code` and `http_status` is answered from those attributes; whether a receipt is written follows the code table (C:356-366) for that code. This covers PE-14a's `EnvelopeRefusal`, PE-13b's `CommandRefusal` (which accepts every code in C:352-366) and PE-13c's `ReservationBlocked` (`rule_violation`, 422) and `InEncounter` (`in_encounter`, 422). The executor imports no PE-13c symbol.
   - PE-13a `StateRowMissing`, `PinUnavailable` and `UnknownSchemaVersion` give `503 authority_unavailable`, nothing written (C:153-154, C:784-785).
   - PE-13a `ProfileMismatch` and `LockOrderError`, and this task's `SessionBindError` (Decision 13), give `500 server_error`: they are programming errors (check 8 runs first; lock order is the type's responsibility; a bad bind is the type's bug). `AuthorityMismatch` after the fence has passed under the stream lock is also a programming error and gives 500 (judgment; record in the report).
   - A lock timeout or serialization failure (`OperationalError` with SQLSTATE `55P03`, `40001`, `40P01`) gives `503 temporarily_unavailable`; an exception raised by `COMMIT` itself gives `503 temporarily_unavailable` with outcome unknown; any other exception before commit, including a `TypeError` from check 1, gives `500 server_error` (C:364-366).
5. **Lock timeout.** On PostgreSQL the executor issues `SET LOCAL lock_timeout = '<STREAM_LOCK_TIMEOUT_MS>ms'` before `locks.stream()`, in both the command transaction and the refusal transaction. `STREAM_LOCK_TIMEOUT_MS` is a module constant in `core/commands/executor.py`, default 5000; PE-14d's writers and PE-14f's status read and abandon import the same constant. On SQLite nothing is issued.
6. **Locks, lock plan and the same-session rule.** The executor builds `locks = CommandLocks(session, campaign_id, lock_stream=streams.lock_stream)` on the transaction's session and passes `lock_stream` explicitly (PE-13a has no default). After the ctx is built, the executor calls the command type's `lock_plan(ctx)`, which acquires lock-order steps 2-5 through `ctx.locks` for the subject, every `expected` reference and the payload ids. Lock methods read through PE-13a's locked repository path and filter on the bound `campaign_id`, so another campaign's id is recorded as `None` and answers 404 at check 5; locking a hidden or missing id reveals nothing. `lock_plan` registers stream-serialized subjects (campaign, session, grant) with `ctx.locks.register_serialized(kind, id, record)`, without a row lock. `ctx.records` is `ctx.locks.records`. Checks 4-9 read only rows already locked or registered; `apply(ctx)` may lock only rows it creates. `lock_plan`, `apply`, the check-9 adapter and the authorizer use only `ctx.session` and `ctx.locks`: they never call `runtime.database()`, never open a session and never read `flask.g`. Every writer takes the stream row before the campaign row and before any state or authorization row; no process lock is taken while the stream row is held.
7. **Seq at emit time.** `apply` emits events through `ctx.emit(EventDraft) -> (event_id, seq)`, which calls PE-14a's `allocate` on `ctx.locks.stream_row` (the row held by this transaction's locks) at emit time, so a row `apply` writes can carry its own declare seq. The receipt's seq range and event ids come from the emitted events. Seqs are contiguous because nothing else allocates while the lock is held.
8. **Step 4 is a slot here.** The SQL re-read of authorization inputs (C:429-436) needs grants and SQL token records, which are PE-14c's. Immediately after the receipt lookup and before the ctx is built, the executor calls `authorizer = factory(session, checked.principal, campaign_id)` with the transaction's session. A replay answers from this authorizer (Decision 9), so it exists before any ctx does. The authorizer takes no row locks, so this adds no lock-order step. Tests use a stub implementing PE-13b's protocol (including `override_allowed(type)`) that reads `campaign_memberships` and `character_assignments` from the same session, never `flask.g`. PE-14c replaces the factory with `authorizer.load`.
9. **Projection is a callback** (`project`, as PE-13b). It covers `result`, `events[]`, `revisions`, `conflicts` and `current` (C:420-421): a revision key or event for a record the caller cannot see is omitted. The executor ships no projector and no default; a missing `project` is a `TypeError`. The event-ID filter that other entry points use is `core/commands/event_visibility.py` (PE-14f), built on `held_projections(authorizer, *, subjects, encounter_id)`, a module-level function that PE-14f adds to `core/commands/authorizer.py` over PE-14c's loaded authorizer (it is not a method on PE-14c's class); PE-14f's status read and abandon do not take a `project` callback. Concrete `revisions`, `result`, `conflicts` and `current` projections remain PE-31 and the adapters (PE-15 onward). A replay of a committed receipt returns `replayed: true`, the recorded status (HTTP 202 when the receipt's `applied_status` is `pending`, else 200) and a fresh projection. "Access lost" (C:355) means the authorizer, re-read in this transaction (Decision 8), now refuses the caller's campaign role for the type (check 4's predicate); the replay then answers `403` with no body (judgment on what "access lost" tests; record in the report). A replay of a rejected receipt answers that code's HTTP status with `{"ok": false, "code", "operation_id", "replayed": true}` and no `conflicts` or `current` (the receipt holds no body, C:244); clients refetch.
10. **Applied status.** `apply` reports whether an action is left open; the executor then answers HTTP 202 with `status: "pending"` and writes the receipt with `applied_status = pending`, else 200 `committed` with `applied_status = committed` (C:354; PE-14a column).
11. **Post-commit hook.** `services/session_events.py` (new) holds `after_commit(campaign_id, last_seq)` with no subscribers by default. It never calls `sse_broadcast` (`app.py:2833`, which takes `_LIVE_CAMPAIGN_DISPATCH_LOCK`) and is invoked only after `COMMIT` returns. PE-31b attaches the hub (C:449-451).
12. **Event visibility input.** Command types supply a visibility map per event; the writer fills unlisted projections with `none`, forces `gm` to `full` (C:411-412), rejects an unknown class, and rewrites any `changes[]` entry with path `notes` to `{"path": "notes", "changed": true}` (C:418-419). Override is stored in the event's `explain[]` as `{"override": {"reason": ...}}` (C:138); the event shape has no dedicated field, so record this choice in the report. Whether the caller may send `override` at all is PE-13b's check 4 (`override_allowed`), not the writer's.
13. **Session context.** After the authorizer is built and before the ctx is built, the executor reads the campaign's `status = 'active'` `campaign_sessions` row once, in the same transaction and without a row lock (every session-row writer holds the stream row, C:466), and stores its id as `ctx.session_id` (`None` if there is none). `ctx.emit` writes `ctx.session_id` into every event and never re-reads the row, so a row that `apply` sets to `ended` (with autoflush) does not change the id. `EventDraft` has no `session_id` field; passing one is a `TypeError`, so nothing client-supplied reaches the column.
   - The one lifecycle exception is `bind_started_session(ctx, session_id: str) -> None`, implemented in `core/commands/events.py` and bound by the executor to PE-13b's `ctx.bind_started_session` slot. It succeeds only when `ctx.session_id` was `None` at the snapshot, it has not already been called in this command, and, after a flush, a row with this `campaign_id` and this `session_id` exists with `status = 'active'` (it must have been inserted in this transaction, since there was no active row at the snapshot under the held stream lock). It then sets `ctx.session_id`. Any failure raises `SessionBindError` (500, nothing written).
   - `session.start` (PE-14e) inserts its row, binds, then emits `session.started`. `session.end` needs no bind: the snapshot is the ending session, and its `action.cancelled` and `session.ended` events carry it.
   - Events committed by a later command after `session.end` carry `NULL`, while the legacy path kept tagging the ended id until the next start (`services/obsidian_sync.py:746-753`). This is a recorded semantic change; PE-60's mirror (C:606-609) must expect it.
14. **Campaign pin.** After the session snapshot and before the ctx is built, the executor reads `pin = CampaignPinRepository(session).pin(campaign_id)` (PE-13a) once, in the same transaction and without a row lock, and stores it as `ctx.pin`. It is read for every command type, because every command must match the pin and check 8's `rules_mismatch` applies to all types (C:31-33, C:122-123). A missing or malformed pin raises PE-13a's `PinUnavailable`, which Decision 4 answers `503 authority_unavailable` with nothing written; `ctx.pin` is never `None`. A replay answers before this read (step 8), so it never needs a pin. No campaign has a pin today (PE-13a Decision 5): a db-authority campaign gets one from the PE-19/24 import, and every test that runs a command through `execute`, here and in PE-14d and PE-14e, writes `campaigns.settings["rules"]` in its fixture.

15. **Client metadata (PE-14a implementation review 1, item 1).** Check 1 (`parse_envelope` in PE-14a's `core/commands/envelope.py`, edited here) validates `client` before any transaction opens: it is absent, `null` or a mapping, else `400 bad_envelope`. Inside a mapping, `origin` is absent, `null`, or a string matching `[a-z0-9][a-z0-9_.-]{0,31}` (1-32 characters, so it always fits `game_events.origin`, `String(32)`), else `400 bad_envelope`. The other client keys (`app_version`, `sent_at`) are not interpreted and are not part of the digest (C:236-240). `envelope.client_origin(envelope) -> str` returns the validated origin, or `"unknown"` when `client` or `origin` is absent or `null`; `ctx.emit` writes only that value, never `client.origin` read directly. Origin is self-reported metadata, never an authority input.
16. **The hashed envelope is never changed (item 2).** `checked.envelope`'s outer mapping is read-only, but nested mappings and lists are ordinary, mutable detached copies (PE-13b's runner relies on `isinstance(..., list)`). The boundary is: `lock_plan`, the checks, the adapter, `apply` and the projector must not modify `ctx.envelope` or anything inside it; a command that needs a working value copies it first. The executor enforces this: immediately before the receipt insert, on both the commit path and before a refusal receipt is written, it recomputes `command_digest(checked.envelope)`; a value different from `checked.digest` raises `EnvelopeMutated`, answered `500 server_error` with nothing written (the transaction rolls back and no refusal receipt is written).
17. **An applied command always emits (item 2's companion, from the PE-14a follow-up).** If `apply` returns without emitting at least one event, the executor raises `NoEventsEmitted` before the receipt insert: `500 server_error`, the transaction rolls back, nothing written. So every `committed` receipt has a seq range and event ids, and the post-commit hook's `last_seq` and the response `watermark` are always the receipt's `seq_last`. The schema still allows an event-less committed receipt; this is an executor invariant with its own test, not a CHECK.

## Approach

Allowed files:
- new `core/commands/executor.py`, `core/commands/events.py`
- new `services/session_events.py`
- new `tests/persistence/test_command_executor.py`, `tests/persistence/test_command_executor_postgresql.py`
- edit `core/commands/envelope.py` and `tests/persistence/test_command_envelope.py` for Decision 15 only (client validation and `client_origin`)

Do not edit `core/commands/checks.py`: PE-13b's `CheckContext` already declares every field this task uses (`session`, `locks`, `principal`, `campaign_id`, `envelope`, `authorizer`, `project`, `content_descriptor`, `pin`, `emit`, `session_id`, the three flags and the `bind_started_session` slot). If a field is missing when this task starts, stop and report rather than adding it. Do not edit `core/commands/__init__.py` (created by whichever of PE-13b, PE-13c and PE-14a lands first, containing only `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` and a newline; callers import submodules) or `core/persistence/__init__.py`.

1. `execute(envelope, principal, *, route_campaign_id, content_descriptor, project)`; every keyword is required (a missing one is a `TypeError`, never a default). The docstring states that `principal` is a verified `core.request_context.Principal`, never derived from the body.
   1. `checked = check_envelope(envelope, principal, route_campaign_id=route_campaign_id)` (PE-14a), no transaction; record `envelope_checked`. `check_envelope` first runs PE-14a's `load_command_modules()`, so every real type module (and its import-time registrations) is loaded before check 1; the executor imports no type module itself.
   2. `with database.transaction() as session:` (one transaction).
   3. `locks = CommandLocks(session, campaign_id, lock_stream=streams.lock_stream)`.
   4. On PostgreSQL, `SET LOCAL lock_timeout` (Decision 5).
   5. `locks.stream()`.
   6. Fence (Decision 2); record `fence_passed`.
   7. Receipt lookup in the Decision 3 order. On a miss record `receipt_absent`.
   8. `authorizer = factory(session, checked.principal, campaign_id)` (Decision 8). On a replay, check access and answer per Decision 9 with nothing written.
   9. Read the active session id (Decision 13).
   10. Read the campaign pin with `CampaignPinRepository(session).pin(campaign_id)`; `PinUnavailable` answers 503 with nothing written (Decision 14).
   11. Build `ctx` with `session`, `locks`, `principal = checked.principal`, `campaign_id`, `envelope = checked.envelope`, `authorizer`, `project`, `content_descriptor`, `pin` (step 10), `emit`, `session_id`, the three flags, and `bind_started_session` bound to this ctx.
   12. `lock_plan(ctx)` (Decision 6).
   13. PE-13b `run_checks(ctx, spec, adapter)` for checks 4-9, with `adapter` taken from the command type's `CommandType.rules_adapter` (PE-14a); the runner is built from it. `content_descriptor` and `pin` for check 8 are already in `ctx`.
   14. `apply(ctx)`: writes through PE-13a repositories built from `ctx.session`, emits events via `ctx.emit` (Decision 7).
   15. Assert at least one event was emitted (Decision 17) and that `command_digest(checked.envelope) == checked.digest` (Decision 16). Insert the receipt (`status = committed`, `applied_status`, `code` NULL, `checked.principal_kind`, `checked.principal_id`, `checked.digest`, revisions written, event ids, seq range, `stream_epoch`) and the event rows; commit (C:425-443).
   16. After commit: `after_commit(campaign_id, last_seq)`. Body per C:388-392 with `watermark` = last emitted seq, everything projected through `project`.
2. Refusal path (C:368-378): roll back; a second short transaction builds its own `CommandLocks` on its own session (same `lock_stream`), sets the lock timeout, calls `locks.stream()`, re-runs the fence (changed: insert nothing, answer 503 or 409), re-checks the receipt key in the Decision 3 order (answer from it), otherwise inserts the `rejected` receipt (`applied_status` NULL, the refusal `code`, `checked.principal_kind`, `checked.principal_id`, `checked.digest`, no seq range). Send the refusal only after that commits. Codes whose table row says "nothing" (`bad_envelope`, `operation_id_reused`, `not_permitted`, `resync_required`, every 503) skip the second transaction and write nothing (C:356-364). A `stale_revision` refusal body is PE-13b's `conflict_body`, projected.
3. `events.py`: `EventDraft` (no `session_id` field), `SessionBindError`, `bind_started_session` (Decision 13), and `ctx.emit` turning a draft into a row with `event_id = new_id()`, the seq allocated from `ctx.locks.stream_row`, the current `stream_epoch`, `occurred_at` UTC, origin from `client_origin(checked.envelope)` (Decision 15), principal kind and id from `checked`, the visibility rules of Decision 12, and `session_id = ctx.session_id`.
4. Never inside the transaction (C:455-459): loading rules content (the caller passes `content_descriptor` in), file writes, waiting, taking a process lock, opening another session. The AST and `sys.modules` acceptance tests below pin the import side of this; the same-session test pins the session side.

## Acceptance

- [ ] Signature: calling `execute` without any one of `route_campaign_id`, `content_descriptor` or `project` raises `TypeError`.
- [ ] Order: check 1 runs before a transaction opens (a bad envelope opens no session); `run_checks` called without the executor's three flags raises `CheckOrderError`; a spy shows the authorizer factory runs after the receipt lookup and before `lock_plan`, `lock_plan` runs before check 4, and `apply` runs after check 9.
- [ ] Check 1 principal: a LEGACY_GM principal, and an INTEGRATION_TOKEN principal on a test type whose `allowed_principals` is {USER}, each answer `403 not_permitted`, open no session and write no receipt; a non-`Principal` argument answers `500 server_error` with nothing written (`test_check1_principal_refusals_write_nothing`).
- [ ] Receipts and events are keyed by the authenticated principal: the same `operation_id` from user U and token T gives two independent receipts, and each event's `principal_kind`/`principal_id` equal `checked`'s (`test_receipt_and_events_keyed_by_authenticated_principal`).
- [ ] Same session (`test_callbacks_share_executor_session_and_locks`): the test type's `lock_plan` and `apply` record `ctx.session`, `ctx.locks` and `ctx.locks.campaign_id`; they are the executor's session and locks and the envelope's campaign. `Database.transaction` and `Database.session` are wrapped on the instance `runtime.database()` returns (not `runtime.database` itself). A commit opens exactly 1 transaction, a `stale_revision` refusal 2, a check-4 `not_permitted` refusal 1 and a replay 1; any further `transaction()` or `session()` call while the executor's transaction is open raises and fails the test.
- [ ] Cross-campaign: an envelope for campaign A whose payload names campaign B's character answers 404 at check 5 with nothing written (`test_cross_campaign_payload_id_is_404`).
- [ ] Commit path: state row revision, receipt and events are written in one transaction; an exception injected after the state write and before the receipt insert leaves nothing (no state, no receipt, no event, seq unchanged).
- [ ] Seq: a test type whose `apply` emits three events gets contiguous seqs from `ctx.locks.stream_row`, and a row it writes after the first emit stores that event's seq (declare-seq case); the receipt's seq range and event ids match the emitted events.
- [ ] Same key and digest replays: `replayed: true`, recorded status, nothing written, fresh projection; with access lost, `403` with no body. Replaying a command that answered 202 returns 202 `pending`.
- [ ] Replay of a rejected receipt (for example `stale_revision`) answers 409 with exactly `{ok: false, code, operation_id, replayed: true}` and no `conflicts` or `current`.
- [ ] Same key, different digest: `409 operation_id_reused`; abandoned key (receipt inserted directly with NULL digest): `409 abandoned` for any digest, including one equal to a previous attempt's.
- [ ] Each code-table row (C:352-366) has a test asserting HTTP status, body code and whether a receipt was written.
- [ ] Exceptions: a fake `core.commands` exception with `code = "in_encounter"`, `http_status = 422` answers 422 with a receipt; one with `code = "rule_violation"` likewise; `StateRowMissing` answers `503 authority_unavailable` with nothing written; `ProfileMismatch`, `LockOrderError` and `SessionBindError` answer `500 server_error` with nothing written.
- [ ] Pin (`test_missing_pin_is_503_authority_unavailable`): with no `campaigns.settings["rules"]`, and again with a malformed one, a fresh command answers `503 authority_unavailable` with no receipt, no event and `lock_plan` never called; the same key replays from an existing receipt without a pin. With a pin set, `ctx.pin` equals `CampaignPinRepository(session).pin(campaign_id)`.
- [ ] Projection: a committed command that writes a record hidden from the caller omits its revision key from `revisions` and its event from `events[]`.
- [ ] Refusal receipt commits before the answer; a fault between rollback and refusal insert that flips authority gives 503 and no receipt (race a, C:380); an old-epoch retry of an operation whose receipt exists gives `409 resync_required`, not a replay (race b, C:381-382).
- [ ] Import guard, AST: no module under `core/commands/` imports `core.persistence.character_files`, a rules-content loader, `app`, `core.campaigns`, `core.obsidian_sync` or `core.obsidian_session_history` (C:455-459; no process lock under the stream row).
- [ ] Import guard, runtime (`test_commands_package_imports_no_process_lock_module`): a subprocess imports every `core.commands.*` module, then asserts that none of `core.obsidian_sync`, `core.obsidian_session_history`, `core.campaigns` or `app` is in `sys.modules`. This catches indirect imports.
- [ ] Crash case: the receipt insert fails once, the client retries the same key, the state is applied exactly once (`obsidian-reuse-review.md:165-175`).
- [ ] `COMMIT` raising answers `503 temporarily_unavailable`; a retry with the same key answers from the receipt if the first commit landed.
- [ ] Event rows: unlisted projections `none`, `gm` `full`, unknown class rejected, notes changes reduced, override recorded in `explain[]`.
- [ ] Session context: `test_event_session_id_survives_row_ending_in_apply` (a test type sets the active row to `ended` with autoflush on, then emits; the event carries the original id); `test_event_session_id_null_without_active_session`; `test_bind_started_session_tags_later_events` (emit before the bind gives NULL, after it the new id); `test_bind_started_session_refuses`, parametrized over an id with no row, a row in another campaign, an ended row, a session already active at the snapshot and a second bind, each answering 500 with nothing written; `test_event_draft_rejects_session_id`.
- [ ] The post-commit hook runs once after commit and not on refusal or replay; nothing in the transaction calls `sse_broadcast`.
- [ ] Client metadata (Decision 15, in `test_command_envelope.py`): `client` as a list, integer or string, and `origin` as an integer, an empty string, a 33-character string or one with an upper-case letter or a space, each answer `400 bad_envelope` before any session opens; absent `client`, `client: null` and absent `origin` give events with `origin = "unknown"`; `origin: "obsidian"` is stored as is. Changing only `client` keeps the digest.
- [ ] Hashed envelope (Decision 16, `test_envelope_mutation_is_500_nothing_written`): a test type whose `apply` appends to a nested payload list, and one whose `lock_plan` changes a nested subject value, each answer `500 server_error` with no receipt, no event and seq unchanged; for a normal command, the digest recomputed from `ctx.envelope` after `apply` equals `checked.digest`.
- [ ] Emit before receipt (Decision 17, `test_apply_without_event_is_500_nothing_written`): a test type whose `apply` writes a state row and emits nothing answers `500 server_error`; the state write, receipt and seq are all absent. Every committed receipt in the suite has a non-NULL seq range.
- [ ] PostgreSQL (CI): two commands on one campaign serialize on the stream lock and get contiguous seqs; with `STREAM_LOCK_TIMEOUT_MS` lowered by monkeypatch (the real constant, not a hand-set `SET`), a contended stream lock maps to `503 temporarily_unavailable` with nothing written; both transition races repeated with real contending sessions.
- [ ] `python -m pytest -q tests/persistence/test_command_executor.py tests/persistence/test_command_envelope.py tests/persistence/test_command_checks.py tests/persistence/test_state_repositories.py tests/persistence/test_command_stream_schema.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_command_executor_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

(Decision 15 is the one change to PE-14a's modules; nothing else in `envelope.py`, `registry.py` or `digest.py` changes.)

- Owned elsewhere: real authorizer, `override_allowed`, grant and token records (PE-14c); stream-locked authorization writes (PE-14d); session commands and history backfill, including where `session.end` cancels and ends (PE-14e); status read, abandon, their routes, the event-ID filter `core/commands/event_visibility.py` and `held_projections` (PE-14f); the HTTP command route and the first real command types (PE-15 onwards); building the token `Principal` (`Principal.integration_token(token_record['id'], campaign_id=...)`) from the verified bearer record is the route's job (PE-15), and `execute` never builds a principal; hub wake and SSE (PE-31b); record and result projections (PE-31, adapters); event and watermark reads (PE-14a's `stream_watermark`/`events_after`).
- No route, no `app.py`, no `core/route_policy.py`, no migration, no change to PE-13c's module, no change to `core/commands/checks.py`.

### Carried from PE-14a implementation review 1 (Claude, 2026-11-16)

Codex accepted PE-14a at `83948e45` with three non-blocking requirements for this brief. Each is now a Decision with an acceptance test:

1. Client metadata validated and normalized at check 1 before any event reads it, with an explicit `"unknown"` fallback (Decision 15; `envelope.py` and its tests added to Allowed files).
2. The complete checked envelope is unchanged after hashing; the boundary is documented and the executor re-checks the digest before every receipt insert (Decision 16).
3. An applied command must emit an event before its receipt commits (Decision 17).

PE-14a's header and the task map no longer claim PE-14a was independent of PE-13b: as built it imports `expected_kinds`. Budget raised by 30 product and 50 test lines for these.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review

### Brief review 1 — changes requested (Codex, 2026-11-14)

Reviewed PR #259 at `4b21f404` against `791936f1`.

1. **R1-1 (P2, shared): wire the campaign-bound locks and same-session context.**
   Resolve PE-13a/13b's missing constructor/context inputs before specifying
   `lock_plan(ctx)` and `apply(ctx)` as complete interfaces. Permit the agreed
   context fields here, and test that callbacks use this transaction rather
   than creating another session.
2. **R1-2 (P2, shared): wire authenticated principal validation before the
   transaction.** The existing check-1 call has no principal argument although
   PE-14a requires principal checks there. Follow the explicit owner/signature
   correction in PE-14a's Review.
3. **R1-5 (P2): reconcile the package-wide import ban with PE-14e's reader.**
   Acceptance bans every `core/commands` module from importing
   `core.obsidian_sync`, while PE-14e requires its new
   `core/commands/session_history.py` to use that module's shared
   `campaign_lock` and file paths outside the transaction. Both tests cannot
   pass as prescribed. Prefer placing the file-reading half outside the
   transaction-only package, or specify a narrow structural exception with a
   test proving file I/O/process locking precedes the transaction. An indirect
   import or a duplicate lock is not a correction.
4. **R1-6 (P2): specify the lifecycle event's session context.** Approach 3
   obtains an event's `session_id` from the currently active session, but
   PE-14e sets the row to `ended` before emitting `session.ended`. The normal
   autoflush before that lookup leaves no active row, so the ending event gets
   NULL. The current legacy path deliberately retains the ending ID
   (`services/obsidian_sync.py:746-753`), and C:606-609 uses session identity
   for mirroring. Define a trusted lifecycle-aware session context or explicit
   validated event input, covering both start and end; do not accept an
   arbitrary client session ID. Tests must assert that start, cancellation and
   end events belong to the correct session even after the row changes state.
5. **R1-10 (P2, consumer clarification): name the status-route projector.**
   This task accepts an injected `project` callback and explicitly defers
   concrete projections. PE-14f incorrectly treats it as an available provider.
   Clarify that boundary and reconcile PE-14f's concrete implementation or
   dependency as described in its Review. No permissive default is acceptable.

The fixed check order, fence-before-receipt rule, refusal transaction,
emit/seq allocation, exception mapping and post-commit notification boundary
otherwise agree. The estimate is large but bounded; re-estimate any additional
work required by these interface corrections. Docs only, no product tests;
review edits are uncommitted.

### Brief revision 1 (Claude, 2026-11-14)

1. **R1-1 (campaign-bound locks, same-session context).** `execute` now builds `CommandLocks(session, campaign_id, lock_stream=streams.lock_stream)` on the transaction's session, with `lock_stream` passed explicitly (PE-13a has no default), and runs the fixed order transaction, locks, lock timeout, `stream()`, fence, receipt lookup, authorizer, session snapshot, pin read, ctx, `lock_plan`, `run_checks`, `apply` (Approach 1). The authorizer is built before the ctx because the ctx requires it and a replay needs it (Decision 8). `lock_plan` and `apply` use only `ctx.session` and `ctx.locks`; stream-serialized subjects go through `ctx.locks.register_serialized`; `ctx.records` is `ctx.locks.records`; another campaign's id is `None` and answers 404 (Decision 6). `ctx.emit` allocates from `ctx.locks.stream_row` (Decision 7). The refusal transaction builds its own locks (Approach 2). `core/commands/checks.py` leaves Allowed files, since PE-13b now declares every ctx field. New acceptance: `test_callbacks_share_executor_session_and_locks` with the counts commit 1, `stale_revision` 2, check-4 `not_permitted` 1, replay 1, and `test_cross_campaign_payload_id_is_404`.
2. **R1-2 (authenticated principal).** Check 1 is `check_envelope(envelope, principal, route_campaign_id=...)`, returning `CheckedEnvelope`; `EnvelopeRefusal` maps by attribute and a non-`Principal` is a 500 (Decisions 1 and 4). The receipt key, digest, event principal fields and `ctx.principal`/`ctx.envelope` come only from `checked` (Decisions 1 and 3, Approach 1-3). `execute`'s signature is unchanged; its docstring says the principal is verified and never from the body. Building the token `Principal` from the bearer record is PE-15's (Out of scope). New acceptance: the check-1 principal refusals and the principal-keyed receipts and events.
3. **R1-5 (import ban vs. PE-14e's reader).** The reader moves to `core/obsidian_session_history.py`, outside `core/commands` (PE-14e). The AST guard adds `core.obsidian_session_history` to its ban list (`core.persistence.character_files`, a rules-content loader, `app`, `core.campaigns`, `core.obsidian_sync`, `core.obsidian_session_history`), and a new subprocess `sys.modules` test catches indirect imports of `core.obsidian_sync`, `core.obsidian_session_history`, `core.campaigns` and `app` (Acceptance, two import-guard bullets, which are the authoritative lists). No exception to the ban is introduced.
4. **R1-6 (lifecycle session context).** New Decision 13: the active session id is snapshotted once into `ctx.session_id` after the authorizer and before the ctx is built; `ctx.emit` never re-reads it; `EventDraft` has no `session_id`; `bind_started_session` in `events.py` is the only way to set it, for `session.start`, with `SessionBindError` mapped to 500 (Decision 4). `session.end` needs no bind. Post-end events carry NULL, recorded as a semantic change. The old Approach 3 clause that read the active row at emit time is gone; the "Event rows" acceptance bullet lost its `session_id` clause and a new "Session context" bullet carries the R1-6 tests.
5. **R1-10 (projector boundary).** Decision 9 now says the executor ships no projector and no default, names PE-14f's `event_visibility.py` (built on `held_projections(authorizer, *, subjects, encounter_id)`, a module-level function that PE-14f adds to `core/commands/authorizer.py` over PE-14c's loaded authorizer; it is not a method on PE-14c's class) as the event-ID filter for other entry points, and leaves record and result projections to PE-31 and the adapters. Out of scope is reworded to match.

6. **Campaign pin (found in re-check, not an R1 item).** `ctx.pin` had no step that read it. New Decision 14 and Approach step 10 read `CampaignPinRepository(session).pin(campaign_id)` once, after the session snapshot and before the ctx, for every type; `PinUnavailable` answers `503 authority_unavailable` with nothing written (Decision 4), so `ctx.pin` is never `None`. Replays answer before the read. Tests that run commands through `execute`, including PE-14d's and PE-14e's, must write a pin in their fixtures. New acceptance: `test_missing_pin_is_503_authority_unavailable`. Approach steps after 9 are renumbered.

7. **Authorizer principal (re-check).** Decision 8 and Approach step 8 now call `factory(session, checked.principal, campaign_id)`, not the raw `principal` argument, so they agree with Decision 1's "everything after check 1 uses only `checked`".

Budget: was about 550 product and 750 test lines, now about 600 and 950; the 700-line stop is unchanged. The increase is R1-1 (about +10/+50), R1-2 (about +5/+40), R1-5/R1-6 (about +30/+90) and the pin read (about +5/+20). The earlier 890 test figure was below this sum and is replaced; the stop line covers product lines only, so nothing else moves.

Judgments for Codex to challenge:
- The authorizer factory runs before `lock_plan` and serves both replay and checks 4-9; it takes no row locks, so it is not a lock-order step.
- The three check flags are recorded as each check passes and handed to the ctx constructor, since the ctx is built after the receipt lookup.
- A `TypeError` from check 1 (non-`Principal`) answers 500, nothing written, as "any other exception before commit".
- `SessionBindError` is a 500 programming error, not a refusal code.
- The pin is required for every type (a missing pin is `503 authority_unavailable`), rather than optional and skipped by check 8, because C:31-33 says every command must match it.
- The session snapshot takes no row lock; it relies on every session-row writer holding the stream row (C:466).
- Post-`session.end` events carry NULL rather than the ended id.
- If PE-13b's merged `CheckContext` lacks a field this brief needs, the implementer stops and reports instead of editing `checks.py`.

### Brief review 2 - accepted (Codex, 2026-11-14)

Reviewed PR #259 at `51ac0e51` against `791936f1`. **R1-1, R1-2, R1-5 and R1-6 are closed; the R1-10 interface now agrees with PE-14f. No new blocking findings.** Check 1, campaign-bound locks, the complete context, emit-time seq allocation and exception attributes agree across the producer/consumer briefs. The active-session snapshot and restricted start bind retain the ending session id for cancellation/end events. The history reader is outside the guarded command package. The required executor projector remains an explicit adapter responsibility; PE-14f supplies its own real event-ID filter without a missing callback.

Requiring a valid campaign pin for every new command, with missing/malformed pins giving 503 and replay preceding the pin read, is consistent with check 8 and the activation prerequisites. Later commands carrying NULL session_id after an end is a coherent, explicitly recorded change for PE-60. The estimate and 700-product-line stop are acceptable. Docs-only review; no product tests ran.

### Carried from PE-13b implementation review 1 (Codex, 2026-11-15)

Fixtures and real adapters pass `pin["content"]` (not the whole pin) as `content_descriptor`, and every `project(principal, records)` returns a mapping keyed `"<kind>:<id>"`; PE-13b drops any conflict whose key is absent. Build `CheckSpec` from PE-14a's `CommandType` in that shape (subject `(field, kind)`, payload ids field-to-kind). See PE-13b implementation review 1 (Codex, 2026-11-15) in [PE-13b](PE-13b-check-order-conflicts.md) for the full text; reconcile this brief before its build.
