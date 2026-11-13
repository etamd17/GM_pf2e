# PE-11b: SQL-mode campaign-archive restore for D&D characters

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** ready (PE-11a merged in PR #254; reconcile against main before starting)
- **Branch:** `claude/pe-11b-dnd5e-archive-restore` from current main after PE-11a is accepted and merged. Record the exact starting SHA.
- **Depends on:** [PE-11a](PE-11a-dnd5e-locator.md): its storage-to-path map, D&D locator, `_validate_locator` and the decisions it fixes.
- **Context:**
  - [PE-01 contract](../../player-release/state-command-contract.md) section 1 (the D&D locator holds for every row).
  - `core/persistence/campaign_import.py` (`_CHARACTER_STORES` 26, `_ID_PATTERN` 27, `_json_files` 60-80, `_remap` 99-119, `_prepare` 122-193).
  - The SQL branch of the restore route, `app.py:11516-11519`.
  - The D&D layout, `systems/dnd5e/store.py:5-8`.
  - Line numbers are from main `93aede06`.
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
   - `dnd5e/characters/<file>` is the D&D store, with system `dnd5e`, **only in a `dnd5e` campaign**. In a PF2e or Cosmere campaign, `dnd5e/` files keep today's behaviour and restore as inert plain JSON, unregistered. The D&D routes have no campaign-system gate (`systems/dnd5e/routes.py:62-66, 185-193`), so such files exist in real archives. Record that gap in the report for PE-15.

   Any staged path whose leading parts casefold to a store prefix (`party_data`, `cosmere_pcs`, `dnd5e/characters`) without being spelled exactly that way refuses the import. Check this in `_json_files` on the walked directory names. app.py pre-creates `party_data/` and `cosmere_pcs/` in canonical case but not `dnd5e/`, so on Windows a `DnD5e/characters/<id>.json` entry would otherwise extract as an unregistered sheet that the case-insensitive D&D runtime still serves.

   Character files must sit directly under their prefix. The nested-directory guard in `_json_files` applies under `dnd5e/characters/` only, so `dnd5e/plans/`, `dnd5e/images/` and `dnd5e/encounter.json` still restore as plain campaign files. The store key, not the first path part, goes to `_validate_locator` and the system lookup.
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
  - a sheet under a case-variant prefix (`DnD5e/characters/<32hex>.json`, `dnd5e/Characters/<32hex>.json`), built directly in the staged tree so the test behaves the same on Linux and Windows;
  - a D&D sheet whose `system` is not `dnd5e`;
  - `dnd5e/characters/<sub>/x.json`;
  - a duplicate old id across stores.
- [ ] PF2e and Cosmere restore behaviour, including the strict campaign check and the Cosmere rename, is unchanged. A PF2e archive that contains `dnd5e/characters/<32hex>.json` still restores, with that file as inert plain JSON and no D&D row.
- [ ] The `<new id>.json` collision check is covered with a monkeypatched `new_id`.
- [ ] `python -m pytest -q tests/persistence/test_campaign_sql_import.py tests/persistence/test_runtime_http.py tests/test_campaign_backup.py tests/character_workflows/test_archive_privacy.py tests/persistence/test_dnd5e_locator.py` passes.
- [ ] `python -m pytest -q tests/persistence -m "not postgresql"` passes; `git diff --check` passes. The report gives actual counts.

## Out of scope / do not touch

- `app.py` (PE-15 owns it):
  - **JSON-mode restore (`app.py:11410-11520`).** It still keeps D&D file ids and the source `owner_user_id`, which today's D&D routes trust (`systems/dnd5e/routes.py:83`). This is a pre-existing limitation; record it in the report for PE-15.
  - **The import classifier (`app.py:11171`).** D&D sheets keep the general 4 MiB JSON limit rather than the character limits.
- `core/backups.py`: campaign-archive export keeps D&D sheets as raw files. Under PE-11a's decision 1 the file is authoritative until PE-15, so there is no SQL overlay.
- Runtime export (PE-11c), routes, schema and migrations.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
