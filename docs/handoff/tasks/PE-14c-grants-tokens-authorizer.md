# PE-14c: Grant and token records and the step-4 authorizer

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14b; user decision on token storage, Decision 1)
- **Branch:** `claude/pe-14c-grants-tokens` from current main after PE-14b merges. Record the exact starting SHA.
- **Depends on:** [PE-14b](PE-14b-command-executor.md) (executor and its authorizer factory slot), and through it PE-14a (`lock_stream`, `_0005`) and PE-13a (`EncounterStateRepository.is_combatant`, authority rules). The user's answer to Decision 1.
- **Followed by:** PE-14d (stream lock in every authorization writer; grant and token commands), PE-14f (status read and abandon use `authorizer.load` and the replacement and revocation helpers).
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: check 4 and 5 roles and visibility (C:106-117); terminal (C:118); override only from the GM or a scoped token (C:137-140); step-4 re-read list (C:429-436); authorization changes take the stream lock (C:460-466); grant kinds (C:464); token terms, QM and live (C:841-852); matrix rows (C:854-876); fresh authorization at commit (C:878); QM limits, no rules override (C:879-882); projections and the union rule (C:635-648); grants are db-only (C:794-797); status-read authorization, replacement and permanent revocation (C:262-275, C:326-331); round-trip class for grants (C:682); populated downgrade (C:831-837); PE-14 row (C:1090).
  - Tokens today are per-campaign files: `core/obsidian_sync.py` `tokens_file` :40, `campaign_lock` :52-56 (a process `RLock`), `_load_tokens` :63-69, `create_token` :72-95 (12-hex `id` :79, bearer format :80, digest `_token_digest` :59-60), `list_tokens` :103-105, `verify_token` :108-136 (takes `campaign_lock` at :112; `last_used_at` written at most every 300 s, :124-135), `revoke_token` :139-147. Bearer check `token_required` `services/obsidian_sync.py:517-541` (bearer verify at :519-528, `verify_token` call :524, active-table 409 at :529-536); mint route :562-579, revoke route :581-591. No other product caller of these functions.
  - Backend modes: `core/persistence/runtime.py:39-50`. `shadow` keeps JSON authoritative; `sql_enabled()` is true only for `OWNERSHIP_BACKEND=sql`.
  - Lock-order neighbours: account-creation advisory lock `core/persistence/identity.py:32-34`; process lock taken outside the transaction `core/persistence/workflow_store.py:27`.
  - Project `CLAUDE.md`: `core.auth._load_users` and `core.campaigns.get_campaign` memoize on `flask.g`; the authorizer must not use them (C:429-430).
  - Migrations: PE-14a's `_0005` is head when this starts (`20261115_0005_command_stream.py`, date part may differ). Tests that pin the head literally: `tests/persistence/test_runtime_backend.py` parametrized revision list (:115-118 at `791936f1`) and `if revision == ...` (:130); PE-14a's single-head test in `tests/persistence/test_command_stream_schema.py`; PE-14a's `PE10` pins in `tests/persistence/test_dnd5e_identity_schema.py`. `tests/persistence/test_game_state_schema.py:47` is `HEAD = runtime._REQUIRED_REVISION` and needs no edit.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 600 product lines (including migration `_0006`) and 650 test lines. Stop and report past about 750 product lines. (Planning estimate; raised from 500 for the token mirror, `override_allowed`, the `grant` visibility kind, the grant consume API and the head-pin test edits.)

## Goal

Store grants and integration-token records in SQL with scope and `replaces_credential_id`, and replace PE-14b's stub authorizer with one that re-reads every authorization input from SQL inside the command transaction and answers checks 4 and 5 (including whether `override` is allowed) per the permission matrix. No new grant or token is issued through a command yet (PE-14d).

## Decisions this brief fixes (reviewer: challenge these first)

1. **Token storage (USER DECISION REQUIRED).** The contract needs the token record and scope re-read inside the stream-locked transaction (C:435) and a committed, permanent revocation readable in the status read (C:275). A file cannot join that transaction. Options:
   - **A (recommended): SQL is the token store in SQL mode.** New `integration_tokens` table. Only with `runtime.sql_enabled()` (`OWNERSHIP_BACKEND=sql`) do `create_token`/`verify_token`/`revoke_token`/`list_tokens` use SQL; `json` and `shadow` keep `tokens.json` as the bearer authority, unchanged.
     - **Import.** The lazy import of `tokens.json` runs only on the bearer path (`verify_token`, called from `services/obsidian_sync.py:524-528`), before any stream-locked transaction: take `campaign_lock`, read the file, release it; then, only if the file lists an `id` that SQL lacks or a `revoked_at` that a live SQL row lacks, open its own transaction, on PostgreSQL `SET LOCAL lock_timeout` from `STREAM_LOCK_TIMEOUT_MS` before `lock_stream`, then `lock_stream(session, campaign_id)`, insert missing rows (`id` kept as `credential_id`, scope `["@live"]`, C:851-852; `minted_by_user_id` NULL) with insert-if-absent, and copy a file revocation onto a live row (revocation only ever moves one way), then commit. `authorizer.load` never imports: a missing token row means not authorized.
     - **Mirror.** In SQL mode, mint and revoke also update `tokens.json` after the SQL commit, as a non-authoritative mirror (same `id`, digest and `revoked_at`), under `campaign_lock` taken after the commit. So switching back to JSON mode never revives a revoked token. Revoke also stamps a file record that SQL has not imported yet, and `list_tokens` in SQL mode shows SQL rows plus file rows not yet imported (read-only), so the GM page can see and revoke a token that has never authenticated.
     - One source of truth per mode; the bearer path changes in SQL mode only.
   - **B: SQL shadow record.** `tokens.json` stays the bearer authority in every mode; SQL holds `credential_id`, scope, `replaces_credential_id` and `revoked_at`, written after the file. Smaller change to the Obsidian path, but a token can be revoked in one store and live in the other (C:458: a file write paired with a SQL write is never atomic), and every db-authority check would need both stores.
   Until the user picks, this brief assumes A for its file list and acceptance.
2. **Grants table** `campaign_grants(grant_id 32-hex PK, campaign_id FK campaigns CASCADE, kind in quartermaster|level_unlock|retrain_grant, user_id NULL, character_id NULL, target_level NULL, build_revision NULL, issued_by_kind, issued_by_id, issued_at, consumed_at NULL, consumed_by_operation_id NULL, revoked_at NULL)`, with checks: `quartermaster` needs `user_id`; `level_unlock` needs `character_id` and `target_level`; `retrain_grant` needs `character_id` and `build_revision` (C:859, C:861); partial unique one live QM per `(campaign_id, user_id)` (live = `consumed_at IS NULL AND revoked_at IS NULL`). Per-character unlocks are a judgment: the matrix ties both to the character's publish. `core/persistence/grants.py` exposes `live_level_unlock(campaign_id, character_id, target_level)`, `live_retrain(campaign_id, character_id, build_revision)` and `consume(grant_id, operation_id)`: a compare-and-set `UPDATE ... WHERE consumed_at IS NULL AND revoked_at IS NULL` that raises on a rowcount of 0. Consumers (PE-18b, PE-15, PE-37) call it inside their command transaction.
3. **Token scope** is a JSON list of command `type` names fixed at mint, plus the symbolic entry `"@live"` meaning "the current live-command set" (C:852), so tokens that exist today get `["@live"]`. A token may send `override` for a type only when its scope holds the entry `"override:<type>"`; `"@live"` never implies override (a judgment on how "a token scoped for it", C:137, is spelled; record it in the report). The authorizer refuses a token any type naming membership, ownership, tokens or QM grants whatever its scope (C:849-851).
4. **Replacement.** `replaces_credential_id` is honoured only when both tokens belong to the same campaign and the replacement was minted by a principal who was that campaign's GM at mint (`minted_by_user_id` recorded) (C:268-270, C:328-331).
5. **Visibility for check 5.** The authorizer resolves visibility per record kind from the projection table (C:635-645) and the union of roles (C:647-648) for kinds that exist now: `campaign`, `character.build`, `character.play`, `encounter`, `combatant`, `clock`, and `grant` (visible to the GM, the grant character's owner and editor, and the QM user for their own QM grant; no other principal, tokens included). Later kinds register their own resolver. A `party` principal sees `character.play` (HP and conditions) but not `character.build` details (C:642, C:856). PE-14d's `grant.revoke` uses kind `grant` as its subject.
6. **Override (check 4).** `override_allowed(type)` is true for the GM, the site admin (C:847: GM includes the site admin), or a token whose scope holds `"override:<type>"` (C:137, C:876); false for every other role, the QM included (C:880-881). PE-13b defines the protocol and refuses with `403 not_permitted`; this task implements it.
7. **Lock order.** The authorizer runs inside a transaction that already holds the stream row and takes no further lock: it reads the campaign row without `FOR UPDATE` (the stream lock serializes authorization writes, PE-14d) and takes no process lock. Token import and mirror writes take `campaign_lock` only outside any transaction that holds a stream row; no process lock is ever taken while a stream row is held.

## Approach

Allowed files:
- `core/persistence/models.py` (two new models only), `core/persistence/__init__.py` (exports of the two models only)
- new `migrations/versions/<date>_0006_grants_tokens.py`, `down_revision` PE-14a's `_0005`; `core/persistence/runtime.py` (`_REQUIRED_REVISION` only)
- `tests/persistence/test_runtime_backend.py` (the parametrized revision list and the `if revision ==` line only: add the `_0006` head as accepted, keep `_0005` in the list as a refused older revision)
- `tests/persistence/test_command_stream_schema.py` (PE-14a's): add `PE14A = "<date>_0005"` and use it instead of `head`/`HEAD` in its downgrade-scoped and offline cases; move its single-head test to `test_grants_tokens.py`
- `tests/persistence/test_dnd5e_identity_schema.py` and `tests/persistence/test_game_state_schema.py`: only if a downgrade case still runs from `head` and fails; pin it to that module's own revision (`PE10`, `PE12`)
- new `core/persistence/grants.py`, `core/persistence/integration_tokens.py`
- `core/obsidian_sync.py` (`create_token`, `verify_token`, `revoke_token`, `list_tokens` only; option A)
- new `core/commands/authorizer.py`; `core/commands/executor.py` (factory binding only). `core/commands/__init__.py` already exists (PE-13b/13c/14a); do not add re-exports.
- new `tests/persistence/test_grants_tokens.py` (includes the single-head test), `tests/persistence/test_command_authorizer.py`; new cases appended to `tests/test_obsidian_sync.py` (existing cases unchanged)

1. Models and migration (follow 0003/`_0005` conventions). Populated downgrade refused while any grant or token row exists (C:682, C:831-837) and offline; empty downgrade succeeds.
2. Update the head pins listed above so the existing suites still test `_0005`'s and 0004's own guards.
3. `integration_tokens(credential_id PK, campaign_id FK CASCADE, label, token_digest, last_four, scope JSON, replaces_credential_id NULL, minted_by_user_id NULL, created_at, last_used_at NULL, revoked_at NULL)`. Keep the bearer format and digest (`core/obsidian_sync.py:59-60`, :79-80) unchanged so existing plugins keep working. `last_used_at` keeps its 300-second granularity. Import and mirror per Decision 1. PE-14d later puts mint and revoke under the stream lock; here they are plain SQL transactions followed by the mirror write.
4. `grants.py` per Decision 2.
5. `authorizer.py`: `load(session, principal, campaign_id)` reads, in the caller's transaction, the user row (disabled, admin), the campaign row (GM, `trashed_at`) without `FOR UPDATE`, memberships, assignments, live grants, the token record and scope, and the active `campaign_sessions` row (C:429-436). It works with no `campaign_state_authority` row and under any authority (PE-14f relies on this). Exposes `campaign_role_allows(type)`, `visible(record)` (PE-13b D3 protocol; PE-13a records already carry `kind`), `subject_role_allows(type, subject)`, `override_allowed(type)`, `is_live(character_id)` (an active session row AND PE-13a's `EncounterStateRepository.is_combatant(campaign_id, character_id)`, C:842-843; never the process slot; it never passes `allow_stale`, so it is reached only after the fence), and for PE-14f `is_recorded_replacement(original_credential_id)` (Decision 4) and `token_revoked(credential_id)` (C:266, C:275). `visible(record)` dispatches on `record.kind` through a module-level `register_visibility(kind, resolver)` in `core/commands/authorizer.py`, where `resolver(authorizer, record) -> bool`; the module that owns a kind calls it at import time (Decision 5; PE-14e's `session`), and an unregistered kind is refused (not visible).
6. Bind the executor's authorizer factory to it.

## Acceptance

- [ ] Migration: one head; `_REQUIRED_REVISION` names it; the single-head test lives in `test_grants_tokens.py` and names `_0006`; populated downgrade refused with one grant, with one token, and offline; empty downgrade succeeds; `test_runtime_backend.py` accepts `_0006` and refuses `_0005`; `test_command_stream_schema.py` downgrade cases still assert `_0005`'s own refusal messages.
- [ ] Grant checks: each kind's required columns, one live QM per user, consumed and revoked rows ignored by the authorizer. `consume` succeeds once; a second `consume`, or `consume` of a revoked grant, raises and writes nothing. `live_level_unlock` and `live_retrain` return only live rows for the exact target level or build revision.
- [ ] Option A, SQL mode: an existing `tokens.json` token keeps authenticating after lazy import, with the same `credential_id` and scope `["@live"]`; revocation is permanent and visible in a new transaction; revoke in SQL mode, switch to JSON mode, and the token is refused; a token minted in SQL mode authenticates after switching to JSON mode; a file token never used as a bearer is listed and can be revoked in SQL mode, and later bearer use is refused.
- [ ] Option A, `json` and `shadow` modes: no SQL token read or write; every existing case in `tests/test_obsidian_sync.py` passes unchanged (new cases are additions only).
- [ ] Lock order: a spy on `campaign_lock` and `lock_stream` shows the import releases `campaign_lock` before `lock_stream`, the mirror takes `campaign_lock` only after commit, and `authorizer.load` with a token that has no SQL row returns not-authorized without touching the file or `campaign_lock`.
- [ ] Authorizer reads come from the session: a test that seeds `flask.g` caches with stale data shows they are ignored. `load` succeeds with no authority row and with authority `file`.
- [ ] Matrix: one test per role (owner, editor, viewer, party, QM, GM, admin, token in scope, token out of scope) for at least one existing record kind, checking 404 before 403 (C:116-117); `grant` visibility for the GM, the character's owner and editor, the QM on their own QM grant, and 404 for a viewer, party member, other QM and token.
- [ ] `override_allowed`: true for the GM, the site admin and a token scoped `"override:<type>"`; false for owner, editor, QM, and a token scoped only `["@live"]` or `[<type>]`.
- [ ] A token can never be authorized for a membership, ownership, token or QM-grant type, even if its scope lists it.
- [ ] `replaces_credential_id` naming another campaign's credential is ignored; a replacement minted by a non-GM is ignored.
- [ ] `is_live` is false with an active encounter but no active session, and false for a character not in the active encounter.
- [ ] `python -m pytest -q tests/persistence/test_grants_tokens.py tests/persistence/test_command_authorizer.py tests/persistence/test_command_executor.py tests/persistence/test_command_stream_schema.py tests/persistence/test_runtime_backend.py tests/persistence/test_dnd5e_identity_schema.py tests/persistence/test_game_state_schema.py tests/test_obsidian_sync.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_grants_tokens.py tests/persistence/test_game_state_schema.py -m postgresql --strict-markers` (round trip including `_0006`) executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Existing route responses, authority settings and worker count are unchanged; existing plugin tokens keep working.

## Out of scope / do not touch

- Owned elsewhere: taking the stream lock in membership, assignment, GM, trash, grant and token writers, and the grant and token commands including `grant.revoke` (PE-14d); sessions (PE-14e); status read, abandon and the `token_verified` decorator (PE-14f); consuming `level_unlock` / `retrain_grant` (PE-18b, PE-15, PE-37); QM finalize (PE-48); token scope UI (PE-62).
- No change to `token_required`, `process_command` (`services/obsidian_sync.py:651-778`, PE-60) or the SSE hub.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
