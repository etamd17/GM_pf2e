# PE-14e: Session start/end commands, folder form and session-history backfill

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14d)
- **Branch:** `claude/pe-14e-sessions-backfill` from current main after PE-14d merges. Record the exact starting SHA.
- **Depends on:** [PE-14d](PE-14d-stream-locked-authorization-writes.md) (redeem_invite and the other authorization writers take the stream lock, which the deadlock test below needs). May run in parallel with [PE-14f](PE-14f-status-read-abandon.md); the two share no files.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: `session_id` format (C:26); check 6 for `session.start` (C:118-120); sessions, `session.start`/`session.end`, legacy history (C:471-507); never inside a transaction (C:455-458); activation preconditions, last one: no `live` legacy `current_session` (C:665); `migrating → db` needs the backfill complete or accepted (C:666); round trip keeps `campaign_sessions` and backfills (C:683); PE-20 case B, `s-file-era-01` refused after re-import (C:760-764); PF2e slot (C:808-812, C:816-818); "live" excludes the process slot (C:842-844); F4 lossless ids and boundary cases (C:613-617); F6 (C:623); PE-14 row (C:1090); event type names are each task's (C:1102).
  - Schema: `CampaignSession` `core/persistence/models.py:778-814` (`session_id` String(128) :788, nullable `started_at` for backfill :790-793, `origin` String(32) with no CHECK :795, `uq_campaign_sessions_one_active` :807-813); `Campaign.system` :118 (values `SYSTEMS` :99).
  - Legacy sessions: `services/obsidian_sync.py:689-714` (start: ids `session-YYYY-MM-DD-xxxxxxxx`, any string cut at 120 :693-695; end sets `awaiting_processing` :712), each event carries `session_id` :753, routes :785-799. `core/obsidian_sync.py`: `runtime_file` :44, `events_file` :48, `campaign_lock` :52, `default_runtime` :150-163 (`current_session`; no `used_session_ids` on main), `load_runtime` :166-180 (swallows decode errors through `core/storage.py:281-286` `load_json`), `read_events` :276-296 (skips undecodable lines at :288-289). `core/storage.py:289-292` `load_json_strict` keeps decode errors.
  - Folder mapping: `tools/obsidian-plugin/main.js:48-54` `safeSegment`.
  - Advisory lock: `core/persistence/identity.py:26-36` (`BEGIN IMMEDIATE` on SQLite, `pg_advisory_xact_lock(73420401)` for account creation on PostgreSQL :32-34).
  - Sibling interfaces: PE-14a `lock_stream`, `CommandType` (`lock_plan`, terminal predicate, allowed principals); PE-14b `execute(...)`, `ctx.emit`; PE-13b `CommandRefusal`, check-9 adapter; PE-14c `authorizer.load`.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 300 product lines and 400 test lines. Stop and report past about 400 product lines. (Planning estimate; split from the earlier combined PE-14e, whose status read and abandon are now PE-14f.)

## Goal

Durable, db-authority `session.start` and `session.end` command types with lossless ids, an exact Python port of the plugin's folder mapping for collision checks, and the fail-closed routine that backfills file-era session ids for PE-24. No route is added: commands are exercised through PE-14b's `execute`; PE-15 adds the HTTP command route.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Folder form is an exact port of `safeSegment`** (`main.js:48-54`), in `core/commands/folder_form.py`:
   - `s = value or 'unknown'` (JS `String(value || 'unknown')`);
   - replace each character of `[\/:*?"<>|#^[\]]` with `-`;
   - collapse each run of JS-whitespace characters to one space, then strip JS whitespace from both ends. JS whitespace is the literal set `\t \n \v \f \r U+0020 U+00A0 U+1680 U+2000-U+200A U+2028 U+2029 U+202F U+205F U+3000 U+FEFF`. Python's `\s` and `str.strip()` must not be used: they also match U+001C-U+001F and U+0085 and miss U+FEFF;
   - cut to 100 UTF-16 code units: encode `utf-16-le`, keep the first 200 bytes, decode with `errors='ignore'`. Where an astral character straddles unit 100, JS leaves a lone high surrogate and the port drops it (a recorded difference; a lone surrogate is not a usable folder name). No strip after the cut, as in JS;
   - an empty result becomes `unknown`.
   A new-form id's folder form is itself (C:484). Collision test: `folder_form(a).casefold() == folder_form(b).casefold()`.
2. **`session.start`** (subject: the campaign; principals: GM, or a token whose scope includes the type, through PE-14c):
   - id format `[a-z0-9][a-z0-9_-]{7,99}` (C:26). A malformed id is `422 validation_failed` at check 9, with a receipt. Judgment: C:482 names no code, and PE-14a's check 1 has no payload validator, so a check-1 `400` would need a registry change outside this task. Record it in the report.
   - check 6 (terminal predicate): a row with the same id, or with a case-folded equal folder form, → `409 already_finalized` (C:118-120, C:482-484).
   - check 9: another `active` row in this campaign → `422 rule_violation`; for a PF2e campaign, the slot check (decision 3) → `422 rule_violation`.
   - apply: insert `status = active`, `started_at`, `started_by` = principal id, `origin = command`; emit `session.started` through `ctx.emit` (C:488).
3. **PF2e slot check** (C:486-487, C:810-812). For a campaign with `system = 'pf2e'`, after the stream lock (taken by the executor), take a transaction-scoped advisory lock with a new constant `PF2E_SESSION_SLOT_LOCK_KEY`, distinct from 73420401 and declared beside it in `core/persistence/identity.py` (the existing literal becomes `ACCOUNT_CREATION_LOCK_KEY` with its value unchanged). Then refuse with `422 rule_violation` if any other `pf2e` campaign has an `active` `campaign_sessions` row. Lock order: stream row, then slot key; no transaction that holds the slot key takes another stream row. On SQLite the single-writer lock already serializes; no key is taken. It never reads the process-global slot (C:843-844) and never writes another campaign (C:817-818). Only `campaign_sessions` rows count; a file-authority campaign's `runtime.json` session is not visible here (C:474-476; judgment, record it). **Taking** the slot (rebuilding `PARTY_LIBRARY` and encounter caches from committed rows, C:809-812) happens after commit and is a PE-17/18 follow-up; this task never touches the process slot.
4. **`session.end`** (same principals). Subject = `{session_id}` of a `campaign_sessions` row. Missing, or hidden from the caller → `404 not_found` (check 5). `core/commands/sessions.py` registers a `session` resolver: visible to the GM and to a token of this campaign; hidden from everyone else. This is a judgment, because C has no session row; record it. `status = ended` → `409 already_finalized` (check 6). Then, at check 9, the open-actions hook: open actions → `422 rule_violation` naming them (C:489-492). With a GM `override` (check 4 refuses a non-GM override, PE-13b), the hook's `cancel_all` runs in the same transaction first. Apply: set `ended`, `ended_at`, `ended_by`; emit `session.ended`.
5. **Open-actions hook is a registry with an explicit default.** `core/commands/sessions.py` holds one registered provider with `open_actions(ctx) -> list`, `lock(ctx)` (lock-order step 3 for the rows it reads) and `cancel_all(ctx)`. The shipped provider is named `NoActionTable`: it returns `[]` and locks nothing, and the report records "no action table yet". PE-30's follow-on registers the real one. A missing provider fails at registry construction, never passes silently.
6. **Session rows are guarded by the stream row.** Every writer of a campaign's `campaign_sessions` rows (`session.start`, `session.end`, the backfill) holds that campaign's stream lock, so the check-6 and check-9 reads under the stream lock are stable without row locks. Neither type's `lock_plan` takes a step 2-5 lock except the hook's step 3; `campaign_sessions` registers no step-5 position. Each type's `lock_plan` reads its subject (the campaign, or the `campaign_sessions` row) into `ctx.records` without a row lock. Judgment; record it.
7. **Backfill in two halves.** Reading files is never done inside a transaction, and no process lock may be held with a stream row (C:455-458):
   - `read_session_history(campaign_id) -> LegacyHistory`, outside any transaction: under `campaign_lock` it reads `runtime.json` with `load_json_strict` (a missing file is empty, not partial; an undecodable file is one unreadable `(file, None)`) and `events.jsonl` line by line (an undecodable line, or a non-string `session_id`, is an unreadable `(file, line)`). It must not use `load_runtime` or `read_events`, which hide decode errors. Sources: `current_session.id`, `used_session_ids` where present, and each event's `session_id`. A field absent from main's format is not an incomplete read (C:499-500).
   - `backfill_session_history(session, campaign_id, history, accepted) -> BackfillReport`, in the caller's transaction: calls `lock_stream` first, then inserts each new id as `status = ended`, `origin = file_backfill`, times NULL. An id already present is skipped. Listed as collisions: a file-era id that reuses or folder-collides with an existing row, and legacy ids whose folder forms collide with each other (C:500-502). `accepted` is a set of `(file, line)` items and collision items; each accepted item is recorded as quarantined in the report (PE-24 persists it in the run's `details`). `complete` is true only when every unreadable item and every collision is accepted; it is never true on a partial read (C:503-506).
   - A `live` legacy `current_session` is reported, not inserted as active: activation is refused while one exists (C:665). Legacy `awaiting_processing` maps to `ended`. PE-24 calls both halves and decides activation.
8. **Event names** (C:1102): `session.started` and `session.ended`.

## Approach

Allowed files:
- new `core/commands/sessions.py`, `core/commands/session_history.py`, `core/commands/folder_form.py`; `core/commands/__init__.py` only if absent, with exactly this content: `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline (the same text in every brief that may create it, so parallel branches do not conflict)
- `core/persistence/identity.py` (the two lock-key constants only; behaviour unchanged)
- new `tests/persistence/test_session_commands.py`, `tests/persistence/test_session_history_backfill.py`, `tests/persistence/test_folder_form.py`, `tests/persistence/test_session_postgresql.py` (copy the `postgres_database` fixture use from `tests/persistence/test_workflow_postgresql.py:22-24`)

1. `folder_form.py` per decision 1, with golden vectors generated once by running `main.js`'s `safeSegment` under node and checked in as literals (record the command in the report; CI needs no node).
2. `sessions.py`: the two `CommandType`s, their terminal predicates, `lock_plan`s and check-9 rules (decisions 2-6), registered in PE-14a's registry; refusals are PE-13b `CommandRefusal`s.
3. `session_history.py` per decision 7.
4. Lock-key constants in `identity.py`; the existing account-creation call uses the named constant.

## Acceptance

- [ ] Folder form: golden vectors match `safeSegment` for `""` and `None` (`unknown`), every rewritten character, `:` and `.`, a leading space, mixed whitespace runs, a U+FEFF run, U+001F (kept, not collapsed), a 120-character legacy id, and an astral character straddling unit 100 (the port's output equals the JS output minus its trailing lone surrogate; asserted and commented).
- [ ] Session ids: 8- and 100-character new-form ids accepted; uppercase, 7-character, 101-character and leading-`_` ids give `422 validation_failed` with a receipt and no row. F4 cases (C:616-617) collide or not exactly as decision 1 says: legacy `a:b…` vs new `a-b…` collide; `.` is not rewritten; a case-only pair collides; a legacy id with a leading space collides with its trimmed form; a legacy id over 100 characters collides with its 100-unit prefix.
- [ ] Reused id and folder-form collision give `409 already_finalized` with a receipt; a second active session gives `422 rule_violation`; after backfilling `s-file-era-01`, `session.start("s-file-era-01")` gives `409 already_finalized` (C:760-764).
- [ ] PF2e: a PF2e campaign starting while another PF2e campaign has an active row gives `422 rule_violation`; a D&D campaign is unaffected; `PF2E_SESSION_SLOT_LOCK_KEY != ACCOUNT_CREATION_LOCK_KEY`; nothing reads or writes the process slot (spy).
- [ ] `session.end`: missing or hidden id → 404; ended → 409 `already_finalized`; with a registered test provider reporting an open action → `422 rule_violation` naming it; with a GM override it cancels then ends in one transaction; a non-GM override → 403 (PE-13b's check 4); the default provider is `NoActionTable`, and constructing the registry with none raises.
- [ ] Backfill: inserts `ended` rows from `current_session`, `used_session_ids` and `events.jsonl`; skips existing ids; lists collisions with rows and among legacy ids; a missing optional field is not partial; an undecodable `runtime.json` and an undecodable `events.jsonl` line each make `complete` false; the same with every item accepted gives `complete` true and lists them as quarantined; a `live` `current_session` is reported, not inserted active; the reader takes no lock while a stream row is held (it runs before the transaction); the writer calls `lock_stream` first (spy).
- [ ] PostgreSQL (CI): two PF2e campaigns start sessions concurrently, exactly one succeeds; `session.start` and an account-creating `redeem_invite` on the same campaign run concurrently and both finish.
- [ ] `python -m pytest -q tests/persistence/test_folder_form.py tests/persistence/test_session_commands.py tests/persistence/test_session_history_backfill.py tests/persistence/test_command_executor.py tests/persistence/test_identity_runtime.py tests/test_obsidian_sync.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_session_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Legacy `/sessions/start` and `/sessions/end` behave exactly as before for file-authority campaigns; authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: status read and abandon (PE-14f); calling the backfill and refusing activation (PE-24); taking the PF2e slot after `session.start` (PE-17/18, C:810-811, C:1091); legacy route refusal for non-file campaigns (activation gate / PE-60, C:383-386); the action table and its real open-actions provider (PE-30 follow-on); the HTTP command route (PE-15); session UI.
- No route, no `app.py`, no `core/route_policy.py`, no `services/obsidian_sync.py`. Do not change `process_command` or `runtime.json` handling.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
