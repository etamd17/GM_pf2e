# SE-01: PF2e sheet edit, part 1 (meta fields and GM adjustments)

<!-- ============ BRIEF (written before work starts) ============ -->

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
(display name, deity, sanctification, languages, senses, size, background text).
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
- **Changed:**
- **Tests run:**
- **Skipped / not verified:**
- **Known wrong / limitations:**
- **Follow-ups (not done here):**

## Review
