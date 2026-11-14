# PE-13b: Fixed check order, `expected` model and conflict body

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-13a)
- **Branch:** `claude/pe-13b-check-order` from current main after PE-13a merges. Record the exact starting SHA.
- **Depends on:** [PE-13a](PE-13a-state-repositories.md). Runs in parallel with PE-13c and PE-14a; it imports nothing from either.
- **Followed by:** PE-14b (executor), which runs checks 1-3, calls the type's `lock_plan`, then this runner.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: subject, dependency set and payload ids (C:87-95); check order, steps 2-9 inside the transaction under the stream lock (C:97-128); `expected` required list (C:130-135); override, GM or scoped token only, never bypasses 1-8 (C:137-140) and its matrix row (C:876); prompt-status entries and intent commands (C:142-145); conflict body (C:147-151); lock order (C:156-164); profile to edition table (C:35-39); content changes, only rule-deriving commands fail (C:41-43); edition mismatch keys on `build.edition` (C:48-50); code table (C:352-366); response fields pass through the caller's projection (C:420-421); visibility is the union of role projections (C:112-113, C:647-648); worked examples 1-4 (C:997-1034); PE-13 row, including the repository-only profile invariant (C:1089); PE-14 row (C:1090).
  - PE-13a: `core/persistence/state_repositories.py` (frozen records, `list`, `ProfileMismatch`), `core/persistence/command_locks.py` (`CommandLocks`).
  - Line numbers are from `791936f1` (main after PE-11c and PE-11b merged); only `core/persistence/campaign_import.py` changed since the `4e5c5059` drafts.
- **Change budget:** about 330 product lines and 450 test lines. Stop and report past about 450 product lines. (Planning estimate.)

## Goal

One ordered runner for checks 4-9 that every db-authority command will call inside its transaction, after PE-14b's executor has run checks 1-3 and the type's `lock_plan`. The first failing check answers with the contract's code, hidden records always answer 404 before any revision is compared, and a stale write gets the exact conflict body. No command uses it yet.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Who owns which check.** The contract fixes the order of all nine (C:97-128). Check 1 is PE-14a's `check_envelope(envelope, route_campaign_id)`, which PE-14b calls before any transaction; checks 2 (fence) and 3 (receipt lookup) are PE-14b's executor, under the stream lock (C:1090). `run_checks` executes checks 4-9 only. It requires the ctx flags `envelope_checked`, `fence_passed` and `receipt_absent`, and raises `CheckOrderError` if any is unset. PE-14b sets `fence_passed` only after `CommandLocks.stream()` and the fence, so the flags also stand for "the stream row is held" (C:97-98). The check-9 rules adapter is a required constructor argument: an unfilled adapter slot fails at construction, never passes silently.
2. **Rows are locked before checks read them.** After the receipt check, PE-14b calls the command type's `lock_plan(ctx)`, which takes lock-order steps 2-5 through PE-13a's `CommandLocks` for the subject, every `expected` reference and every payload id (C:156-164; locking a hidden or missing id reveals nothing). Checks 4-9 read only those locked rows, through `ctx.records`; `apply` may lock only rows it creates. The contract forbids comparing a revision before visibility passes, not reading one (C:111-112), so the guarantee here is: no revision is compared, and no branch depends on a revision, before check 5 passes.
3. **Authorizer is a protocol.** Checks 4 and 5's role and visibility parts need the step-4 SQL re-read (C:429-436), which is PE-14c's. This task defines the protocol `Authorizer` with `campaign_role_allows(type)`, `override_allowed(type)`, `visible(record)` and `subject_role_allows(type, subject)`, and tests with a fake. PE-14c implements it (`override_allowed`: GM, site admin, or a token scoped for the type's override).
4. **Command-type facts are a protocol too.** PE-14a's registry is not a dependency (the two run in parallel). The runner takes a structural `CheckSpec`: `type`, subject kind, dependency set (each `expected` kind and its relation to the subject, as a predicate), payload id fields, terminal predicate and `derives_rules` (C:87-95, C:118-120, C:41-42). PE-14a's `CommandType` carries these fields; PE-14b passes it in. Tests use a stub spec.
5. **`expected` vocabulary.** Entry kinds: `character.build`, `character.play`, `encounter`, `clock` (rev entries, `{kind, id, rev}`) and `prompt` (status entry, `{kind: "prompt", id, status: "open"}`, C:142-143). Kinds are a registry so PE-30, PE-40 and PE-42..48 add theirs (`action`, `item`, `wallet`, request, stash item, project) without editing the runner. The revision key in responses is `"<kind>:<id>"` (C:390).
6. **Projection is a callback.** The conflict body's `conflicts[]` and `current` pass through a `project(principal, records)` callback (C:150, C:420-421); a record the caller cannot see is omitted from both. Projections belong to PE-31 and the adapters (C:1097); tests use a stub that drops hidden records.
7. **Installed content is an input.** Check 8's `503 content_mismatch` compares the pin with an installed-content descriptor that PE-14b receives as `content_descriptor` before the transaction opens, because rules content is never loaded inside a transaction (C:455-457). It applies only when the spec sets `derives_rules` (C:41-42).
8. **Edition is read from the build, not the profile column.** `422 edition_mismatch` keys on the subject character's build edition (D&D `build.edition`, read through the rules adapter) differing from the campaign pin (C:48-50, profile to edition at C:35-39). The `rules_profile` column cannot differ, because PE-13a refuses that write with `ProfileMismatch` (C:1089).
9. **Override.** An envelope carrying `override` from a principal for whom `authorizer.override_allowed(type)` is false gets `403 not_permitted` at check 4, nothing written (C:137, C:876). An allowed override reaches only the check-9 adapter. The runner never lets it skip checks 4-8, and PE-14b never lets it skip 1-3 (C:139-140).

## Approach

Allowed files:
- new package `core/commands/`: `__init__.py` (created by whichever of PE-13b, PE-13c and PE-14a lands first, with exactly this content, the one line `"""Command pipeline. Import submodules directly; this package re-exports nothing."""` followed by a newline, so parallel branches merge cleanly; callers import submodules), `checks.py`, `conflicts.py`
- new `tests/persistence/test_command_checks.py`

Do not edit `core/persistence/__init__.py`; import PE-13a's submodules directly.

1. `checks.py` errors: `CommandRefusal(code, *, conflicts=None, current=None)` accepting every code in the code table (C:352-366), with `code` and `http_status` attributes looked up from that table (an unknown code raises `ValueError`). PE-14b answers any `core.commands` exception from these two attributes; whether a refusal writes a receipt is PE-14b's code-table mapping, not this module's. `CheckOrderError` for a precondition the caller broke (an unset flag, a record read that `lock_plan` did not lock); PE-14b answers it as `500 server_error`.
2. `CheckContext` (a dataclass PE-14b builds and fills step by step: the three flags as each check passes, `records` through `lock_plan(ctx)`, and `emit`; `run_checks` only reads it): principal, `campaign_id`, the validated envelope (type, subject ref, `expected`, payload ids, `rules`, `override`), the `authorizer`, the `project` callback, `content_descriptor`, the campaign pin, `records` (every key the type's `lock_plan` registered: locked through `CommandLocks`, or, for kinds that have no `CommandLocks` step because every writer of them holds the stream row (campaign, `campaign_sessions`, `campaign_grants`), read under the stream lock; a missing row is `None`; a stream-serialized row is stored as a frozen record with `kind` (`campaign`, `session` or `grant`), `id`, `revision = None` and its columns as `payload`, so check 5 and `visible(record)` treat it like a PE-13a record), and the flags `envelope_checked`, `fence_passed`, `receipt_absent`.
3. `CheckRunner(spec, adapter, kinds=DEFAULT_KINDS)`: `adapter` is required (`TypeError`/`ValueError` at construction if missing) and supplies `build_edition_matches(subject_record, pin)` and the check-9 `check(ctx, override)`. `run_checks(ctx, spec, adapter)` = `CheckRunner(spec, adapter).run(ctx)`, the entry point PE-14b calls. `run(ctx)` first raises `CheckOrderError` if any flag is unset, then in the fixed order (C:106-128):
   - **4** `authorizer.campaign_role_allows(type)` else 403 `not_permitted`; then, if the envelope carries `override` and `authorizer.override_allowed(type)` is false, 403 `not_permitted`.
   - **5** the subject, **every** `expected` reference and every declared payload id: present in `ctx.records` (absent key → `CheckOrderError`), row exists, same campaign, `authorizer.visible(record)`, and each `expected` reference matches its declared relation to the subject → else 404 `not_found`, one identical body for hidden, missing and wrong-relation. No revision is compared before this passes. Then `authorizer.subject_role_allows(type, subject)` else 403 `not_permitted`.
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
- [ ] `CommandRefusal` exposes `code` and `http_status` for every code in C:352-366 and rejects an unknown code.
- [ ] `python -m pytest -q tests/persistence/test_command_checks.py tests/persistence/test_state_repositories.py` passes; `git diff --check` passes.
- [ ] Existing route responses, authority settings and worker count are unchanged.

## Out of scope / do not touch

- Owned elsewhere: check 1, the dependency-set registry with `derives_rules` and `requires_expected`, envelope and digest (PE-14a); fence, receipts, `lock_plan` invocation, code-table receipt mapping and the transaction runner (PE-14b); the step-4 SQL re-read and the real `Authorizer`, including `override_allowed` (PE-14c); projections (PE-31, adapters); every command type, its `lock_plan` and its terminal predicate (PE-14d, PE-15+); PE-13c's `InEncounter`/`ReservationBlocked` (their own module, not built on `CommandRefusal`).
- `core/persistence/__init__.py`, PE-13a's modules, routes, migrations, `app.py` and live writers.
- No PostgreSQL case is needed here: the runner takes no lock of its own (PE-13a and PE-14a/b prove locking).

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
