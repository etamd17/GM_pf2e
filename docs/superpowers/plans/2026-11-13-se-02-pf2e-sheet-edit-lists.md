# SE-02 PF2e Sheet Edit (feats, features, companions, Import JSON) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The GM and the owning player can add, remove and reorder feats; hide, add and reorder class/ancestry features; edit a companion in place; and import an exported JSON file back into the same character.

**Architecture:**
- Feats and features extend SE-01's `build['sheet_edits']` layer with two new sections. `Character.__init__` applies `feats.added`/`feats.removed` where `self.raw_feats` is assigned (`app.py`, around line 5275), so the rule engine, the feat loop and domain detection all see the edited list. Ordering and features are display-only.
- Companion edit reuses #252's two buckets: the edited pet is written to `pets_custom`, and an imported original is tombstoned in `pets_removed`.
- Import JSON is a new route that replaces the character's build from an uploaded export, after checks (decision D1 below).

**Tech Stack:** Flask, Jinja, vanilla JS, pytest; scratch venv Python 3.11.

**Spec:** `docs/superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md`; brief `docs/handoff/tasks/SE-02-pf2e-sheet-edit-lists.md`. Branch `claude/se-02-pf2e-sheet-edit-lists` = SE-01 tip `3fd0c4eb` + main `6fa94aa9` (#252). Rebase onto main once SE-01 merges.

## Decision for review (D1: Import JSON semantics)

The spec says to run an import through `_merge_pf2e_import`. That merge copies only the Pathbuilder rules keys from the upload and **keeps** the current file's `sheet_edits`, `pets_custom`, notes, HP and so on. A GM who fixes something in a text editor would see those fixes silently dropped, which defeats the request ("fix a sheet in a text editor and re-import it").

This plan does a **full build replace** instead:
- The uploaded file's `build` becomes the build.
- The current envelope (`id`, `campaign_id`, `owner_user_id`, `system`) is kept.
- An upload whose `id` is present and differs from the target gets 409; so does one whose `build.name` differs.
- The live state is flushed first, and the confirm step warns that HP, conditions and anything else changed since the export are replaced by the file's values.

Approve this, or tell me to keep the merge semantics.

## Global Constraints

- Everything from SE-01's plan still holds: `DATA_DIR=$(mktemp -d)`; on macOS also `TMPDIR` set to a non-symlinked directory (`/var` is a symlink and trips "Character storage path is unsafe"); no snapshot regeneration; validate before any read; flush before read; 400/403/404/409 with `{"success": false, "error": msg}`; `data-se-additive` on all new sheet markup; no inline handlers; no emojis; the commit trailer.
- New patch sections replace their list **wholesale** (the client sends the full list). An empty list deletes the key.
- Limits:
  - `feats.added` ≤ 40 entries `{name ≤ 80, type ≤ 30, level int 1..20}`, stored as `[name, None, type, level]`.
  - `feats.removed` and `feats.order` ≤ 200 names ≤ 80.
  - `features.hidden` and `features.order` ≤ 200 names ≤ 80.
  - `features.custom` ≤ 30 `{name ≤ 80, desc ≤ 2000}`.
- Permissions: `feats` and `features` are allowed for the owner and the GM. `adjust` stays GM-only.
- New routes are registered in `core/route_policy.py`: `update_pet` and `import_character_json` in the `ROUTE_PC_NAME` owner-or-GM list.

## Review Focus

1. **Removing a feat that is also in `added`**, or adding a name the build already has: no duplicate on the sheet; `added` wins over `removed` for the same name. Test in Task 1.
2. **Re-import** of a Pathbuilder file that now contains a feat the GM had added in-app: shown once. Test in Task 1.
3. **Editing an imported pet and renaming it:** the old card does not come back after a re-import (tombstone), and the new card shows once. Test in Task 3.
4. **Import JSON of a file for another character, or a non-PF2e file:** 409/400, and the file is byte-identical. Test in Task 4.
5. **Escaping:** a feat or pet named with an apostrophe (`Thieves' Tools`, `Go'el`) round-trips through the modal and the edit button with no inline JS. Test in Tasks 2 and 3 (render asserts `data-` attributes, `test_inline_handler_escaping.py` green).

---

### Task 1: Feats and features in the layer and the engine

**Files:**
- Modify: `core/sheet_edits.py` (add `feats`/`features` sections to `validate_patch`; remove the "not available yet" 400).
- Modify: `app.py` `Character.__init__` at `self.raw_feats = ...`; after the feat loop (order); `class_features` and `ancestry_features` properties.
- Test: `tests/test_sheet_edits.py` (update `test_invalid_patches_400`: `{'feats': {'added': []}}` becomes valid as a clear, so replace it with `{'feats': {'added': [{'name': ''}]}}`).

**Interfaces:**
- Produces:
  - `core.sheet_edits.validate_patch` accepts `feats: {added?, removed?, order?}` and `features: {hidden?, custom?, order?}`.
  - `Character.removed_feats: list[str]`, the Pathbuilder feat names the edit removed (for the restore list in Task 2).
  - `Character.hidden_features: list[str]`.
  - Each `self.feats` entry gains `'source': 'sheet' | 'build'`.

- [ ] **Step 1:** Failing tests:
  - `test_feat_patch_validation` (shapes and limits above, owner allowed).
  - `test_added_feat_rule_applies`: add `Toughness` to a PC without it; HP rises by the level, if Toughness has a compendium rule in this repo; otherwise assert it appears in `pc.feats` with `source == 'sheet'`. Check `COMPENDIUM_RULES` for `toughness` first and pick the right assertion.
  - `test_removed_pathbuilder_feat_gone_and_listed` (`pc.removed_feats`).
  - `test_added_and_removed_same_name_shows_once`.
  - `test_feat_order_applied` (listed names first in the given order, the rest after in original order).
  - `test_feat_edits_survive_reimport`.
  - `test_hidden_feature_not_listed_and_custom_feature_appended`.
- [ ] **Step 2:** Run; expect failures (400 "not available yet" and missing attributes).
- [ ] **Step 3:** Implement. Name matching is case-insensitive on `feat[0]`. `added` entries are skipped if the build already has that name.
- [ ] **Step 4:** Run `tests/test_sheet_edits.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py` (with `party_data/` copied from the main checkout so the live snapshots run; delete it after). All pass, with no snapshot changes.
- [ ] **Step 5:** Commit "SE-02: feats and features in the sheet edit layer".

### Task 2: Feats tab in the edit modal

**Files:**
- Modify: `templates/_pc_sheet/_edit_modal.html` (third tab "Feats", for the owner and the GM).
- Modify: `templates/_pc_sheet/_js_sheet_edit.html`.
- Modify: `app.py::_render_pf2e_sheet` (pass `se_feats`: the current `pc.feats` names/types/levels plus their source, `pc.removed_feats`, `pc.class_features` + `pc.ancestry_features` names with a hidden flag, and the custom features).
- Test: `tests/test_sheet_edits.py` render tests.

UI:
- Each feat row: name · type · level, with buttons up, down, remove. Each name lives in `data-se-feat="<name>"`.
- "Removed from import" list, each with Restore.
- "Add feat": a text input with a `<datalist>` filled from `GET /api/compendium_search?q=<text>&cat=feats` (debounced 250 ms, minimum 2 characters), plus type select (Ancestry, Class, Skill, General, Archetype, Bonus) and a level number.
- Features list with Hide/Restore, and "Add custom feature" (name and description).
- Save sends `feats: {added, removed, order}` and `features: {hidden, custom, order}` built from the current DOM state, only when changed.

- [ ] **Step 1:** Failing render tests:
  - `id="se-feats"` is present for both the owner and the GM.
  - A feat named `Thieves' Tools` renders as `data-se-feat="Thieves&#39; Tools"` with no `onclick`.
  - Removed feats are listed.
- [ ] **Step 2:** Run (fail). **Step 3:** Implement. **Step 4:** Run the render tests plus `test_pf2e_player_sheet_ui.py test_inline_handler_escaping.py test_player_sheet_inline_handlers.py` and `tools/check_templates.py`. **Step 5:** Commit "SE-02: feats tab in the sheet edit modal".

### Task 3: Edit a companion in place

**Files:**
- Modify: `app.py`; add `update_pet` after `remove_pet`.
- Modify: `core/route_policy.py`.
- Modify: `templates/_pc_sheet/_tab_pets.html` (an "Edit" button per card: `data-se-additive data-se-pet='{{ pet|tojson }}'`).
- Modify: `templates/_pc_sheet/_js_pet_form.html`. Split `savePet` into `petFromForm()` plus a send step, and when `window._petEditOriginal` is set, POST to `/api/update_pet/` with `{original, pet}` instead of `/api/add_pet/`. Ruling: this changes an existing script partial, not markup or ids.
- Modify: `templates/_pc_sheet/_js_sheet_edit.html` (the Edit click fills the existing form fields, sets `_petEditOriginal`, and opens `#pet-add-modal`; the title changes to "Edit companion").
- Test: `tests/test_sheet_edits.py`.

**Interfaces:** `POST /api/update_pet/<pc_name>` with body `{"original": str, "pet": {...form shape...}}`.
- Returns 200 `{success}`; 400 (missing or invalid); 404 (no pet named `original`).
- A custom pet is replaced in place in `pets_custom`.
- An imported pet: the edited dict is appended to `pets_custom`, and `original` is added to `pets_removed`.
- Uses `require_pc_json`, then `save_and_reload_character`, then `_broadcast_pc_state`.

- [ ] **Step 1:** Failing route tests (subprocess, Kyle fixture `tests/fixtures/kyle_l10.json`; check it has a Pathbuilder `pets` entry and otherwise inject one):
  - `test_update_custom_pet_in_place`.
  - `test_update_imported_pet_moves_to_custom_and_tombstones`, which also asserts that a re-import does not resurrect the original name.
  - `test_update_pet_unknown_404_file_unchanged`.
  - `test_update_pet_bad_body_400`.
  - A render test: the Edit button carries `data-se-pet` and no `onclick`.
- [ ] **Step 2:** Run (fail). **Step 3:** Implement. **Step 4:** Run the tests plus `test_pc_save_reload_race.py`, the route policy tests and the escaping tests. **Step 5:** Commit "SE-02: edit a companion in place".

### Task 4: Import JSON to the same character (per D1)

**Files:**
- Modify: `app.py` (`import_character_json` near `export_character`).
- Modify: `core/route_policy.py`.
- Modify: `templates/_pc_sheet/_header.html` ("Import JSON..." menu item, `data-se-additive`, with a hidden file input).
- Modify: `templates/_pc_sheet/_js_sheet_edit.html`: read the file, `confirm()` showing the name, level and the overwrite warning, then POST JSON.
- Test: `tests/test_sheet_edits.py`.

**Interfaces:** `POST /api/import_character_json/<pc_name>` with body = an exported document (`{id?, build: {...}}` or a bare build).
- 400: not a dict, no `build.name`, or `system` present and not `pf2e`.
- 409: `build.name` differs from `pc_name`, or `id` is present and differs from the current file's `id`.
- Otherwise: `require_pc_json`, then the new document = the current envelope keys + the uploaded `build`, then `save_and_reload_character` (which constructs the actor first, so an unloadable build aborts 503 without writing), then `_broadcast_pc_state`, then 200.

- [ ] **Step 1:** Failing tests:
  - `test_import_json_round_trip` (export via `/api/export_character`, change the deity and a `sheet_edits` adjustment in the JSON, import; both changes are applied and `id`/`owner_user_id` are unchanged).
  - `test_import_json_wrong_name_409`.
  - `test_import_json_wrong_id_409`.
  - `test_import_json_bad_body_400_file_unchanged`.
- [ ] **Step 2:** Run (fail). **Step 3:** Implement. **Step 4:** Run the tests plus the race and route-policy tests. **Step 5:** Commit "SE-02: import an exported JSON to the same character".

### Task 5: Verification, review, report, PR

- [ ] Full `pytest -q` (background, `TMPDIR` fixed). Compare the failure set against main the same way as SE-01.
- [ ] Dev server with an isolated `DATA_DIR` (temporary `launch.json` entries, reverted after). In the browser pane:
  - add, remove and reorder a feat on Go'el;
  - hide a class feature;
  - import `kyle_l10.json` and edit Kyle's imported companion (the card updates and the old one doesn't return after re-import);
  - export Go'el, edit the deity in the JSON, and import it.
  - Screenshots.
- [ ] A fresh whole-branch reviewer (Opus); then one fix pass, each fix test-first.
- [ ] Report in `docs/handoff/tasks/SE-02-pf2e-sheet-edit-lists.md`, queue row set to `review`, a log line. Push; open the PR based on `main` (stacked on SE-01: the PR description says to review after #253, and it shows only SE-02 commits once #253 merges).
