# SE-01 PF2e Sheet Edit (meta + GM adjustments) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The GM and the owning player can fix a PF2e character's meta fields on its
sheet, and the GM can add signed adjustments to engine-computed stats. Both
survive re-import.

**Architecture:** A pure module `core/sheet_edits.py` validates and merges patches
into `build['sheet_edits']`. `Character.__init__` reads that layer: ability
adjustments go into `self.mods` before the rule engine, meta overrides replace
the matching attributes, and `Character._adj(key)` adds a term to each derived
stat. One write route, plus a GM reset route. A new modal partial on the sheet.

**Tech Stack:** Flask, Jinja, vanilla JS, pytest. Python 3.11+
(`~/.local/bin/python3.11`; the system python3 is 3.9 and cannot import `app`).

**Spec:** `docs/superpowers/specs/2026-11-12-pf2e-sheet-edit-design.md`; brief
`docs/handoff/tasks/SE-01-pf2e-sheet-edit-core.md`.

## Global Constraints

- Run every pytest with `DATA_DIR=$(mktemp -d)`; the checkout's own data files make account routes 503.
- A document without `sheet_edits` derives byte-identically to today. Never regenerate snapshots.
- Adjustments are ints in [-20, 20]. Zero deletes the key.
- `adjust` keys: `str dex con int wis cha ac fortitude reflex will perception hp_max speed class_dc spell_dc`, plus `skill:<lowercase skill>` for the 16 skills and `skill:lore:<name>`.
- `meta` keys: `display_name deity sanctification languages senses size background`.
  - Strings are trimmed and at most 80 characters.
  - Lists hold at most 30 strings of at most 40 characters each.
  - `size` must be one of Tiny, Small, Medium, Large, Huge or Gargantuan.
  - An empty string or list deletes the key.
- `adjust` from a non-GM gets 403. Any validation failure gets 400 `{"success": false, "error": msg}`. Both write nothing.
- Routes flush before read (`require_pc_json`) and save via `save_and_reload_character`.
- Player sheet changes are additive: new partials, CSS appended at the end of the stylesheet, and no existing id or handler moved.
- Names reach JS through `data-*` attributes, never inline JS strings.
- No emojis anywhere.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Clearing a meta field** (deity set to "") reverts to the Pathbuilder value instead of showing "". Test in Task 2.
2. **A negative `hp_max` adjustment larger than the computed HP** keeps max HP at least 1, and current HP is capped to it. Test in Task 2.
3. **A `spell_dc` adjustment on a non-caster** (`spell_attack == 0`) does not create a spell DC. Test in Task 2.
4. **Live HP changed but not yet flushed**, then a meta edit: the edit must not revert the HP. This is covered by `require_pc_json` and `tests/test_pc_save_reload_race.py`; test in Task 3.
5. **Wrong types** (`"2"` as an adjustment, a non-list for languages, an unknown key, a non-dict body) get 400 with the file byte-identical. Test in Task 1 (pure) and Task 3 (route).

---

### Task 1: Pure validate/merge module

**Files:**
- Create: `core/sheet_edits.py`
- Test: `tests/test_sheet_edits.py`

**Interfaces:**
- Produces:
  - `ADJUST_KEYS: frozenset[str]` (base keys, without the `skill:` ones)
  - `META_KEYS: tuple[str, ...]`
  - `SIZES: tuple[str, ...]`
  - `class SheetEditError(ValueError)` with attribute `status: int` (400 or 403)
  - `validate_patch(patch: object, *, is_gm: bool) -> dict`, which returns the cleaned patch with only the `meta`/`adjust` sections present, or raises `SheetEditError`. Sections `feats`/`features` raise 400 "Feat and feature editing is not available yet."
  - `merge_patch(existing: dict | None, clean: dict) -> dict`, which returns a new `sheet_edits` dict. A cleared meta field or 0 adjustment deletes its key, and empty sections are dropped.
- [ ] **Step 1: Write the failing tests**

```python
from core.sheet_edits import validate_patch, merge_patch, SheetEditError
import pytest

def test_adjust_requires_gm():
    with pytest.raises(SheetEditError) as e:
        validate_patch({'adjust': {'ac': 1}}, is_gm=False)
    assert e.value.status == 403

@pytest.mark.parametrize('bad', [
    {'adjust': {'ac': '2'}}, {'adjust': {'ac': 21}}, {'adjust': {'nope': 1}},
    {'meta': {'size': 'Colossal'}}, {'meta': {'languages': 'Common'}},
    {'meta': {'deity': 'x' * 81}}, {'meta': {'bogus': 'x'}}, [], 'str',
    {'feats': {'added': []}}, {'adjust': {'ac': True}},
])
def test_invalid_patches_400(bad):
    with pytest.raises(SheetEditError) as e:
        validate_patch(bad, is_gm=True)
    assert e.value.status == 400

def test_skill_and_lore_keys_accepted():
    clean = validate_patch({'adjust': {'skill:athletics': 2, 'skill:lore:warfare': -1}}, is_gm=True)
    assert clean['adjust'] == {'skill:athletics': 2, 'skill:lore:warfare': -1}

def test_merge_clears_and_keeps():
    cur = {'meta': {'deity': 'Ragathiel', 'size': 'Small'}, 'adjust': {'ac': 1}}
    out = merge_patch(cur, validate_patch({'meta': {'deity': ''}, 'adjust': {'ac': 0}}, is_gm=True))
    assert out == {'meta': {'size': 'Small'}}
    assert cur['meta']['deity'] == 'Ragathiel'   # input not mutated
```

- [ ] **Step 2: Run to verify failure**

Run: `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q tests/test_sheet_edits.py`
Expected: an ImportError for `core.sheet_edits`.

- [ ] **Step 3: Implement `core/sheet_edits.py` with the interfaces above and the limits from Global Constraints**

Reject `bool` explicitly (it is an `int` subclass). Trim strings. Keep `skill:` keys lower-case.

- [ ] **Step 4: Run to verify pass.** Same command; expected: all pass.

- [ ] **Step 5: Commit** `core/sheet_edits.py tests/test_sheet_edits.py` with the message "SE-01: sheet_edits validate/merge module".

### Task 2: Character applies the layer

**Files:**
- Modify `app.py`:
  - `Character.__init__`: after the mods loop (around line 5306), after the `senses` derivation, and after `deity`/`sanctification`/`languages` (around line 5913) and `size`/`background` (around line 5165).
  - `base_ac`/`ac` (around line 6758), `_calc_save`, `perception`, `class_dc`, `spell_attack`, `_save_breakdown`, `hp_breakdown`, `skills`.
  - The `self.hp` line (around line 6189) and `base_speed` (around line 6201).
- Modify: `app.py::_merge_pf2e_import`: add `'sheet_edits'` to `PRESERVE_KEYS`.
- Test: `tests/test_sheet_edits.py`

**Interfaces:**
- Consumes: the `sheet_edits` dict shape from Task 1.
- Produces:
  - `Character.sheet_edits: dict` (always a dict)
  - `Character.display_name: str`
  - `Character._adj(key: str) -> int`
  - `Character.adjusted_keys: frozenset[str]`, the keys with a nonzero adjustment, used by the UI marker in Task 4.

Implementation notes:
- `_adj` returns `safe_int(self.sheet_edits.get('adjust', {}).get(key), 0)`.
- The ability adjustment is applied as `self.mods[k] += self._adj(k)` inside the loop, before `display_mod` is built. `_adj` must therefore be usable before the rest of `__init__` runs, so set `self.sheet_edits` early, next to `self._build_ref`.
- Apply `hp_max` before the drained deduction, then `self.hp = max(1, self.hp)`.
- `spell_attack` adds `self._adj('spell_dc')` only when the computed attack is greater than 0.
- Breakdown strings append `GM adj +N`/`GM adj -N` when the value is nonzero.
- Meta `background` overrides `self.background`. `senses` are unioned with the derived list, order preserved and duplicates dropped.

- [ ] **Step 1: Write the failing tests** (in-process: `import app; from copy import deepcopy`; load `tests/fixtures/goel_l10.json`; build with `app.Character(doc, '')`; a helper `mk(edits)` sets `doc['build']['sheet_edits'] = edits`)

```python
def test_no_edits_identical(goel):            # every derived number equal with {} and with no key
def test_each_adjust_moves_only_its_stat(goel):
    # for key, getter in [('ac', ac), ('fortitude', fort), ('reflex', ref), ('will', will),
    #   ('perception', perception), ('class_dc', class_dc), ('speed', speed), ('hp_max', hp)]:
    # +2 on that key -> that getter +2, every other getter unchanged
def test_con_adjust_flows_to_hp_and_fort(goel):  # con +1 -> fort +1, hp +level
def test_skill_adjust(goel):                     # skill:religion +3 -> Religion total +3, others same
def test_frightened_still_applies(goel):         # ac +1 and frightened 1 -> base+1-1
def test_spell_dc_adjust(goel):                  # Go'el casts: spell_dc +2; non-caster (spell_attack 0) stays 0
def test_hp_floor(goel):                         # hp_max -20 at a low level -> hp >= 1, current_hp <= hp
def test_meta_overrides_and_clear(goel):         # deity 'Ragathiel' shown; deity key absent -> PB deity
def test_display_name_default_and_set(goel):     # default == name; set -> display_name changes, name unchanged
def test_edits_survive_reimport(goel):
    # existing = goel with sheet_edits {'meta':{'deity':'Ragathiel'},'adjust':{'ac':1}}
    # merged = app._merge_pf2e_import(existing, stale_pb_with_deity_Rathathiel)
    # Character(merged).deity == 'Ragathiel' and ac == base + 1
def test_adjusted_keys_marker(goel):             # {'ac'} when only ac nonzero
```

Write each as a full test with real assertions against the getters named above.

- [ ] **Step 2: Run to verify failure.** Run `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q tests/test_sheet_edits.py`; the new tests fail.

- [ ] **Step 3: Implement the hooks and the `PRESERVE_KEYS` entry**, per the notes above.

- [ ] **Step 4: Run the new tests and the regression gate**

Run: `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q tests/test_sheet_edits.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py`
Expected: all pass, with no snapshot files changed (`git status tests/snapshots` is clean). `tests/test_pb_import_correctness.py` is not on main; record that in the report.

- [ ] **Step 5: Commit** "SE-01: Character applies sheet_edits (meta, GM adjustments)".

### Task 3: Write routes

**Files:**
- Modify: `app.py`; add the routes after `remove_pet` (around line 25409). Do not edit `add_pet`/`remove_pet`.
- Test: `tests/test_sheet_edits.py`

**Interfaces:**
- Consumes: `validate_patch`, `merge_patch`, `SheetEditError`; `require_pc_json(pc_name) -> (pc_json, file_path, err)`; `save_and_reload_character`.
- Produces:
  - `POST /api/sheet_edits/<pc_name>`: body is a patch. Returns 200 `{"success": true, "sheet_edits": {...}}`, or 400/403 `{"success": false, "error": msg}`. Decorated with `@require_pc_self_or_gm`.
  - `POST /api/sheet_edits/<pc_name>/reset`: body `{"section": "meta"|"adjust"}`, or `{}` for all. Decorated with `@gm_required`; it returns 200 like the route above.

- [ ] **Step 1: Write the failing route tests.** Use the subprocess pattern from `tests/test_two_hand_grip.py`: `DATA_DIR` set to a temp dir, `GM_PASSWORD=''`, import `goel_l10.json` via `/api/import_pathbuilder`.

```python
# test_route_meta_roundtrip: POST meta deity 'Ragathiel' -> 200;
#   assert A.PARTY_LIBRARY[name].deity == 'Ragathiel' and the file's build.sheet_edits.meta.deity == 'Ragathiel'
# test_route_owner_adjust_403: monkeypatch A._is_gm = lambda: False after import
#   (GM_PASSWORD '' keeps @require_pc_self_or_gm open) -> POST adjust -> 403; file bytes unchanged
# test_route_bad_input_400_file_unchanged: POST {'meta': {'size': 'Colossal'}} and POST b'not json'
#   -> 400 each; file bytes identical to before
# test_route_flushes_dirty_hp_first: set A.PARTY_LIBRARY[name].current_hp = 5; A._persist_pc_combat_state(name)
#   (marks dirty, not flushed); POST meta -> file's build.current_hp == 5
# test_route_reset_gm_only: seed adjust ac 1; reset {'section':'adjust'} -> sheet_edits has no 'adjust';
#   with _is_gm patched False -> 403
```

Each test prints a sentinel, and the outer assert checks for the sentinel and shows stdout/stderr on failure. Confirm the dirty-marking helper name with `grep -n "def _persist_pc_combat_state" app.py` before writing that test.

- [ ] **Step 2: Run to verify failure** (404s on the new routes).

- [ ] **Step 3: Implement both routes.**
  - Order: `request.get_json(silent=True)`, then `validate_patch` (map `SheetEditError` to its status), then `require_pc_json`, then `merge_patch`, then write `build['sheet_edits']` (pop the key when the merge returns `{}`), then `save_and_reload_character`.
  - Validation happens before the read so a rejected patch never touches the file.

- [ ] **Step 4: Run** `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q tests/test_sheet_edits.py tests/test_pc_save_reload_race.py`. Expected: all pass. The race test must stay green; if it flags the new routes, the read order is wrong.

- [ ] **Step 5: Commit** "SE-01: sheet_edits write and reset routes".

### Task 4: Sheet UI and display name

**Files:**
- Create: `templates/_pc_sheet/_edit_modal.html`, `templates/_pc_sheet/_js_sheet_edit.html`
- Modify:
  - `templates/player_sheet.html`: include both partials, next to the other `_pc_sheet` includes.
  - `templates/_pc_sheet/_header.html`: an "Edit character" menu item after "Daily Prep", and the header name uses `pc.display_name`.
  - `templates/_pc_sheet/_left_rail.html`: a pencil button in the meta card header; the deity row shows when set.
  - Append CSS to the end of the player sheet's stylesheet. Find it with `grep -n "<style\|\.css" templates/player_sheet.html | head`.
  - `app.py::_render_pf2e_sheet`: pass `is_gm=_is_gm()` and `computed_stats` (below).
  - Display name on the tracker rows, the player encounter view and the GM party board: find each name render with `grep -n "c.name\|pc.name" templates/tracker.html templates/player*.html templates/party*.html`, and change only display text, never keys, URLs or handler arguments.
- Test: `tests/test_sheet_edits.py`

**Interfaces:**
- Consumes: `Character.display_name`, `Character.adjusted_keys`, `Character._adj`, `Character.sheet_edits`, and the Task 3 routes.
- Produces: `computed_stats: list[dict]` passed to the template. Each entry is `{key, label, computed, adj}`, where `computed` = the current total minus `_adj(key)`. Order: abilities, AC, saves, Perception, HP max, speed, class DC, spell DC (only if the PC casts), then skills.

UI behaviour:
- The modal has two tabs. The Numbers tab is wrapped in `{% if is_gm %}`.
- Details fields use the Pathbuilder values as placeholders, read from `pc._build_ref`.
- Save sends only the changed sections. It disables the button while in flight; on a non-2xx response it shows `error` in the modal and stays open; on a 2xx response it calls `location.reload()`.
- An adjusted stat on the sheet gets `<span class="gm-adj-dot" title="GM adjustment">GM</span>` when `key in pc.adjusted_keys`, added next to AC, the saves and Perception only. Skills get it in their breakdown text from Task 2.

- [ ] **Step 1: Write the failing render tests** (subprocess, GM open mode). GET `/characters/<id>`: look up the id from the imported file's `id`, or use the route that `tests/test_pf2e_player_sheet_ui.py` uses to render a sheet.
  - The HTML contains `id="sheet-edit-modal"` and `data-sheet-edit-open`.
  - The Numbers tab marker `id="sheet-edit-numbers"` is present for the GM and absent with `_is_gm` patched to False.
  - With `display_name` "Go'el the Red", the header shows the escaped text, and `/player/levelup/` links still use the original name.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Build the partials, includes, CSS and render arguments.**
- [ ] **Step 4: Run** `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q tests/test_sheet_edits.py tests/test_inline_handler_escaping.py tests/test_player_sheet_inline_handlers.py tests/test_pf2e_player_sheet_ui.py` and `~/.local/bin/python3.11 tools/check_templates.py`. Expected: all pass, templates clean.
- [ ] **Step 5: Commit** "SE-01: sheet edit modal and display name".

### Task 5: Full verification and handoff report

**Files:**
- Modify: `docs/handoff/tasks/SE-01-pf2e-sheet-edit-core.md` (Report), `docs/handoff/QUEUE.md` (status `review`, plus a plain log line).

- [ ] **Step 1:** Run `DATA_DIR=$(mktemp -d) ~/.local/bin/python3.11 -m pytest -q` in the background. Expected: no failures beyond those present on main at the starting SHA; compare by running the same command on main if any test fails.
- [ ] **Step 2: Dev server** with an isolated `DATA_DIR` (a `.claude/launch.json` entry using `~/.local/bin/python3.11 app.py`, `GM_PASSWORD=''`, `PORT=5057`, `FLASK_DEBUG=true`). Import `goel_l10.json`, then in the browser pane:
  - set the deity to Ragathiel and reload: it shows;
  - re-import a copy with "Rathathiel" and confirm the sheet still shows Ragathiel;
  - add AC +1 and confirm the GM marker and the tooltip;
  - restart the server with `GM_PASSWORD=test`, open the sheet without GM login (player session) and confirm there is no Numbers tab and that a `javascript_tool` POST with `adjust` returns 403.
  - Screenshot the modal.
- [ ] **Step 3:** Fill in the report with the real test numbers, set the queue row to `review`, add the log line, and commit "SE-01: report".
