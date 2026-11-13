# SE-02: PF2e sheet edit, part 2 (feats, features, companions, Import JSON)

<!-- ============ BRIEF (written before work starts) ============ -->

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
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
