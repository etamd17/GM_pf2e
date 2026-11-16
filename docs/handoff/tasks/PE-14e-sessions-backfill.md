# PE-14e: Session start/end commands, folder form and session-history backfill

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14d); brief accepted (Codex brief review 2, 2026-11-14)
- **Branch:** `claude/pe-14e-sessions-backfill` from current main after PE-14d merges. Record the exact starting SHA.
- **Depends on:** [PE-14d](PE-14d-stream-locked-authorization-writes.md) (redeem_invite and the other authorization writers take the stream lock, which the deadlock test below needs). May run in parallel with [PE-14f](PE-14f-status-read-abandon.md); the two share no files.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: `session_id` format (C:26); check 6 for `session.start` (C:118-120); override accepted only from the GM or a scoped token, never bypassing checks 1-8 (C:137-140); sessions, `session.start`/`session.end`, legacy history (C:471-507), including "With a GM `override`" for the cancelling end (C:489-492) and the complete-versus-accepted read (C:497-507); never inside a transaction (C:455-458); encounter close, also "With a GM `override`" (C:563); mirror identity keys on `session_id` (C:606-609); activation preconditions, last one: no `live` legacy `current_session` (C:665); `migrating → db` needs the backfill complete or accepted (C:666); round trip keeps `campaign_sessions` and backfills (C:683); PE-20 case B, `s-file-era-01` refused after re-import (C:760-764); PF2e slot (C:808-812, C:816-818); "live" excludes the process slot (C:842-844); GM includes the site admin (C:847); "rules override" row, token "if scoped" (C:876); F4 lossless ids and boundary cases (C:613-617); F6 (C:623); PE-14 row (C:1090); event type names are each task's (C:1102).
  - Schema: `CampaignSession` `core/persistence/models.py:778-814` (`session_id` String(128) :788, nullable `started_at` for backfill :790-793, `origin` String(32) with no CHECK :795, `uq_campaign_sessions_one_active` :807-813); `Campaign.system` :118 (values `SYSTEMS` :99).
  - Legacy sessions: `services/obsidian_sync.py:689-714` (start: ids `session-YYYY-MM-DD-xxxxxxxx`, any string cut at 120 :693-695; end sets `awaiting_processing` :712), each event carries `session_id` :753 and keeps the ended id after an end, because `current_session` stays set (:746-753), routes :785-799. `core/obsidian_sync.py`: `runtime_file` :44, `events_file` :48, `campaign_lock` :52, `default_runtime` :150-163 (`current_session: None` :157; no `used_session_ids` on main), `append_player_request` writes `session_id: None` outside a session (:230-235), `load_runtime` :166-180 (swallows decode errors through `core/storage.py:281-286` `load_json`), `read_events` :276-296 (skips undecodable lines at :288-289). `core/storage.py:289-292` `load_json_strict` keeps decode errors. `tests/test_obsidian_sync.py:179-207` runs an ordinary command with no session, so main's history legitimately holds null ids.
  - Folder mapping: `tools/obsidian-plugin/main.js:48-54` `safeSegment`.
  - Advisory lock: `core/persistence/identity.py:26-36` (`BEGIN IMMEDIATE` on SQLite, `pg_advisory_xact_lock(73420401)` for account creation on PostgreSQL :32-34).
  - Sibling interfaces: PE-13a `CommandLocks` (`register_serialized`, `STREAM_SERIALIZED_KINDS` = campaign, session, grant); PE-13b `CheckContext` (`session_id`, `bind_started_session`), `CommandRefusal`, check-9 adapter; PE-14a `lock_stream`, `CommandType` (`lock_plan`, terminal predicate, `allowed_principals`, `override_gm_only`, `derives_rules`, `rules_adapter` with `check(ctx, override)` and `build_edition_matches(subject_record, pin)`, required for every type); PE-14b `execute(...)`, `ctx.emit`, the `ctx.session_id` snapshot and `ctx.bind_started_session` (`events.py`, `SessionBindError`), and the `core/commands` import guard (AST ban list plus a subprocess `sys.modules` check); PE-14c `authorizer.load`, `override_allowed`, `campaign_role_allows` (with its explicit session rule, the "PE-14c campaign_role_allows session rule") and `register_visibility(kind, resolver)` (with its session visibility resolver rule); PE-13a `CampaignPinRepository.pin` read by PE-14b (Decision 14), which answers `503 authority_unavailable` for a campaign with no pin.
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 355 product lines and 600 test lines. Stop and report past about 430 product lines. (Planning estimate. Brief revision 1 raised it from 300/400 with a 400 stop line, for the module split, the parse table, the report fields and the override tests.)

## Goal

Durable, db-authority `session.start` and `session.end` command types with lossless ids, an exact Python port of the plugin's folder mapping for collision checks, and the fail-closed routine that backfills file-era session ids for PE-24: a file reader outside `core/commands` and a database half inside it. No route is added: commands are exercised through PE-14b's `execute`; PE-15 adds the HTTP command route.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Folder form is an exact port of `safeSegment`** (`main.js:48-54`), in `core/commands/folder_form.py`:
   - `s = value or 'unknown'` (JS `String(value || 'unknown')`);
   - replace each character of `[\/:*?"<>|#^[\]]` with `-`;
   - collapse each run of JS-whitespace characters to one space, then strip JS whitespace from both ends. JS whitespace is the literal set `\t \n \v \f \r U+0020 U+00A0 U+1680 U+2000-U+200A U+2028 U+2029 U+202F U+205F U+3000 U+FEFF`. Python's `\s` and `str.strip()` must not be used: they also match U+001C-U+001F and U+0085 and miss U+FEFF;
   - cut to 100 UTF-16 code units: encode `utf-16-le`, keep the first 200 bytes, decode with `errors='ignore'`. Where an astral character straddles unit 100, JS leaves a lone high surrogate and the port drops it (a recorded difference; a lone surrogate is not a usable folder name). No strip after the cut, as in JS;
   - an empty result becomes `unknown`.
   A new-form id's folder form is itself (C:484). Collision test: `folder_form(a).casefold() == folder_form(b).casefold()`.
2. **`session.start`** (subject: the campaign; `allowed_principals = {USER, INTEGRATION_TOKEN}`; check 4 role: the PE-14c campaign_role_allows session rule (GM or site admin, or a token of this campaign whose scope includes the type); this task does not define it):
   - registration shape (PE-14a): subject kind `campaign` (`subject = {campaign_id}`); dependency set empty; payload id fields none: the new `session_id` is a value that must not exist yet, checked by check 6's terminal predicate and the check-9 adapter (PE-13b check 6), never a check-5 record id; `requires_expected=False`.
   - id format `[a-z0-9][a-z0-9_-]{7,99}` (C:26). A malformed id is `422 validation_failed` at check 9, with a receipt. Judgment: C:482 names no code, and PE-14a's check 1 has no payload validator, so a check-1 `400` would need a registry change outside this task. Record it in the report.
   - check 6 (terminal predicate): a row with the same id, or with a case-folded equal folder form, → `409 already_finalized` (C:118-120, C:482-484). Backfilled `file_backfill` rows count.
   - check 9: another `active` row in this campaign → `422 rule_violation`; for a PF2e campaign, the slot check (decision 3) → `422 rule_violation`.
   - registration: `derives_rules=False`, and a `rules_adapter` whose `check(ctx, override)` holds the check-9 rules above in this order: id format, second active row, PF2e slot. Its `build_edition_matches` returns True, since the subject is a campaign, not a character; with `derives_rules=False` check 8 never depends on it.
   - apply, in order: insert `status = active`, `started_at`, `started_by` = principal id, `origin = command`; call `ctx.bind_started_session(id)` (PE-14b); emit `session.started` through `ctx.emit` (C:488). The event carries the new id. The executor's snapshot found no active row, so without the bind the event would carry NULL.
3. **PF2e slot check** (C:486-487, C:810-812). For a campaign with `system = 'pf2e'`, after the stream lock (taken by the executor), take a transaction-scoped advisory lock with a new constant `PF2E_SESSION_SLOT_LOCK_KEY`, distinct from 73420401 and declared beside it in `core/persistence/identity.py` (the existing literal becomes `ACCOUNT_CREATION_LOCK_KEY` with its value unchanged). Then refuse with `422 rule_violation` if any other `pf2e` campaign has an `active` `campaign_sessions` row. Lock order: stream row, then slot key; no transaction that holds the slot key takes another stream row. On SQLite the single-writer lock already serializes; no key is taken. It never reads the process-global slot (C:843-844) and never writes another campaign (C:817-818). Only `campaign_sessions` rows count; a file-authority campaign's `runtime.json` session is not visible here (C:474-476; judgment, record it). **Taking** the slot (rebuilding `PARTY_LIBRARY` and encounter caches from committed rows, C:809-812) happens after commit and is a PE-17/18 follow-up; this task never touches the process slot.
4. **`session.end`** (`allowed_principals = {USER, INTEGRATION_TOKEN}`; registered with `override_gm_only=True`). Subject = `{session_id}` of a `campaign_sessions` row. Check 4 role: the PE-14c campaign_role_allows session rule, as for `session.start`.
   - registration shape (PE-14a): subject kind `session` (`subject = {session_id}`); dependency set empty; no payload id fields (`override` is an envelope field, C:237, not payload); `requires_expected=False`.
   - Check 4, override: only the GM or the site admin may send `override` on `session.end` (C:490 "With a GM `override`", C:847). Because the registered `CommandType` has `override_gm_only=True` (PE-14a), PE-14c's `override_allowed` refuses a token's override with `403 not_permitted`, even one scoped `"override:session.end"`, before checks 5-9. This narrows C:876's general "if scoped" rule for this one type. Judgment; record it. Consequence, also recorded: the GM's Obsidian pane is token-authenticated (C:849-852), so it cannot do the one-step cancel-and-end; the GM does that from a GM user session.
   - Check 5: missing, or hidden from the caller → `404 not_found`. `core/commands/sessions.py` calls PE-14c's `register_visibility("session", resolver)` at import time. The resolver implements PE-14c's session visibility resolver rule: visible to the GM or site admin (C:847) and to a token of this campaign; hidden from everyone else. It reads only the authorizer attributes PE-14c names for that rule. This is a judgment, because C has no session row; record it.
   - Check 6: `status = ended` → `409 already_finalized`.
   - Check 9, the open-actions hook (decision 5): open actions and no `override` → `422 rule_violation` naming them, with nothing changed (C:489-492). With an `override` the hook passes. It does not re-check the principal: an override that reaches check 9 already passed check 4.
   - Registration: `derives_rules=False`, and a `rules_adapter` whose `check(ctx, override)` is the open-actions hook above. Its `build_edition_matches` returns True, since the subject is a session row, not a character; with `derives_rules=False` check 8 never depends on it.
   - Apply, in order: assert `subject.session_id == ctx.session_id` (PE-14b's snapshot; check 6 already refused an ended subject, so the subject is the active row); with an `override`, the provider's `cancel_all(ctx)`, whose `action.cancelled` events carry `ctx.session_id`; set `ended`, `ended_at`, `ended_by`; emit `session.ended`, which carries the ending id from the snapshot.
   - A token scoped `session.end` without override ends a session that has no open actions.
   - Events from later commands carry NULL `session_id`, because no row is active at their snapshot. Legacy keeps tagging the ended id until the next start (`services/obsidian_sync.py:746-753`). This is a recorded semantic change; PE-60's mirror (C:606-609) must expect it.
5. **Open-actions hook is a registry with an explicit default.** `core/commands/sessions.py` holds one registered provider with `open_actions(ctx) -> list`, `lock(ctx)` (lock-order step 3 for the rows it reads) and `cancel_all(ctx)`. `open_actions` runs at check 9; `cancel_all` runs only from `session.end`'s `apply`, never at check 9. The shipped provider is named `NoActionTable`: it returns `[]` and locks nothing, and the report records "no action table yet". PE-30's follow-on registers the real one. A missing provider fails at registry construction, never passes silently.
6. **Session rows are guarded by the stream row.** Every writer of a campaign's `campaign_sessions` rows (`session.start`, `session.end`, the backfill) holds that campaign's stream lock, so the check-6 and check-9 reads under the stream lock are stable without row locks. Neither type's `lock_plan` takes a step 2-5 lock except the hook's step 3; `campaign_sessions` registers no step-5 position. Each type's `lock_plan` registers its subject through `ctx.locks.register_serialized(kind, id, record)`, with no row lock: `session.start` registers `("campaign", campaign_id, ...)`, `session.end` registers `("session", session_id, StateRecord.serialized("session", session_id, payload) or None)`, where `payload` is built from the `campaign_sessions` row read under the stream lock; it never passes the raw ORM row. Both kinds are in PE-13a's `STREAM_SERIALIZED_KINDS`, and `register_serialized` requires `stream()` to have run. The `record` argument is `StateRecord | None`; `None` means no such row in this campaign (check 5, `404`). PE-14b's `ctx.session_id` snapshot reads the active row under the same stream lock, so it agrees with these reads. Judgment; record it.
7. **Backfill in two modules.** Files are never read inside a transaction, and no process lock may be held with a stream row (C:455-458). PE-14b's guard forbids any `core/commands` module from importing `core.obsidian_sync`, so the reader lives outside the package. Imports go one way only.
   - `core/obsidian_session_history.py` (outside `core/commands`) holds only `read_session_history(campaign_id: str) -> LegacyHistory`. It imports `core.obsidian_sync` (`campaign_lock`, `runtime_file`, `events_file`), `core.storage.load_json_strict` and the types from `core.commands.session_history`. It takes no SQLAlchemy session and must be called before any transaction. It must not use `load_runtime` or `read_events`, which hide decode errors.
   - `core/commands/session_history.py` holds the pure types (`LegacyHistory`, `UnreadableItem`, `Collision`, `BackfillReport`) and `backfill_session_history(session, campaign_id, history, accepted) -> BackfillReport`. It does no file I/O, takes no process lock, and imports neither `core.obsidian_sync` nor `core.obsidian_session_history`.

   **Reading** (under `campaign_lock`, both files):
   - `events.jsonl` is read in binary and split on `b"\n"`. Lines are 1-based physical lines. Each line is decoded as strict UTF-8, then `json.loads`.
     - A whitespace-only line is skipped. This includes the empty piece after the final newline.
     - A decode or JSON error, including a truncated last line, gives `UnreadableItem("events.jsonl", n, None, "malformed_json")`.
     - A value that is not an object gives reason `"not_object"`.
     - `session_id` absent or `null`: no session. It is skipped and is not unreadable; this is main's ordinary out-of-session event.
     - `session_id` a `str` of 1-120 characters (main cuts at 120): a legacy id. Judgment: 121-128 characters would fit the `String(128)` column but are refused, matching main's writer.
     - Any other value (number, bool, list, object, `""`, more than 120 characters) gives `"malformed_session_id"`.
   - `runtime.json` is read with `load_json_strict`. A decode error gives `("runtime.json", None, None, "malformed_json")`; a top-level value that is not an object gives `"not_object"`.
     - `current_session` absent or null: no session. Not an object, or `.id` not a valid legacy id: `("runtime.json", None, "current_session", "malformed_session_id")`.
     - `current_session.status == "live"` sets `live_session`. Any other status (main writes `awaiting_processing`) makes its id a candidate `ended` row.
     - `used_session_ids` absent: fine (C:499-500). Present but not a list: `("runtime.json", None, "used_session_ids", "malformed_session_id")`; a bad element `i`: field `"used_session_ids[i]"`, same reason.
   - For either file, a missing file is empty, not partial. Any other `OSError` gives `(file, None, None, "io_error")`.
   - `LegacyHistory(ids, live_session, unreadable, source_sha256)`, with the property `read_complete = not unreadable`. `source_sha256` is the SHA-256 over `runtime.json` then `events.jsonl` as read under the lock; each file contributes a tag (`missing`, or its byte length) followed by its bytes, so a missing file and an empty file differ.

   **Writing** (`backfill_session_history`, in the caller's transaction). It calls `lock_stream(session, campaign_id)` first, then classifies each candidate id against this campaign's rows:
   - Exact match with an `origin = 'file_backfill'` row: `skipped_existing`.
   - Exact match with any other origin: `Collision("row", (id,))` (a file-era start reused a db-era id after a rollback).
   - Case-folded `folder_form` equal to a row with a different id: `Collision("row", sorted ids)`.
   - Case-folded `folder_form` equal among the new candidates: `Collision("legacy", sorted ids)` (C:500-502).
   - A collision whose ids are all existing `file_backfill` rows is not listed again; it was settled when they were inserted. So a re-import after a rollback (C:683) produces no new collisions.

   Keys: `UnreadableItem.key = ("unreadable", file, line, field)`; `Collision.key = ("collision", kind, tuple(sorted(ids)))`. `accepted` is a frozenset of these keys.

   `BackfillReport` fields: `read_complete`, `unreadable`, `collisions`, `quarantined` (items whose key is in `accepted`), `unaccepted` (all others), `live_session`, `source_sha256`, `inserted`, `skipped_existing`, and `gate_satisfied = not unaccepted`. There is no `complete` field. Acceptance never makes `read_complete` true (C:503-506). `gate_satisfied` is the C:666 "complete or accepted" input.

   Inserts happen only when `gate_satisfied and live_session is None`. Then every new candidate id, including ids in accepted collisions, is inserted as `status = ended`, `origin = 'file_backfill'`, times NULL. Otherwise nothing is inserted, and the report is still returned (fail closed). A `live` legacy `current_session` is reported, never inserted, and blocks all inserts: activation is refused while one exists (C:665). The backfill writes no `game_events`. PE-24 calls both halves and decides activation.
8. **Event names** (C:1102): `session.started` and `session.ended`.

## Approach

Allowed files:
- new `core/commands/sessions.py`; `core/commands/registry.py` (append `"core.commands.sessions"` to `COMMAND_MODULES` only, PE-14a); new `core/commands/session_history.py` (types and the database half; no file I/O, no process lock); new `core/commands/folder_form.py`; `core/commands/__init__.py` only if absent, with exactly this content: `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline (the same text in every brief that may create it, so parallel branches do not conflict)
- new `core/obsidian_session_history.py` (the reader only; may import `core.obsidian_sync`, `core.storage` and `core.commands.session_history`)
- `core/persistence/identity.py` (the two lock-key constants only; behaviour unchanged)
- new `tests/persistence/test_session_commands.py`, `tests/persistence/test_session_history_backfill.py` (reader and database-half tests), `tests/persistence/test_folder_form.py`, `tests/persistence/test_session_postgresql.py` (copy the `postgres_database` fixture use from `tests/persistence/test_workflow_postgresql.py:22-24`). Fixture rule for both command test files: every campaign has a `db` `campaign_state_authority` row and a `campaigns.settings["rules"]` pin (`{profile, content}`, C:31-33) matching its `system` (PF2e for the `session.start`/`session.end` and slot cases, C:37; the D&D control campaign gets a D&D pin), and every envelope's `rules` and `epoch` match that pin and the row's `stream_epoch` (C:102-103, C:122-123; PE-14b Decision 14). Tests pass `execute` a test `content_descriptor` equal to the pin (check 8's `content_mismatch` is skipped anyway, since both types have `derives_rules=False`) and a pass-through test `project` that returns its input; both exist in tests only

1. `folder_form.py` per decision 1, with golden vectors generated once by running `main.js`'s `safeSegment` under node and checked in as literals (record the command in the report; CI needs no node).
2. `sessions.py`: the two `CommandType`s with `allowed_principals = {USER, INTEGRATION_TOKEN}` and, for `session.end`, `override_gm_only=True`; both with `derives_rules=False` and a `rules_adapter` (PE-14a requires one; PE-14b builds PE-13b's check runner from it) whose `check(ctx, override)` holds that type's check-9 rules and whose `build_edition_matches` returns True; their terminal predicates, `lock_plan`s (`register_serialized`) and `apply` bodies (decisions 2-6); the open-actions provider registry with `NoActionTable`. Registered in PE-14a's registry; refusals are PE-13b `CommandRefusal`s.
3. `core/commands/session_history.py`: the types and `backfill_session_history` per decision 7.
4. `core/obsidian_session_history.py`: `read_session_history` per decision 7.
5. Lock-key constants in `identity.py`; the existing account-creation call uses the named constant.

## Acceptance

- [ ] Folder form: golden vectors match `safeSegment` for `""` and `None` (`unknown`), every rewritten character, `:` and `.`, a leading space, mixed whitespace runs, a U+FEFF run, U+001F (kept, not collapsed), a 120-character legacy id, and an astral character straddling unit 100 (the port's output equals the JS output minus its trailing lone surrogate; asserted and commented).
- [ ] Session ids: 8- and 100-character new-form ids accepted; uppercase, 7-character, 101-character and leading-`_` ids give `422 validation_failed` with a receipt and no row. F4 cases (C:616-617) collide or not exactly as decision 1 says: legacy `a:b…` vs new `a-b…` collide; `.` is not rewritten; a case-only pair collides; a legacy id with a leading space collides with its trimmed form; a legacy id over 100 characters collides with its 100-unit prefix.
- [ ] Reused id and folder-form collision give `409 already_finalized` with a receipt; a second active session gives `422 rule_violation`; after backfilling `s-file-era-01`, `session.start("s-file-era-01")` gives `409 already_finalized` (C:760-764).
- [ ] PF2e: a PF2e campaign starting while another PF2e campaign has an active row gives `422 rule_violation`; a D&D campaign is unaffected; `PF2E_SESSION_SLOT_LOCK_KEY != ACCOUNT_CREATION_LOCK_KEY`; nothing reads or writes the process slot (spy).
- [ ] Registration: both types have `allowed_principals == {USER, INTEGRATION_TOKEN}`; `test_session_end_override_policy_registered_with_command_type` asserts the registered `session.end` has `override_gm_only` true and `session.start` has it false. `test_session_types_register_rules_adapter`: both types have `derives_rules` false and a non-None `rules_adapter` whose `build_edition_matches` returns True; calling `session.start`'s `check` directly with a second active row raises `422 rule_violation`, and `session.end`'s `check` with a test provider reporting an open action and no override raises `422 rule_violation`.
- [ ] `session.end`, all through `execute`: missing or hidden id → 404; ended → 409 `already_finalized`; the default provider is `NoActionTable`, and constructing the registry with none raises. With a registered test provider reporting one open action:
  - `test_session_end_gm_override_cancels_then_ends` and `test_session_end_site_admin_override_cancels_then_ends`: cancel, then end, in one transaction.
  - `test_session_end_override_scoped_token_refused_403_nothing_cancelled`: a token scoped `["session.end", "override:session.end"]` sending `override` gets `403 not_permitted` at check 4; `cancel_all` is not called, the row stays `active`, and there is no `action.cancelled` or `session.ended` event. The test asserts nothing about receipts (PE-14b's code mapping owns that).
  - `test_session_end_scoped_token_without_override_open_actions_422`: a token scoped `["session.end"]` gets `422 rule_violation` naming the open action, with nothing changed.
  - `test_session_end_scoped_token_no_open_actions_ends`: with the default provider, the same token ends the session.
- [ ] Lifecycle events: `test_lifecycle_events_carry_session_id` (`session.started` has the new id; with a GM override and one open action, `action.cancelled` and `session.ended` both have the ending id, read back from `game_events` after commit); `test_event_after_session_end_has_null_session`.
- [ ] Backfill (`test_session_history_backfill.py`):
  - `test_main_format_history_with_null_ids_is_complete`: a fixture written with the real `core.obsidian_sync.append_event` and `save_runtime` (ordinary events with `session_id: None`, a `player_request` event, a session's events, `current_session` `awaiting_processing`) gives `read_complete` and `gate_satisfied` true, inserts only the session id as `ended`, and quarantines nothing.
  - inserts `ended` rows from `current_session`, `used_session_ids` and `events.jsonl`; `test_missing_files_and_absent_used_session_ids_are_not_partial`.
  - `test_malformed_json_line_and_malformed_session_id_are_distinct` (`5`, `[]`, `""` and a 121-character id give `malformed_session_id`; an absent key is skipped); `test_invalid_utf8_line_is_unreadable_not_fatal`; `test_truncated_final_line_is_malformed_json`; `test_runtime_not_object_is_unreadable`; `test_malformed_current_session_and_used_session_ids_element`. Line numbers are 1-based.
  - `test_rerun_after_reimport_skips_backfilled_ids_without_collisions`; `test_file_era_reuse_of_command_row_is_row_collision`; folder collisions with rows and among legacy ids are listed.
  - `test_partial_history_accepted_satisfies_gate_without_claiming_read`: one bad line and one collision, both accepted, give `read_complete` false, `gate_satisfied` true, both quarantined, and the readable ids inserted.
  - `test_unaccepted_item_inserts_nothing`; `test_live_current_session_inserts_nothing` (reported as `live_session`, no row of any status for it).
  - `test_source_sha256_changes_with_file_content`.
  - `test_backfill_does_no_file_io_or_process_lock`: `builtins.open`, `os.open` and `core.obsidian_sync.campaign_lock` are patched to raise during `backfill_session_history`, which still succeeds. The writer calls `lock_stream` first (spy). No `game_events` row is written.
  - The reader lives outside `core/commands` and runs before the transaction; PE-14b's import-guard tests (AST and subprocess `sys.modules`) pass with this task's modules present.
- [ ] PostgreSQL (CI): two PF2e campaigns start sessions concurrently, exactly one succeeds; `session.start` and an account-creating `redeem_invite` on the same campaign run concurrently and both finish.
- [ ] `python -m pytest -q tests/persistence/test_folder_form.py tests/persistence/test_session_commands.py tests/persistence/test_session_history_backfill.py tests/persistence/test_command_executor.py tests/persistence/test_identity_runtime.py tests/test_obsidian_sync.py` passes.
- [ ] `python -m pytest -q tests/persistence/test_session_postgresql.py -m postgresql --strict-markers` executes and passes in CI; zero collected or skipped cases are not acceptance.
- [ ] `git diff --check` passes. Legacy `/sessions/start` and `/sessions/end` behave exactly as before for file-authority campaigns; authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: status read and abandon (PE-14f); taking the PF2e slot after `session.start` (PE-17/18, C:810-811, C:1091); legacy route refusal for non-file campaigns (activation gate / PE-60, C:383-386); the action table and its real open-actions provider (PE-30 follow-on); the HTTP command route (PE-15); session UI.
- PE-24 (calls the backfill and decides activation) must: persist `read_complete`, `quarantined` and `source_sha256` in the run's `details`; use `gate_satisfied` as the C:666 input; refuse an `accepted` set taken from a report whose `source_sha256` differs from the current read; and refuse activation while `live_session` is set (C:665).
- PE-30 follow-up: the encounter-close command type registers with `override_gm_only=True` (C:563).
- Refusing to mint an `"override:session.end"` scope entry (PE-14d/PE-62) is not done; such an entry is inert.
- No route, no `app.py`, no `core/route_policy.py`, no `services/obsidian_sync.py`, no `core/obsidian_sync.py`. Do not change `process_command`, the `runtime.json` writers or `runtime.json` handling.

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

1. **R1-5 (P2, shared): place the file reader consistently with the AST guard.**
   Decision 7 requires the real shared `core.obsidian_sync.campaign_lock`, but
   Approach places that reader inside `core/commands`, where PE-14b prohibits
   that import. Move the file-reading half outside the guarded package, or
   define a narrow, tested exception proving it runs before any transaction.
   Keep the database backfill half free of file I/O/process locks.
2. **R1-6 (P2, shared): preserve the session on lifecycle events.** Setting
   `ended` before `ctx.emit` conflicts with PE-14b deriving event session IDs
   from the active row. Use the agreed trusted lifecycle context and assert
   correct IDs on `session.started`, action cancellations and `session.ended`.
   See PE-14b's Review for the autoflush case and existing-source evidence.
3. **R1-7 (P2): resolve the scoped-token override contradiction.** Decision 4
   and Acceptance say check 4 refuses every non-GM override; PE-14c explicitly
   permits an `override:session.end` scoped token at that check. Specify whether
   session-end cancellation has a type-specific GM/admin-only restriction or
   follows the general scoped-token rule, consistently with the accepted
   contract, and change both briefs and tests. Do not assume the existing
   generic check implements the narrower rule.
4. **R1-8 (P2): preserve legitimate legacy history and truthful completeness.**
   Decision 7 treats every non-string event `session_id` as unreadable. Main
   legitimately writes `session_id: null` for ordinary events outside a session
   (`core/obsidian_sync.py:157,230-235`,
   `services/obsidian_sync.py:746-753`; the ordinary-command fixture in
   `tests/test_obsidian_sync.py:179-207` exercises that state). Skip absent/null
   IDs as no session; distinguish malformed non-null IDs and malformed JSON.
   Otherwise a normal history unnecessarily requires GM quarantine.
   Separately, Decision 7/Acceptance set `complete=true` after unreadable items
   are accepted, contrary to C:503-507's distinction between a complete read
   and an explicitly accepted partial read. Specify separate read-completeness
   and accepted-quarantine/activation-ready fields. Add a main-format history
   fixture containing null IDs, and a partial-history case whose acceptance
   permits the documented gate without claiming the unreadable history was read.

The lossless ID/folder mapping, separate PF2e slot key, session-row serialization
and default action-provider boundary otherwise fit the plan. Re-estimate the
budget if these corrections change module ownership. Docs only; no product
tests ran. Review edits are uncommitted.

### Brief revision 1 (Claude, 2026-11-14)

1. **R1-5 (reader placement).** Resolved in Decision 7, Goal and Allowed files. The reader is a new `core/obsidian_session_history.py` outside `core/commands`; it alone imports `core.obsidian_sync`. `core/commands/session_history.py` keeps the types and the database half, with no file I/O and no process lock; imports go one way. PE-14b's guard gains `core.obsidian_session_history` in its AST ban list and a subprocess `sys.modules` check. Acceptance adds `test_backfill_does_no_file_io_or_process_lock` and requires PE-14b's guard tests to pass with these modules present. No exception to the guard is needed.
2. **R1-6 (session id on events).** Resolved in Decisions 2, 4 and 6. PE-14b snapshots the active row into `ctx.session_id` once, before `lock_plan`, and `ctx.emit` never re-reads it. `session.start` inserts, calls `ctx.bind_started_session(id)`, then emits. `session.end`'s `apply` asserts `subject.session_id == ctx.session_id`, runs `cancel_all` (override only), sets ended, then emits; all those events carry the ending id. `cancel_all` moved from check 9 to `apply`, where `ctx.emit` may write. Events after the end carry NULL, unlike legacy; recorded as a semantic change for PE-60. Tests: `test_lifecycle_events_carry_session_id`, `test_event_after_session_end_has_null_session`.
3. **R1-7 (override on `session.end`).** Resolved in Decision 4, Approach 2, Acceptance and Out of scope. Only the GM or site admin may override `session.end`. The policy lives on the type: PE-14a's `CommandType.override_gm_only`, set to True at `register()` for `session.end`, read by PE-14c's `override_allowed`, which fails closed for an unregistered type. There is no separate import-time policy registry, so no import order can open it. A token scoped `"override:session.end"` gets `403 not_permitted` at check 4. The six tests from R1-7 are in Acceptance; the 403 test asserts no receipt behaviour. The PE-30 encounter-close follow-up is in Out of scope. The optional mint refusal is not done.
4. **R1-8 (legacy parsing and completeness).** Resolved in Decision 7 and the Backfill acceptance. Null or absent ids are skipped, not unreadable; malformed JSON, non-objects and malformed ids are distinct reasons with 1-based lines; a truncated last line is `malformed_json`. `complete` is gone: `read_complete` and `gate_satisfied` are separate, and acceptance never sets `read_complete`. Exact matches against `file_backfill` rows are `skipped_existing`, so a re-import does not collide with itself. Inserts happen only when `gate_satisfied and live_session is None`. `source_sha256` is recorded, and PE-24's obligations are in Out of scope. The backfill writes no `game_events`.
5. **D1 wording.** Decision 6: each `lock_plan` registers its subject through `ctx.locks.register_serialized(...)` (kinds `campaign` and `session`, both in `STREAM_SERIALIZED_KINDS`) instead of reading it into `ctx.records`. The record passed is a `StateRecord.serialized(kind, id, payload)` for an existing row, or `None` for a missing one, never a raw ORM row.
6. **Rules adapter (PE-14a Decision 6).** Decisions 2 and 4, Approach 2 and the Context sibling-interfaces line: both types register `derives_rules=False` and a `rules_adapter`, which PE-14a's `register()` requires. Its `check(ctx, override)` holds the check-9 rules (start: id format, second active row, PF2e slot; end: the open-actions hook). Its `build_edition_matches` returns True, since neither subject is a character. Acceptance adds `test_session_types_register_rules_adapter`.
7. **D2 `allowed_principals`.** Decisions 2 and 4 and Approach 2: both types take `{USER, INTEGRATION_TOKEN}`; Acceptance checks it. This is a kind filter only; override stays at check 4.
8. **Issues 0/3/10 (fixture).** Allowed files, test bullet: one fixture rule for `test_session_commands.py` and `test_session_postgresql.py`: db authority row, pin matching the campaign's system, envelope `rules` and `epoch` matching, a test `content_descriptor` equal to the pin and a pass-through test `project`. Judgment: the D&D control campaign gets a D&D pin, not a PF2e one.
9. **Issue 5 (registration shape).** Decisions 2 and 4 each gain one line: subject kind, empty dependency set, payload id fields (`session_id`, kind `session`, for start; none for end), `requires_expected=False`.
10. **Issue 11 (roles and visibility).** Decisions 2 and 4 and Context now point to the PE-14c campaign_role_allows session rule for check 4 and to PE-14c's session visibility resolver rule through `register_visibility("session", ...)`, with "GM or site admin" in the resolver. This task no longer assumes either rule.
11. **Budget.** Raised from about 300 product + 400 test (stop 400) to about 355 + 600 (stop 430): module split and parse reasons (+35/+110), review fixes for R1-8 (+15/+40), R1-7 (+3/+50).

Judgments for Codex to challenge:
- `override_gm_only` narrows C:876's "if scoped" for `session.end`; the GM's token-authenticated Obsidian pane cannot do the one-step cancel-and-end.
- Ids of 121-128 characters are refused as `malformed_session_id`, though the column holds 128.
- `source_sha256` format: each file contributes a tag (`missing` or its byte length) and then its bytes, so a missing file differs from an empty one.
- Ids in accepted collisions are still inserted as `ended`; only their quarantine is recorded.
- A collision whose ids are all existing `file_backfill` rows is not listed again.
- A `live` `current_session` blocks every insert, not only its own id.
**Final audit.** `session.start` declares no payload id field: the new `session_id` is a value (declaring it would make check 5 refuse every valid new id). `core.commands.sessions` is appended to PE-14a's `COMMAND_MODULES`, so the session types and the import-time `register_visibility("session", ...)` exist before check 1.

### Brief review 2 - accepted (Codex, 2026-11-14)

Reviewed PR #259 at `51ac0e51` against `791936f1`. **R1-5, R1-6, R1-7 and R1-8 are closed; no new blocking findings.** The reader/database split satisfies the import guard. Start binds its inserted session, while end/cancellation events use the executor's snapshot. Null/absent legacy session ids are ordinary out-of-session history; accepting unreadable history changes `gate_satisfied`, never `read_complete`. The body and acceptance cases agree with the revised interfaces.

Accepted judgments: the new session id is a creation value, so `session.start` declares no existing-record payload ids; GM-only force-end intentionally excludes the token-authenticated Obsidian pane; events from later commands carry NULL session_id after end, as recorded for PE-60.

Non-blocking implementation clarifications:
1. Check 6 must skip collision lookup/folder conversion for a missing or non-string new session id, leaving the promised check-9 `422 validation_failed` to answer it. Add missing/null/list/object cases alongside the malformed-string cases; do not let a bind/type exception turn them into 500.
2. Accepting an exact id collision with an existing command-origin row retains that row unchanged and quarantines the collision. Decision 7 inserts only *new* ids; the broader revision-response wording does not authorize a duplicate insert or overwrite. Exercise this accepted-collision case.

The bounded split, dependency window and product stop remain acceptable; preserve the stated acceptance matrix. Docs-only review; no product tests ran.

### Carried from PE-13b implementation review 1 (Codex, 2026-11-15)

The fixture rule above (a `content_descriptor` "equal to the pin" and a pass-through `project` returning its input) predates PE-13b's implementation. Before the build, change it to `content_descriptor = pin["content"]` and a test `project` returning `"<kind>:<id>"`-keyed projections. Both session types have `derives_rules=False` and no `expected` conflicts, so the branches are skipped today, but the fixture must still match the real interface. See PE-13b implementation review 1 (Codex, 2026-11-15) in [PE-13b](PE-13b-check-order-conflicts.md) for the full text; reconcile this brief before its build.
