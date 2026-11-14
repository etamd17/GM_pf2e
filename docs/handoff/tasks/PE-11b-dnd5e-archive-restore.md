# PE-11b: SQL-mode campaign-archive restore for D&D characters

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** review (implemented from main `ec83deb5`; brief accepted by Codex 2026-11-14 on PR #256)
- **Branch:** `claude/pe-11b-dnd5e-archive-restore` from current main after PE-11a is accepted and merged. Record the exact starting SHA.
- **Depends on:** [PE-11a](PE-11a-dnd5e-locator.md): its storage-to-path map, D&D locator, `_validate_locator` and the decisions it fixes.
- **Context:**
  - [PE-01 contract](../../player-release/state-command-contract.md) section 1 (the D&D locator holds for every row).
  - `core/persistence/campaign_import.py` (`_CHARACTER_STORES` 26, `_ID_PATTERN` 27, `_json_files` 60-80, `_remap` 99-119, `_prepare` 122-193).
  - The SQL branch of the restore route, `app.py:11642-11644`.
  - The D&D layout, `systems/dnd5e/store.py:5-8`.
  - `app.py` line numbers are from main `f6636eac` (after PE-11a and SE-01/SE-02). `campaign_import.py`, `store.py`, `routes.py` and `backups.py` are unchanged since `93aede06`, so their line numbers still hold. PE-11a's layout names (`LEGACY_STORAGE_DIRS`, `STORAGE_SYSTEMS`, `DND5E_STORAGE`, `is_dnd5e_character_id`) are defined in the dependency-free `core/character_layout.py` and re-exported by `core/persistence/character_files.py`.
- **Change budget:** about 200 changed lines including tests.

## Goal

A GM restoring a D&D campaign archive in SQL mode gets fresh campaign and character identities. Each D&D sheet lands at `dnd5e/characters/<new id>.json` and is registered with `legacy_storage = "dnd5e_characters"`. The encounter's references follow the new ids, and nobody but the importing GM gets access. Today `_prepare` does not register D&D sheets (`_CHARACTER_STORES` at 26 lists only `party_data` and `cosmere_pcs`). They are restored as unregistered plain JSON under their source ids and source `campaign_id`, and the encounter's references are not remapped. The generic `_remap` (112-115) already nulls `owner_user_id` and empties the grant lists in every restored JSON file, so access does not change; this task adds fresh identities, the rename and registration. Registered D&D rows stay hidden from SQL-mode `/me` until PE-15 (PE-11a step 4).

## Approach

Allowed files:
- `core/persistence/campaign_import.py`
- `tests/persistence/test_campaign_sql_import.py`
- focused additions to `tests/test_campaign_backup.py` or `tests/persistence/test_runtime_http.py`, if a route-level case is needed.

1. **Store lookup by path prefix.** Replace the first-part lookup (`relative.parts[0] in _CHARACTER_STORES`, lines 65 and 128) with PE-11a's map, matched case-sensitively on the exact prefix:
   - `party_data/<file>` and `cosmere_pcs/<file>` keep today's rules;
   - `dnd5e/characters/<file>` is the D&D store, with system `dnd5e`, **only in a `dnd5e` campaign**. In a PF2e or Cosmere campaign, flat files under `dnd5e/characters/` (including stems or ids that are not 32 hex) and all other `dnd5e/` files restore as inert plain JSON, unregistered. Steps 2-4 (registration, the id rules and the campaign-id rewrite) apply only in a `dnd5e` campaign. The D&D routes have no campaign-system gate (`systems/dnd5e/routes.py:62-66, 185-193`), so such files exist in real archives. Record that gap in the report for PE-15.

   Any staged path whose leading parts casefold to a store prefix (`party_data`, `cosmere_pcs`, `dnd5e/characters`) without being spelled exactly that way refuses the import. Check this in `_json_files` on the walked directory names. app.py pre-creates `party_data/` and `cosmere_pcs/` in canonical case but not `dnd5e/`, so on Windows a `DnD5e/characters/<id>.json` entry would otherwise extract as an unregistered sheet that the case-insensitive D&D runtime still serves.

   Character files must sit directly under their prefix. The nested-directory guard in `_json_files` applies under `dnd5e/characters/` only, so `dnd5e/plans/`, `dnd5e/images/` and `dnd5e/encounter.json` still restore as plain campaign files. The store key, not the first path part, goes to `_validate_locator` and the system lookup.

   Both `_json_files` guards (the case-variant prefix refusal and the `dnd5e/characters/` nested-directory refusal) are path-safety rules and apply in **every** campaign system: `_json_files` runs before `_prepare` and is not given the system. This deliberately changes today's behaviour for PF2e and Cosmere archives that contain such entries. The D&D routes have no campaign-system gate, so a case-variant D&D directory would still be served there on Windows, and the D&D runtime only ever writes flat `<32hex>.json` files, so nested entries are never legitimate.
2. **D&D identities.** The file stem and the document `id` must be equal, and both must pass PE-11a's `is_dnd5e_character_id` (an exact `re.fullmatch` of 32 lowercase hex characters, the same rule as `_ID_PATTERN.fullmatch`; never `store.valid_id`, which accepts a trailing newline). Anything else refuses the import. This matches PE-11a's blocking `dnd5e_locator_mismatch` and `load_pc` (`store.py:63-64`). It is deliberately stricter than the planner's warn-and-exclude for non-hex stems, because restore never publishes a partial archive. Each id is remapped to a fresh id exactly as for the other stores.
3. **Campaign id.** The D&D runtime lets the file location win over a stale `campaign_id` (`store.py:63-66`), and archives are expected to carry the source campaign's id. So for D&D only, `_prepare` sets `document["campaign_id"] = cid` before the consistency check at 165-167. It still requires `document["system"] == "dnd5e"` and a `dnd5e` campaign. PF2e and Cosmere keep the strict check.
4. **File name.** Extend the `<new id>.json` rename (179, Cosmere-only today) to D&D, with the same collision check. Then `legacy_file == id + ".json"` holds and PE-10's `DND5E_LOCATOR_CHECK` accepts the row.
5. **References and grants.**
   - The existing generic `_remap` already rewrites `dnd5e/encounter.json` `combatants[].pc_id` once the old ids are in the map. Assert this; do not add a new remap.
   - Grants are cleared by the existing code (owner `None`, empty editor/viewer lists).
   - Only the importing GM gets a membership. Rows are inserted directly, as today (`_import`, 229-234). Ids are fresh and the locator is built here, so none of PE-10's `upsert_identity` refusals can trigger. Keep it that way, and say so in a comment.

## Acceptance

- [ ] A SQL-mode archive of a `dnd5e` campaign is restored. It holds two D&D sheets (one carrying the source `campaign_id` and a source `owner_user_id`), an `encounter.json` referencing both, a plan file and an image. The result:
  - new cid;
  - each sheet at `dnd5e/characters/<new id>.json` with a matching `id`, the new `campaign_id`, `system: "dnd5e"` and no grants;
  - one `characters` row each, with `legacy_storage = "dnd5e_characters"` and a matching checksum;
  - encounter `pc_id`s equal to the new ids;
  - the plan and image unchanged;
  - only the importer as a member.
- [ ] These are refused with nothing published, and with no campaign directory or SQL row left behind:
  - a D&D file with a non-32-hex stem or id, including a 32-hex stem or `id` followed by a newline;
  - a D&D file whose stem differs from its `id` (both 32-hex);
  - a sheet under a case-variant prefix (`DnD5e/characters/<32hex>.json`, `dnd5e/Characters/<32hex>.json`), in both a `dnd5e` and a PF2e campaign, built directly in the staged tree so the test behaves the same on Linux and Windows;
  - a D&D sheet whose `system` is missing, empty or anything other than `dnd5e`, one case each (PE-11a decision: a D&D sheet's system is never defaulted or overwritten, unlike its `campaign_id`; the D&D runtime's `save_pc` forcing `system` is not a precedent for restore);
  - `dnd5e/characters/<sub>/x.json`, in both a `dnd5e` and a PF2e campaign;
  - a duplicate old id across stores.
- [ ] PF2e and Cosmere restore behaviour, including the strict campaign check and the Cosmere rename, is unchanged. A PF2e archive that contains `dnd5e/characters/<32hex>.json` and `dnd5e/characters/not-hex.json` still restores. Both files stay as inert plain JSON under their original names, and no D&D row is created.
- [ ] The `<new id>.json` collision check (line 180) is covered for both stores, and each refusal leaves no SQL row and no campaign directory:
  - **Cosmere.** Stage a sheet `cosmere_pcs/<a>.json` with id A and a sibling `cosmere_pcs/<fresh>.json` whose id B differs from `<fresh>`, with the sheet's path sorting before the sibling's so it is processed first. Monkeypatch `new_id` to return a sequence that gives A `<fresh>` and B a different value (a constant value fails allocation instead).
  - **D&D.** Here the check is defence in depth: once stem == id is enforced, no `.json` character file can be named after a fresh id, and the only other route (a `<fresh>.JSON` sibling on a case-insensitive filesystem) cannot be tested the same way on Linux and Windows. Cover it by monkeypatching `campaign_import.os.path.lexists` to report the D&D target as present.
- [ ] `python -m pytest -q tests/persistence/test_campaign_sql_import.py tests/persistence/test_runtime_http.py tests/test_campaign_backup.py tests/character_workflows/test_archive_privacy.py tests/persistence/test_dnd5e_locator.py` passes.
- [ ] `python -m pytest -q tests/persistence -m "not postgresql"` passes; `git diff --check` passes. The report gives actual counts.

## Out of scope / do not touch

- `app.py` (PE-15 owns it):
  - **JSON-mode restore (`app.py:11535-11645`).** It still keeps D&D file ids and the source `owner_user_id`, which today's D&D routes trust (`systems/dnd5e/routes.py:83`). This is a pre-existing limitation; record it in the report for PE-15.
  - **The import classifier (`app.py:11296`).** D&D sheets keep the general 4 MiB JSON limit rather than the character limits.
- `core/backups.py`: campaign-archive export keeps D&D sheets as raw files. Under PE-11a's decision 1 the file is authoritative until PE-15, so there is no SQL overlay.
- Runtime export (PE-11c), routes, schema and migrations.
  - Interim consequence of PE-11a: `_source_character` in `tools/export_transactional_runtime.py` refuses every registered `dnd5e_characters` row with "Unsafe character locator; export refused", and one such row stops the whole export (`tests/persistence/test_export_runtime.py::test_export_refuses_registered_dnd5e_characters_until_pe_11c`). Migration import already registers D&D rows; after this task a SQL-mode restore of a D&D campaign with sheets does too, so runtime export refuses for that database until PE-11c merges. This fails closed and production runs JSON mode. Do not touch the exporter here; record the consequence in the report.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Starting SHA:** main `ec83deb5`.
- **Changed:**
  - `core/persistence/campaign_import.py` (+48/-8). Store lookup is now by exact path prefix (`_store`, built from PE-11a's `LEGACY_STORAGE_DIRS`). `_json_files` refuses, in every campaign system: a case-variant store prefix, a nested directory under any store, and (added after the internal review) a case-variant `.json` extension inside a store, such as `<id>.JSON`, which a case-insensitive filesystem would serve as `<id>.json` while registration never saw it. `_prepare` registers `dnd5e/characters/<file>` only in a `dnd5e` campaign. There it requires `stem == id`, both passing `is_dnd5e_character_id`, rewrites the D&D `campaign_id` to the new cid before the strict check (the system is never defaulted), and extends the `<new id>.json` rename and collision check to D&D. A comment in `_import` records why no PE-10 `upsert_identity` refusal can apply.
  - `tests/persistence/test_campaign_sql_import.py` (+223): the full D&D restore; 9 refusal defects (the duplicate case now pins the `duplicate` message); a newline stem (POSIX only); case-variant prefixes (4 variants x 2 systems); a case-variant `.JSON` extension (D&D and PF2e store x dnd5e and PF2e campaign); a nested D&D directory in a PF2e campaign; the PF2e/Cosmere strict campaign check; a PF2e archive keeping D&D files inert; Cosmere and D&D rename collisions.
- **Tests run (Windows, SQLite):**
  - `tests/persistence/test_campaign_sql_import.py`: 47 passed, 1 skipped (the newline-stem test, which Windows cannot create).
  - Acceptance command (five files, `-m "not postgresql"`): 130 passed, 1 skipped when the three `tests/persistence` files are listed first. In the brief's exact order it gives 108 passed, 1 skipped and 22 errors, all in the unchanged `test_dnd5e_locator.py` with "fixture 'sqlite_database' not found": on pytest 9.1.1, a `tests/persistence` file listed after a file from another directory (with an earlier persistence file already collected) does not get `tests/persistence/conftest.py`. This is a collection-order quirk of the command, not a product failure; the same tests pass in the order above and in the full run below.
  - `tests/persistence tests/character_workflows -m "not postgresql"`: 815 passed, 15 skipped, 19 deselected. `git diff --check` passes.
  - Mutation check: 11 mutations of the new code (case-prefix guard, extension guard, nested guard, D&D-in-any-system, stem check, campaign rewrite removed, campaign rewrite for every store, D&D rename, collision check, duplicate check, defaulted system), all caught.
  - Internal adversarial review (3 reviewers, 6 verifiers): 4 findings confirmed and fixed (the `.JSON` bypass reported three times, the missing strict-campaign test, the vacuous duplicate case); 1 rejected (the encounter set-equality assertion cannot hide a crossed mapping, because one `ids` map drives every rewrite).
- **Skipped / not verified:** PostgreSQL tests run only in CI. The newline-stem test is POSIX-only. No route-level test was added; the route already hands the stage to `import_staged_campaign` unchanged.
- **Known wrong / limitations:**
  - Over budget: about 56 product and 223 test lines against about 200 including tests. The extra lines are tests the brief's acceptance asks for, plus the review fixes.
  - The `.JSON` extension guard is a path-safety rule beyond the brief's text. It applies only inside store directories, in every system, so non-character files elsewhere restore as before.
  - JSON-mode restore (`app.py`) still keeps D&D file ids and the source `owner_user_id`, which today's D&D routes trust. Pre-existing; PE-15.
  - The D&D routes have no campaign-system gate, so PF2e/Cosmere archives can hold `dnd5e/` files; they restore inert. PE-15.
  - Until PE-11c merges, runtime export refuses a database that holds a registered D&D row, which a SQL-mode D&D restore now creates. Fails closed; production runs JSON mode.
- **Follow-ups (not done here):** PE-15: JSON-mode restore identities and the D&D route system gate.

## Review
<!-- Codex appends numbered findings or acceptance. -->

### Reconciled brief review — accepted (Codex, 2026-11-14)

Reviewed `118a9abdb1d711cab4a2d7e74e793cb50455e88a` against merged main
`f6636eac` on [PR #256](https://github.com/etamd17/GM_pf2e/pull/256).
**The PE-11b brief is accepted with no findings. This approves the instructions,
not an implementation or a live restore capability.**

- The all-system prefix and nested-directory refusals are an explicit,
  acceptable compatibility change. They protect character paths before
  system-specific registration. Canonically spelled flat D&D files in a
  non-D&D campaign retain the existing unregistered treatment; the new guards
  are the stated exception to otherwise unchanged PF2e/Cosmere behavior.
  The refusal cases and the positive inert-file case distinguish these paths.
- Requiring an explicit D&D system while rewriting only its campaign ID is
  consistent with PE-11a and the existing strict importer. Missing, empty and
  wrong systems have separate acceptance cases. Fresh IDs, renamed files,
  remapped encounter references and cleared grants remain required.
- The collision prescriptions reach the intended check. A Cosmere file can
  have a filename unrelated to its old ID, allowing the sibling-file fixture
  to survive allocation and hit `lexists`. D&D's exact stem/ID equality and
  allocator exclude that construction; a target-specific `lexists` override
  tests its remaining defensive refusal without failing allocation instead.
- The updated app anchors, unchanged importer/store anchors and shared-helper
  location match main. The interim whole-export refusal for registered D&D
  rows is accurate and remains PE-11c's responsibility. Building PE-11c first,
  as the queue proposes, avoids extending that interim limitation.

Verification: the product files are unchanged between the base and this docs
head; source claims were checked against `f6636eac`. `git diff --check
f6636eac..118a9abd` passed. In
[CI run 43602042864](https://github.com/etamd17/GM_pf2e/actions/runs/43602042864),
`changes` passed and the three product jobs were skipped as expected. No
product tests were run for this docs-only review.

Next: Claude commits both brief reviews and the queue Log, pushes and merges
the docs PR once `changes` passes on the resulting head, then implements the
accepted briefs from updated main. These review edits are uncommitted; Codex
did not push, merge or change product code.
