# PE-11a: D&D locator, read-side identity and forward-import planning

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready (PE-10 accepted and merged in PR #250; brief reconciled with main `93aede06` on 2026-11-11)
- **Branch:** `claude/pe-11a-dnd5e-locator` from current main. Record the exact starting SHA.
- **Depends on:** [PE-10](PE-10-dnd5e-identity.md) (merged), using the accepted PE-01 contract.
- **Followed by:**
  - [PE-11b](PE-11b-dnd5e-archive-restore.md): SQL-mode archive restore.
  - [PE-11c](PE-11c-dnd5e-runtime-export.md): runtime reverse export.

  Both depend on this task. "PE-11" in older documents means all three, with one exception: PE-40 needs only this task's D&D identities and locator, because PE-11b and PE-11c touch no item, wallet or ledger tables.
- **Context:**
  - [PE-01 contract](../../player-release/state-command-contract.md) section 1 (identifiers, D&D locator) and section 7 (archive, runtime export).
  - [PR4B transfer/rollback boundaries](../../remediation/pr4b-runtime-cutover.md).
  - The D&D store layout: `systems/dnd5e/store.py`, lines 5-8 and 29-90 (`valid_id`, `pc_path`, `list_pcs`, `load_pc`, `new_pc`).
  - Line numbers below are from `93aede06`.
- **Change budget:** about 350 changed lines including tests. If the planner part alone goes past about 250, stop and report before expanding.

**Why this was split.** The original PE-11 brief covered the locator, forward import, archive restore and runtime export. A read-only audit against `93aede06` found two problems:
- It estimated 650-750 changed lines, against the brief's 500-line split trigger.
- The brief treated `dnd5e_characters` as a directory name and missed the planner's system checks.

PE-11b and PE-11c hold the rest of the original work.

## Goal

Recognize exactly `campaigns/<cid>/dnd5e/characters/<pid>.json` as a D&D character, in both the shared identity adapter and the PR4A/PR4B planner. Make `build_plan` accept D&D campaigns and characters, and report the ones it cannot import instead of guessing. No D&D route or writer starts using any of this.

## Decisions this brief fixes (reviewer: challenge these first)

1. **D&D file authority until PE-15.**
   - Today the D&D routes authorize from the file's `owner_user_id` (`systems/dnd5e/routes.py:83`, `app.py:1250-1264`).
   - Every D&D create, reassign and delete writes only the file (`routes.py:192/219/245`, `878-893`, `690`).
   - Nothing in `systems/dnd5e` imports `core.persistence`.

   So until PE-15 switches the routes, the D&D JSON file stays authoritative for D&D content and ownership in every backend mode. SQL D&D rows are an identity index, plus the ownership snapshot taken at import.

   One SQL reader of owner grants is live today. SQL-mode `/me` lists cards from `ownership.characters_for_user` (`ownership.py:153-178`, through `core/campaigns.py:305-307` and `app.py:11749`). That function does not filter by system. Step 4 keeps D&D rows out of it, so SQL-mode `/me` keeps showing no D&D cards, as it does today. PE-15 removes that exclusion when it wires the D&D routes to SQL. PE-11c relies on this rule.
2. **D&D id rule for the locator.**
   - A D&D file has a locator only when its stem passes `systems.dnd5e.store.valid_id` (lowercase 32-hex). These are the only ids the D&D store creates or reads (`store.py:29,43,76,86`).
   - PE-10's wider repository bound (`repositories.py::_DND5E_CHARACTER_ID`, at most 64 `[A-Za-z0-9_-]`) stays as the outer database rule. It is not loosened or made public.
   - Any other file under `dnd5e/characters/` is reported by the planner, never registered. This is contract section 1: "the dry run reports nonconforming ids".
3. **D&D campaign_id.**
   - The D&D runtime lets the file's location win over the `campaign_id` inside the file (`store.py:63-66`), because restored backups carry stale ids.
   - The adapter's `_resolve_identity` stays strict: a foreign `campaign_id` fails closed, as for every store. PE-15 normalizes the id before resolving, as `load_pc` does.
   - The planner reports a mismatch as a warning and uses the containing campaign.
4. **No D&D writes through the adapter.** Until PE-15, every adapter write entry point refuses `dnd5e_characters` explicitly.

## Approach

Allowed files:
- `core/persistence/character_files.py`
- `core/persistence/ownership.py`
- `core/character_workflows/recovery.py`, only if `pending_for(store=...)` needs the logical storage name
- `tools/migrate_transactional_store.py`
- `tools/export_transactional_runtime.py`: only the import of the shared map and `_reject_pending_batches`'s directory list
- new `tests/persistence/test_dnd5e_locator.py`
- focused additions to `tests/persistence/test_migrate_transactional_store.py` and `tests/persistence/test_export_runtime.py`

1. **One storage-to-path map.** Add a public map next to `locator` in `character_files.py`:

   ```python
   LEGACY_STORAGE_DIRS = {
       "party_data": ("party_data",),
       "cosmere_pcs": ("cosmere_pcs",),
       "dnd5e_characters": ("dnd5e", "characters"),
   }
   ```

   `dnd5e_characters` is never a directory name. Within the allowed files, every place that turns a storage value into a path, or a path into a storage value, uses this map; that includes the call sites below.

   Hardcoded sites outside the allowed files stay as they are and belong to later tasks:
   - `campaign_import.py:26/179`: PE-11b.
   - `export_transactional_runtime.py:77` (`_source_character`): PE-11c.
   - `app.py:756/11418`, `core/backups.py:87`, `core/character_workflows/identity.py:72` and `publication.py:116`: PE-15.

   The workflows already refuse D&D (`identity.py:70-71`). `app.py:754-759` fails closed, because `locator()` returns `None` for `<cid>/dnd5e_characters/<file>`. Assert that in `test_dnd5e_locator.py`.
2. **`locator(path)`** (`character_files.py:15-30`) returns `(cid, logical_storage, filename)`, never the raw path parts.
   - A D&D path has exactly four parts, `<cid>/dnd5e/characters/<pid>.json`. `pid` must pass `store.valid_id`, and the directory names must match case-sensitively.
   - The symlink/resolve check (line 28) still applies, so a symlinked `dnd5e/` or `characters/` raises `StoreUnavailable`, as for the other stores. Choose the Windows case-variant behaviour (resolve normalizes case there) and assert it.
   - It must return `None` for each of these:
     - `dnd5e/plans/<32hex>.json`
     - `dnd5e/encounter.json`
     - `dnd5e/images/<file>`
     - `dnd5e/characters/<sub>/<pid>.json`
     - `dnd5e/characters/<pid>.json.tmp`, and other names that do not end in `.json`
     - stems that are uppercase, too short, too long or not hex
     - `dnd5e_characters/<pid>.json`
   - `authoritative_document`, `pending_publication` and `assert_available` keep working for the old stores. For D&D, `pending_for` returns nothing, because D&D has no pending-publication path.
3. **`_validate_locator`** (`ownership.py:39`) admits `dnd5e_characters` with the `valid_id` stem rule. **`_resolve_identity`** (`ownership.py:79`) does the following for `dnd5e_characters`:
   - It requires a non-empty `doc["id"]` equal to the stem: no locator fallback and no `new_id()`.
   - It requires `system` to be `dnd5e`. An absent system is accepted for the old stores only.
   - It keeps the strict campaign check.
4. **Write refusal and the `/me` exclusion.**
   - `register_character` (~234), `register_character_in_session` (~252), `write_character_batch` (~318) and `character_files.write_document` (73) refuse `dnd5e_characters` with a clear `ValueError` ("D&D characters are not written through the shared adapter until PE-15"). This applies both when creating and when updating an identity.
   - Put the refusal in a guard on those entry points, not in `_validate_locator`, so that reads still resolve.
   - Replace the two-way storage-to-system expressions (`ownership.py:265`, `character_files.py:99`) with an explicit map that refuses unknown storage.
   - In `ownership.characters_for_user`, add `Character.legacy_storage != "dnd5e_characters"` to the where clause, with a comment naming PE-15 as the task that removes it.
5. **Planner (`tools/migrate_transactional_store.py`).**
   - **System checks.**
     - The campaign-system check (837-856, `invalid_campaign_system`) admits `dnd5e`.
     - The character-system check (1160-1180, `invalid_character_system`) applies per store, mirroring `DND5E_LOCATOR_CHECK`: `dnd5e` only in `dnd5e_characters`, and `pf2e`/`cosmere` only in the other two stores.
     - A D&D character in a campaign whose system is not `dnd5e` is a blocking conflict.
     - Unknown systems stay rejected.
     - Admitting `dnd5e` campaigns also closes the gap described under "Context: the gap since PE-10" below.
   - **Enumeration.**
     - `_snapshot_files` (226) and the plan scan (1069) enumerate `dnd5e/characters/*.json` through the map, with `legacy_storage = "dnd5e_characters"` (1206).
     - The `unsupported_source_symlink` rule covers `dnd5e/` and `dnd5e/characters/`.
     - Nested directories and sibling entries (`plans/`, `encounter.json`, `images/`) are never snapshotted as characters.
     - A missing `dnd5e/characters/` yields no rows.
   - **Classification.** These are stable codes; PE-19's dry-run report consumes them rather than re-implementing them.
     - A stem that fails `valid_id` gets the warning `dnd5e_nonconforming_character_file` and is excluded, like `ignored_noncanonical_character_file` (1100).
     - A document whose `id` is missing or differs from its stem gets the blocking conflict `dnd5e_locator_mismatch`.
     - A `campaign_id` that is missing or differs from the containing campaign gets the warning `dnd5e_character_campaign_inferred`, and the containing campaign is used.
     - `character_campaign_mismatch` (1156) stays blocking for the other stores.
   - **Owner.**
     - For `dnd5e_characters` the parser (1213-1269) reads only the scalar `owner_user_id` (1221-1233). Null means no grant.
     - If any of `owner_user_ids`, `editor_user_ids` or `viewer_user_ids` is present and non-empty, emit the warning `dnd5e_ignored_grant_field` and create no assignment from it. This matches the runtime, which passes empty editor and viewer lists (`app.py:1264`).
     - Never infer an owner from a name or a membership.
     - D&D never writes `membership.character_id`, so `owner_without_membership_character_link` (1385) is not emitted for `dnd5e_characters`.
     - `assignment_user_not_member` and `missing_assignment_user` stay blocking.
   - **Paths.**
     - `export_store` uses the map at both joins: the source read and checksum recheck (about 2479-2488), and the destination (about 2493-2499). An import, verify and `export_store` round trip must write `campaigns/<cid>/dnd5e/characters/<pid>.json` with an unchanged checksum.
     - The `duplicate_character_id` message (1309-1318) is display-only. It prints the mapped on-disk path.
     - `content_checksum` (1209) depends only on the document and needs no change.
   - **Runtime export directory list.** Add the D&D directory to `export_transactional_runtime._reject_pending_batches` (124) through the map. Leave every other part of that file to PE-11c.
6. **Guard on route adoption.** Step 5 of the old brief stands: no D&D route opts into this adapter. Route adoption is PE-15, and PE-41 for inventory routes. Add a test that fails if any module under `systems/dnd5e/` imports `core.persistence.character_files` or `core.persistence.ownership`.

### Context: the gap since PE-10

SQL-mode `/campaigns/new` can now create a `dnd5e` campaign (`app.py:11884-11894`, then `campaign_runtime.create_campaign`). `build_plan` still rejects that campaign, so `export_transactional_runtime` (298-300) refuses the whole deployment.

Production runs `json`, so this affects only SQL rehearsals, and it fails closed.

The planner system-check change in step 5 fixes it. The SQL `known_system` CHECK already rules out unknown systems in the database, so the runtime-export test covers only the D&D case.

## Acceptance

- [ ] Exact valid D&D paths resolve to `(cid, "dnd5e_characters", "<pid>.json")`. Every lookalike in step 2, traversal and symlinks return `None` or fail closed, each with a test.
- [ ] `_resolve_identity` for D&D:
  - the matching SQL row resolves;
  - a missing row, an id/stem mismatch, a wrong system or a foreign campaign fails closed;
  - it never falls back to file-supplied ownership in SQL mode.
- [ ] Every adapter write entry point refuses `dnd5e_characters` with no SQL row change and no file write, both with no row and with an existing `upsert_identity` row.
- [ ] In SQL mode, a forward-imported D&D character with an owner assignment is absent from `ownership.characters_for_user` for its owner. The PF2e and Cosmere card tests in `tests/persistence/test_ownership_runtime.py` stay green.
- [ ] Planner fixtures cover the following:
  - a `dnd5e` campaign with three characters:
    - an owned character, which gets an owner assignment;
    - a null-owner character, which gets no assignment;
    - a stale-owner character, which gets the blocking `assignment_user_not_member`;
  - a nonconforming stem: warning, excluded;
  - an id/stem mismatch: blocking;
  - a stale `campaign_id`: warning, imported under the containing campaign;
  - a D&D file carrying `owner_user_ids`/`editor_user_ids`: warning, owner assignment only;
  - a system/store mismatch in each direction: blocking;
  - siblings and nested directories: ignored;
  - a legacy tree whose campaign system is `foo`: still `invalid_campaign_system`.
- [ ] The clean fixture has `import_allowed=True`, and import, verify and `export_store` round-trip the D&D file byte for byte.
- [ ] In `test_export_runtime.py`:
  - runtime export of a SQL store holding a zero-character `dnd5e` campaign succeeds;
  - once D&D rows are registered, runtime export refuses. This is the interim state until PE-11c, which replaces this test.
- [ ] PF2e and Cosmere locator, pending-publication, migration, `/me` and export behaviour is unchanged.
- [ ] `python -m pytest -q tests/persistence/test_dnd5e_locator.py tests/persistence/test_sql_character_http.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py tests/persistence/test_dnd5e_identity_schema.py tests/persistence/test_ownership_runtime.py` passes.
- [ ] `python -m pytest -q tests/character_workflows/test_recovery.py tests/character_workflows/test_migration.py tests/test_dnd5e_app_integration.py tests/test_system_guards.py` passes.
- [ ] `python -m pytest -q tests/persistence -m "not postgresql"` passes, and so does `git diff --check`. The report gives actual test counts and any data- or setup-dependent skips.

## Out of scope / do not touch

- Campaign-archive restore: `campaign_import.py`, PE-11b.
- Runtime export of D&D characters (`_source_character`, `_write_legacy_tree`, `_verify_authority`): PE-11c.
- `core/backups.py`, `app.py`, and every D&D route or store module. Route/store cutover is PE-15, and PE-41 for inventory.
- Character archive and the `archived_at` readers: PE-41.
- Production data, deployment settings, migrations and schema. PE-10's checks are final for this task.
- Do not weaken publication-repair barriers or claim file-plus-SQL atomicity.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
