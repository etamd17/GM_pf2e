# PE-14g: Token rollback export gate (Option A only)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-14c); brief accepted (Codex brief review 1, 2026-11-14); required under the chosen Option A
- **Priority:** must merge before production runs `OWNERSHIP_BACKEND=sql` with SQL token storage.
- **Branch:** `claude/pe-14g-token-rollback-export` from current main after PE-14c merges. Record the exact starting SHA.
- **Depends on:** [PE-14c](PE-14c-grants-tokens-authorizer.md) (the `integration_tokens` table and model, the `["@live"]` scope rule, revoke write-ahead and the file-copy repair). PE-14c Decision 1 is Option A (user, 2026-11-14), so this task is required. May run in parallel with [PE-14d](PE-14d-stream-locked-authorization-writes.md), [PE-14e](PE-14e-sessions-backfill.md) and [PE-14f](PE-14f-status-read-abandon.md); it shares no files with them.
- **Followed by:** PE-15 (rows naming "PE-14" wait for all its children, this one included). Any production use of SQL token storage.
- **Context:**
  - C = `docs/player-release/state-command-contract.md`: paired file and SQL writes are never atomic (C:458); revocation is permanent (C:275); existing tokens get `@live` (C:851-852); the per-campaign `db -> exporting -> file` transition does not change `OWNERSHIP_BACKEND`, so tokens stay in SQL there (C:668-670).
  - Token storage is chosen by `OWNERSHIP_BACKEND` (`backend` and `sql_enabled`, `core/persistence/runtime.py:39-51`), not by per-campaign authority. So the only rollback that moves tokens back to files is the PR4B whole-store rollback, sql -> json.
  - The legacy token file: `integration_dir` and `tokens_file` `core/obsidian_sync.py:36-41` (`campaigns/<cid>/integrations/obsidian/tokens.json`); `SCHEMA_VERSION = 2` :22; timestamp format `utc_now` :32-33 (`%Y-%m-%dT%H:%M:%SZ`); `_load_tokens` :63-69 silently treats a non-object file as empty; record shape `create_token` :81-89 (`id`, `label`, `token_hash`, `last_four`, `created_at`, `last_used_at`, `revoked_at`); `verify_token` :108-136 skips any record with a truthy `revoked_at` (:116) and parses `last_used_at` with the format above (:126-132).
  - PE-14c's SQL row: `integration_tokens(credential_id, campaign_id, label, token_digest, last_four, scope, replaces_credential_id, minted_by_user_id, created_at, last_used_at, revoked_at)`. `credential_id` is the file's `id`; `token_digest` is the file's `token_hash`. PE-14c's revoke stamps the file before SQL, its mint writes SQL before the file, and its repair copies SQL to the file lazily. So the file copy can be stale in either direction, and PE-14c never claims it protects rollback. This task is the gate.
  - The exporter, `tools/export_transactional_runtime.py` at `791936f1` (PE-11c's merged code is the base):
    - `_private_json` :72-74; `_require_canonical` :81-89; `_strict_document` :185-194.
    - `_source_character` :92-119 resolves a source in `campaigns` and, for trash, `campaigns_trash`, prefers the active copy, and refuses a symlinked component or a non-canonical path.
    - `_write_legacy_tree` :260-368. It never reads or writes `tokens.json`; a campaign's staged root is `campaigns_trash` when `trashed_at` is set, else `campaigns` (:302-306).
    - `_verify_authority(staging, records, invitation_state, dnd5e)` :376-430, called from `export_runtime` at :501 and directly, with those four positional arguments, by `tests/persistence/test_export_runtime.py:728` and :730. Those direct calls must keep working unchanged.
    - `export_runtime` :433-554: repeatable-read session and `_database_records` :457-459; `current_records` recheck :504-507; source fingerprint recheck :509-520; D&D listing recheck :521-526; manifest `report` :527-537; the `except` at :549-550 turns `SQLAlchemyError`, `OSError`, `ValueError` and `ImportError` into a generic message, while `RuntimeExportError` (a `RuntimeError` through `MigrationToolError`, `tools/migrate_transactional_store.py:97`) passes through, so every token refusal must raise `RuntimeExportError` itself.
  - `migration._database_records` (`tools/migrate_transactional_store.py:1898`) reads a fixed entity set that does not include `integration_tokens`, so the existing SQL recheck does not see a token change. `migration._canonical_sha256` :141. The planner fingerprints only the files it recognizes (`_snapshot_files` :220-271), so an extra `tokens.json` in the export tree does not change `build_plan`.
  - SQL soft-trash only stamps `trashed_at` and keeps the active-path files (`delete_campaign`, `core/persistence/campaign_runtime.py:316-326`; comment :338). A SQL-trashed campaign's token file is therefore under `campaigns/<cid>`; PR4A-imported trash is under `campaigns_trash/<cid>`.
  - Runbook: `docs/remediation/pr4b-runtime-cutover.md`, "Runtime rollback export" :65-112. It already forbids a direct flip back to `json` after SQL mutations and says to restore non-exported files from the matching volume backup (:109-112).
  - Tests: `tests/persistence/test_export_runtime.py` (fixture `imported_store` :40-61; trashed-root cases :145-161; source-change case :217-234). `tests/character_workflows/test_migration.py` also calls `export_runtime` (:82, :131, :183, :197, :218).
  - Line numbers are from `791936f1`.
- **Change budget:** about 110 product lines and 160 test lines, runbook prose not counted. Stop and report past about 200 product lines.

## Goal

Make the supported PR4B rollback carry Obsidian tokens from SQL. `export_runtime` writes every campaign's `integrations/obsidian/tokens.json` from SQL `integration_tokens` plus the quiesced source file, so a SQL-revoked token stays refused after rollback and a SQL-minted token keeps working, whatever state PE-14c's file copy is in. If the source file or SQL changes or cannot be read, the export refuses; it never guesses.

## Decisions this brief fixes (reviewer: challenge these first)

1. **Sources.** In the export's repeatable-read session, read every `integration_tokens` row ordered by `campaign_id`, `created_at`, `credential_id`. For each campaign in `records["campaigns"]`, resolve the source `tokens.json` the way `_source_character` does (:92-119): look in `campaigns/<cid>`, and also in `campaigns_trash/<cid>` when `trashed_at` is set; prefer the active copy; refuse a symlinked component or a non-canonical path. A missing file is fine. Read each source once, keep its SHA-256, and parse it with `_strict_document`.
2. **Malformed or inconsistent source refuses.** The export raises `RuntimeExportError("Obsidian token file for campaign <cid> cannot be exported; repair it first")` when the file is not strict JSON, is not an object, has a `tokens` value that is not a list, has a record that is not an object or lacks a string `id` or `token_hash`, lists the same `id` twice, lists an `id` whose SQL row has a different `token_digest`, or lists an `id` that is a SQL credential of another campaign. The message names the campaign id and no secret. (Judgment: `_load_tokens` would treat a broken file as empty, which could drop a file-only revocation; the exporter is stricter on purpose.)
3. **Union by id, revocation one-way.**
   - An id in SQL: every field comes from the SQL row (`id` = `credential_id`, `token_hash` = `token_digest`, `label`, `last_four`, `created_at`, `last_used_at`), except `revoked_at`, which is SQL's if set, else the file's.
   - An id only in the file: the file record as read.
   - Order: SQL rows in read order, then file-only records in file order.
   - Timestamps from SQL are written in the file format `%Y-%m-%dT%H:%M:%SZ` (UTC), so the legacy `last_used_at` throttle keeps parsing them.
   - Document: `{"schema_version": 2, "tokens": [...]}`. Other top-level keys of the source file are not carried (judgment: `_load_tokens` reads only `tokens`).
4. **Scope fails closed.** The legacy file cannot hold scope. Any SQL token whose scope is not exactly `["@live"]` (an `override:` entry or a narrowed list) is written with `revoked_at` set to one export timestamp, and listed in the manifest's `token_scope_revoked` as `{credential_id, campaign_id}`. SQL is not changed. The GM re-mints such tokens after a rollback.
5. **Where files go.** A file is written only for a campaign that has SQL rows or a source file, at `staging/<root>/<cid>/integrations/obsidian/tokens.json`, where `<root>` is the root of that campaign's staged `campaign.json` (:302-306), with `_private_json`. A SQL-trashed campaign's source is read from `campaigns/<cid>` but written under `campaigns_trash/<cid>`.
6. **Rechecks before publication** (writers must be quiesced; these catch an accident):
   - Next to the `current_records` recheck (:504-507), re-read the token rows in `current_session` with the same query and compare. A difference raises `"SQL state changed during export; keep writers quiesced"`.
   - Right after the fingerprint loop (:509-520), re-resolve each campaign's source path (or none) and compare it and its SHA-256 with what was read, with the same symlink and canonical checks. A difference, including a file that appeared or vanished, raises `"Obsidian token file changed during export; keep writers quiesced"`.
7. **Evidence and verify.**
   - The manifest `report` (:527-537) gains `integration_tokens: {<cid>: {"count": n, "revoked": n, "digest": <_canonical_sha256 of the staged document>}}` and `token_scope_revoked: [...]`. Both are empty when there are no tokens, so existing callers see only two new keys.
   - `_verify_authority` (:376-430) gains one keyword-only argument, `expected_tokens: Mapping | None = None`, a map from staged `tokens.json` path to expected document; `export_runtime`'s call (:501) passes it. `None` means "expect no `tokens.json` anywhere", so a stray staged file still refuses and the existing four-argument callers (test_export_runtime.py:728-730) keep working unchanged. It re-reads every staged `tokens.json` with `_strict_document` and compares it with the expected document; it also refuses a staged `tokens.json` for a campaign it did not expect. A difference raises `"Legacy normalization changed token authority; export refused"`.
8. **Runbook.** One paragraph in "Runtime rollback export", after :109-112: restore the volume backup first, then copy the export over it; `tokens.json` always comes from the export, never from the backup; for a trashed campaign, delete the backup's `campaigns/<cid>/integrations/obsidian/tokens.json`, because the export writes that campaign's file under `campaigns_trash` and the stale active-path copy could otherwise come back; tokens listed in `token_scope_revoked` must be re-minted; a broken source token file refuses the export and must be repaired first.
9. **Unsupported direct flip.** Setting `OWNERSHIP_BACKEND=json` without this export stays unsupported (runbook :109-112). PE-14c owns the test that a direct flip never revives a revoked token; this task does not change that path.

## Approach

Allowed files:
- `tools/export_transactional_runtime.py`: token staging, rechecks, manifest and verify only.
- `tests/persistence/test_export_runtime.py`: additions only; existing cases unchanged.
- `docs/remediation/pr4b-runtime-cutover.md`: one paragraph (Decision 8).
- This brief.

1. Add a token stager called from `export_runtime` after `_write_legacy_tree`, inside the repeatable-read session (:471-473). It returns the expected documents, the source paths and fingerprints, the scope-revoked list and the manifest counts (Decisions 1-5).
2. Add the two rechecks (Decision 6), the manifest keys and the `_verify_authority` argument (Decision 7).
3. Write the runbook paragraph (Decision 8).
4. Tests use the existing `imported_store` fixture; `create_schema()` builds PE-14c's table from the models. Add token rows through PE-14c's model and token files directly on disk. For the legacy check, point `core.storage` at the export tree and call `core.obsidian_sync.verify_token`.

## Acceptance

- [ ] `test_export_writes_tokens_from_sql_not_mirror`: the source file lists a SQL-revoked token as live and lacks a SQL-minted one. The exported file has the first revoked and includes the second with the same digest. Legacy `verify_token` against the export tree accepts the minted token and refuses the revoked one. (This replaces PE-14c's former "switch to JSON mode" acceptance bullet: rollback goes through the export.)
- [ ] `test_export_keeps_unimported_file_tokens_and_file_revocations`: a file-only token is kept as read; a file revocation on a live SQL row is exported as revoked.
- [ ] `test_export_revokes_non_live_scope_tokens_in_manifest`: tokens scoped `["session.start"]` and `["@live", "override:session.end"]` are exported revoked and listed in `token_scope_revoked`; their SQL rows are unchanged.
- [ ] `test_export_refuses_malformed_token_file`: parametrized over bad JSON, a non-object, a non-list `tokens` and a record with no `token_hash`; each refuses naming the campaign and publishes nothing.
- [ ] `test_export_refuses_duplicate_or_mismatched_token_ids`: a duplicate id, an id whose `token_hash` differs from SQL, and an id belonging to another campaign each refuse.
- [ ] `test_export_refuses_token_file_changed_during_export`: changing, adding or removing a source file after staging refuses and publishes nothing.
- [ ] `test_export_refuses_token_rows_changed_during_export`: a token row minted or revoked after staging refuses with the SQL-changed message.
- [ ] `test_export_token_files_follow_trashed_campaign_root`: parametrized over `campaigns` and `campaigns_trash` sources; the staged file sits under the root of that campaign's staged `campaign.json`.
- [ ] `test_export_reads_token_source_from_active_path_of_sql_trashed_campaign`: a campaign trashed in SQL with its file under `campaigns/<cid>` exports it under `campaigns_trash/<cid>`, and the active copy wins when both exist.
- [ ] `test_export_manifest_records_token_counts_and_digest`: counts, revoked counts and digests match the staged files; a campaign with no SQL rows and no file gets no entry and no file.
- [ ] Every existing case in `tests/persistence/test_export_runtime.py` and `tests/character_workflows/test_migration.py` passes unchanged.
- [ ] `python -m pytest -q tests/persistence/test_export_runtime.py tests/character_workflows/test_migration.py tests/test_obsidian_sync.py` passes.
- [ ] `git diff --check` passes. No route, runtime setting or live token path changes.

## Out of scope / do not touch

- The token table, mint, revoke, `verify_token`, `list_tokens`, `repair_token_mirror` and the 503 mapping (PE-14c); the stream lock on token writes (PE-14d).
- The per-campaign `db -> exporting -> file` transition (PE-20, PE-24): tokens stay in SQL there.
- Option B: not chosen (user, 2026-11-14); under it this task would have been dropped.
- `DEPLOY.md`, the planner and the importer: an imported `tokens.json` is not read by the forward migration, and that does not change here.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review

Created in brief revision 1 (Claude, 2026-11-14) from R1-4: it takes over the exporter rollback gate split out of PE-14c. It has not been reviewed yet and awaits its first Codex review.

### Brief review 1 - accepted (Codex, 2026-11-14)

First review at PR #259 head `51ac0e51` against `791936f1`. **No blocking findings; accepted as the required Option A rollback task.** The separate SQL token snapshot/recheck is necessary because the existing generic exporter does not include that table. The SQL/file union preserves file-only revocation, includes SQL-only minting, refuses inconsistent identities and narrows unrepresentable scopes by revoking them in the staged legacy copy. Canonical-path checks, change-during-export refusal, active/trash precedence, staged authority verification and the rollback overlay instructions agree with the current exporter and legacy bearer reader.

Splitting this work from PE-14c is sound: it needs PE-14c's schema only, adds no migration, and can proceed alongside PE-14d/e/f without shared product files. The 200-product-line stop is workable. The 160-test-line estimate is optimistic for the failure/race/trash/scope matrix; preserve every acceptance case and report test-only growth rather than dropping cases to fit it.

This accepts the brief only. PE-14c must merge first, and PE-14g must be built before production SQL token storage is enabled. Docs CI at this head passed `changes`; test/postgresql/browser were correctly skipped. The committed diff passes `git diff --check`; no product tests ran.
