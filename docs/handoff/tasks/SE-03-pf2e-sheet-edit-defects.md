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
    - New `_sheet_list`: every list read from the edit layer ignores a
      hand-edited value that is not a list, so the sheet still loads.
  - `templates/_pc_sheet/_edit_modal.html`: removed rows carry type, level and
    children and say which child feats go with them; a hidden
    `data-se-replaced-list` carries re-added imports so a save keeps them out.
    Dropping the sheet's copy moves the import's copy into the Removed list (2, 3, 6).
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
  - `tests/test_sheet_edits.py`: 102 passed (11 new, each failed first).
  - New `tests/browser/test_sheet_edit_browser.py` (CI `browser` job): the
    companion form is `position: fixed` over the window and starts blank after
    an Edit; a restored feat keeps its level. Not run locally (no Playwright).
  - Level-up chain, engine and prerequisite tests, inline-handler escaping: passed.
  - `tools/check_templates.py`: all templates parse.
  - Full suite without `tests/persistence`, browser and PostgreSQL: stopped at a
    third with no failures (too slow here); CI runs it whole.
  - The feats-tab and pet-form JS was exercised in jsdom against rendered
    sheets (remove a parent, restore it, restore a server-rendered row,
    Edit then "+ Add Pet", drop the sheet copy of a replaced import); all
    checks passed. Not a committed test.
- **Skipped / not verified:**
  - The overlay fix was checked by reading the cascade; the new browser test is
    its real-browser check and runs in CI only.
  - Not verified on Railway.
- **Known wrong / limitations:**
  - A level-up that retrains away a sheet-added feat leaves it in
    `sheet_edits.feats.added`, so it still shows.
  - Restored or re-appended imported feats go to the end of `build['feats']`
    (they are hidden, and `order` sorts the display).
  - A level-up page left open while the GM removes a feat X, then submitted
    with X in it, treats X as a new pick: X comes back and leaves the removed list.
- **Follow-ups (not done here):**
  - SE-01's `display_name` on the tracker and party views.

## Review

### Review 1 (claude, fresh reviewer, 2026-11-26)

0 P1, 1 P2, 5 P3. XSS in the new `data-se-children` attributes, builder-format
feats, non-list feat entries, characters without edits and domain detection
were probed and found fine.

- **R1-1 (P2, fixed):** the level-up reconcile skipped a hidden child feat when
  a kept feat shared its name, so restoring the parent could not bring the child
  back. Now only a name the page could not have echoed blocks it. Test extended.
- **R1-2 (P3, fixed):** undo replaced the whole removed list, losing removals
  made after the level-up. It now adds back only the names the level-up took off
  (`sheet_feats_unremoved`).
- **R1-3 (P3, not fixed):** a stale level-up page can bring back a feat the GM
  removed meanwhile. Listed under Known wrong.
- **R1-4 (P3, fixed):** a non-list value inside `feats` or `features` (for
  example `removed: 5`) raised TypeError and broke the sheet. Pre-existing; now
  read through `_sheet_list`, with a test.
- **R1-5 (P3, fixed):** dropping the sheet copy of a replaced import left the
  import's copy invisible until reload. It now moves to the Removed list.
- **R1-6 (P3, partly fixed):** JS behaviour had no committed test. Added the
  browser test above; the drop/restore-with-children flow is still covered only
  by the one-off jsdom run. The QUEUE.md row is left to a separate docs change so
  the three open pull requests do not conflict on it.

### CI follow-up (claude, 2026-11-26)

- **CI-1 (P3, fixed):** the browser job failed on Kyle, who has Additional Lore
  twice (Awarded at 9, Skill Feat at 4). Removal is saved by name, so a save drops
  both copies, but the dialog removed only the clicked row, and Restore put back
  nothing because the other copy was still listed. Pre-existing since SE-02. Now
  Remove takes every imported copy of that name, the removed row lists each copy's
  type and level (`copies`), and Restore puts them all back. Unit and browser
  tests added; the single-feat browser test now picks a feat taken once.
