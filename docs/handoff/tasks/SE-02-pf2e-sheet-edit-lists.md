# SE-02: PF2e sheet edit, part 2 (feats, features, companions, Import JSON)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Status:** review (Codex)
- **Owner:** claude
- **Reviewer:** codex
- **Branch:** `claude/se-02-pf2e-sheet-edit-lists` from main after SE-01 and the
  Bug 2 `remove_pet` fix (`claude/sheet-save-bugs-gm-edit-b4bb8f`) are merged.
- **Depends on:** SE-01; the sheet save-bug fixes (Bugs 1 and 2 of the 2026-11-12
  report).
- **Context:** the design in
  [2026-11-12-pf2e-sheet-edit-design.md](../../superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md),
  sections "Data model" (`feats`, `features`), "Routes" and "Sheet UI".
- **Change budget:** roughly 500 changed lines including tests.

## Goal

The GM and the owning player can add, remove and reorder feats, and hide, add and
reorder class features, all with compendium autocomplete. They can edit a
companion in place. They can re-import an exported JSON file into the same
character.

## Approach

1. **`Character.__init__`:**
   - Apply `sheet_edits.feats.added` and `.removed` to the feat list before the
     feat loop, so the rule engine sees them.
   - Use `order` to sort the display only.
   - Use `features.hidden`, `.order` and `.custom` to shape the `class_features`
     and `ancestry_features` display.
2. **`POST /api/sheet_edits`:** accept the `feats` and `features` sections, with
   validation per the spec.
3. **`POST /api/update_pet/<pc_name>`:**
   - Find the pet by its original name in `pets_custom` or Pathbuilder
     `build['pets']`, using the same matching as the merged `remove_pet` fix.
   - Return 404 if no pet matches.
   - Write the edit to `pets_custom`, with the tombstone for Pathbuilder pets.
4. **`POST /api/import_character_json/<pc_name>`:**
   - Run the upload through `_merge_pf2e_import` into the same file, keeping the
     envelope and `sheet_edits`.
   - A name mismatch gets 409, invalid JSON gets 400, and a non-PF2e document gets 400.
5. **UI:**
   - A Feats tab in the edit modal.
   - An "Edit" button on each pet card that pre-fills the Add Companion form
     (`_tab_pets.html`, `_js_pet_form.html`).
   - "Import JSON..." in the `⋯` menu.
   - All additive, with `data-*` attributes for names.

## Acceptance

- [ ] `tests/test_sheet_edits.py` is extended:
  - an added Toughness raises HP;
  - a removed Pathbuilder feat is gone, and both survive re-import;
  - the display order is applied;
  - a hidden feature is not rendered;
  - `update_pet` edits a Pathbuilder pet and a custom pet, and an unknown pet
    gets 404;
  - Import JSON round-trips export to import with the id and owner unchanged;
  - a name mismatch gets 409.
- [ ] The regression gate and race/escaping tests from SE-01 still pass.
- [ ] `DATA_DIR=$(mktemp -d) pytest -q` and `python tools/check_templates.py` pass.
- [ ] Dev-server check in the browser:
  - add, remove and reorder a feat;
  - edit Kyle's companion;
  - export, edit and import Go'el's JSON.

## Out of scope / do not touch

- Real renames.
- Builder `?edit=` loading.
- The inventory reorg.
- Cosmere and D&D.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Base:** SE-01 (#253) plus main `6fa94aa9` (#252 save-bug fixes), later main `affb6231` via SE-01. Plan: `docs/superpowers/plans/2026-11-13-se-02-pf2e-sheet-edit-lists.md`.
- **User decision D1 (2026-11-13):** Import JSON keeps the Pathbuilder-update merge semantics. Rules data comes from the file; HP, conditions, notes, in-app companions and sheet edits are kept.
- **Changed:**
  - `core/sheet_edits.py`:
    - New `feats` section (added/removed/order) and `features` section (hidden/custom/order). Each list is replaced wholesale.
    - New `validate_pet`.
  - `app.py` `Character`:
    - `feats.added`/`removed` are applied where `raw_feats` is assigned, so the rule engine, domain detection and focus pool all see them (an added Toughness raises HP, an added Fleet adds 5 ft).
    - The display is sorted by `order`, and each feat is tagged with its `source`.
    - `class_features` and `ancestry_features` honour hidden, custom and order.
    - New `removed_feats` and `hidden_features`.
  - `app.py` routes:
    - `POST /api/update_pet/<pc_name>`: a custom pet is merged in place. An imported pet is moved to `pets_custom` and its original name is tombstoned in `pets_removed`. 404 for an unknown pet; 409 when renaming onto another companion.
    - `POST /api/import_character_json/<pc_name>`: GM only, merge per D1. 409 for another character's name or id; 400 for a nameless or non-PF2e file.
    - The sheet edit save refuses to hide a toggle that is switched on.
  - `core/route_policy.py`: `update_pet` and `import_character_json` added.
  - Templates:
    - The modal's Feats tab: remove, restore, up/down, add with compendium autocomplete, hide/show features, custom features.
    - An Edit button on each companion card, which pre-fills the existing form.
    - "Import JSON..." in the overflow menu.
    - `savePet` targets `update_pet` when editing and now reports a failed save; its old behaviour silently did nothing.
    - CSS for the new lists.
- **Tests run** (scratch Python 3.11 venv, `DATA_DIR=$(mktemp -d)`, and `TMPDIR` set to a non-symlinked directory because macOS `/var` is a symlink):
  - `tests/test_sheet_edits.py`: 90 passed.
  - Snapshots and Pathbuilder ground truth: all passed, with the live `party_data/` copied in and no snapshot regenerated.
  - Race, route-policy, UI-10 inventory and inline-handler tests: passed.
  - `tools/check_templates.py`: 113 templates parse.
  - Full `pytest -q`: 5516 passed, 23 failed, 187 errors. The 10 browser failures and every error need Playwright or PostgreSQL. The other 13 (`test_backups`, `test_campaign_soft_delete`, `test_scheduled_backups`) fail identically on unmodified main locally. The failure set is the same before and after the review fixes.
- **Dev server** (isolated `DATA_DIR`, browser pane):
  - Go'el: removed Diehard, added Fleet (Speed 25 -> 30), moved Toughness to the top, hid Raise Symbol; all persisted after reload.
  - Kyle: edited the imported "Jonathon - Mature Wyvern" to "Jonathon (Wyvern)" with AC 18. After a Pathbuilder re-import only the edited card remains.
  - Import JSON through the real menu flow: the deity came from the file, and the file's own `sheet_edits` was ignored per D1.
- **Final review:** a fresh reviewer (Opus) found 0 critical, 4 important and 6 minor issues.
  - Fixed, each with a test that failed first:
    - A player could raise stats through Import JSON (the route is now GM only).
    - Hiding a feature whose toggle was on stranded its effect.
    - Sheet feat types didn't match Pathbuilder's category names, which broke Kineticist impulses.
    - A companion edit wiped fields the form doesn't carry.
  - Re-graded and fixed:
    - Renaming a companion onto another's name (duplicates, which `remove_pet` then deleted together).
    - An imported companion type missing from the form's list was erased (fixed in JS, browser-only).
- **CI fix:** PR #255's `browser` job failed because every level-up page returned 500. The page serializes the character object to JSON, and SE-02 had stored the sheet-added feat names as a `set`. It is now a list, with a regression test (`test_levelup_page_renders_with_sheet_feat_edits`). The local suite missed this because the browser tests cannot run here.
- **Skipped / not verified:**
  - The unknown-type fix has no automated test (JS only).
  - Not verified on Railway.
  - The browser and PostgreSQL suites need Playwright and a database, which are not installed locally; CI runs both.
- **Known wrong / limitations:**
  - A feat in both `removed` and `added` also appears under "Removed from the import" in the modal.
  - A restored feat row shows level 1 until reload.
  - `feats.removed` matches by name, so a later level-up pick with the same name stays hidden.
  - "+ Add Pet" after an Edit keeps the edited values in the form.
  - Removing a parent feat leaves its `childChoice` rows.
  - The Add Companion form renders in the page flow (`position: relative` beats the `fixed` class); Edit scrolls to it.
  - About 960 changed lines outside docs, nearly double the brief's ~500-line budget.
- **Follow-ups (not done here):**
  - The five deferred minors above.
  - Make the companion form a real overlay.
  - SE-01's follow-up: `display_name` on the tracker and the party views.

## Review
