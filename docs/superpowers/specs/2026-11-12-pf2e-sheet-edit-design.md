# PF2e sheet edit path: design

Status: approved in conversation 2026-11-12 (sections 1-4). Tasks: SE-01, SE-02.
Request: `~/Downloads/2026-11-12 Sheet save bugs and GM edit.md`, "Request: GM edit
mode" and Bug 3. Bugs 1 and 2 of the same report are fixed separately on
`claude/sheet-save-bugs-gm-edit-b4bb8f`.

## Goal

The GM and the owning player can correct a PF2e character on its sheet
(`/characters/<id>`, `templates/player_sheet.html`) without a Pathbuilder
re-import. The immediate need is Go'el's deity, "Rathathiel" to "Ragathiel".

Every save either persists or returns a non-2xx response with a message. The sheet
never re-renders silently unchanged.

## Decisions (user, 2026-11-12)

1. **Numbers are GM-only.** The owning player can edit meta fields, feats,
   features and companions, but not numbers.
2. **Name edits set a display name only.** `build.name` stays the identity key for
   the file, `PARTY_LIBRARY`, the live cache, encounters, Obsidian and
   `resolve_legacy_name`.
3. **Stats stay engine-computed.** The level-up engine (level, `class_matrix.py`
   timing, items, compendium rules) remains the only source of every base value. A
   GM edit is a signed **adjustment** added on top. Nothing is pinned: there are no
   "set to" values and no rank overrides, so level-ups always flow through.

## Data model

One preserved layer inside the character document:

```
build['sheet_edits'] = {
  "meta": {
    "display_name": str, "deity": str, "sanctification": str,
    "languages": [str], "senses": [str], "size": str, "background": str
  },
  "adjust": {            # ints in [-20, 20]; absent or 0 = no adjustment
    "str", "dex", "con", "int", "wis", "cha",
    "ac", "fortitude", "reflex", "will", "perception",
    "hp_max", "speed", "class_dc", "spell_dc",
    "skill:<name>"       # the 16 skills plus lores, lower-case
  },
  "feats": {
    "added":   [[name, extra, type, level]],   # Pathbuilder feat entry shape
    "removed": [name],                          # names of Pathbuilder feats to drop
    "order":   [name]                           # display order only
  },
  "features": {
    "hidden": [name], "custom": [{"name": str, "desc": str}], "order": [name]
  }
}
```

- `meta.background` overrides only the displayed background name (e.g. a typo in
  "Acolyte"). It changes no boosts or skills, and there is no separate backstory field.
- An absent key means "no edit". Clearing a meta field deletes its key, which
  reverts it to the Pathbuilder value.
- `sheet_edits` is added to `PRESERVE_KEYS` in `_merge_pf2e_import`, so an
  "Update <name>" re-import refreshes the rules data and keeps the edits.
  Pathbuilder keys are never rewritten by an edit.
- **Invariant:** a document without `sheet_edits` derives exactly what it does
  today. The snapshot and ground-truth suites are the proof.

## How `Character.__init__` applies it

Each step is a small additive change; the existing derivation is untouched.

- **Ability adjustments** are applied to `self.mods` immediately after the mods
  loop and before the rule engine, so they propagate to skills, attacks, HP (Con),
  class DC and spell DC. `ability_display` reflects the adjusted mod.
- **Feats:** `added` is appended to and `removed` filtered out of the feat list
  before the feat loop, so an added feat's compendium rules apply (Toughness raises
  HP). `order` sorts the display list only.
- **Features:** `hidden` and `order` filter and sort the `class_features` and
  `ancestry_features` display. `custom` entries are appended as plain text features
  with no mechanics.
- **Meta:** overrides `deity`, `sanctification`, `languages`, `senses` (merged with
  the derived senses), `size` and `background` (display only) after they are read.
  `display_name` becomes a new attribute, `self.display_name`, which defaults to
  `self.name`.
- **Total adjustments** go through one helper, `self._adj(key) -> int` (0 when
  absent). It is added as a term in `base_ac`, `ac`, `_calc_save` (keyed by
  `prof_key`), `perception`, `class_dc`, `spell_attack` (so `spell_dc` follows; key
  `spell_dc`), the `self.hp` line (`hp_max`), `base_speed` (`speed`) and each skill
  total (`skill:<name>`). Conditions, buffs, the shield and toggles still stack on
  top. Breakdown tooltips gain a `GM adj +N` part.

## Routes

All write routes follow the save/reload race rule: `_flush_pc_dirty` before the
read, then `save_and_reload_character`, which aborts 503 on a failed write.
Validation runs before any write; a rejected request writes nothing.

| Route | Auth | Behaviour |
|---|---|---|
| `POST /api/sheet_edits/<pc_name>` | `@require_pc_self_or_gm` | The body is a partial patch `{meta?, adjust?, feats?, features?}`. A patch containing `adjust` from a non-GM gets 403. A validation failure gets 400 with a message. Success returns 200 `{success, sheet_edits}`. |
| `POST /api/sheet_edits/<pc_name>/reset` | GM | `{section}` or all; clears edits. |
| `POST /api/update_pet/<pc_name>` | `@require_pc_self_or_gm` | Edits a pet by its original name; 404 if no pet matches. Built on the Bug 2 `remove_pet` fix. |
| `POST /api/import_character_json/<pc_name>` | `@require_pc_self_or_gm` | Runs an exported file through `_merge_pf2e_import` into the same file. The id/owner envelope and `sheet_edits` are preserved. A `build.name` that differs from the target gets 409. |

**Validation:**
- Adjustments must be integers in [-20, 20] with keys from the allowed set.
- Size must be one of Tiny, Small, Medium, Large, Huge or Gargantuan.
- Text is capped at 80 characters (names, deity, background).
- Languages and senses are capped at 30 items.
- Feat names must be non-empty. A name not in the compendium is allowed as homebrew.

## Sheet UI

All of it is additive, per the CLAUDE.md player-sheet rule. Markup goes in a new
partial `_pc_sheet/_edit_modal.html`, JS in `_pc_sheet/_js_sheet_edit.html`, and
CSS is appended to the end of the stylesheet. No existing id, handler or `pc_update`
repaint path moves.

- **Entry points:** "Edit character" in the `⋯` overflow menu, and a pencil on the
  left-rail meta card.
- **Details tab** (GM and owner):
  - Display name, deity, sanctification, a size select and the background name.
  - Languages and senses as chip inputs.
  - Each field's placeholder is the Pathbuilder value.
- **Numbers tab** (GM only; not rendered for players):
  - One row per stat: `computed | adj [-][n][+] | = shown`. The computed value
    comes from the engine and is read-only.
  - "Reset all".
- **Feats tab** (GM and owner):
  - The feat list with remove and up/down reorder.
  - "Add feat" with `/api/compendium_search` autocomplete plus type and level fields.
  - Removed Pathbuilder feats are listed with "restore".
  - Class features: hide/restore, and add a custom text feature.
- **Companions:** each pet card gets "Edit", which opens the existing Add Companion
  form pre-filled and saves through `update_pet`.
- **Import JSON:** in the `⋯` menu next to export. File picker, then a confirm step
  showing the name and level, then post.
- **Markers:** an adjusted stat shows a small "GM" marker, and its tooltip shows
  the adjustment.
- **Escaping:** names reach handlers through `data-*` attributes, never interpolated
  into inline JS strings.
- **Saving:** Save is disabled while a request is in flight. A non-2xx response
  shows the server message and keeps the modal open. A 2xx response reloads.

## Testing

- **Regression gate:** `tests/test_pc_snapshots.py`, `tests/test_pb_ground_truth.py`
  and `tests/test_pb_import_correctness.py` pass unchanged.
- **New `tests/test_sheet_edits.py`:**
  - Each adjustment moves exactly its own stat.
  - Ability adjustments propagate to skills, HP and DCs.
  - A Frightened PC with an AC adjustment still takes the penalty.
  - Adjustments survive a level change and the `_merge_pf2e_import` re-import.
  - An added feat's rule applies, and a removed Pathbuilder feat is gone.
  - Deity "Ragathiel" survives re-import of a Pathbuilder file that still says
    "Rathathiel".
  - The display name renders while `pc.name` and the URLs stay unchanged.
  - Route errors: owner `adjust` gets 403; bad input gets 400 and the file is
    unchanged; unknown pet gets 404; Import JSON name mismatch gets 409.
- **Existing guards:** `tests/test_pc_save_reload_race.py` covers the new routes
  (flush before read). `tests/test_inline_handler_escaping.py` and
  `python tools/check_templates.py` stay green.
- **Final checks:**
  - Full `DATA_DIR=$(mktemp -d) pytest -q`.
  - A dev-server check with an isolated `DATA_DIR`: fix the deity, adjust AC as
    GM, confirm 403 as a player, add a feat, edit a pet, and complete an Import
    JSON round trip.

## Split

- **SE-01:** the data layer, the `_adj` hooks, meta and adjustments, the
  `sheet_edits` routes, and the Details and Numbers tabs.
- **SE-02:** feats and features, companion edit, and Import JSON. Companion edit
  waits until the Bug 2 `remove_pet` fix is on main.

## Out of scope

- Real renames.
- Cosmere and D&D sheets.
- Builder `?edit=` loading.
- The inventory reorg (on hold).
- The sticky HP chip (deferred).
- The player-release command layer (PE-16/PE-18b will wrap this derivation later;
  `sheet_edits` is one more input for it to carry).
