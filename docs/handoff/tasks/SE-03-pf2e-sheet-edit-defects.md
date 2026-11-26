# SE-03: PF2e sheet edit, known defects from SE-01 and SE-02

<!-- ============ BRIEF (written before work starts) ============ -->

- **Status:** review
- **Owner:** claude
- **Reviewer:** claude
- **Branch:** `claude/se-03-sheet-edit-fixes` from main `b80fe3cc`.
- **Depends on:** SE-01 (#253) and SE-02 (#255), both merged.
- **Context:** the "Known wrong / limitations" lists in
  [SE-01](SE-01-pf2e-sheet-edit-core.md) and [SE-02](SE-02-pf2e-sheet-edit-lists.md).
- **Change budget:** roughly 300 changed lines including tests.

## Goal

Fix the defects SE-01 and SE-02 shipped with, without changing how the sheet
edit layer is stored:

1. A hand-edited file with a non-dict `sheet_edits` section makes the edit route 500.
2. A feat in both `removed` and `added` also appears under "Removed from the import".
3. A restored feat row shows level 1 until reload.
4. `feats.removed` matches by name, so a later level-up pick with the same name stays hidden.
5. "+ Add Pet" after an Edit keeps the edited values in the form.
6. Removing a parent feat leaves its `childChoice` rows.
7. The Add Companion form renders in the page flow instead of as an overlay.

Out of scope: `display_name` on the tracker and party views (SE-01 follow-up),
the Numbers tab baseline note and the `meta.size` token footprint.

## Report

- **Base:** main `b80fe3cc`.
- **Found while fixing 4:** the level-up page starts from `pc.raw_feats`, which
  SE-02 made the *edited* feat list, and `submit_levelup` saved that list as the
  build. So since SE-02 every level-up baked the sheet-added feats into
  `build['feats']` and dropped the imported feats the sheet had removed from the
  build (Restore had nothing left to bring back; only undoing that level-up did).
  Fixed with defect 4.
- **Changed:**
  - `core/sheet_edits.py` `merge_patch`: a section that is not a dict starts
    fresh when patched (1).
  - `app.py`:
    - New helpers above `Character`: `_raw_feat_type_level`,
      `_pb_feat_children`, `_split_sheet_removed_feats`.
    - `Character._edited_feats` uses the split, so a removed Pathbuilder
      `parentChoice` feat takes its `childChoice` feats with it (their rules
      too). It also records `removed_feat_rows` (name, type, level, children) (3, 6).
    - `_sheet_edit_feats`: feat rows carry their children; `removed` holds rows,
      not names, and leaves out feats the sheet re-added; those ride along as
      `replaced` (2, 3).
    - New `_levelup_feats_keep_sheet_edits`, called where `submit_levelup`
      stores the feats: echoed sheet additions are dropped, feats hidden by the
      sheet are put back, and a new pick matching a removed name replaces the
      old copy and leaves the removed list (4). The old list is kept in
      `level_history` as `previous_sheet_feats_removed`, and `revert_level`
      restores it.
  - `templates/_pc_sheet/_edit_modal.html`: removed rows carry type, level and
    children and say which child feats go with them; a hidden
    `data-se-replaced-list` carries re-added imports so a save keeps them out (2, 3, 6).
  - `templates/_pc_sheet/_js_sheet_edit.html`: Remove moves a parent's child
    rows with it; Restore (and re-adding by name) puts the row back with its
    type, level and children; "+ Add Pet" resets the form to its defaults;
    Edit no longer scrolls to the form (3, 5, 6, 7).
  - `static/css/system.css`: `.sheet-ember-host > .fixed.inset-0` keeps
    `position: fixed; z-index: 50`. The rule above it made every direct child
    of the host `position: relative; z-index: 1`, and the companion form is the
    only fixed overlay that sits directly in the host (checked on a rendered
    sheet) (7).
- **Tests run** (Python 3.11 venv, `DATA_DIR` in a temp dir):
  - `tests/test_sheet_edits.py`: 101 passed (10 new, each failed first).
  - Level-up chain, engine and prerequisite tests, inline-handler escaping: passed.
  - `tools/check_templates.py`: all templates parse.
  - Full suite without `tests/persistence`, browser and PostgreSQL: see below.
  - The feats-tab and pet-form JS was exercised in jsdom against rendered
    sheets (remove a parent, restore it, restore a server-rendered row,
    Edit then "+ Add Pet"); all checks passed. Not a committed test.
- **Skipped / not verified:**
  - The overlay fix was checked by reading the cascade, not in a real browser.
  - Not verified on Railway.
- **Known wrong / limitations:**
  - A level-up that retrains away a sheet-added feat leaves it in
    `sheet_edits.feats.added`, so it still shows.
  - Restored or re-appended imported feats go to the end of `build['feats']`
    (they are hidden, and `order` sorts the display).
- **Follow-ups (not done here):**
  - SE-01's `display_name` on the tracker and party views.

## Review
