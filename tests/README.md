# Tests

Snapshot tests for the rules engine. The four party PCs are loaded from
`party_data/`, run through the full `Character.__init__` pipeline (PB
import, ability mods, proficiencies, ActiveEffectLike rules, PB-mods item
bonuses, armor lookups, attacks), and a stable subset of the result is
checked against `tests/snapshots/<name>.json`.

The point: **any number that changes silently in the rules engine shows
up in a diff here**. The 14 audit gaps closed in commit `dd8abe7b` are
exactly the kind of regression these tests catch on the next refactor.

## Running

```bash
pip install -r requirements-dev.txt
python3 -m pytest tests/ -v
```

## Updating a snapshot

When you intentionally change a value (fixing a bug, changing a formula,
adding a field), tests will fail with a path to a `.json.new` candidate.
Diff it against the saved snapshot:

```bash
diff tests/snapshots/amadeus_l3.json tests/snapshots/amadeus_l3.json.new
```

Confirm every changed line is intended. If yes, accept:

```bash
mv tests/snapshots/amadeus_l3.json.new tests/snapshots/amadeus_l3.json
```

To regenerate everything from scratch (e.g. after a snapshot-helper
schema change):

```bash
rm tests/snapshots/*.json
python3 -m pytest tests/  # creates baselines, then re-run to verify
```

## Adding new fixtures

1. Drop the new PC's Pathbuilder JSON into `party_data/<key>.json`.
2. Add an entry to `PARTY_FIXTURES` in `tests/test_pc_snapshots.py`
   mapping the snapshot key (e.g. `"newpc_l3"`) to the filename.
3. Run `pytest`; the first run creates the baseline.
4. Inspect `tests/snapshots/newpc_l3.json` against PF2e rules-as-written
   before locking it in. The point of locking is to freeze a *correct*
   value, not whatever the engine happens to produce.

## What's snapshotted

`tests/_snapshot.py` defines `serialize_character(c)`. It captures:

- Identity (name, level, class, ancestry, heritage, deity, languages)
- Resources (HP, focus_max, hero_points)
- Computed sheet numbers (AC, Fort/Ref/Will, Perception, Speed,
  class_dc, spell_dc, spell_attack, initiative)
- Ability modifiers
- The full `proficiencies` table — the rules-engine output after PB
  import + class progression + feats + ActiveEffectLike rules
- All skill totals
- Senses, immunities (sorted)
- Armor + shield stats
- Feats (name + level only — feat copy churn doesn't cascade)
- Strikes (name + first-strike mod + damage formula + traits)
- `rule_modifiers` — every typed bonus the engine accumulated

Fields deliberately *not* snapshotted: `_build_ref` (would just echo the
input), `instance_id`, `file_path`, `session_notes`, `expended_slots`,
combat-only state (`current_hp`, `conditions`, `reaction_used`).

## Bugs caught while building this suite (2026-05-09)

1. **`ac_dex_cap` ignored on PB imports.** Pathbuilder doesn't export
   the field, and the BUILDER_ARMOR fallback didn't backfill it
   (only `armor_str_req` / `ac_item` / penalties). For Amadeus's chain
   mail, the full DEX +2 was applied past the +1 cap, giving AC 21
   instead of the correct 20. Fixed in `app.py` next to the existing
   armor fallback.

2. **PB weapons never got `damage` / `traits` populated.** Pathbuilder
   exports `die`, `damageType`, `prof`, `display`. The attacks property
   read `w['damage']` and fell back to `1d4` for everything. Bastard
   Sword, Morningstar, Trident, Javelin all displayed as `1d4`. Fixed
   by enriching `_raw_weapons` from PB's `die`/`damageType` plus a
   BUILDER_WEAPONS lookup for traits.

3. **Ancestry/heritage senses ignored.** Go'el (Orc) and Kyle (Awakened
   Animal) both have `Darkvision` in PB's `specials` array, but the
   senses list was empty for any PC who got darkvision from ancestry
   alone (no feat or compendium-Sense-rule). The map tool's PC-token
   creation reads `pc.senses` to set the token's `darkvision` flag —
   so Orc PCs were silently rendered as if blind in dark ambient.
   Fixed by walking `build['specials']` for sense keywords during init.

## Known follow-ups (not blocking Week 1)

- `self.senses` can hold both `"Low-Light-Vision"` and `"Low-Light Vision"`
  (hyphen vs space, two compendium-rule branches that don't share a
  normalizer). Cosmetic only; the map tool checks via case-insensitive
  substring match, so behavior isn't affected. Worth normalizing once
  during a senses cleanup pass.
- `/api/creature/<name>` returns `strikes: []` for PCs (not snapshotted
  here because it's an API surface, not a Character field). PCs need
  their attacks wired through that route so the map sheet can roll.
