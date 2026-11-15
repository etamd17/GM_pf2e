# PE-14f: Operation-status read and operation.abandon

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14d); brief revision 1 returned for re-review
- **Branch:** `claude/pe-14f-status-read-abandon` from current main after PE-14d merges. Record the exact starting SHA.
- **Depends on:** [PE-14d](PE-14d-stream-locked-authorization-writes.md) (token revocation and replacement committed under the stream lock, which finality relies on). Uses PE-14a's receipts, events table and `streams.lock_stream(session, campaign_id)`; PE-14b's `STREAM_LOCK_TIMEOUT_MS`; and PE-14c's `authorizer.load`, `token_revoked`, `is_recorded_replacement` and token records. This task owns the event-ID projector (Decision 7) and needs nothing from PE-31; PE-31 depends on this task instead. May run in parallel with [PE-14e](PE-14e-sessions-backfill.md) and PE-14g; they share no files.
- **Followed by:** PE-31 (imports `event_visible_to` for replay filtering; does not edit `core/commands/event_visibility.py`).
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: pending-operation binding and quarantine triggers (C:253-260); status read, its answer table, `epoch`, finality, abandon (C:261-307); server acceptance cases (C:308-337); client cases owned elsewhere (C:339-345); event visibility fixed at commit, unlisted projection means `none`, `gm` always `full`, a reveal never re-exposes old events (C:409-417); projection table and union rule (C:635-648); roles, Party and GM-includes-admin (C:846-847); a token views as `gm_integration` (C:856); step 4 re-reads authorization from SQL, never `flask.g` (C:425-436); code table (C:352-366).
  - Token boundary: `token_required` in `services/obsidian_sync.py:517-541` verifies the bearer for `X-Campaign-ID` at :519-528 and refuses a campaign that is not the website's active table with `409` at :529-536; `API_PREFIX` :18; existing token routes :785-799. `RoutePolicy.AUTHENTICATED` (`core/route_policy.py:34`) and `RoutePolicy.INTEGRATION_TOKEN` (:44), token routes declared at :443-452; `TOKEN_CAMPAIGN_MISMATCH` (`core/access.py:100-104`), `_token_access` (:192-204), token policy dispatch (:270-271). Every new endpoint goes in `core/route_policy.py` (`docs/handoff/PROTOCOL.md`, conventions).
  - Role inputs for the projector: `campaign_memberships.role` (`core/persistence/models.py:215`) and `character_assignments.role` (:251). PE-14c's `load` already reads both, plus live grants, in the caller's session.
  - Token storage is PE-14c Decision 1, Option A (user, 2026-11-14). This task trusts only PE-14c's SQL `integration_tokens.revoked_at` for revocation, which is correct under either option, because PE-14d commits revocation to SQL under the stream lock and PE-14c never clears it or lets a mode switch bypass it. Only SQL `revoked_at` gives revocation finality. A token with no SQL row, or one revoked only in `tokens.json` (for example a partial revoke not yet repaired by PE-14c's `repair_token_mirror`), stays `absent_pending`. PE-14g is the rollback gate; this task does not rely on the file mirror.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 400 product lines and 705 test lines (the decisions table says 650; 705 is the sum of the parts in Brief revision 1 item 5), including `held_projections` and the event-ID projector. Stop and report past about 480 product lines.

## Goal

The server side of pending-operation recovery: a stream-locked operation-status read and `operation.abandon`, mounted for website users and for integration tokens, with every server case the contract lists (C:308-337), and event ids projected for the reader by a concrete projector owned here.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Routes.** One handler per operation, two mounts each, four endpoints in `core/route_policy.py`:
   - `GET /api/campaigns/<campaign_id>/operations/<operation_id>` and `POST .../abandon`, policy `AUTHENTICATED`; the handler conceals with 404.
   - `GET /api/integrations/obsidian/v1/operations/<operation_id>` and `POST .../abandon`, policy `INTEGRATION_TOKEN`, campaign from `X-Campaign-ID`.
   - Query `principal=<kind>:<id>&epoch=<n>` (C:262); abandon body `{principal}` (C:299). Abandon writes the target key's receipt and is idempotent, so it carries no envelope of its own.
   - Neither runs the command fence (check 2): a reader with a stale stored epoch must still get the receipt or `absent_final` (C:275, C:332-333).
2. **Token mounts use a new `token_verified` decorator.** It verifies the bearer for `X-Campaign-ID` exactly as `token_required` does (`services/obsidian_sync.py:519-528`: same 401 and 400 answers) but skips the active-table check (:529-536), because a campaign switch is a quarantine trigger (C:256) and the status read is the recovery path. Existing routes keep `token_required` unchanged. The decorator runs before any transaction, so its `campaign_lock` (inside `verify_token`) is never taken while a stream row is held.
3. **Parsing before the transaction** (static, reveals nothing): a malformed `operation_id` (C:27), a `principal` that is not `user:<id>` or `integration_token:<id>`, and a missing or non-integer `epoch` each give `400 bad_envelope` (C:278-279). Abandon takes no epoch. `operations.py` parses the query's `kind:id` itself into the receipt key `(principal kind, principal id)`. It never calls PE-14a's `principal_key` (`core/commands/envelope.py`): that function serializes an authenticated `Principal`, and the queried original is a receipt key, not an authenticated principal. The reader's own identity comes from the route boundary only.
4. **One short transaction holding the stream lock** (C:263-268). `read_status` and `abandon` take no `session` argument; each opens `runtime.database().transaction()` itself. As its first statements, on PostgreSQL issue `SET LOCAL lock_timeout` from PE-14b's `STREAM_LOCK_TIMEOUT_MS` (imported, not copied), then `streams.lock_stream(session, campaign_id)`; `55P03` gives `503 temporarily_unavailable`. Inside it, in this order:
   1. Authorize the reader through PE-14c's `authorizer.load(session, reader, campaign_id)`, never from `flask.g` (C:265, C:429-430). The route boundary supplies only the reader's identity (user id, or credential id and campaign). Authorized: the campaign's GM; the original principal itself while still valid (for a user: enabled and a campaign member; for a token: not revoked; judgment on "valid", record it); or a token recorded as the original token's replacement (`is_recorded_replacement`, honoured only as PE-14c Decision 4 allows: same campaign, GM-minted). Anyone else gets `404 not_found`, whether or not a receipt exists (C:277, C:321).
   2. Receipt lookup by `(campaign_id, principal kind, principal id, operation_id)`. A receipt answers `200 {state, code, event_ids, operation_id}`: `state` is `committed` for `applied_status` `committed` or `pending`, `rejected` for a refusal, `abandoned` for `code = abandoned`. Event ids pass through `event_visibility.visible_event_ids` with the authorizer loaded in step 1 (Decision 7).
   3. No receipt. Within the same stream-locked observation:
      1. If `original` is `integration_token:<cid>` and `authorizer.token_revoked(cid)` is true (SQL `integration_tokens.revoked_at` only), answer `200 {state: absent_final, epoch: <request epoch>}`. This holds under any authority value and with no `campaign_state_authority` row (C:275).
      2. Otherwise, if an authority row exists and the request epoch is below its `stream_epoch`, answer `absent_final` echoing the request epoch (C:275, C:284).
      3. Otherwise answer `absent_pending` (C:276).

      A missing authority row disables only the epoch rule (ii). Receipts can still exist without an authority row: Decision 5's abandon writes one, and step 2 answers from it. No command commits without an authority row (PE-14b Decision 2: 503, nothing written). A token with no SQL row, or one revoked only in `tokens.json`, answers `absent_pending`; that is not final, so C:276 is not violated, and the GM resolves it with abandon. Under Option A, PE-14c's SQL-mode revoke of a never-imported file token inserts a revoked SQL row, so a committed revoke does give finality.
5. **Abandon** (C:299-304): same parsing, lock and authorization. A receipt exists: answer from it, as Decision 4.2. Otherwise insert `status = rejected`, `code = abandoned`, `digest` NULL, `applied_status` NULL, no events, and answer `200 {state: abandoned}`. The receipt's `stream_epoch` is the current one, or 1 (the `stream_epoch` floor) when there is no authority row (judgment; record it). With no authority row abandon still inserts its receipt. A later arrival of the original gets `409 abandoned` from PE-14b's receipt lookup, whatever its digest, once it passes the fence. While the campaign has no authority row (or authority `file`), it gets `503 authority_unavailable` from the fence first, and nothing is written.
6. **Finality race.** PE-14d's revocation and every `stream_epoch` change take the stream lock, so the read's authorization, receipt lookup and epoch/token read are one observation (C:264-268, C:315-319). `lock_stream` creates the stream row on first use without an authority row (PE-14a Decision 1), so revocation is serialized in file and no-authority campaigns too.
7. **Event-ID projector** (owned here; no PE-31 dependency). The status read returns only `event_ids`, and each event's visibility is fixed at commit (C:409-417), so the filter needs only the committed `visibility` map and the reader's current roles.
   - `held_projections(authorizer, *, subjects: list[dict], encounter_id: str | None) -> frozenset[str]` is added by this task to `core/commands/authorizer.py` as an additive module-level function that takes PE-14c's loaded authorizer as its first argument; PE-14c's class is not changed. Callers write `held_projections(authorizer, subjects=..., encounter_id=...)`, never `authorizer.held_projections(...)`. It reads only what `load` already read, in the same session. It returns the union of projections the principal holds now (C:647-648):
     - a GM or site admin holds `{"gm"}`;
     - an integration token holds `{"gm_integration"}`;
     - a user who is a player member of the campaign holds `member`, whatever the subjects;
     - for each subject of kind `character`, the user holds its `character_assignments` role (`owner`, `editor` or `viewer`), or `party` if they are a player member with no assignment to it (C:846-847);
     - a user holds `qm` if they have a live QM grant;
     - a user holds `encounter_player` if `encounter_id` is not null and they are a player member. Judgment: the contract does not say who holds `encounter_player`; record it in the report for Codex to challenge. It is evaluated against the event's committed `encounter_id`, never the current live encounter, so visibility does not shift when the encounter ends;
     - a disabled user or a non-member holds the empty set.
   - New `core/commands/event_visibility.py`:
     - `event_visible_to(authorizer, event_row) -> bool`: true if `gm` is held. Otherwise true if some held projection P has `visibility.get(P)` equal to `"full"` or starting with `"redact:"`. Any other value counts as `none`. A missing, NULL or non-object `visibility` counts as `none` for every projection except `gm`. Held projections come from `held_projections(authorizer, subjects=event_row.subjects, encounter_id=event_row.encounter_id)`.
     - `visible_event_ids(session, authorizer, campaign_id, event_ids) -> list[str]`: loads rows with `campaign_id = :campaign_id AND event_id IN (...)` from the caller's session and keeps receipt order. A receipt id with no matching row is an integrity error and answers `500 server_error`, never a silent drop.
   - The predicate never consults current record visibility, so a later reveal never re-exposes an earlier event (C:417).
   - `read_status` and `abandon` take no `project` argument and have no default, identity or stub projection path. Rejected and abandoned receipts have no events, so their empty list comes from the data.
   - PE-31 imports `event_visible_to` and must not edit `event_visibility.py`. If PE-31 needs redacted bodies, it adds its own module that calls the predicate.

## Approach

Allowed files:
- new `core/commands/operations.py`; new `core/commands/event_visibility.py`; `core/commands/authorizer.py` (additive module-level function `held_projections` only; nothing else in PE-14c's module changes); `core/commands/__init__.py` only if absent. Create it with exactly this content: `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline, and nothing else (no re-exports). If it already exists, leave it alone
- `app.py` (register the two user-facing routes only); `services/obsidian_sync.py` (add `token_verified` and register the two token routes only; `token_required` unchanged); `core/route_policy.py` (four entries)
- new `tests/persistence/test_operation_status.py`, `tests/persistence/test_event_visibility.py` (includes the `held_projections` tests), `tests/persistence/test_operation_status_postgresql.py` (copy the `postgres_database` fixture use from `tests/persistence/test_workflow_postgresql.py:22-24`); focused additions to `tests/test_route_policy_inventory.py`, `tests/test_access_policy_unit.py` and `tests/test_obsidian_sync.py` (new cases only; existing cases pass unchanged)

1. `authorizer.py`: add `held_projections` (Decision 7).
2. `event_visibility.py`: `event_visible_to` and `visible_event_ids` (Decision 7).
3. `operations.py`: `read_status(*, campaign_id, reader, original, operation_id, epoch)` and `abandon(*, campaign_id, reader, original, operation_id)`, per Decisions 3-7. Neither takes a `session`: each opens `runtime.database().transaction()` itself, issues `SET LOCAL lock_timeout` (PostgreSQL) and then `lock_stream` first, and calls `visible_event_ids` with the authorizer from `load`.
4. The four routes and policy entries (Decisions 1-2).

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
- [ ] A receipt with `applied_status = pending` reads as `committed`.
- [ ] Finality without an authority row: `test_revoked_original_no_authority_row_gm_reads_absent_final`, `test_revoked_original_no_authority_row_replacement_reads_absent_final` (both echo the request epoch), `test_valid_original_token_no_authority_row_stays_pending`, `test_file_only_revocation_stays_pending` (Option A; a seeded file record, no skip), `test_abandon_without_authority_row_then_read_returns_abandoned`; an unauthorized read with no authority row is 404. Authority `file`: `test_revoked_original_authority_file_reads_absent_final`, and a stale epoch gives `absent_final`.
- [ ] Event-ID projector (`test_event_visibility.py`): `test_held_projections_per_role` (owner, editor, viewer, party, member, QM, GM, admin, token, disabled user, non-member); `test_held_projections_encounter_player_only_with_encounter_id`; `test_gm_sees_all_event_ids`; `test_owner_sees_full_and_redacted_ids`; `test_unlisted_projection_is_omitted`; `test_member_projection_event_visible_to_unassigned_player`; `test_null_visibility_map_gm_only`; `test_hidden_actor_event_omitted_for_encounter_player`; `test_later_reveal_does_not_expose_old_event` (commit a hidden event, then a reveal event, then read: only the reveal id); `test_demoted_reader_loses_owner_projection` (assignment removed before the read: the event is omitted and the receipt state is still returned); `test_token_reader_uses_gm_integration`; `test_missing_event_row_is_server_error`; `test_order_preserved`.
- [ ] `read_status` and `abandon` accept no `session` and no `project` argument (their signatures are exactly the keyword-only ones in Approach step 3), open their own `runtime.database().transaction()`, and call `visible_event_ids` with the authorizer returned by `load` (signature check plus spy).
- [ ] Token mount: a status read with the campaign not the active table returns the receipt or `absent_*`, not 409; existing token routes still answer 409 in that case.
- [ ] Authorization uses `authorizer.load` inside the stream-locked transaction (spy: called after `lock_stream`, before the receipt lookup; `flask.g` user and campaign caches poisoned in the test change nothing). `held_projections` and `visible_event_ids` use that transaction's session.
- [ ] Abandon: inserts `abandoned` once; a second abandon answers from that receipt; a later arrival of the original, under `db` authority at the current epoch, gets `409 abandoned` whatever its digest; with no authority row it gets `503 authority_unavailable` and nothing is written; abandon of a committed op answers from its receipt; an unauthorized abandon is 404 and writes nothing.
- [ ] PostgreSQL (CI): a read blocks while X holds the stream lock, then returns X's receipt (or `absent_pending` if X rolled back); a read that takes the lock first returns `absent_pending` and X waits; a fault-injected commit of X followed by a revocation or epoch bump between the read's receipt lookup and its epoch/token read never yields `absent_final`; with no authority row, a read that takes the lock first answers `absent_pending` while a concurrent revoke waits, the next read after the revoke commits answers `absent_final`, and with the order reversed (revoke first) the read blocks and then answers `absent_final`; with `STREAM_LOCK_TIMEOUT_MS` lowered by monkeypatch, a contended read answers `503 temporarily_unavailable`.
- [ ] Route policy coverage tests pass with the four endpoints declared.
- [ ] `python -m pytest -q tests/persistence/test_operation_status.py tests/persistence/test_event_visibility.py tests/persistence/test_command_authorizer.py tests/persistence/test_command_executor.py tests/test_obsidian_sync.py tests/test_route_policy_inventory.py tests/test_access_policy_unit.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_operation_status_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: sessions, folder form and backfill (PE-14e); client-side binding, quarantine, status-read handling and the client cases (PE-15, PE-17/18, PE-18b, PE-41 and later writers; PE-60 for the plugin, C:339-345, C:1097); the plugin's quarantine list (PE-60); token storage, the lazy `tokens.json` import, the mirror and `repair_token_mirror` (PE-14c); the token rollback export gate (PE-14g).
- Record and result projections (`revisions`, `result`, `conflicts`, `current`) and redacted event bodies: PE-31 and adapters (PE-15 onward). PE-31 imports `event_visible_to` and does not edit `event_visibility.py`.
- In `core/commands/authorizer.py`, change nothing but the additive module-level function `held_projections`.
- Do not change `token_required`, `process_command` or `runtime.json` handling.

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

1. **R1-9 (P2): evaluate permanent token revocation without an authority row.**
   Decision 4.3 and Acceptance force `absent_pending` when the authority row
   is missing, before evaluating the original token's committed revocation.
   C:275 makes that revocation an independent sufficient condition for
   `absent_final`. PE-14c/d intentionally support SQL token records in file/
   no-authority campaigns, and the GM or recorded replacement can read them.
   After authorization and receipt lookup, a missing authority row should
   disable only epoch-based finality, not revocation-based finality. Test GM
   and replacement readers of a revoked original with no authority row, plus
   a still-valid original that remains pending. Correct the rationale that
   no receipt can exist yet: Decision 5 correctly allows abandon to create one.
2. **R1-10 (P2): give the mounted routes a real projection provider.**
   The dependency/Decision 4/Approach refer to PE-14b's `project` callback, but
   that task only accepts an injected callback and explicitly leaves real
   projections to PE-31/adapters. No declared prerequisite provides one for
   these live HTTP mounts, and the Approach never assigns its implementation.
   Name a concrete provider/dependency, or assign a small event-ID projector
   here with an explicit interface and allowed file; otherwise keep the mounts
   inactive until the provider exists. Test committed event visibility plus
   current reader authorization, including hidden and later-revealed events.
   An identity callback, test stub, or unconditional empty list is not an
   implementation of the promised projected receipt result. Reconcile the
   dependency cells/parallel window if the chosen provider requires PE-31.

The stream-locked observation, authorization before receipt lookup, stale-epoch
recovery, token boundary without the active-table gate, and serialized abandon
otherwise match the accepted contract. The budget is plausible if the projector
is kept bounded; update it if responsibilities grow. Docs only; no product
tests ran. Review edits are uncommitted.

### Brief revision 1 (Claude, 2026-11-14)

1. **R1-9 (revocation finality without an authority row).** Decision 4.3 now checks the original token's SQL revocation first, under any authority and with no authority row; then the epoch rule, which alone needs an authority row; then `absent_pending`. The wrong rationale ("no receipt can exist yet") is gone: abandon can write one, and 4.2 answers from it. A token with no SQL row or a file-only revocation stays `absent_pending`, which is not final, and abandon resolves it. Context bullet 4 says only SQL `revoked_at` gives finality and cites PE-14c's never-cleared invariant and PE-14g as the rollback gate. The no-authority acceptance bullet is replaced by the named finality tests, and the PostgreSQL bullet adds the three orderings for a revoke racing a no-authority read (the revoke waits on the stream lock, so it can never commit under a read that holds it).
2. **R1-10 (a real projection provider).** New Decision 7: this task owns the event-ID projector. It adds `held_projections` to `core/commands/authorizer.py` as an additive module-level function, `held_projections(authorizer, *, subjects, encounter_id) -> frozenset[str]`, not a method (PE-14c's class is unchanged, to keep PE-14c inside its stop line) and a new `core/commands/event_visibility.py` with `event_visible_to` and `visible_event_ids`. The rules include `member`; a NULL or non-object visibility map is gm-only; a missing event row is `500 server_error`. `read_status` and `abandon` drop `project`, with no stub path. Depends-on no longer names PE-14b's `project` callback; Followed-by names PE-31, which imports `event_visible_to` and does not edit it. Allowed files, Approach, Acceptance (projector tests, including hidden and later-revealed events) and Out of scope are updated.
3. **D2 note.** Decision 3 now says `operations.py` parses the query's `kind:id` itself and never calls PE-14a's `principal_key` (`core/commands/envelope.py`).
4. **Late original after a no-authority abandon.** Decision 5 and the Abandon acceptance bullet now say the late original passes PE-14b's fence before its receipt lookup: `409 abandoned` under `db` authority at the current epoch, `503 authority_unavailable` with nothing written while there is no authority row.
5. **Budget.** Was about 300 product + 450 test, stop 400. Now about 400 + 705, stop 480 (product 300+60+3+35 = 398; test 450+170+25+60 = 705, above the decisions table's 650): the projector and finality reorder (+60 product, +170 test), `member` and the NULL map (+3, +25), and `held_projections` moved here from PE-14c (+35, +60), rounded.
6. **Who opens the transaction.** `read_status` and `abandon` drop the `session` parameter and open `runtime.database().transaction()` themselves, issuing `SET LOCAL lock_timeout` (PostgreSQL) then `lock_stream` first (Decision 4, Approach step 3); the acceptance signature check now requires no `session`.
7. **Budget arithmetic.** The test figure is now about 705 (the sum of item 5's parts), not 650; this departs from the decisions table. The 480 product stop line is unchanged.

Judgments for Codex to challenge:
- `encounter_player` is held by any player member when the event's committed `encounter_id` is not null. The contract does not define its holder. It can over-include a player absent from that encounter, but only for ids the command type marked visible to `encounter_player`.
- `held_projections` is a module-level function in PE-14c's module, written by this task, taking the authorizer as its first argument; PE-14c's class is not changed. The alternative is to keep it in `event_visibility.py` reading the authorizer's loaded fields, which couples PE-14f's module to PE-14c's private state.
- Revocation finality echoes the request epoch even when there is no authority row to check it against.
- "Valid" for an original user means enabled and a campaign member (unchanged from Decision 4.1).
