# PE-11c: Runtime reverse export of D&D characters

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked (PE-11a)
- **Branch:** `claude/pe-11c-dnd5e-runtime-export`, from current main after PE-11a is accepted and merged. It does not need PE-11b. Record the exact starting SHA.
- **Depends on:** [PE-11a](PE-11a-dnd5e-locator.md), for the storage-to-path map, the D&D planner enumeration and codes, and decision 1 (D&D files stay authoritative until PE-15).
- **Context:**
  - [PR4B runtime rollback export](../../remediation/pr4b-runtime-cutover.md).
  - [PE-01 contract](../../player-release/state-command-contract.md) section 7, where the PE-41 archived filter wraps this tool.
  - `tools/export_transactional_runtime.py`:
    - `_source_character`, lines 71-100, which chooses between the active and trash roots at 83-87;
    - `_reject_pending_batches`, lines 115-136;
    - `_write_legacy_tree`, lines 138-216, with the campaign root at 167-171 and the character overlay at 173-201;
    - `_verify_authority`, lines 218-249;
    - `export_runtime`, lines 251-345, with the post-staging fingerprint check at about 309-316.
  - Line numbers are from main `93aede06`.
- **Change budget:** about 200 changed lines including tests.

## Goal

A SQL deployment that holds D&D campaigns and imported D&D characters can produce a runtime rollback export that a legacy JSON runtime can load. The export must not revert, invent or silently drop D&D ownership.

## The problem this brief settles

Until PE-15, the D&D routes write only files: create (`systems/dnd5e/routes.py:192/219/245`), owner reassignment (`878-893`) and delete (`690`). Today's exporter overlays SQL assignments onto every registered payload (173-201). Applied to D&D, that would cause three failures:

- **(a)** Any GM owner reassignment made since import is reverted to the import-time SQL assignment.
- **(b)** D&D characters created since import have no row, so they are left out.
- **(c)** A D&D character deleted since import makes the whole export refuse: `_source_character` raises "Registered character payload is missing".

## Approach

This follows PE-11a's decision 1: the D&D file is authoritative until PE-15.

Allowed files:
- `tools/export_transactional_runtime.py`
- `tests/persistence/test_export_runtime.py`. Replace PE-11a's interim D&D-refusal test there with the cases below, and do not edit the PF2e and Cosmere cases.
- `docs/remediation/pr4b-runtime-cutover.md`, for one short D&D rollback note.

1. **`_source_character`** admits `dnd5e_characters` and builds the path through PE-11a's map.
2. **D&D payloads are copied, not overlaid.**
   - **Source.** For each `dnd5e` campaign, read `campaigns/<cid>/dnd5e/characters/`. If the campaign has `trashed_at`, also read `campaigns_trash/<cid>/dnd5e/characters/`. When a stem exists under both roots, the active copy wins, as `_source_character` already does.
   - **Destination.** Copy every file that has a PE-11a locator into the same staging root as that campaign's `campaign.json`: `campaigns_trash` when `trashed_at` is set, otherwise `campaigns` (167-171). Copy it whether or not it has a SQL row.
   - **No overlay.** Never apply the SQL ownership overlay to a D&D document.
   - **Byte-for-byte, with one exception.** Copies are byte for byte. The exception is a file whose `owner_user_id` is not a current member of the campaign, or not a SQL user. The staged copy gets `owner_user_id: null`, the source file is never changed, and the original owner goes in the drift list as `stale_owner_cleared`. The same applies to any non-empty `owner_user_ids`, `editor_user_ids` or `viewer_user_ids`. Without the null, the post-staging planner would block on `assignment_user_not_member`, and a rollback should not depend on GM cleanup.
   - **Excluded ids.** `_write_legacy_tree` takes `excluded_character_ids`, an empty set in this task. The D&D scan, the drift list and the locator check in step 4 all skip files whose stem is in that set. PE-41 passes the archived ids there. Because this copy is driven by files on disk, a records-only view cannot exclude an archived D&D row. This task does not read `archived_at`.
   - **Unlocated files.** Files without a locator, such as nonconforming stems, are not character payloads. They come back with the operator's DATA_DIR merge, as every unregistered file does today; this is the module docstring's existing contract.
3. **Drift is reported, not overlaid.** The export manifest gets a `dnd5e_drift` list, which the operator reads before rollback. None of these entries refuse the export:
   - a row whose file is missing: reported and skipped;
   - a file that has no row;
   - a SQL owner that differs from the file's `owner_user_id`;
   - a file `campaign_id` that is missing or differs from its row, which is PE-11a's warning `dnd5e_character_campaign_inferred`;
   - `stale_owner_cleared`, from step 2.

   **Not drift.** A copied file whose `id` is missing or differs from its stem, or whose `system` is not `dnd5e`, makes the post-staging planner block, with `dnd5e_locator_mismatch` or `invalid_character_system`. The export then refuses, with an error that names the file. Do not exclude or rewrite such files.
4. **Verification.**
   - `_verify_authority` and the post-staging planner run see the copied D&D files. The D&D character locators the planner finds must equal the copied file set, minus `excluded_character_ids`.
   - D&D `character_assignments` are left out of the authority-equality check, because SQL is not D&D's authority yet. Name that exclusion in a comment pointing to PE-15.
   - PF2e and Cosmere verification is unchanged and stays strict.
   - **Changed during export.** Each copied D&D file is hashed from the bytes actually read, including files without a row, and added to the fingerprint map that `_write_legacy_tree` returns. After staging, `export_runtime` re-checks those paths at about 309-316. It also re-lists each D&D source directory and refuses if the set of located files has changed. The refusal uses the existing "changed during export" error and leaves no output directory.
5. **PE-41 boundary.** PE-41's archived-row filter (contract section 7) wraps these functions, passes `excluded_character_ids`, and adds the D&D archived-row case.

## Acceptance

- [ ] **Drift after import.** Start from a SQL store forward-imported with the PE-11a planner: one D&D campaign with two D&D characters. Then, file-only: the GM reassigns one character, creates a third and deletes one. The export succeeds and contains the reassigned file's current owner and the new character, and no deleted character. The manifest lists exactly those three drifts.
- [ ] **No drift.** With no drift, the exported D&D files are byte-identical to the source. Re-importing the export with the PE-11a planner gives `import_allowed=True` with the same D&D locators.
- [ ] **Stale owner.** A D&D file whose owner was removed from the campaign exports with a null owner in the staged copy, the source unchanged, and one `stale_owner_cleared` entry. Re-import is allowed.
- [ ] **Bad file.** A D&D file with an id/stem mismatch, or with a system other than `dnd5e`, makes the export refuse with no output tree and an error naming the file. A stale `campaign_id` exports and is listed in `dnd5e_drift`.
- [ ] **Trashed campaign.** A trashed `dnd5e` campaign exports correctly from both source locations: one character under `campaigns_trash/<cid>/` and one under `campaigns/<cid>/`. Both land under `staging/campaigns_trash/<cid>/dnd5e/characters/`, and the planner reports `legacy_root='campaigns_trash'`.
- [ ] **Excluded id.** A file whose id is in `excluded_character_ids` is not copied, not in `dnd5e_drift`, and not counted in the locator check.
- [ ] **Changes during export.** Between `_write_legacy_tree` and the post-staging check, editing, adding or deleting a D&D character file makes the export refuse and leaves no output directory.
- [ ] **Old stores.** PF2e and Cosmere export behaviour and its strict authority check are unchanged. The existing PF2e and Cosmere cases in `test_export_runtime.py` pass unmodified.
- [ ] `python -m pytest -q tests/persistence/test_export_runtime.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_dnd5e_locator.py` passes.
- [ ] `python -m pytest -q tests/persistence -m "not postgresql"` passes, and so does `git diff --check`. The report gives actual counts.

## Out of scope / do not touch

- Reading `archived_at` and the archived filter itself (PE-41).
- Switching D&D authority to SQL, and showing D&D cards on SQL-mode `/me` (PE-15).
- Archive restore (PE-11b).
- Binary assets and non-character D&D files (`encounter.json`, `plans/`, `images/`). These stay in the operator's DATA_DIR merge, as today.
- Routes, schema and migrations.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
