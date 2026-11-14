# PE-14d: Stream-locked authorization writes, grant and token commands

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14c)
- **Branch:** `claude/pe-14d-auth-writes` from current main after PE-14c merges. Record the exact starting SHA.
- **Depends on:** [PE-14c](PE-14c-grants-tokens-authorizer.md) (grant and token records, the authorizer, the token-storage option the user picks there). Through it: PE-14a (`lock_stream`), PE-14b (executor, `STREAM_LOCK_TIMEOUT_MS`), PE-13a (`CommandLocks`).
- **Followed by:** PE-14e (sessions) and PE-14f (status read, abandon); both are blocked on this task and may run in parallel.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: authorization changes take the stream lock, strictness (C:460-469); file authority checks authorization once per request, not under the stream lock (C:789-790); grants are db-only (C:794-797); token rules (C:849-852); grant issue matrix row (C:860); revocation is permanent (C:275); a grant or membership change closes affected subscriptions (C:628-630); event type names are registered by the implementing task (C:1102); PE-15 later turns the D&D GM owner route into a stream-locked assignment command (C:1091); PE-14 row (C:1090).
  - Writers that change campaign authorization inputs today. All but the last group lock the campaign row first; none takes a stream row:
    - `core/persistence/services.py` `TransactionalStore.redeem_invite` :84 (membership insert :178, role :191), `claim_character` :241 (via `_claim_locked_character` :423, assignment :451), `set_membership_role` :315, `remove_membership` :351 (delete :370), `_lock_campaign` :386, `_locked_membership` :397;
    - `core/persistence/campaign_runtime.py` `create_campaign` :145 (inserts the campaign :152-156 and the creator's GM membership :157 in one transaction), `_locked_campaign` :168, `add_member` :243, `_change_membership` :274 (calls `lifecycle.invalidate_member`, `core/character_workflows/lifecycle.py:54`, which takes no process lock), `delete_campaign` :316 (`trashed_at` :322), `restore_campaign` :329 (`trashed_at` :350);
    - `core/persistence/ownership.py` `_lock_campaign` :200, `register_character` :240, `register_character_in_session` :269 (assignment :304; also reached from `core/character_workflows/recovery.py:175-185`), `write_character_batch` :319, `release_character` :367, `delete_character` :391;
    - `core/persistence/identity.py` `_transaction` :26 (SQLite `BEGIN IMMEDIATE` :31; PostgreSQL `pg_advisory_xact_lock(73420401)` :32-34 when creating an account), `redeem_invite` :276 (opens `_transaction(account_creation=...)` :284, locks the invitation row before the campaign id is known, then calls `TransactionalStore.redeem_invite`);
    - `core/persistence/workflow_store.py` `SqlWorkflowStore.transaction` :23 (takes `core/campaigns.py:42` `_CAMPAIGN_STORE_LOCK` :27, outside the transaction, before the campaign row);
    - `core/persistence/campaign_import.py` `_import` (creates a new campaign and the importer's GM membership in one transaction; `upsert_membership(..., role='gm')` is :266 at `791936f1`; cite it by function, since line numbers drift);
    - `tools/migrate_transactional_store.py:2360` `campaign_repository.upsert_membership(` and :2368 `set_assignment(` (offline migration tool);
    - repository primitives in `core/persistence/repositories.py`: `CampaignRepository.upsert` :116 (sets `trashed_at` :142), `upsert_membership` :150-170, `CharacterRepository.set_assignment` :237-260;
    - token mint and revoke: `core/obsidian_sync.py` `create_token` :72, `revoke_token` :139 (both under `campaign_lock` :52), via `services/obsidian_sync.py:573`, `:583-589`.
  - Backend modes: SQL writes happen only when `runtime.sql_enabled()` (`OWNERSHIP_BACKEND=sql`, `core/persistence/runtime.py:48-51`); `json` and `shadow` write JSON (`core/campaigns.py:48` and the matching branches). Database errors in writers surface through `runtime.store_call` as `StoreUnavailable` (runtime.py:80-95).
  - Post-commit stream closure today: `app.py:2699` `_close_sse_subscribers_for_user`, called at `app.py:11118`, `:11135`, `:17170`.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 380 product lines and 650 test lines. Stop and report past about 480 product lines. (Planning estimate; the AST inventory test and the PostgreSQL race tests are most of the test lines.)

## Goal

Every SQL transaction that changes a campaign's GM, trashed status, membership, assignment, grants or tokens takes that campaign's stream row before the campaign row and before any state or authorization row, so command commits see authorization strictly at commit; grant issue/revoke become executor commands and token issue/scope/revoke become stream-locked writes. Visible behaviour of existing routes does not change.

## Decisions this brief fixes (reviewer: challenge these first)

1. **One global lock rule: stream row before campaign row.** Every writer calls `lock_stream(session, campaign_id)` (PE-14a) before the campaign row and before any state or authorization row, then locks the campaign row as today. Command transactions (PE-14b) take the stream row and read the campaign row without `FOR UPDATE` (PE-14c). This removes the stream/campaign deadlock cycle. A writer that reaches several campaigns (none known today) locks streams in ascending `campaign_id`.
   - **May precede the stream row:** only locks that no command transaction takes: the account-creation advisory lock 73420401 and SQLite `BEGIN IMMEDIATE` (identity.py:31-34), the invitation row lock (`identity.redeem_invite`), and process locks taken outside the transaction (`_CAMPAIGN_STORE_LOCK`, workflow_store.py:27). No stream-holding transaction may take 73420401.
   - **Exception: `create_campaign`** inserts the campaign row and the creator's GM membership first, then calls `lock_stream` in the same transaction (the stream row's FK to campaigns makes an earlier call impossible). No other transaction can reference the uncommitted campaign, so nothing needs serializing. `campaign_import._import` follows the same pattern. Both are declared in the AST test's `EXEMPT` list.
2. **Wherever the write is a SQL transaction.** The rule attaches to the SQL transaction, whatever the campaign's authority: the contract makes the lock part of every authorization change (C:460), and in file authority it only serializes against nothing, at negligible cost. Today that is `sql` mode only; `json` and `shadow` write JSON and are unchanged.
3. **Process locks stay outside or before.** `workflow_store` keeps `_CAMPAIGN_STORE_LOCK` first, then the stream row, then the campaign row. No code path may take a process lock (`_CAMPAIGN_STORE_LOCK`, `_LIVE_CAMPAIGN_DISPATCH_LOCK` `app.py:535`, `ENCOUNTER_LOCK` `app.py:2312`, `campaign_lock` `core/obsidian_sync.py:52`, D&D store locks) while holding a stream row. Command transactions never take a process lock. The `tokens.json` mirror write (PE-14c) runs after the SQL commit, so its `campaign_lock` is never held with the stream row.
4. **Lock timeout.** On PostgreSQL each writer issues `SET LOCAL lock_timeout` from PE-14b's `STREAM_LOCK_TIMEOUT_MS` (import it; never a second copy) immediately before `lock_stream`. In plain writers a timeout (55P03) surfaces through the existing database-error path (`StoreUnavailable`), so no route gains a new response; grant commands get the executor's `503 temporarily_unavailable`. Import the constant inside the writer function, never at module top (a module-top import from `core/commands/executor.py` into `core/persistence/` makes an import cycle, because `core/persistence/__init__.py` imports `.services` and the executor imports `core.persistence` modules).
5. **Commands vs. plain writes.** Membership, assignment, GM and trash changes stay plain SQL writes that take the lock (they run in file authority too). Grant issue/revoke are executor commands, so they are db-only (C:794-797): `grant.issue_level_unlock`, `grant.issue_retrain`, `grant.set_quartermaster`, `grant.revoke`; GM, or a scoped token for the first two only (C:860, C:851). Subjects: the character for unlock/retrain; the campaign for QM; for `grant.revoke`, the grant itself (kind `grant`, visibility per PE-14c), so a missing or hidden grant is `404` at check 5 and a revoked or consumed grant is terminal: `409 already_finalized` at check 6. Each type's `lock_plan(ctx)` locks, through PE-13a's `CommandLocks`, the subject character's rows named in `expected` (none for QM and revoke); grant rows are not a `CommandLocks` step and are serialized by the stream row, which every grant write and every `consume` (PE-14c) holds. `lock_plan` reads the campaign (QM) or the grant (revoke) into `ctx.records`. Token issue, scope change and revoke are plain stream-locked writes callable by the GM in any authority, since tokens predate db authority. Grant event names are registered here (C:1102): `grant.issued`, `grant.revoked`, emitted through `ctx.emit` (PE-14b).
6. **Tokens follow PE-14c's storage decision.** Option A (recommended there): in `sql` mode the token row is written in a transaction that takes the stream row first; the `tokens.json` mirror follows the commit (Decision 3); `json` and `shadow` keep `tokens.json` as bearer authority, unchanged and unlocked. `scope` and `replaces_credential_id` exist only on the SQL record. If the user picks Option B, the SQL shadow record write takes the stream row the same way, and the file write stays outside it. Record which option was implemented.
7. **Subscription closure** stays post-commit and unchanged in this task; PE-31b owns closing streams "before any further frame" once events flow (C:628-630). Record this.

## Approach

Allowed files:
- the writer modules listed in Context (lock calls and the `lock_timeout` statement only, plus the token scope parameter in `core/obsidian_sync.py` and its mint route body in `services/obsidian_sync.py`); `core/persistence/campaign_import.py` (`_import` only)
- new `core/commands/grant_commands.py`; `core/persistence/grants.py`, `core/persistence/integration_tokens.py` (issue/revoke helpers)
- `core/route_policy.py` only if a route signature changes (none planned)
- new `tests/persistence/test_auth_writes_stream_lock.py`, `tests/persistence/test_auth_writes_postgresql.py`; focused additions to `tests/persistence/test_campaign_runtime.py`, `test_transactional_services.py`, `test_ownership_runtime.py`, `test_identity_runtime.py`, `tests/test_obsidian_sync.py`

1. Add the `lock_timeout` statement and `lock_stream` to each writer per Decision 1, keeping `lifecycle.invalidate_member` inside `_change_membership`'s transaction. In `identity.redeem_invite`, take the stream row once the invitation's campaign is known and before `TransactionalStore.redeem_invite` locks the campaign. `verify_token`'s lazy import transaction (PE-14c D1) is among the writers: it takes the stream row with the same `lock_timeout` statement. The offline migration tool takes no stream lock.
2. Grant commands on the PE-14b executor per Decision 5, with dependency sets and `lock_plan`.
3. Token mint takes an optional `scope` (default `["@live"]`) and optional `replaces_credential_id` (same campaign only); revoke stamps `revoked_at` once and never clears it.
4. AST inventory test. It scans `core/`, `services/`, `tools/` and `app.py` for `CampaignMembership(`, `CharacterAssignment(`, `upsert_membership`, `set_assignment`, `.trashed_at =`, writes to `.role` on a membership or assignment, `session.delete` of one, and grant or token row writes. Each hit must follow a `lock_stream` call in the same function, or appear in an explicit `EXEMPT` dict with a reason: `create_campaign` and `campaign_import._import` ("campaign created in this transaction; `lock_stream` follows the insert"); `tools/migrate_transactional_store.py` ("offline migration tool, no server running"); the `repositories.py` primitives and helpers such as `_claim_locked_character` ("every caller holds the stream row; covered by the spy test"). An `EXEMPT` entry that no longer matches a hit fails the test.

## Acceptance

- [ ] Each listed writer takes the stream row before the campaign row: a spy asserts `lock_stream` precedes the campaign `FOR UPDATE` in each. The spy covers `create_campaign` and `campaign_import._import` with their declared order (campaign insert, then `lock_stream`), `identity.redeem_invite` (advisory lock and invitation row, then stream, then campaign) and `SqlWorkflowStore.transaction` (process lock, then stream, then campaign).
- [ ] PostgreSQL (CI): a command transaction holding the stream lock blocks a concurrent `remove_membership`, and the command commits under the old membership; the reverse order makes the command refuse at commit (`404` or `403` per check 5, C:997-1002). No deadlock between a membership write and a command on the same campaign (100 iterations). An account-creating `redeem_invite` and a command on the same campaign, run concurrently, both finish.
- [ ] PostgreSQL (CI): a writer blocked behind a held stream row fails after `STREAM_LOCK_TIMEOUT_MS` (lowered via monkeypatch) with `StoreUnavailable`, nothing written.
- [ ] PostgreSQL (CI): every existing case in `tests/persistence/test_postgresql_concurrency.py` keeps its outcome; a token revoke racing a command by that token serializes on the stream lock.
- [ ] Grant commands: GM issues and revokes; a scoped token issues `level_unlock` and `retrain_grant` only; a token is refused QM grants regardless of scope; a QM cannot issue anything (C:879-882); revoking a revoked or consumed grant gives `409 already_finalized`; revoking a hidden or missing grant gives `404`; in file authority every grant command answers `503 authority_unavailable`.
- [ ] Token (Option A, `sql` mode): a replacement for another campaign's credential is refused at mint; revocation is permanent; `json` and `shadow` mode token behaviour is unchanged and takes no stream lock.
- [ ] AST inventory test passes, fails on a planted unlocked write in each scanned tree, and fails on a stale `EXEMPT` entry.
- [ ] Existing suites unchanged: `python -m pytest -q tests/persistence tests/test_obsidian_sync.py -m "not postgresql" --strict-markers` passes with the counts reported; existing `tests/test_obsidian_sync.py` cases pass unchanged, new cases are additions.
- [ ] `python -m pytest -q tests/persistence/test_auth_writes_postgresql.py tests/persistence/test_postgresql_concurrency.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `python -c "import core.persistence"` and `python -c "import core.commands.executor"` each succeed in a fresh interpreter.
- [ ] `git diff --check` passes. Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: the D&D GM owner route as a command (PE-15); consuming grants (PE-15, PE-18b, PE-37, through PE-14c's `consume`); the `tokens.json` mirror and lazy import (PE-14c); QM finalize (PE-48); closing subscriptions before the next frame (PE-31b); sessions (PE-14e); status read and abandon (PE-14f); no `stream_epoch` bump here: C:29 bumps it on campaign-archive restore (PR4B), which is not `restore_campaign`'s trash restore; whichever task owns that path bumps it under this lock.
- No change to how routes authorize in file authority (C:789-790).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
