# SE-01: PF2e sheet edit, part 1 (meta fields and GM adjustments)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Status:** review (Codex)
- **Owner:** claude (stat derivation and save format are high-risk: Claude implements)
- **Reviewer:** codex
- **Branch:** `claude/se-01-pf2e-sheet-edit-core` from current main. Record the starting SHA.
- **Depends on:** none. This is maintenance of a shipped tool and does not wait on
  the player-release PE chain.
- **Context:** the design in
  [2026-11-12-pf2e-sheet-edit-design.md](../../superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md)
  is the source of truth. CLAUDE.md "High-risk areas" applies: PB import, the
  save/reload race and inline-handler escaping.
- **Change budget:** roughly 500 changed lines including tests.

## Goal

The GM and the owning player can fix a PF2e character's meta fields on the sheet
(display name, deity, sanctification, languages, senses, size, background name; display only).
The GM can also add signed adjustments to engine-computed numbers. Both survive an
"Update <name>" re-import, and every failed save reports an error.

## Approach

1. **Re-import:** in `app.py::_merge_pf2e_import`, add `'sheet_edits'` to
   `PRESERVE_KEYS`.
2. **`Character.__init__`** (`app.py`, class `Character`):
   - Read `build.get('sheet_edits') or {}` once.
   - Apply ability adjustments to `self.mods` right after the mods loop, before
     the rule engine, and update `ability_display`.
   - Apply meta overrides after `deity`, `sanctification` and `languages` are read
     and after `senses` and `size` are set. Senses are merged with the derived ones.
   - Add `self.display_name`, defaulting to `self.name`, and `self.sheet_edits`.
3. **`Character._adj(key)`:** returns an int, 0 when absent. Add it as a term in:
   - `base_ac` and `ac`;
   - `_calc_save` (key = `prof_key`);
   - `perception`;
   - `class_dc`;
   - `spell_attack` (key `spell_dc`, so the DC follows);
   - the `self.hp` line (`hp_max`), before the drained deduction and the
     `current_hp` cap;
   - `base_speed` (`speed`);
   - each skill total in `skills` (`skill:<name>`, lores included).

   `_save_breakdown` and `hp_breakdown` gain a `GM adj` part when it is nonzero.
4. **Routes** (`app.py`, near `add_pet`). `POST /api/sheet_edits/<pc_name>` with
   `@require_pc_self_or_gm`, and `POST /api/sheet_edits/<pc_name>/reset` (GM only).
   - Flush before read via `require_pc_json`.
   - Validate per the spec. A non-GM `adjust` gets 403, and invalid input gets 400;
     both write nothing.
   - Merge the patch: a cleared meta field or a 0 adjustment deletes its key.
   - Save with `save_and_reload_character`.
   - Accept only the `meta` and `adjust` sections here. `feats` and `features`
     get 400 "not supported yet" until SE-02.
5. **UI:**
   - New `templates/_pc_sheet/_edit_modal.html` and `_js_sheet_edit.html`, included
     from `player_sheet.html`.
   - An "Edit character" item in the `⋯` menu (`_header.html`) and a pencil on the
     left-rail meta card (`_left_rail.html`).
   - A Details tab, and a Numbers tab rendered only when the GM is viewing.
   - Show `pc.display_name` in the sheet header, tracker rows, the player encounter
     view and the GM party board. Keys, URLs and handlers stay on `pc.name`.
   - Append CSS only, at the end of the stylesheet.
   - Pass names through `data-*` attributes, never inline JS strings.
   - Show the server message on a non-2xx response.

## Acceptance

- [ ] `DATA_DIR=$(mktemp -d) pytest -q tests/test_pc_snapshots.py tests/test_pb_ground_truth.py tests/test_pb_import_correctness.py` passes with no snapshot regeneration.
- [ ] New `tests/test_sheet_edits.py` covers:
  - each adjustment key moves only its stat;
  - a Con adjustment raises HP and Fortitude;
  - Frightened still lowers adjusted AC;
  - edits survive `_merge_pf2e_import` with a stale Pathbuilder deity;
  - the display name renders while URLs keep `pc.name`;
  - owner `adjust` gets 403;
  - bad size or out-of-range adjustment gets 400 with the file byte-identical;
  - a GM reset clears the edits.
- [ ] `pytest -q tests/test_pc_save_reload_race.py tests/test_inline_handler_escaping.py` passes.
- [ ] `DATA_DIR=$(mktemp -d) pytest -q` passes (full suite) and `python tools/check_templates.py` is clean.
- [ ] Dev server with an isolated `DATA_DIR`, verified in the browser pane:
  - the GM changes the deity, it shows after reload and survives a re-import;
  - a GM AC adjustment shows with the marker;
  - a player session gets no Numbers tab, and a direct POST is refused with 403.

## Out of scope / do not touch

- Feats, features, companions and Import JSON (SE-02).
- `remove_pet` and `daily_prep`, which are being changed on
  `claude/sheet-save-bugs-gm-edit-b4bb8f`.
- Real renames.
- Cosmere and D&D.
- Any existing sheet id, handler or `pc_update` repaint path.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Starting SHA:** `93aede06` (main); spec and plan commits `f96f4cdd`..`359dc435`.
- **Changed:**
  - `core/sheet_edits.py` (new): patch validation and merge. Only `meta` and `adjust` are accepted; `feats` and `features` return 400 until SE-02.
  - `app.py` `Character`:
    - `sheet_edits` is read first; ability adjustments are applied in the mods loop.
    - `_adj` terms are added to AC, the saves, Perception, class DC, spell attack (spell DC), HP (with a floor of 1 only when adjusted), speed, the skills and lores.
    - The breakdown tooltips show `GM adj`.
    - `_apply_sheet_meta` runs at the end of `__init__`.
    - New `adjusted_keys` and `display_name`.
  - `app.py` routes: `POST /api/sheet_edits/<pc_name>` (owner or GM; a non-GM `adjust` gets 403) and `/reset` (GM). Both validate before the read, flush before the read via `require_pc_json`, and broadcast `pc_update`.
  - `app.py` sheet render: `_sheet_edit_numbers`, `_sheet_edit_pathbuilder_meta`; `sheet_edits` added to `PRESERVE_KEYS`.
  - `core/route_policy.py`: `update_sheet_edits` (owner or GM by `pc_name`), `reset_sheet_edits` (live campaign GM).
  - Templates:
    - New `_pc_sheet/_edit_modal.html` (native `<dialog>`; the Numbers tab is rendered only for the GM).
    - New `_pc_sheet/_js_sheet_edit.html` (event listeners only).
    - Entry points in the `⋯` menu and the meta rail.
    - Header shows `display_name`; GM markers on the adjusted header stats.
    - A new `<style>` block after `ds.css`.
  - `tests/test_pf2e_player_sheet_ui.py`: the UI-10 inventory parser skips `data-se-additive` subtrees. The existing digests are unchanged.
- **Tests run** (Python 3.11 venv, `DATA_DIR=$(mktemp -d)`):
  - `pytest -q tests/test_sheet_edits.py`: 61 passed.
  - `pytest -q tests/test_pc_snapshots.py tests/test_pb_ground_truth.py`: all passed, including the 4 live-party snapshots (local `party_data/` copied in); no snapshot regenerated.
  - `pytest -q tests/test_pc_save_reload_race.py tests/test_inline_handler_escaping.py tests/test_player_sheet_inline_handlers.py tests/test_pf2e_player_sheet_ui.py tests/test_route_policy_inventory.py tests/test_access_policy_unit.py`: passed.
  - `python tools/check_templates.py`: all 113 templates parse.
  - Full `pytest -q`: 5462 passed, 37 failed, 187 errors.
    - That failure set is identical before and after the change.
    - All 27 non-browser failures also fail on unmodified main `93aede06` locally.
    - The 10 browser failures and all errors need Playwright or PostgreSQL, which are not installed locally; CI runs both.
- **Dev server** (isolated `DATA_DIR`, browser pane):
  - The GM fixed Go'el's deity to Ragathiel; it survived a merge re-import of the stale "Rathathiel" file.
  - AC +1 showed 27 -> 28 in the modal and in the header with the GM marker.
  - With `GM_PASSWORD` set, the player session shows no Numbers tab; `adjust` returned 403 "Only the GM can adjust numbers."; a meta edit returned 200 and kept the GM's adjustment.
- **Final review:** a fresh reviewer (Opus) found 0 critical, 2 important and 3 minor issues.
  - Fixed, each with a test that failed first:
    - Lore adjustments ignored on builder-made PCs (`lore: x` key).
    - No `pc_update` broadcast after a save.
    - A large negative spell DC adjustment hid the Spell DC row (re-graded to important).
- **CI fix (`f15f7313`):** the first CI `test` run failed `test_remediation_characterization.py::test_viewing_a_sheet_does_not_change_the_session_actor`. The sheet render read `pc.sheet_edits` on that test's name-only stub actor and returned 500; it now uses `getattr`, with a regression test. Locally that test had been hidden by a macOS temp-path failure (`/var` is a symlink, which makes `Character storage path is unsafe`). With `TMPDIR` set to a real path it passes, and 13 of the 27 "pre-existing" local failures also pass. The other 13 are backup and campaign-delete tests that fail the same way on main.
- **Skipped / not verified:**
  - `tests/test_pb_import_correctness.py` does not exist on main, so it was not run.
  - Not verified on Railway.
- **Known wrong / limitations:**
  - The Numbers tab's "computed" column includes the active conditions, raised shield, buffs and the HP floor, so the shown baseline can be off by those; the saved delta is still correct.
  - A hand-edited file with a non-dict `sheet_edits` section makes the edit route 500.
  - A `meta.size` override also changes the map token footprint, which goes beyond display.
  - Over the brief's ~500-line budget: about 1,000 lines, of which 392 are tests.
- **Follow-ups (not done here):**
  - Show `display_name` on tracker rows, the player encounter view and the GM party board. This was deferred because `tracker.html` is high-risk and those views key on `pc.name`.
  - SE-02.

## Review
