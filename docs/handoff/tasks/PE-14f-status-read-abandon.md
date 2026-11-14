# PE-14f: Operation-status read and operation.abandon

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14d)
- **Branch:** `claude/pe-14f-status-read-abandon` from current main after PE-14d merges. Record the exact starting SHA.
- **Depends on:** [PE-14d](PE-14d-stream-locked-authorization-writes.md) (token revocation and replacement committed under the stream lock, which finality relies on). Uses PE-14a's receipts and `lock_stream`, PE-14b's `STREAM_LOCK_TIMEOUT_MS` and `project` callback, and PE-14c's `authorizer.load` and token records. May run in parallel with [PE-14e](PE-14e-sessions-backfill.md); the two share no files.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: pending-operation binding and quarantine triggers (C:253-260); status read, its answer table, `epoch`, finality, abandon (C:261-307); server acceptance cases (C:308-337); client cases owned elsewhere (C:339-345); step 4 re-reads authorization from SQL, never `flask.g` (C:425-436); code table (C:352-366).
  - Token boundary: `token_required` in `services/obsidian_sync.py:517-541` verifies the bearer for `X-Campaign-ID` at :519-528 and refuses a campaign that is not the website's active table with `409` at :529-536; `API_PREFIX` :18; existing token routes :785-799. `RoutePolicy.AUTHENTICATED` (`core/route_policy.py:34`) and `RoutePolicy.INTEGRATION_TOKEN` (:44), token routes declared at :443-452; `TOKEN_CAMPAIGN_MISMATCH` (`core/access.py:100-104`), `_token_access` (:192-204), token policy dispatch (:270-271). Every new endpoint goes in `core/route_policy.py` (`docs/handoff/PROTOCOL.md`, conventions).
  - Token storage depends on PE-14c Decision 1 (user decision). This task reads token revocation only from PE-14c's SQL `integration_tokens.revoked_at`, which is correct under either option, because PE-14d commits revocation to SQL under the stream lock.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 300 product lines and 450 test lines. Stop and report past about 400 product lines. (Planning estimate; split from the earlier combined PE-14e.)

## Goal

The server side of pending-operation recovery: a stream-locked operation-status read and `operation.abandon`, mounted for website users and for integration tokens, with every server case the contract lists (C:308-337).

## Decisions this brief fixes (reviewer: challenge these first)

1. **Routes.** One handler per operation, two mounts each, four endpoints in `core/route_policy.py`:
   - `GET /api/campaigns/<campaign_id>/operations/<operation_id>` and `POST .../abandon`, policy `AUTHENTICATED`; the handler conceals with 404.
   - `GET /api/integrations/obsidian/v1/operations/<operation_id>` and `POST .../abandon`, policy `INTEGRATION_TOKEN`, campaign from `X-Campaign-ID`.
   - Query `principal=<kind>:<id>&epoch=<n>` (C:262); abandon body `{principal}` (C:299). Abandon writes the target key's receipt and is idempotent, so it carries no envelope of its own.
   - Neither runs the command fence (check 2): a reader with a stale stored epoch must still get the receipt or `absent_final` (C:275, C:332-333).
2. **Token mounts use a new `token_verified` decorator.** It verifies the bearer for `X-Campaign-ID` exactly as `token_required` does (`services/obsidian_sync.py:519-528`: same 401 and 400 answers) but skips the active-table check (:529-536), because a campaign switch is a quarantine trigger (C:256) and the status read is the recovery path. Existing routes keep `token_required` unchanged. The decorator runs before any transaction, so its `campaign_lock` (inside `verify_token`) is never taken while a stream row is held.
3. **Parsing before the transaction** (static, reveals nothing): a malformed `operation_id` (C:27), a `principal` that is not `user:<id>` or `integration_token:<id>`, and a missing or non-integer `epoch` each give `400 bad_envelope` (C:278-279). Abandon takes no epoch.
4. **One short transaction holding the stream lock** (C:263-268). On PostgreSQL issue `SET LOCAL lock_timeout` from PE-14b's `STREAM_LOCK_TIMEOUT_MS` (imported, not copied), then `lock_stream`; `55P03` → `503 temporarily_unavailable`. Inside it, in this order:
   1. Authorize the reader through PE-14c's `authorizer.load(session, reader, campaign_id)`, never from `flask.g` (C:265, C:429-430). The route boundary supplies only the reader's identity (user id, or credential id and campaign). Authorized: the campaign's GM; the original principal itself while still valid (for a user: enabled and a campaign member; for a token: not revoked; judgment on "valid", record it); or a token recorded as the original token's replacement (`replaces_credential_id`, honoured only as PE-14c Decision 4 allows: same campaign, GM-minted). Anyone else gets `404 not_found`, whether or not a receipt exists (C:277, C:321).
   2. Receipt lookup by `(campaign_id, principal kind, principal id, operation_id)`. A receipt answers `200 {state, code, event_ids, operation_id}`: `state` is `committed` for `applied_status` `committed` or `pending`, `rejected` for a refusal, `abandoned` for `code = abandoned`. Event ids pass through PE-14b's `project` callback; an event for a record the reader cannot see is omitted.
   3. No receipt: read `campaign_state_authority`. No authority row → `absent_pending` (no receipt can exist yet and finality cannot be established). Otherwise `absent_final`, echoing the stored epoch, if that epoch is below the current `stream_epoch`, or the original principal is an integration token whose `revoked_at` is set; else `absent_pending` (C:275-276, C:284).
5. **Abandon** (C:299-304): same parsing, lock and authorization. A receipt exists → answer from it, as decision 4. Otherwise insert `status = rejected`, `code = abandoned`, `digest` NULL, `applied_status` NULL, no events, and answer `200 {state: abandoned}`. The receipt's `stream_epoch` is the current one, or 1 (the `stream_epoch` floor) when there is no authority row (judgment; record it). With no authority row abandon still inserts its receipt. A later arrival of the original then gets `409 abandoned` from PE-14b's lookup, whatever its digest.
6. **Finality race.** PE-14d's revocation and every `stream_epoch` change take the stream lock, so the read's authorization, receipt lookup and epoch/token read are one observation (C:264-268, C:315-319).

## Approach

Allowed files:
- new `core/commands/operations.py`; `core/commands/__init__.py` only if absent. Create it with exactly this content: `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline, and nothing else (no re-exports). If it already exists, leave it alone
- `app.py` (register the two user-facing routes only); `services/obsidian_sync.py` (add `token_verified` and register the two token routes only; `token_required` unchanged); `core/route_policy.py` (four entries)
- new `tests/persistence/test_operation_status.py`, `tests/persistence/test_operation_status_postgresql.py` (copy the `postgres_database` fixture use from `tests/persistence/test_workflow_postgresql.py:22-24`); focused additions to `tests/test_route_policy_inventory.py`, `tests/test_access_policy_unit.py` and `tests/test_obsidian_sync.py` (new cases only; existing cases pass unchanged)

1. `operations.py`: `read_status(session, *, campaign_id, reader, original, operation_id, epoch, project)` and `abandon(session, *, campaign_id, reader, original, operation_id, project)`, per decisions 3-6. Both run inside a transaction they open themselves.
2. The four routes and policy entries (decisions 1-2).

## Acceptance

- [ ] Every server status-read case in C:308-337, one test each:
  - a late arrival after `absent_pending` commits once, and the next read returns it;
  - X commits, then a revocation or an epoch change commits, then the read: X's receipt, never `absent_final`;
  - an unauthorized reader (another editor of the same character) gets 404 with and without a receipt: A a user at the current epoch and with a stale stored epoch; A a token, also after its revocation;
  - the GM, and the recorded replacement token, after A's revocation with no receipt get `absent_final` echoing the epoch; before revocation the replacement gets `absent_pending`;
  - a same-campaign non-replacement token gets 404; an other-campaign token is refused at the boundary (401, or 403 `token_campaign_mismatch`); a `replaces_credential_id` naming another campaign's credential is ignored; none of these carries a receipt or an `absent_*` state;
  - a stale stored epoch with a committed receipt returns the receipt;
  - a GM reading a listed plugin operation at the current epoch with a valid token gets `absent_pending`; no `epoch` gives 400;
  - a refusal receipt written after `absent_final`: the next read returns it, nothing applied.
- [ ] A receipt with `applied_status = pending` reads as `committed`; an event for a hidden record is omitted from `event_ids`.
- [ ] Token mount: a status read with the campaign not the active table returns the receipt or `absent_*`, not 409; existing token routes still answer 409 in that case.
- [ ] Authorization uses `authorizer.load` inside the stream-locked transaction (spy: called after `lock_stream`, before the receipt lookup; `flask.g` user and campaign caches poisoned in the test change nothing).
- [ ] No authority row: an authorized read answers `absent_pending`, an unauthorized one 404, and abandon inserts its receipt. Authority `file`: the rules of decision 4 apply unchanged (stale epoch → `absent_final`).
- [ ] Abandon: inserts `abandoned` once; a second abandon answers from that receipt; a later arrival of the original gets `409 abandoned` whatever its digest; abandon of a committed op answers from its receipt; an unauthorized abandon is 404 and writes nothing.
- [ ] PostgreSQL (CI): a read blocks while X holds the stream lock, then returns X's receipt (or `absent_pending` if X rolled back); a read that takes the lock first returns `absent_pending` and X waits; a fault-injected commit of X followed by a revocation or epoch bump between the read's receipt lookup and its epoch/token read never yields `absent_final`; with `STREAM_LOCK_TIMEOUT_MS` lowered by monkeypatch, a contended read answers `503 temporarily_unavailable`.
- [ ] Route policy coverage tests pass with the four endpoints declared.
- [ ] `python -m pytest -q tests/persistence/test_operation_status.py tests/persistence/test_command_executor.py tests/test_obsidian_sync.py tests/test_route_policy_inventory.py tests/test_access_policy_unit.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_operation_status_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: sessions, folder form and backfill (PE-14e); client-side binding, quarantine, status-read handling and the client cases (PE-15, PE-17/18, PE-18b, PE-41 and later writers; PE-60 for the plugin, C:339-345, C:1097); the plugin's quarantine list (PE-60); token storage and the lazy `tokens.json` import (PE-14c).
- Do not change `token_required`, `process_command` or `runtime.json` handling.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
