# PE-13b: Fixed check order, `expected` model and conflict body

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-13a); brief accepted (Codex brief review 2, 2026-11-14)
- **Branch:** `claude/pe-13b-check-order` from current main after PE-13a merges. Record the exact starting SHA.
- **Depends on:** [PE-13a](PE-13a-state-repositories.md). Runs in parallel with PE-13c and PE-14a; it imports nothing from either.
- **Followed by:** PE-14b (executor), which runs check 1 before its transaction, then inside it takes the stream lock, runs checks 2-3, builds the authorizer and the `CheckContext`, calls the type's `lock_plan`, then this runner.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: subject, dependency set and payload ids (C:87-95); check order, steps 2-9 inside the transaction under the stream lock (C:97-128); `expected` required list (C:130-135); override, GM or scoped token only, never bypasses 1-8 (C:137-140) and its matrix row (C:876); prompt-status entries and intent commands (C:142-145); conflict body (C:147-151); lock order (C:156-164); profile to edition table (C:35-39); content changes, only rule-deriving commands fail (C:41-43); edition mismatch keys on `build.edition` (C:48-50); code table (C:352-366); response fields pass through the caller's projection (C:420-421); visibility is the union of role projections (C:112-113, C:647-648); worked examples 1-4 (C:997-1034); PE-13 row, including the repository-only profile invariant (C:1089); PE-14 row (C:1090).
  - PE-13a: `core/persistence/state_repositories.py` (frozen records, `list`, `ProfileMismatch`, `StateRowMissing`, `StateRecord.serialized`), `core/persistence/command_locks.py` (`CommandLocks(session, campaign_id, *, lock_stream)` with attributes `session`, `campaign_id`, `stream_row`; the read-only `records` mapping `(kind, id) -> StateRecord | None`; `register_serialized(kind, id, record)` over the allowlist `STREAM_SERIALIZED_KINDS = {campaign, session, grant}`; `LockOrderError`).
  - PE-14a: `check_envelope(envelope, principal, *, route_campaign_id) -> CheckedEnvelope` and `CommandType`, including `override_gm_only` (read by the authorizer, not by this runner).
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 345 product lines and 480 test lines. Stop and report past about 450 product lines. (Planning estimate.)

## Goal

One ordered runner for checks 4-9 that every db-authority command will call inside its transaction, after PE-14b's executor has run checks 1-3 and the type's `lock_plan`. The first failing check answers with the contract's code, hidden records always answer 404 before any revision is compared, and a stale write gets the exact conflict body. No command uses it yet.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Who owns which check.** The contract fixes the order of all nine (C:97-128). Check 1 is PE-14a's `check_envelope(envelope, principal, *, route_campaign_id) -> CheckedEnvelope`, which PE-14b calls before any transaction with the authenticated `Principal` (never one read from the body); checks 2 (fence) and 3 (receipt lookup) are PE-14b's executor, under the stream lock (C:1090). `run_checks` executes checks 4-9 only. It requires the ctx flags `envelope_checked`, `fence_passed` and `receipt_absent`, and raises `CheckOrderError` if any is unset. PE-14b sets `fence_passed` only after `locks.stream()` and the fence, so the flags also stand for "the stream row is held" (C:97-98). The check-9 rules adapter is a required constructor argument: an unfilled adapter slot fails at construction, never passes silently.
2. **Rows are locked before checks read them.** After the receipt check, PE-14b builds `ctx` and calls the command type's `lock_plan(ctx)`, which takes lock-order steps 2-5 through `ctx.locks` (PE-13a's campaign-bound `CommandLocks`) for the subject, every `expected` reference and every payload id (C:156-164; locking a hidden or missing id reveals nothing). Stream-serialized rows (campaign, session, grant) are entered through `ctx.locks.register_serialized(...)`; combatants are registered by `ctx.locks.encounter(...)`. Checks 4-9 read only those rows, through `ctx.records` (= `ctx.locks.records`): a key absent from it is `CheckOrderError`, a key mapped to `None` (no such key in this campaign) is 404. A missing state row for an existing character raises PE-13a's `StateRowMissing` inside `lock_plan`, before the runner. `apply` may lock only rows it creates. The contract forbids comparing a revision before visibility passes, not reading one (C:111-112), so the guarantee here is: no revision is compared, and no branch depends on a revision, before check 5 passes.
3. **Authorizer is a protocol.** Checks 4 and 5's role and visibility parts need the step-4 SQL re-read (C:429-436), which is PE-14c's. This task defines the protocol `Authorizer` with `campaign_role_allows(type)`, `override_allowed(type)`, `visible(record)` and `subject_role_allows(type, subject)`, and tests with a fake. PE-14c implements it (`override_allowed`: GM, site admin, or a token scoped for the type's override unless the registered `CommandType` sets `override_gm_only`). PE-14b builds it as `factory(session, principal, campaign_id)` after the receipt lookup and before `ctx`, so `authorizer` is a required `ctx` field. The authorizer, `lock_plan`, `apply` and the check-9 adapter use only `ctx.session` and `ctx.locks`: never `runtime.database()`, a new session or `flask.g` (C:429-436).
4. **Command-type facts are a protocol too.** PE-14a's registry is not a dependency (the two run in parallel). The runner takes a structural `CheckSpec`: `type`, the subject's record kind, dependency set (each `expected` kind and its relation to the subject, as a predicate), payload id fields each with its record kind (Decision 5), terminal predicate and `derives_rules` (C:87-95, C:118-120, C:41-42). PE-14a's `CommandType` carries these fields; PE-14b passes it in. Tests use a stub spec.
5. **`expected` vocabulary and record kinds.** Two separate sets.
   - *`expected` entry kinds* (what an envelope may list in `expected`): `character.build`, `character.play`, `encounter`, `clock` (rev entries, `{kind, id, rev}`) and `prompt` (status entry, `{kind: "prompt", id, status: "open"}`, C:142-143). Kinds are a registry so PE-30, PE-40 and PE-42..48 add theirs (`action`, `item`, `wallet`, request, stash item, project) without editing the runner. The revision key in responses is `"<kind>:<id>"` (C:390).
   - *Record kinds* (the `kind` half of a `ctx.records` key, which check 5 accepts for the subject and payload ids): every rev entry kind above, plus `combatant` (registered with its encounter by `ctx.locks.encounter(...)`; it has no revision of its own, so it is not a valid `expected` kind) and the stream-serialized kinds `campaign`, `session` and `grant` (PE-13a's `STREAM_SERIALIZED_KINDS`; `revision = None`, also not valid in `expected`). These strings are PE-13a's (its Approach 3) and must match it.
   - `CheckSpec` declares the record kind of the subject and of each payload id field (Decision 4), so check 5 looks each id up as `(declared_kind, id)` and never infers a kind from the value. An `expected` entry is looked up under its own entry kind.
6. **Projection is a callback.** The conflict body's `conflicts[]` and `current` pass through a `project(principal, records)` callback (C:150, C:420-421); a record the caller cannot see is omitted from both. Projections belong to PE-31 and the adapters (C:1097); tests use a stub that drops hidden records.
7. **Installed content is an input.** Check 8's `503 content_mismatch` compares the pin with an installed-content descriptor that PE-14b receives as `content_descriptor` before the transaction opens, because rules content is never loaded inside a transaction (C:455-457). It applies only when the spec sets `derives_rules` (C:41-42).
8. **Edition is read from the build, not the profile column.** `422 edition_mismatch` keys on the subject character's build edition (D&D `build.edition`, read through the rules adapter) differing from the campaign pin (C:48-50, profile to edition at C:35-39). The `rules_profile` column cannot differ, because PE-13a refuses that write with `ProfileMismatch` (C:1089).
9. **Override.** An envelope carrying `override` from a principal for whom `authorizer.override_allowed(type)` is false gets `403 not_permitted` at check 4, nothing written (C:137, C:876). An allowed override never bypasses checks 4-8 (the runner) or 1-3 (PE-14b) (C:139-140). After check 4, only the type's check-9 adapter and its `apply` may act on it (for example `session.end`'s `apply` runs `cancel_all` when `override` is present, PE-14e). Type-specific narrowing (for example `session.end`, GM or site admin only) lives inside `override_allowed`, driven by `CommandType.override_gm_only`; the runner stays generic.

## Approach

Allowed files:
- new package `core/commands/`: `__init__.py` (created by whichever of PE-13b, PE-13c and PE-14a lands first, with exactly this content, the one line `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline, so parallel branches merge cleanly; callers import submodules), `checks.py`, `conflicts.py`
- new `tests/persistence/test_command_checks.py`

Do not edit `core/persistence/__init__.py`; import PE-13a's submodules directly.

1. `checks.py` errors: `CommandRefusal(code, *, conflicts=None, current=None)` accepting every code in the code table (C:352-366), with `code` and `http_status` attributes looked up from that table (an unknown code raises `ValueError`). PE-14b answers any `core.commands` exception from these two attributes; whether a refusal writes a receipt is PE-14b's code-table mapping, not this module's. `CheckOrderError` for a precondition the caller broke (an unset flag, a record read that `lock_plan` did not lock); PE-14b answers it as `500 server_error`.
2. `CheckContext`: a non-frozen dataclass that PE-14b builds once, after the receipt lookup and the authorizer factory, and then fills (`lock_plan` registers rows through `ctx.locks`; PE-14b sets `emit` and `bind_started_session`). `run_checks` only reads it. The field list is closed:
   - `session`: the executor's transaction session.
   - `locks`: the campaign-bound `CommandLocks` on that session, with `stream()` already called.
   - `principal`: `CheckedEnvelope.principal`.
   - `campaign_id`.
   - `envelope`: `CheckedEnvelope.envelope` (type, subject ref, `expected`, payload ids, `rules`, `override`).
   - `authorizer`: required, no default.
   - `project`, `content_descriptor`, `pin` (the campaign pin).
   - `emit`: default `None`; PE-14b sets it, and it allocates from `ctx.locks.stream_row`.
   - `session_id`: default `None`; PE-14b snapshots the active session id once, before `ctx` is built (R1-6).
   - `envelope_checked`, `fence_passed`, `receipt_absent`: default `False`.
   - `bind_started_session`: a callable slot, default `None`. PE-14b implements it in `events.py` and sets it; this task only declares it.
   - `records` is a property returning `self.locks.records`, not a field, so the two cannot drift. A stream-serialized row (`campaign`, `session` or `grant`) is the frozen `StateRecord.serialized(kind, id, payload)` with `revision = None`, so check 5 and `visible(record)` treat it like any PE-13a record.
   - `__post_init__` raises `CheckOrderError` unless `locks.campaign_id == campaign_id` and `locks.session is session`.
3. `CheckRunner(spec, adapter, kinds=DEFAULT_KINDS)`: `adapter` is required (`TypeError`/`ValueError` at construction if missing) and supplies `build_edition_matches(subject_record, pin)` and the check-9 `check(ctx, override)`. `run_checks(ctx, spec, adapter)` = `CheckRunner(spec, adapter).run(ctx)`, the entry point PE-14b calls. `run(ctx)` first raises `CheckOrderError` if any flag is unset, then in the fixed order (C:106-128):
   - **4** `authorizer.campaign_role_allows(type)` else 403 `not_permitted`; then, if the envelope carries `override` and `authorizer.override_allowed(type)` is false, 403 `not_permitted`.
   - **5** the subject and every declared payload id (keyed by the record kind `spec` declares for it), and **every** `expected` reference (keyed by its entry kind): present in `ctx.records` (absent key → `CheckOrderError`), value not `None`, same campaign, `authorizer.visible(record)`, and each `expected` reference matches its declared relation to the subject → else 404 `not_found`, one identical body for hidden, missing and wrong-relation. No revision is compared before this passes. Then `authorizer.subject_role_allows(type, subject)` else 403 `not_permitted`.
   - **6** the spec's terminal predicate `terminal(ctx, subject_record) -> bool` (it sees the payload through `ctx`, e.g. `session.start` keys on `session_id`) → 409 `already_finalized`.
   - **7** every `expected` entry through its kind (rev entries compare `revision`; prompt entries compare `status == "open"`) → 409 `stale_revision` carrying every mismatch, projected per Decision 6. Intent commands with empty `expected` pass (C:143-145). Whether a type requires `expected` is check 1 (PE-14a), not here.
   - **8** pins, in this order (C:122-125): 409 `rules_mismatch` when the envelope's `rules` differs from the campaign pin; 503 `content_mismatch` when `spec.derives_rules` and `content_descriptor` differs from the pin; 422 `edition_mismatch` when the subject is a character and `adapter.build_edition_matches(...)` is false.
   - **9** `adapter.check(ctx, override)`; its refusals are `CommandRefusal`s with 422 codes.
4. `conflicts.py`: `conflict_body(operation_id, conflicts, current)` returning exactly `{"ok": false, "code": "stale_revision", "operation_id", "conflicts": [{"kind", "id", "expected", "current"}], "current"}` (C:147-151), with `conflicts` and `current` already projected.

## Acceptance

- [ ] Order, each pinned by its own test: failing 5 and 7 gets 404 (the QM loses example 4 without a role on Bo's character, C:1032-1033); separately, failing 5 and 9 gets 404; failing 6 and 7 gets `409 already_finalized` (C:1020-1022); failing 7 and 9 gets `409 stale_revision` (C:1030-1031); a second tab expecting an old build rev gets `409 stale_revision` while an intent command with empty `expected` passes (C:1012-1014).
- [ ] A hidden subject, a hidden `expected` reference, a missing payload id and a wrong-relation reference give byte-identical 404 bodies. A spy (revision proxies that record every comparison and truth test) proves no revision is compared, and no branch depends on a revision, before check 5 passes.
- [ ] Visible subject without subject role: 403, and the fake authorizer records that nothing was written (C:997-1002).
- [ ] An envelope carrying `override` from a principal whose `override_allowed(type)` is false gets 403 `not_permitted` with nothing written; an allowed GM override does not bypass any of checks 4-8 (one test per check).
- [ ] `stale_revision` lists every mismatched entry and its body matches C:147-151 exactly, passed through the stub projector (a hidden conflicting row is omitted from `conflicts` and `current`).
- [ ] A prompt-status entry passes while `open` and conflicts once it is not; two prompt inputs to one action do not conflict with each other.
- [ ] Pin order: `rules` differing from the pin gives 409 `rules_mismatch` even when content also differs; content differing gives 503 for a `derives_rules=True` type, and a `derives_rules=False` type passes check 8 with mismatched content; a character whose `build.edition` differs from the campaign pin gives 422 `edition_mismatch`, with the fixture written through PE-13a with a matching `rules_profile`.
- [ ] `run` raises `CheckOrderError` when any of `envelope_checked`, `fence_passed`, `receipt_absent` is unset (one test each) and when a reference was not locked by `lock_plan`; constructing a runner without the check-9 adapter fails.
- [ ] `CheckContext` composes with PE-13a's real `CommandLocks` (built with a test-only `lock_stream` stub, `stream()` called): `test_check_context_records_are_lock_records` asserts `ctx.records is ctx.locks.records`; a mismatched `campaign_id` or a different session raises `CheckOrderError`; the three flags default to `False` and `emit`, `session_id` and `bind_started_session` to `None`; omitting `authorizer` fails at construction.
- [ ] A key registered as `None` gives the same byte-identical 404 body as a hidden record; a stream-serialized record registered through `register_serialized` passes check 5 when visible, and so does a `combatant` payload id registered by `encounter()`; a payload id whose spec-declared record kind differs from the kind it was locked under is `CheckOrderError`.
- [ ] `CommandRefusal` exposes `code` and `http_status` for every code in C:352-366 and rejects an unknown code.
- [ ] `python -m pytest -q tests/persistence/test_command_checks.py tests/persistence/test_state_repositories.py` passes; `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: check 1 (`check_envelope`, `CheckedEnvelope`, `allowed_principals`), the dependency-set registry with `derives_rules`, `requires_expected` and `override_gm_only`, envelope and digest (PE-14a); `CommandLocks`, `register_serialized` and `StateRecord.serialized` (PE-13a); `streams.lock_stream` (PE-14a); fence, receipts, the authorizer factory call, the `session_id` snapshot, `emit`, the `bind_started_session` implementation, `lock_plan` invocation, code-table receipt mapping and the transaction runner (PE-14b); building the token `Principal` from the bearer record (the route, PE-15); the step-4 SQL re-read and the real `Authorizer`, including `override_allowed` (PE-14c); projections (PE-31, adapters); every command type, its `lock_plan` and its terminal predicate (PE-14d, PE-15+); PE-13c's `InEncounter`/`ReservationBlocked` (their own module, not built on `CommandRefusal`).
- `core/persistence/__init__.py`, PE-13a's modules, routes, migrations, `app.py` and live writers.
- No PostgreSQL case is needed here: the runner takes no lock of its own (PE-13a and PE-14a/b prove locking). The composing test uses SQLite and a stub `lock_stream`; the stub stays in the test file.

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

1. **R1-1 (P2, shared with PE-13a/14a/14b): complete `CheckContext`.**
   Approach 2 lists its fields but provides neither the transaction session
   nor the campaign-bound `CommandLocks`. PE-14b's `lock_plan(ctx)` and
   `apply(ctx)` need those to lock and write within the executor's transaction,
   and its allowed edit here currently covers only emit/flags. Publish the
   shared context/access interface now, along with locked/missing-key
   registration, and prove it composes with the lock manager and executor.
   See PE-13a's Review for the companion constructor correction.

Checks 4-9, concealment before revision comparisons, the override restriction,
structural protocols and the identical no-re-export package initializer
otherwise agree with the contract. The declared parallel window is reasonable
after the shared interface is corrected. No separate budget finding.
Docs-only source review; no product tests ran. Edits are uncommitted.

### Brief revision 1 (Claude, 2026-11-14)

- **R1-1 (complete `CheckContext`).** Resolved in Approach 2, which now gives the closed field list: `session`, `locks`, `principal`, `campaign_id`, `envelope`, `authorizer` (required), `project`, `content_descriptor`, `pin`, `emit`, `session_id`, the three flags and the `bind_started_session` slot. `records` is a property over `locks.records`, and `__post_init__` checks the campaign and session identity. Decision 2 says how rows get into `records` (lock methods, `encounter()` for combatants, `register_serialized` for campaign/session/grant) and that `None` means only "no such key in this campaign". Decision 3 places the authorizer factory before `ctx` and states the same-session rule. Acceptance gains a composing test against PE-13a's real `CommandLocks`. PE-14b no longer needs to edit `checks.py`: the R1-6 fields are declared here.
- **R1-2 (authenticated principal), wording.** Decision 1 cites `check_envelope(envelope, principal, *, route_campaign_id) -> CheckedEnvelope`; Approach 2 defines `ctx.principal` and `ctx.envelope` as `checked.principal` and `checked.envelope`. Out of scope names PE-14a and the route (PE-15) as owners.
- **R1-7 (`session.end` override), wording.** Decision 3's parenthetical and Decision 9 now say that type-specific narrowing lives in `override_allowed`, driven by PE-14a's `CommandType.override_gm_only`. Decision 9 also no longer says an allowed override reaches only the check-9 adapter: it says an override never bypasses checks 4-8, and that after check 4 the type's check-9 adapter and its `apply` may act on it (PE-14e's `session.end` `apply` runs `cancel_all`). The runner is unchanged.
- **Record kinds vs `expected` kinds (cross-brief with PE-13a).** Decision 5 now separates `expected` entry kinds from the record kinds check 5 accepts for the subject and payload ids (adds `combatant`, `campaign`, `session`, `grant`, matching PE-13a Approach 3). Decision 4 and Approach 3 check 5 say `CheckSpec` declares the record kind of the subject and of each payload id field. One Acceptance clause added. Judgment for Codex: a payload id locked under a different kind than the spec declares is `CheckOrderError` (caller bug), not 404. No budget change.
- **Budget.** Was about 330 product and 450 test lines; now about 345 and 480 (+15 product for the context fields, property and identity checks; +30 test for the composing tests). The 450-line stop is unchanged.

Judgments for Codex to challenge:
1. `__post_init__` raises `CheckOrderError` (answered as 500) rather than `TypeError`/`ValueError`, so a mis-built context goes through the same caller-broke-a-precondition path as an unset flag.
2. `bind_started_session` is a plain callable field defaulting to `None`, not a method, so this module needs no import from PE-14b's `events.py`.
3. The composing test uses the real PE-13a `CommandLocks` on SQLite with a stub `lock_stream` kept in the test file, rather than a fake locks object, so the interface is proven against the actual lock manager.
4. `StateRowMissing` is raised in `lock_plan` (PE-13a), so the runner never sees it; the runner treats `None` only as 404.

### Brief review 2 - accepted (Codex, 2026-11-14)

Reviewed PR #259 at `51ac0e51` against `791936f1`. **R1-1 is closed; no new blocking findings.** The closed `CheckContext` now supplies the session, locks, record view, pin, session snapshot and bind callback required by PE-14b. Record kinds, expected kinds and the same-session composing tests agree with PE-13a/14a/14b. Requiring a rules adapter for every registered type gives the runner a complete check-9 slot even when that type does not derive game rules.

The estimate and 450-product-line stop are acceptable. This is brief acceptance, not implementation acceptance; PE-13a must merge first. Docs-only review; no product tests ran.

### Carried from PE-13a implementation review 1 (Codex, 2026-11-14)

Resolve before building check 5's combatant cases: `CommandLocks.encounter()` (PE-13a) registers only the combatants that exist in the locked encounter, so a requested combatant id that is missing or belongs to another encounter has no key in `ctx.records`, while Decision 2 / Approach 3 treat an absent key as a programming error (500). Provide a scoped way to account for each requested combatant id under the held encounter (recording `None`), or define how the complete locked encounter set proves absence, keeping a missing target (404) apart from an unplanned key (500). `register_serialized` correctly refuses the `combatant` kind, so it is not the route. See the PE-13a Review section.
