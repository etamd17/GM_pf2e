# PE-11c: Runtime reverse export of D&D characters

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** review (R1-1 fixed; returned for implementation review 2, 2026-11-14)
- **Branch:** `claude/pe-11c-dnd5e-runtime-export`, from current main after PE-11a is accepted and merged. It does not need PE-11b. Record the exact starting SHA.
- **Depends on:** [PE-11a](PE-11a-dnd5e-locator.md), for the storage-to-path map, the D&D planner enumeration and codes, and decision 1 (D&D files stay authoritative until PE-15).
- **Context:**
  - [PR4B runtime rollback export](../../remediation/pr4b-runtime-cutover.md).
  - [PE-01 contract](../../player-release/state-command-contract.md) section 7, where the PE-41 archived filter wraps this tool.
  - `tools/export_transactional_runtime.py`:
    - `_source_character`, lines 72-100, which chooses between the active and trash roots at 86-88;
    - `_reject_pending_batches`, lines 116-136;
    - `_write_legacy_tree`, lines 139-216, with the campaign root at 168-171 and the character overlay at 174-202;
    - `_verify_authority`, lines 219-249;
    - `export_runtime`, lines 252-344, with the history sidecar at 286-297, the planner gate at 299-301 and the post-staging fingerprint check at 310-317.
  - Line numbers are from main `f6636eac` (after PE-11a). PE-11a's layout names are defined in the dependency-free `core/character_layout.py` and re-exported by `core/persistence/character_files.py`.
- **Change budget:** about 280 changed product lines in `tools/export_transactional_runtime.py` (raised from 200 for the reference handling Codex asked for in R1-1). Tests in `tests/persistence/test_export_runtime.py` are budgeted separately at about 600 lines, for the 13 acceptance items including the 4x2 reference matrix. The rollback note is not counted. If product code goes past about 330, stop and report; give the test line count separately in the report.

## Goal

A SQL deployment that holds D&D campaigns and imported D&D characters can produce a runtime rollback export that a legacy JSON runtime can load. The export must not revert, invent or silently drop D&D ownership.

## The problem this brief settles

Until PE-15, the D&D routes write only files: create (`systems/dnd5e/routes.py:192/219/245`), owner reassignment (`878-893`) and delete (`690`). Today's exporter overlays SQL assignments onto every registered payload (174-202). Applied to D&D, that would cause three failures:

- **(a)** Any GM owner reassignment made since import is reverted to the import-time SQL assignment.
- **(b)** D&D characters created since import have no row, so they are left out.
- **(c)** A D&D character deleted since import makes the whole export refuse: `_source_character` raises "Registered character payload is missing".

## Approach

This follows PE-11a's decision 1: the D&D file is authoritative until PE-15.

Allowed files:
- `tools/export_transactional_runtime.py`
- `tests/persistence/test_export_runtime.py`. Replace PE-11a's interim test `test_export_refuses_registered_dnd5e_characters_until_pe_11c` (it expects "Unsafe character locator") with the cases below, built on PE-11a's `_dnd5e_store(tmp_path, *, with_character)` helper, extended rather than duplicated. Keep PE-11a's other three D&D export tests passing and unweakened (`test_export_of_a_dnd5e_campaign_without_characters_succeeds`, `test_unresolved_batch_in_the_dnd5e_store_blocks_export`, `test_symlinked_dnd5e_store_blocks_export`), and do not edit the PF2e and Cosmere cases.
- `docs/remediation/pr4b-runtime-cutover.md`, for one short D&D rollback note.

1. **`_source_character`** admits `dnd5e_characters` and builds the path through PE-11a's map.
2. **D&D payloads are copied, not overlaid.**
   - **Source.** For each `dnd5e` campaign, read `campaigns/<cid>/dnd5e/characters/`. If the campaign has `trashed_at`, also read `campaigns_trash/<cid>/dnd5e/characters/`. When a stem exists under both roots, the active copy wins, as `_source_character` already does.
   - **Which files.** A file counts only when it has a PE-11a locator, whose stem rule is `is_dnd5e_character_id` (exact `fullmatch`, never `store.valid_id`). A 32-hex stem followed by a newline is not copied as a character.
   - **Destination.** Copy every such file into the same staging root as that campaign's `campaign.json`: `campaigns_trash` when `trashed_at` is set, otherwise `campaigns` (168-171). Copy it whether or not it has a SQL row.
   - **No overlay.** Never apply the SQL ownership overlay to a D&D document.
   - **Byte-for-byte, with one exception.** Copies are byte for byte. The exception is a file whose `owner_user_id` is not a current member of the campaign, or not a SQL user. The staged copy gets `owner_user_id: null`, the source file is never changed, and the original owner goes in the drift list as `stale_owner_cleared`. `owner_user_ids`, `editor_user_ids` and `viewer_user_ids` are copied unchanged whatever their contents: for D&D the planner only warns `dnd5e_ignored_grant_field` and creates no assignment from them, and the D&D runtime does not read them, so they cannot block re-import. Only the scalar `owner_user_id` is ever nulled. Without the null, the post-staging planner would block on `assignment_user_not_member`, and a rollback should not depend on GM cleanup.
   - **Excluded ids.** `_write_legacy_tree` takes `excluded_character_ids`, an empty set in this task. The D&D scan, the drift list and the locator check in step 5 all skip files whose stem is in that set. PE-41 passes the archived ids there. Because this copy is driven by files on disk, a records-only view cannot exclude an archived D&D row. This task does not read `archived_at`.
   - **Unlocated files.** Files without a locator, such as nonconforming stems, are not character payloads. They come back with the operator's DATA_DIR merge, as every unregistered file does today; this is the module docstring's existing contract.
3. **Drift is reported, not overlaid.** The export manifest gets a `dnd5e_drift` list, which the operator reads before rollback. None of these entries refuse the export:
   - a row whose file is missing: reported and skipped;
   - a file that has no row;
   - a SQL owner that differs from the file's `owner_user_id`;
   - a file `campaign_id` that is missing or differs from its row, which is PE-11a's warning `dnd5e_character_campaign_inferred`;
   - `stale_owner_cleared`, from step 2;
   - `membership_selection_cleared` and `invite_omitted`, from step 4.

   **Not drift.** A copied file whose `id` is missing or differs from its stem, or whose `system` is missing, empty or not `dnd5e` (PE-11a blocks a missing D&D system rather than defaulting it), makes the post-staging planner block, with `dnd5e_locator_mismatch` or `invalid_character_system`. The export then refuses, with an error that names the file. Do not exclude or rewrite such files.

   Naming the file is new code. Today the planner gate (`export_runtime`, 299-301) raises only the generic "Current SQL state cannot form a valid legacy rollback tree". Find the first blocking conflict whose `code` is `dnd5e_locator_mismatch` or `invalid_character_system` and whose `path` is under `campaigns/<cid>/dnd5e/characters/` (or the trash root), and raise `RuntimeExportError` naming that `path` and code. Keep the generic message for every other blocking conflict, including `invalid_character_system` on PF2e or Cosmere files. It is raised as `RuntimeExportError`, so the OSError/ValueError handler (339-340) does not replace it.
4. **References to omitted D&D characters (Codex R1-1).** An omitted D&D id is one whose SQL row has no file (step 3), or one in `excluded_character_ids`. Both cases follow the same rule here; PE-41 needs nothing extra. An omitted character can still be named in two places:
   - a `campaign_memberships.character_id` selection;
   - an invitation's `character_id`. `identity.create_invite` (`core/persistence/identity.py:216-228`) accepts any same-campaign SQL character, and PE-11a does not prohibit importing these links.

   Today the planner blocks a missing invitation target, even with zero uses (`dangling_invite_character`, `tools/migrate_transactional_store.py:1606-1614`). It quietly nulls a missing membership target (1439-1455), and that makes the exporter's unchanged membership comparison (`export_transactional_runtime.py:223-236`) refuse. So the deletion case in step 3 cannot export unless the staged tree handles these references. The staged legacy tree does the following, and only for D&D characters in the omitted set:
   - **Membership selection.** The staged `campaign.json` member keeps its `user_id` and `role`, with `character_id: null`. Report `membership_selection_cleared` with the campaign, user and original character id.
   - **Invitation.** The invitation is left out of the staged `invites.json`, whatever its uses or revocation state. The original SQL row stays in the history sidecar (`HISTORY_FILENAME`), which already holds the full `records` and `invitation_state` (export 286-297). Report `invite_omitted` with the code, the original character id and the state: `live`, `exhausted` or `revoked`. The invite is never kept with `character_id` nulled, because that would turn a character-scoped invite into a campaign-wide one. No active invitation to a nonexistent character remains.
   - **SQL.** SQL is never changed. These are staged-output adjustments only.

5. **Verification.**
   - **Partition the existing locator check.** `_verify_authority` currently compares the locators (`id`, `campaign_id`, `system`, `legacy_root`, `legacy_storage`, `legacy_file`) of every SQL `characters` row against every exported row, and raises "Character registry changed during export validation" (237-244). Split both lists by `legacy_storage`:
     - rows that are not `dnd5e_characters` are compared exactly against SQL, unchanged;
     - for `dnd5e_characters`, the expected list is built from the D&D files actually staged in step 2, not from SQL rows. That set already leaves out `excluded_character_ids`, rows whose file is missing and unlocated files, and includes files with no row. Each expected locator's `legacy_root` is the campaign's staged root (`campaigns_trash` when `trashed_at` is set, otherwise `campaigns`), whatever the SQL row recorded, and its `campaign_id` is the staged campaign directory;
     - sort both D&D lists by (`campaign_id`, `id`) before comparing.

     A mismatch in either partition still raises the existing error.
   - The post-staging planner run sees the copied D&D files, and its D&D locators are checked as in the bullet above.
   - D&D `character_assignments` are left out of the authority-equality check, because SQL is not D&D's authority yet. Name that exclusion in a comment pointing to PE-15.
   - **Expected projection.** `_verify_authority` compares the staged tree against an expected projection of the SQL records. That projection applies exactly the step 4 adjustments:
     - memberships whose `character_id` is an omitted D&D id expect `character_id = None`;
     - invitations whose `character_id` is an omitted D&D id are expected to be absent.
     Everything else in `users`, `campaigns`, `campaign_memberships` and `invites` must match exactly.
   - PF2e and Cosmere verification is unchanged and stays strict.
   - **Changed during export.** Each copied D&D file is hashed from the bytes actually read, including files without a row, and added to the fingerprint map that `_write_legacy_tree` returns. After staging, `export_runtime` re-checks those paths at 310-317. It also re-lists each D&D source directory and refuses if the set of located files has changed. The refusal uses the existing "changed during export" error and leaves no output directory.
6. **PE-41 boundary.** PE-41's archived-row filter (contract section 7) wraps these functions, passes `excluded_character_ids`, and adds the D&D archived-row case.

## Acceptance

- [ ] **Drift after import.** Start from a SQL store forward-imported with the PE-11a planner: one D&D campaign with two D&D characters. Then, file-only: the GM reassigns one character, creates a third and deletes one. The export succeeds and contains the reassigned file's current owner and the new character, and no deleted character. The manifest lists exactly those three drifts.
- [ ] **No drift.** With no drift, the exported D&D files are byte-identical to the source. Re-importing the export with the PE-11a planner gives `import_allowed=True` with the same D&D locators.
- [ ] **Stale owner.** A D&D file whose owner was removed from the campaign exports with a null owner in the staged copy, the source unchanged, and one `stale_owner_cleared` entry. Re-import is allowed.
- [ ] **Bad file.** A D&D file with an id/stem mismatch, a missing `system`, or a system other than `dnd5e`, makes the export refuse with no output tree and an error naming the file. A stale `campaign_id` exports and is listed in `dnd5e_drift`.
- [ ] **Trashed campaign.** A trashed `dnd5e` campaign exports correctly from both source locations: one character under `campaigns_trash/<cid>/` and one under `campaigns/<cid>/`. Both land under `staging/campaigns_trash/<cid>/dnd5e/characters/`, and the planner reports `legacy_root='campaigns_trash'`.
- [ ] **Excluded id.** A file whose id is in `excluded_character_ids` is not copied, not in `dnd5e_drift`, and not counted in the locator check. References to it follow the next item.
- [ ] **References to a deleted character.** After forward import, run each case separately in two variants: a deletion variant, where the character's file is deleted, and an excluded-id variant, where the file stays present and the character's id is passed in `excluded_character_ids`:
  - (a) A live invitation scoped to the character, with uses left and not revoked.
  - (b) An exhausted scoped invitation, with zero uses.
  - (c) A revoked scoped invitation.
  - (d) Only a membership selection naming the character, with no invitation.

  Each case exports. In (a)-(c) the invitation is absent from the staged `invites.json`, present unchanged in the history sidecar, and reported as `invite_omitted` with the right state. No staged invitation is campaign-wide where the source was scoped. In (d) the member keeps the same user and role with a null `character_id`, and `membership_selection_cleared` is reported. Re-importing the export gives `import_allowed=True`, and SQL is unchanged in every case.
- [ ] **Old-store references stay strict.** The same reference to a missing PF2e or Cosmere character still refuses the export.
- [ ] **Newline stem.** On POSIX, a D&D file named `"a" * 32 + "\n" + ".json"` is not copied as a character. Skip this case on Windows, which cannot create the name.
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
- **Started from:** main `ec83deb5` (after PE-11a and the reconciled briefs, PR #256), branch `claude/pe-11c-dnd5e-runtime-export`.
- **Changed:**
  - `tools/export_transactional_runtime.py`:
    - `_source_character` builds paths through PE-11a's map.
    - New `_stage_dnd5e` copies every located D&D file (exact 32-hex stem; the active copy wins over trash) into the campaign's staged root, byte for byte. It never applies the SQL ownership overlay. A scalar `owner_user_id` that is not a current member is nulled in the staged copy only. Grant lists are copied unchanged.
    - Drift goes into the manifest's `dnd5e_drift` with these kinds: `file_without_row`, `owner_differs`, `dnd5e_character_campaign_inferred`, `stale_owner_cleared`, `row_file_missing`, `membership_selection_cleared` and `invite_omitted` (the last one with state `live`, `exhausted` or `revoked`).
    - Omitted D&D characters are rows with no staged file, which covers deleted and excluded characters alike. Membership selections of them are cleared in `campaign.json`, and invites scoped to them are left out of `invites.json`. The history sidecar is unchanged.
    - `_write_legacy_tree` takes `excluded_character_ids` and returns `(fingerprints, dnd5e)`. `export_runtime` takes `excluded_character_ids` as a keyword argument; there is no CLI flag, since PE-41 owns that.
    - `_verify_authority` leaves D&D assignments out (pointing to PE-15). It applies the membership and invite projection, and partitions the locator check: PF2e and Cosmere are compared exactly against SQL, and D&D against the staged files, sorted.
    - A bad D&D file now refuses the export with an error naming the file and the code. Other blocking conflicts keep the generic message.
    - The post-staging check now also refuses when a fingerprinted file is gone or resolves outside DATA_DIR. It also re-lists each D&D store and refuses if the set of located files changed. Both use the existing "changed during export" error.
    - A located D&D file that resolves outside DATA_DIR is refused, which covers a Windows junction.
  - `tests/persistence/test_export_runtime.py`:
    - The interim PE-11a refusal test is replaced. `_dnd5e_store` is extended rather than duplicated, and PE-11a's other three D&D tests and the PF2e and Cosmere tests are untouched.
    - New cases cover the 13 acceptance items, plus grant lists, the generic message for other conflicts, nonconforming stems, a symlinked file, a Windows junction, and a direct `_verify_authority` D&D locator check.
  - `docs/remediation/pr4b-runtime-cutover.md`: a short D&D rollback paragraph.
- **Size:** 262 changed product lines (228 added, 34 removed) against a budget of 280. Tests: 354 changed lines (335 added, 19 removed) against about 600. Docs: 10 lines.
- **Tests run (Windows, repository venv):**
  - Acceptance set (`test_export_runtime.py`, `test_migrate_transactional_store.py`, `test_dnd5e_locator.py`, with `-m "not postgresql"`): **196 passed, 1 skipped**.
  - `tests/persistence -m "not postgresql"`: **472 passed, 1 skipped (the POSIX newline case), 19 deselected**.
  - `tests/character_workflows`, which exercises `_write_legacy_tree` through a racing-writer test, ran before the review fixes: **346 passed, 14 skipped**.
  - `git diff --check`: clean.
- **Verification beyond the suite:**
  - **Mutation checks.** Each of 12 deliberate mutations makes the D&D tests fail. The mutations removed or broke: stale-owner clear, membership clear, invite omission, listing re-check, excluded skip, active-wins, named error, D&D locator comparison, deleted-file check, the exact stem rule, excluded-in-drift, and the junction containment check.
  - **Independent adversarial review.** Three lenses, each finding re-verified. Two findings were confirmed and fixed: the junction containment gap, and an under-specified history-sidecar assertion that now compares the whole row. Four were rejected after verification.
- **Skipped / not verified:**
  - The POSIX newline-stem case is skipped on Windows; it runs in CI.
  - The PostgreSQL tests run in CI only, because local Docker is retired.
  - The Windows junction case only runs on Windows.
- **Known wrong / limitations:**
  - A deleted fingerprinted PF2e or Cosmere file now reports "Character payload changed during export" rather than the generic availability error. Only the message changed; both refuse.
  - A pathologically nested D&D JSON file still ends the CLI with a traceback rather than `error:`, as the planner already does. It still refuses with nothing published.
  - Non-character D&D files (encounter, plans, images) still come back only with the operator's DATA_DIR merge, as the brief specifies.
- **Follow-ups (not done here):** PE-41 passes archived ids through `excluded_character_ids`. PE-15 moves D&D ownership authority to SQL and removes the assignment exclusion.

### Fix round 1 (R1-1), Claude, 2026-11-14

- **Changed:**
  - `tools/export_transactional_runtime.py`: new `_require_canonical(path, message)` applies PE-11a's locator rule: `path.resolve()` must equal the path itself (`root` is already resolved by `_safe_source_root`). It replaces the `is_relative_to(root)` containment check in `_source_character` (PF2e/Cosmere), in `_dnd5e_located` (D&D discovery, so it also runs in the post-staging re-listing) and in the post-staging fingerprint recheck. A junction or link anywhere in the path, including one to another campaign inside DATA_DIR, now refuses with "Character source is not at its own location", or "changed during export" when it appears after staging. Normal active/trash selection and stale `campaign_id` inference are unchanged for canonical paths.
  - Same file, found by my follow-up adversarial review of the fix: `_write_legacy_tree` read each PF2e/Cosmere file twice (`read_bytes` for the fingerprint, then `_load_json_strict` by path). A directory swapped in for the second read and back out before the recheck exported another location's payload with `verified=True` (reproduced natively). It now reads once and hashes and parses the same bytes with the same strict hooks and `_validate_postgres_json`, as `_stage_dnd5e` already did.
  - `tests/persistence/test_export_runtime.py`: the outside-DATA_DIR junction test now expects the new message. New Windows-native tests: a D&D store junctioned to another campaign's store inside DATA_DIR, a PF2e `party_data` junction inside DATA_DIR, and an identical D&D store and an identical PF2e store each swapped for a junction during export. Each asserts refusal, no output, no leftover staging tree and (for the pre-staging cases) unchanged source bytes and SQL. A cross-platform test asserts the PF2e payload is read exactly once before the recheck.
- **Tests run (Windows, repository venv):**
  - Acceptance set (`test_export_runtime.py`, `test_migrate_transactional_store.py`, `test_dnd5e_locator.py`, `-m "not postgresql"`): **201 passed, 1 skipped** (the POSIX newline case).
  - `tests/persistence tests/character_workflows -m "not postgresql"`: **823 passed, 15 skipped, 19 deselected**. `git diff --check` passes.
  - Mutation checks on the junction and read-once tests: 6 of 6 caught (containment-only helper, recheck reverted to containment, recheck removed, D&D discovery check removed, PF2e check removed, double read restored).
  - The four junction tests ran natively here (none skipped). They skip on Linux CI, which cannot create junctions; the read-once test runs everywhere.
- **Review of the fix:** three lenses (bypass, false refusals, tests), each finding re-verified. Two confirmed and fixed: the double read above, and the missing PF2e recheck test (the D&D swap test's comment also overstated which guard fires; corrected). The false-refusal lens found nothing.
- **Known wrong / limitations:** a legitimate DATA_DIR whose on-disk directory names differ in case from `campaigns`, `party_data`, `cosmere_pcs` or `dnd5e/characters` would now be refused on Windows, as PE-11a's locator already does; the app only writes the canonical names.

## Review
<!-- Codex appends numbered findings or acceptance. -->

### Reconciled brief review — accepted (Codex, 2026-11-14)

Reviewed `118a9abdb1d711cab4a2d7e74e793cb50455e88a` against merged main
`f6636eac` on [PR #256](https://github.com/etamd17/GM_pf2e/pull/256).
**The PE-11c brief is accepted with no findings. This approves the instructions,
not an implementation or a live rollback capability.**

- Preserving grant lists is correct for D&D's current authority boundary:
  the planner warns but creates no assignments from those fields, and D&D
  authorization uses the scalar owner only. Nulling only a stale scalar owner
  in the staged copy avoids changing ignored data or the source file.
- The named-error change is correctly scoped to D&D paths and the two specified
  conflict codes. Those conflicts include the file path on main. Other planner
  refusals keep their existing generic message, and `RuntimeExportError` is
  outside the generic OSError/ValueError handler.
- Partitioning locator verification is necessary for file-authoritative D&D:
  new files, deleted files and the campaign's staged trash root cannot be
  compared to an old SQL snapshot. The specified copied-file expectation and
  stable sorting cover that partition while preserving exact SQL checks for
  PF2e/Cosmere. The expected membership/invite projection remains limited to
  omitted D&D IDs; it neither broadens invitations nor changes SQL/history.
- The retained source fingerprints and directory re-list checks include files
  without SQL rows. The existing export tests are identified correctly, with
  only the interim registered-D&D refusal test replaced. The reference matrix,
  source-change cases and old-store strictness remain implementation gates.
- The separately stated test budget is acceptable for this bounded task. It
  openly exceeds the protocol's rough total-size preference; the 330-line
  product stop threshold and separate test count keep that visible. This is
  not permission to include PE-11b, archived filtering or route changes.

Verification: exporter, planner, authorization and test anchors were checked
against `f6636eac`; product files are unchanged by this PR. `git diff --check
f6636eac..118a9abd` passed. In
[CI run 43602042864](https://github.com/etamd17/GM_pf2e/actions/runs/43602042864),
`changes` passed and the three product jobs were skipped as expected. No
product tests were run for this docs-only review.

Next: Claude commits both brief reviews and the queue Log, pushes and merges
the docs PR once `changes` passes on the resulting head. PE-11c can then be
implemented first from updated main; it does not depend on PE-11b. These review
edits are uncommitted; Codex did not push, merge or change product code.

### Implementation review 1 — changes requested (Codex, 2026-11-14)

Reviewed `f8e8ddce9dd07e1109020e8789eb722d7757e2a5` against `ec83deb5`
on [PR #257](https://github.com/etamd17/GM_pf2e/pull/257).
**One finding; do not merge this head.**

1. **Implementation R1-1 (P2): reject an internal Windows junction before
   treating its contents as this campaign's characters.** In
   `tools/export_transactional_runtime.py:167-171`, `is_symlink()` misses a
   Windows junction and `is_relative_to(root)` accepts a junction whose target
   is elsewhere inside DATA_DIR. This is weaker than PE-11a's canonical-path
   check (`core/persistence/character_files.py:58-59`), which rejects the same
   path. A native Windows reproduction made D&D campaign A's
   `dnd5e/characters` directory a junction to campaign B's directory. B was a
   PF2e campaign containing an inert D&D file, a supported legacy case. The
   exporter copied B's character into A's staged directory and returned
   `status='exported', verified=True`; the forward planner then registered
   that character in A. Its stale payload campaign ID only produced drift,
   as designed, so verification did not stop the wrong-campaign copy. A
   stable junction also survives the fingerprint and directory re-list checks.

   Reject path aliases/reparse redirects using the canonical locator rule
   (or an equivalent component check), both while locating files and during
   revalidation. Keep the normal active/trash selection and stale payload
   campaign-ID inference for canonical paths. Add a native Windows regression
   for a junction to another location **inside** DATA_DIR; the existing test
   only covers an outside target. Assert refusal, no published or leftover
   staging tree, and unchanged source files and SQL. This is a verified
   attribution/locator-contract defect in the operator tool, not a claim of a
   new remote access exploit.

   Reproduction and result are in the workspace parent's
   `output/pe11bc-impl-review/junction_probe.py` and
   `junction-probe-result.json`. The result records `is_symlink=false`,
   `resolved_inside_data_dir=true`, the shared locator's `StoreUnavailable`,
   and the export's `verified=true` plus A's resulting planner locator.

Other review conclusions: the seven drift kinds, deleted/excluded omitted set,
scalar-only stale-owner correction, unchanged grant lists and history sidecar,
and narrow membership/invite projection match the brief. D&D locator checks
use the staged-file expectation; PF2e/Cosmere remain exact against SQL. Named
bad-file errors and source-change checks passed review. Supplemental synthetic
probes covered combined omitted references, owner-only drift, files without
SQL rows, strict old-store verification, edits after staging and a newly
preferred active copy. No additional finding was retained.

Verification: the three-file acceptance command passed **195 tests, 2 skipped**
(POSIX newline on Windows; the existing junction test could not create a
junction in the restricted test process). The separate native junction probe
above ran successfully and demonstrated R1-1. `git diff --check ec83deb5..HEAD`
passed. All four jobs (`changes`, `test`, `browser`, `postgresql`) passed on
this exact head in [CI run 43607515464](https://github.com/etamd17/GM_pf2e/actions/runs/43607515464).
The full local persistence suite was not repeated for this review; the
implementer's wider counts remain identified separately in the Report.

For players: the rollback safety net is built, but needs this one correction
before acceptance. This review adds no player screen and does not switch
production away from files. Next: Claude fixes R1-1, adds the regression and
returns the new head for re-review. Review and queue edits are uncommitted in
`GM_pf2e_pe11c_review`; Codex did not commit, push or merge.
