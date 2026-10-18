# D&D 5e section — handoff

Living document for the `feature/dnd5e` work. Update it in place; do not start a new one.

## What it is

A third game system next to PF2e and Cosmere. A campaign whose system is `dnd5e` gets:

| Surface | Route | Who |
|---|---|---|
| GM hub (live party vitals, saved encounters) | `/dnd5e/gm` | GM |
| Encounter builder (4,928 statblocks, 2014 + 2024 difficulty side by side, saved plans, "send to tracker") | `/dnd5e/encounters` | GM |
| Combat tracker (initiative, HP/temp/resist-aware damage, conditions with durations, exhaustion, concentration DCs, death saves, legendary actions/resistances, recharge, hide from players, statblock panel) | `/dnd5e/tracker` | GM |
| Player combat view (turn order, monsters as Unhurt/Wounded/Bloodied/Down, own initiative entry) | `/dnd5e/combat` | everyone |
| Character builder (edition, sources, class, species, background, abilities, every choice, levels 1–20, multiclass, equipment, details) | `/dnd5e/character/<id>/build` | owner / GM |
| Character sheet / manager (D&D Beyond-style: abilities, saves, skills, actions, spells + slots, inventory + attunement + currency, features, notes, rests) | `/dnd5e/character/<id>` | owner / GM |
| Bestiary, Compendium (spells, items, feats, species, backgrounds, classes, rules glossary...) | `/dnd5e/bestiary`, `/dnd5e/compendium` | GM / everyone |
| GM screen (conditions, actions, DCs, cover, travel pace, exhaustion, encounter budgets, 2024 glossary; per edition) | `/dnd5e/gmscreen` | GM |

Sheet ⇄ tracker sync: a PC's HP, temp HP, conditions, exhaustion, death saves and concentration
live **only** in the character file (`state`). The tracker's PC row is a pointer. Damage from the
tracker and healing on the sheet are the same write, broadcast as `dnd5e_pc`, so the two views
cannot drift.

## Architecture

```
dnd5e_data/                  NOT IN GIT (public repo, non-SRD data) -- see "Rules data" below
  resolved/*.json            fully-resolved export (5etools' own loaders: _copy, _versions,
                             magic variants, subraces, inlined class features)
  dnd5e.db                   SQLite built from resolved/ (entity + monster/spell/item indexes)
  _tags.json, images.json, img/{tokens,art}/
systems/dnd5e/
  paths.py      where the data lives (env / Railway volume / repo dir)
  datapack.py   build + validate + install data bundles
  __init__.py   GameSystem registration (CombatProfile, nav)
  ids.py        name|source ids; default sources come from 5etools' tag registry
  content.py    read-only DB access (per-thread connection, LRU of parsed records)
  render.py     5etools entries + {@tags} -> escaped HTML (rolls, reference popovers)
  rules/tables.py     XP by CR, 2014/2024 encounter budgets, multiclass slots, point buy
  rules/grants.py     5etools "grant grammar" -> fixed grants + player Choices
  rules/character.py  build log -> derived sheet + outstanding choices (the engine)
  combat.py     pure combat rules (damage, temp HP, death saves, conditions, turns)
  dice.py       server dice
  store.py      per-campaign files + locks (campaigns/<cid>/dnd5e/...)
  service.py    engine + combat + store; every mutation goes through here
  routes.py     Flask blueprint (`dnd5e.*` endpoints); host hooks, never imports app.py
templates/dnd5e/*.html, static/css/dnd5e.css, static/js/dnd5e.js
```

* **Characters are build logs.** `build` stores decisions (edition, species, background, base
  scores, one entry per level, `choices{id: value}`); every number is recomputed by
  `rules/character.py::compute`. Leveling down truncates the log and drops the picks that no
  longer apply. Saves reject *invalid* builds (422 with reasons) but accept *incomplete* ones —
  outstanding choices show as a count on the builder and summary, like D&D Beyond.
* **Choices are data-driven.** Species/background/feat/class grants use 5etools' grammar and are
  expanded by `grants.py`. Choices that 5etools only states in prose (Expertise, Student of War,
  Scholar, Deft Explorer, Divine/Primal Order, Weapon Mastery...) are in
  `character.py::FEATURE_CHOICES` — extend that table for more.
* **Authorization**: every endpoint is declared in `core/route_policy.py`. `<pid>` routes are in
  `DND5E_PID_ENDPOINTS` (owner or GM, via `CharacterOwnerSource.ROUTE_DND5E_PID`, resolved in
  `app.py::_authorization_character_context`). GM APIs are under `/api/dnd5e/gm/` (also in
  `GM_API_PREFIXES`). Monster data is GM-only in the content API.
* **SSE** (all campaign-scoped): `dnd5e_pc` (live PC state), `dnd5e_encounter` (GM full / players
  masked via `player_filter`), `dnd5e_prompt` (turns, concentration, death saves; players get
  `routes.player_prompts`, which never names a hidden creature), `dnd5e_roll` (GM only).
* **Locks**: `store.mutate_pc` / `store.mutate_encounter`. Order is encounter → PC; sheet routes
  only take the PC lock.
* PF2e routes (`/gm`, `/tracker`, `/player`, ...) redirect to the 5e equivalents when the active
  campaign is 5e (`app.py::_dnd5e_system_redirect`).

## Rules data (not in git)

The GitHub repo is **public** and the 5etools data is mostly non-SRD, so `dnd5e_data/` is
gitignored except `README.md` and `VERSION` (decided 2026-10-12; the earlier plan of committing
it assumed a private repo). Nothing from it was ever pushed.

* **Where the app looks** (`paths.py`): `DND5E_DATA_DIR`; else `<DATA_DIR>/dnd5e_data` once it
  holds a database (production: the Railway volume, survives deploys); else the repo's
  `dnd5e_data/` (dev). `DND5E_DB_PATH` still overrides the database file alone.
* **Building it** (dev machine): the commands in `dnd5e_data/README.md` -- shallow-clone
  `5etools-mirror-3/5etools-src` at `v2.37.0`, `export_5etools.mjs`, `build_dnd5e_db.py`, and
  optionally `fetch_images.py`. No `npm install` is needed. A fresh clone rebuilds a database
  byte-identical to the one used during development (checked 2026-10-12).
* **CI** does the same build (pinned to commit `8c026b80`) before the main test job, so the
  5e tests run there; the art is not fetched in CI.
* **Production install**: `python tools/dnd5e/bundle.py` writes `dist/dnd5e/*` parts under the
  48 MB upload cap (core part gzipped ~10 MB, then image parts). A site admin uploads them at
  `/dnd5e/admin/data`, core first. `datapack.install` accepts only an allow-list of names
  (`dnd5e.db`, `_tags.json`, `images.json`, `VERSION`, `img/{tokens,art}/<20 hex>.webp`) as
  regular files with size caps, stages and validates the database (it must open and hold at
  least 1,000 entities) **before writing anything**, adds images, then swaps the database and
  indexes in with `os.replace`. `content.reload()` drops every cached connection and record.
* **Without the data** every `dnd5e.*` page returns 503 with an "isn't installed" notice (and a
  link to the installer for the admin); `/api/dnd5e/*` returns 503 JSON. Uploaded portraits and
  the installer itself stay reachable.
* `tests/test_dnd5e_render_and_db.py::test_db_matches_resolved_export` fails if the DB and the
  resolved export disagree. Tests that need the real data skip when it is absent;
  `tests/test_dnd5e_datapack.py` and `tests/test_dnd5e_no_data.py` use synthetic data and
  always run.

## Verification

* `pytest -q tests/test_dnd5e_*.py` — engine golden characters (2024 Fighter 1, 2014 Wizard 5,
  2014 Paladin 6/Warlock 5, 2024 Monk 20; every number hand-checked), all 24 core classes 1/5/11/20,
  tables vs the books, renderer over every record, combat rules, services, and an end-to-end
  Flask test (account mode, two players, authorization, tracker ⇄ sheet over HTTP).
* Browser-checked locally: tracker (party + monsters, initiative, start, damage → concentration
  prompt), sheet (sync, spells tab), builder (choices, ASI ⇄ feat, Tough recalculates HP).

## Automation added in the second pass (`rules/effects.py`, `rules/spells.py`)

* Magic items (equipped + attuned): ability-setting items (belts, amulets), attunement ability
  bonuses, `bonusAc`/`bonusSavingThrow`/`bonusAbilityCheck`/`bonusSpellAttack`/`bonusSpellSaveDc`,
  resistances, `modifySpeed`, crit range, charges as tracked uses. (This fixed a bug where item
  save bonuses were computed but never applied.)
* Sheet effect toggles: Rage (resistances + damage folded in; Bear totem), Bladesong, Mage Armor,
  Shield, Shield of Faith, Haste, Barkskin, Bless, Hunter's Mark, Hex, Divine Favor, Shillelagh,
  Dodge. Short-lived ones end on a rest.
* Always-on: Aura of Protection, Dread Ambusher, Thaumaturge/Magician (+skills, +cantrip),
  Improved Divine Smite / Radiant Strikes, Dueling, Two-Weapon / Great Weapon Fighting notes,
  pact weapon (CHA + proficiency), Improved/Superior Critical, GWM/Sharpshooter notes.
* In-feature option blocks are now choices (Hunter's Prey, Totem Spirit, ... 40 blocks); before
  this, characters listed *every* option. Alternative spell lists (Circle of the Land) are a
  choice; before, the first list (Arctic) was always used.
* Limited uses detected from feature text (≈150 PHB/XPHB features: Relentless Endurance, Breath
  Weapon, Wholeness of Body, ...), innate spell free casts, Lucky, custom tracked resources.
* Spell rows: attack/save, damage/healing dice at the character's level, upcast on Cast (server
  roll), Agonizing Blast / Potent Spellcasting / Empowered Evocation; Magical Secrets lists.
* 2014 prose choices: Dragon Ancestor, Favored Enemy, Natural Explorer.
* Species `_versions` are one required builder choice on the base species. The selected resolved
  version supplies all of its grants and feature text together (Draconic Ancestry, 2024 lineages
  and legacies, giant ancestry, and the other data-defined versions). Existing characters that
  stored a version id directly remain valid.
* Spells cast from magic items (`attachedSpells`: wands, staffs) appear on the Spells tab; Cast
  spends the item's charges and uses the item's fixed save DC. A shield hides a weapon's two-handed (versatile) damage.
* Extras tab: companions / familiars / Wild Shape forms with their own HP and statblock. Players
  may add and view **beasts** only (the rest of the bestiary stays GM-only); the GM can add any.
* Mobile: 5e players get a bottom nav (Character / Combat / Party / Rules / Home) with a
  "your turn" ping; inactive effect toggles collapse behind "+ more".
* Tracker rows show PCs' active effects (e.g. rage) next to conditions.

## D&D Beyond import (`systems/dnd5e/ddb_import.py`, `POST /api/dnd5e/import/ddb`)

Characters page > "Import from D&D Beyond": a **public** character's link (the server fetches
`character-service.dndbeyond.com/character/v5/character/<id>`; only the id is taken from the input,
so the request can't be pointed anywhere else), or the exported JSON pasted/uploaded.

* Maps species, background, classes and subclasses by name; edition is detected from the class
  features (every 2024 class has "Epic Boon"), and the form can override it. D&D Beyond source
  ids are **not** used: the 2024-book id couldn't be verified, and `isLegacy` is `false` even on
  old 2014 characters.
* D&D Beyond stores results, not decisions, so choices are filled iteratively from its per-origin
  modifier groups (race / background / class / feat), chosen-option labels, feats, class options
  and spells. ASI slots take the class "bonus *-score" points first; feats only fill what is left.
* Hit points: the recorded hit-die total is spread over the levels so max HP matches.
* Also brought across: inventory (with name normalisation: "Lantern, Hooded", "Potion of Healing
  (Greater)", ammunition counts to 5etools bundles), coins, current/temp HP, death saves,
  conditions + exhaustion, spell-slot use, XP, inspiration, notes and description.
* A report (shown in the builder until hidden) lists what was matched, what wasn't (homebrew),
  score/HP differences, overrides that were not imported, and choices still to make.
* Tested on synthetic fixtures in `tests/fixtures/dnd5e_ddb_*.json` and checked by hand against a
  real public export (a 2014 Sorcerer with homebrew), live through the form.

## PDF import (`systems/dnd5e/pdf_import.py`, `POST /api/dnd5e/import/pdf`)

Same form on the Characters page: choose a `.pdf` instead of a `.json`. Works on D&D Beyond's
"Export to PDF" and on the official fillable sheet.

* D&D Beyond's PDF has **no AcroForm**: the values sit on each page's widget annotations
  (`/T` name, `/V` value). `read_fields` merges both sources; keys are lower-cased with whitespace
  collapsed (`'CLASS  LEVEL'` -> `'class level'`). 20 MB cap, `%PDF` header check, encrypted files
  are tried with an empty password and otherwise refused.
* The sheet is reshaped into a D&D Beyond character-service object (`to_ddb_shape`) and run
  through the **same** importer, so every name-matching rule is shared. Sources of truth:
  * classes from "Druid 8 / Monk 2"; subclass from the name in brackets, else from the
    features list (two or more of a subclass's feature names must appear);
  * edition: `PHB-2024` in the features text;
  * skills from the `P`/`E` markers when the sheet has them (D&D Beyond's does). Only a sheet
    with no markers falls back to "total minus ability mod" -- totals carry other bonuses
    (Magician's +WIS to Nature) and would invent proficiencies;
  * "`| Magician •`" lines under a feature are the option chosen for it;
  * spells by `spellSource#` = class name (`(Always Prepared)` stripped and flagged), otherwise
    feat/species spells; `[R]`/`[C]` suffixes are dropped;
  * inventory from `Eq Name#`; "Leather" -> "Leather Armor"; when there is exactly one suit of
    armour / one shield, it is equipped (the sheet doesn't say what is worn).
* A PDF records **results, not choices**, so `import_fields` then fits the build to the sheet:
  species/background/feat ability choices go to the sheet's highest scores, unexplained ASI
  slots become +2 to the highest score, base scores are solved so every total matches, per-level
  HP is adjusted until max HP matches. Each assumption is a line in the report. AC is compared,
  not forced: a difference is reported (`AC: sheet 19, here 16 (check what is equipped)`).
* Tested on a synthetic PDF generated inside `tests/test_dnd5e_pdf_import.py` (2024 Human
  Soldier Fighter 3, Champion detected from its features) and by hand against two real D&D
  Beyond exports kept out of the repo: both reproduce every ability score and max HP exactly;
  what stays pending there is genuinely missing (rare/homebrew languages, homebrew cantrips).

## Images

**Rules art** (from 5etools, part of the data -- not in git): `python tools/dnd5e/fetch_images.py` (incremental;
`--kinds monster --limit 50` for a sample, `--index-only` to rebuild the index).

* Tokens: every monster with `hasToken`, at 5etools' own path
  `bestiary/tokens/<source>/<ascii name>.webp`, shrunk to 128 px.
* Art: the first internal image in each entity's fluff record (monsters, items, species,
  backgrounds, classes, subclasses, feats, spells), following fluff `_copy`, shrunk to at most
  600 px. Source: the `5etools-mirror-3/5etools-img` GitHub mirror.
* Files are `dnd5e_data/img/{tokens,art}/<sha1 of the path>.webp` -- hashed because the
  mirror has paths that differ only by case, which collide on Windows/macOS. Served by
  `GET /dnd5e/art/<tokens|art>/<name>` to signed-in users only (not `/static`).
  `dnd5e_data/images.json` maps kind -> entity id -> `{token, art}`; `content.image_for(kind, id,
  role)` reads it (reloaded when its mtime changes). A subclass reprinted for the other
  edition's class falls back to the original's art; a magic variant falls back to its base item.
* First full run (2026-10-12): 8,664 files, 0 missing, 0 errors, about 45 minutes at 24 workers.
  Coverage: 4,588 monster tokens, art for 3,269 monsters, 978 items, 215 species,
  95 backgrounds, 29 classes, 164 subclasses, 44 feats, 91 spells.
* Shown on: statblocks everywhere (bestiary, encounter builder, tracker, companions), compendium
  records, bestiary/encounter search rows (tokens), tracker and player combat rows, and the
  builder's class/species/background picks.

**Uploads** (`systems/dnd5e/images.py`): PC portraits (sheet header: click the picture; or the
builder's Details step) and GM art for any non-PC tracker combatant (custom monsters, NPCs).

* Every upload is decoded and **re-encoded** by Pillow (PNG/JPEG/WebP/GIF in; WebP out), with a
  10 MB / 40 MP limit, EXIF rotation applied, 512 px (portraits) or 256 px (combatants).
* Stored at `campaigns/<cid>/dnd5e/images/<uuid4 hex>.webp`; served by `GET /dnd5e/img/<name>`
  to campaign members, and only names matching `^[0-9a-f]{32}\.webp$` ever become paths.
* A replaced or cleared portrait's file is deleted; a deleted character's portrait too.
* Replacing/clearing combatant art, removing a combatant or clearing/replacing an encounter
  collects unused uploads. PC portraits and images still referenced by combatants survive.
  Files younger than ten minutes wait for a later cleanup-triggering action, protecting
  uploads whose row has not been saved yet. Collection skips symlinks and non-upload files;
  filesystem failures are logged and do not fail the combat request. Saved plans currently
  store monster IDs, not uploaded image names; include their references if that changes.
  If a character document cannot be read, collection defers rather than guessing its portrait is unused.
* The old free-text "Portrait URL" detail is no longer offered in the builder (an arbitrary
  external URL in every player's page); existing values are kept in the file but unused.

## Known gaps (honest list)

* **5e characters are only usable while their campaign holds the site's live slot.** The
  per-character routes use the app-wide `LIVE_CHARACTER_OWNER_OR_GM` policy, so building,
  the sheet, portraits and the tracker answer 409 `campaign_not_live` for a 5e campaign that
  isn't live -- the same rule as PF2e and Cosmere, not 5e-specific. Campaign-level pages,
  the compendium and imports work either way. Found on the first live check (2026-10-13).

* **Feature automation is partial.** Automated: ability increases, proficiencies/expertise,
  Jack of All Trades, Remarkable Athlete (2014), Alert, Tough, Dwarven Toughness, Draconic
  Resilience, Unarmored Defense (Barbarian/Monk), Fast/Unarmored Movement, Martial Arts die,
  Primal Champion / Body and Mind, Defense/Archery styles, weapon mastery display, spellcasting
  (DC, attack, cantrips, prepared/known, slots incl. multiclass + pact), common class resources
  (Rage, Focus/Ki, Sorcery Points, Channel Divinity, Bardic Inspiration, Second Wind, Action
  Surge, Indomitable, Wild Shape, Lay on Hands, Arcane Recovery, Superiority Dice). Rage damage
  and Sneak Attack dice appear as notes on the attack rows they apply to (not folded into the
  numbers, since both depend on the moment). Everything else is displayed as rules text: e.g.
  Bladesong AC, Hexblade, Smite dice.
* Spells: preparing a 2014 Cleric/Druid/Paladin list is done in the builder.
* 2014 encounter multiplier counts every monster (DMG says weak ones *may* be ignored).
* **Postgres (SQL ownership) mode**: 5e characters are not registered in the SQL ownership
  tables. Authorization uses the file's `owner_user_id`; `/me` lists 5e characters only in file
  mode. Production does not give the web process a `DATABASE_URL` today (DEPLOY.md), so this
  is not live; it needs an adapter before SQL mode is switched on for the web process.
* Not built: author-private drafts (character_workflows).
* PDF import cannot know: which feat/ASI was taken at which level (assumed), which species /
  background ability choice was made (assumed toward the highest scores), what armour is worn
  when there are several suits, and homebrew content (reported, kept as plain items).

## Data licensing

The repo is public, so **no 5etools content is committed** -- not the data, not the art.
Code that names rules (feature names in `FEATURE_CHOICES`, the GM screen's SRD tables) stays.
Before adding a fixture, check it isn't copied rules text: the D&D Beyond / PDF fixtures are
synthetic characters referencing content by name only.
Local data size: raw 116 MB + resolved 31 MB + DB 43 MB + art 216 MB (4,588 tokens 37 MB,
4,076 art files 179 MB). The installed server copy needs only the DB, indexes and art.
