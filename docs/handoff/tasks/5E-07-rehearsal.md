# 5E-07: Monday rehearsal (D&D Beyond Paladin/Warlock, all classes to 20, GM encounter flow)

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude (heavy-coding rule; Codex reviews)
- **Branch:** `claude/5e-rehearsal` from `origin/main` at `b5fa3a91`
- **Asked by the user (2026-10-15):** check that a GM can run D&D encounters on Monday, and that
  characters of any class can be built and levelled to 20 with correct effects. Their test case is
  a level 10 Paladin 9 / Warlock 1 exported from D&D Beyond as a PDF. The PDF is private: it is
  not committed, and the tests use a synthetic sheet shaped like it.

## Acceptance
- [ ] The rehearsal character imports from its PDF with every total matching the sheet, or each
      difference explained in the import report.
- [ ] Every class and subclass in both editions computes to level 20 with no engine errors.
- [ ] The GM flow works end to end in a browser: campaign, encounter builder with difficulty,
      send to tracker, initiative, damage, concentration, dying/death saves/healing, turn order,
      player view, live sheet sync, level up.
- [ ] Bugs found are fixed with tests; anything left is listed.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report

### What the rehearsal found and fixed

**PDF import (the user's character, "Ser Tobias Greywater").** Before these fixes, the import
differed from the sheet in four places. After them, every ability, save, skill, AC, HP, spell DC,
attack and slot matches.
- **AC 18 vs the sheet's 11.** The importer assumed the only armour and shield in the pack were
  worn. `pdf_import._fit_worn_armor` now takes them off, fewest first, until the AC matches the
  sheet, and says so in the report ("Shield, Chain Mail: carried, not worn").
- **Perception, Stealth and Persuasion expertise were missing.** Skills granted by feats
  (Observant, Skill Expert) were never filled from the PDF's P/E markers, because the PDF shape
  had an empty `feat` modifier group. It now carries the sheet's proficiencies like the other
  origins.
- **False warning "Feat Weapon Mastery has no slot".** D&D Beyond prints the 2024 class feature
  under FEATS. A "feat" that is one of the build's own features is no longer reported.
- **Weapon masteries left pending.** D&D Beyond lists them as options ("Greatsword (Graze)"); the
  matcher now strips the mastery name. Greatsword and Warhammer come across.

**Rules engine.**
- **Invocation prerequisites were never checked.** A Warlock 1 could take Agonizing Blast (needs
  Warlock 2) or Ascendant Step (Warlock 5). `_check_optional_feature_prereqs` now checks class
  level, pact (2014) and required invocation (2024 Thirsting Blade needs Pact of the Blade). Like
  feat prerequisites, it reports a warning rather than blocking, so imported characters still
  load.
- **Spell-choice filters ignored the school and multi-class lists.** Evoker Savant offered any
  Wizard spell (Blade Ward included), and Lore bard's Magical Discoveries ("Cleric;Druid;Wizard")
  offered nothing, so that choice could never be completed.
  - New `rules/spells.spells_for_filter` handles level and class lists, school, ritual and spell
    attack. The builder's options and the engine both use it.
  - The engine now rejects a pick outside the filter, or too many picks.
- **Once-per-long-rest free casts weren't tracked.** Examples: Paladin's Smite, Faithful Steed,
  Undead Thralls. Two 2024 phrasings were added to the use detector:
  - "you must finish a Long Rest before you can cast it in this way again";
  - "you can also cast ... once".

  A sweep showed the change adds 17 trackers across the subclasses, all checked. It also turned
  up two problems, both fixed:
  - a feature whose limit is a spell already tracked as a free cast is not counted twice
    (Fathomless's Grasping Tentacles);
  - 2024 Mystic Arcanum is tracked as one use per spell level (6, 7, 8, 9).
- **Level down left the sheet in an error state.** It now trims picks that the lower level can't
  hold: too many cantrips, spells, invocations or masteries, and spells above the new maximum level.

**UI.**
- **The builder and the character sheet were broken on phones.** Their two-column grid was an
  inline style, which beat the phone breakpoint: the builder's main column was 0px wide under
  400px and the Summary sat on top of the choices. The sheet's 240px sidebar squeezed the main
  column the same way. Both now use classes that collapse to one column under 800px.
- **Tracker prompts appeared twice.** Every concentration or dying prompt showed twice: the HTTP
  reply's JSON is key-sorted and the SSE copy is not, so the de-duplication never matched. It now
  compares key-order-independently.

**Spell casting marks off the right thing** (added after the user asked, 2026-10-16). Slots and
pact slots were already spent by the sheet's Cast button and reset by rests. These were wrong:
- **Free casts spent a slot.** Examples: Divine Smite and Find Steed once per long rest, and
  feat/species spells (Fey-Touched's Misty Step and its chosen spell).
  - Cast now offers the free use first, as "(1 left, no slot)", and marks that use off.
  - Feature-granted ones are linked by `_link_feature_free_casts`.
  - A *chosen* feat spell now carries its uses at all; it had none before.
- **At-will spells spent a slot** (Armor of Shadows -> Mage Armor, plain innate lists). They now
  cost nothing; ritual-only innate spells likewise.
- **Ritual casting** is offered as an option ("no slot, +10 minutes") for casters that can.
- **Mystic Arcanum could not be chosen at all**, and casting a level 6+ spell would have failed for
  lack of a slot.
  - Warlocks get one level 6/7/8/9 spell choice at 11/13/15/17 (both editions).
  - Each is cast only from its own 1/long-rest use.
- **Always-prepared Oath/Domain spells showed no save DC.** They now use the class's ability and
  DC (Bane: CHA 17).
- **The GM tracker didn't show a PC's slots or limited uses.** The PC panel now lists slots left
  and uses left, updated live as the player casts. These rows are GM-only; the player view
  doesn't carry them.
- **Slot choice used `window.prompt`.** It is now a tap-friendly chooser (`D5.choose`).

Verified in the browser on the rehearsal character:
- Divine Smite offered the free use plus each slot. Taking the free use marked
  Paladin's Smite 1/1 and left the slots alone. The second cast offered slots only, and a level 2
  slot rolled 3d8.
- Casting Bane from the sheet changed the tracker's PC panel to L1 3/4 live, and concentration
  moved to Bane.
- Everything spent during testing was put back.

### Verified in the browser (fresh data dir, account mode, phone width and desktop)
- Created a D&D campaign, imported the PDF through the real upload route, and opened the builder.
- Encounter builder:
  - search works;
  - 2024 budgets for a level 10 (Low 1,600 / Moderate 2,300 / High 3,100) match the DMG;
  - 2014 thresholds and the multiplier match (one PC, five monsters = x2.5).
- Sent the encounter to the tracker, rolled NPC initiative and entered the PC's.
- Ran the fight:
  - 24 damage with Bless up: HP 39 and a single "DC 12 to keep Bless" prompt;
  - 30 more damage: DC 15;
  - dropping to 0 made him unconscious, ended concentration and showed the dying prompt;
  - a death save recorded one success; healing 10 cleared dying and unconscious;
  - a bandit killed at 0 HP shows as dead; the turn advanced.
- The second tab (the PC's sheet) repainted HP and "Concentrating: Bless" live.
- The player combat view hides monster HP, greys the dead and shows the PC's own HP.
- Levelled up to Warlock 2: 3 invocations, 2 pact slots, Magical Cunning, multiclass slots still
  4/3/2, HP 63 to 68.

### Sweep
`tools/dnd5e/sweep_classes.py` builds every class x subclass in both editions (310 builds)
to level 20, fills every choice the way a player would, and checks each level 1 to 19 as well.
- **Result: 0 engine errors at level 20.**
- The remaining "incomplete" rows were artefacts of the sweep's naive filler, not app bugs, except
  the Lore filter, which is fixed above.

### Tests
- New `tests/test_dnd5e_rehearsal_fixes.py` (13 tests) and one new PDF import test (a synthetic
  Paladin 9 / Warlock 1 with unworn armour, feat skills and masteries). Each fix has a test.
- `pytest tests/test_dnd5e_*.py` plus route policy, system guards and handler escaping: **157 passed, 1 failed**. `test_db_matches_resolved_export` fails locally only, because it needs
  `dnd5e_data/resolved/` inside the checkout, which CI builds.
- `tools/check_templates.py`: all templates parse.

### Not done / limits (the user should know)
- **Things the PDF doesn't record stay as pending choices** on the user's character:
  - the Warlock's invocation and cantrips (D&D Beyond's sheet listed none);
  - one Paladin skill: D&D Beyond had picked a skill the species already gave, which the 2024
    rules don't allow, so a replacement is needed;
  - two languages: D&D Beyond had Deep Speech and Infernal, but the 2024 origin languages are
    Standard only.
- **"Observant: prerequisite not met" is correct.** 2024 Observant needs INT or WIS 13, and the
  sheet has INT 12 / WIS 9. D&D Beyond doesn't enforce it. Left as a warning.
- **The player view's monster portraits are blank circles** while the GM tracker shows art. Cosmetic;
  not fixed.
- **Masking was checked as the GM viewing the player page,** not with a second player account. The
  masking code itself is unit-tested (`player_prompts` and the masked encounter view).

## Review

### Codex, 2026-10-16 — CHANGES REQUESTED

Reviewed the whole uncommitted rehearsal diff, including the later casting changes, the new tests,
PDF fitting, spell filters, use detection, level-down trimming, mobile CSS and tracker updates.
The focused rehearsal/PDF tests pass, but the casting boundaries below need fixes before acceptance.

### Must-fix

1. **[P1] Link only an explicit free-cast grant to its spell.**
   `systems/dnd5e/routes.py:1080-1097` (`_link_feature_free_casts`) requires only a limited-use
   feature containing "spell slot", then accepts a spell tag anywhere or even an ordinary substring.
   Running the real helper against the installed records links Faithful Steed to **Aid** (ordinary
   prose), Read Thoughts to **Command**, Beguiling Magic to **Charm Person**, and Frozen Haunt to
   **Hunter's Mark**. None of those pairings grants the linked free cast. Even exact spell tags
   are insufficient when the feature mentions a prepared spell or spending a slot to restore an
   unrelated ability. Recognize the explicit grant and the spell it refers to; remove the raw
   substring fallback. Apply the same grant-aware boundary to the new de-duplication at
   `systems/dnd5e/rules/character.py:1909-1923`, which currently suppresses a resource whenever any
   free-cast spell name occurs in its text. Add negative cases above and positive cases for
   Paladin's Smite/Divine Smite and Faithful Steed/Find Steed, retaining intended de-duplication.

2. **[P1] Plain innate does not mean at will.**
   `systems/dnd5e/routes.py:1065-1069` marks every innate grant without structured uses/recharge
   as unlimited. Real level-5 MPMM Aarakocra and Genasi (Earth) builds expose Gust of Wind and Pass
   without Trace with `at_will=True` and no resource, although their species traits limit the free
   casting. Some monk disciplines also use innate records but spend ki. Derive the cost from an
   explicit at-will grant or the owning trait/resource, and leave unsupported costs explicit.
   Test these limited species cases and a resource-cost discipline, plus a genuine at-will spell
   such as Armor of Shadows/Mage Armor.

3. **[P2] Expose ritual casting for unprepared Wizard spellbook rituals.**
   `templates/dnd5e/sheet.html:285` still creates Cast only for prepared spells or cantrips, so the
   new ritual chooser is unreachable for an unprepared spellbook ritual. Both editions' real
   Wizard rows for known-but-unprepared Detect Magic have `prepared=False, ritual=True`.
   Add an explicit ritual-availability signal and render a usable ritual action for eligible
   spellbook spells without permitting normal unprepared casting. Test both editions at the
   rendered/click boundary, including a non-ritual negative case.

4. **[P2] Check invocation spell prerequisites too.**
   `systems/dnd5e/rules/character.py:1208-1223` adds invocation prerequisite checks, but
   `prereq_ok` ignores `spell` and the caller discards unchecked notes. A level-2 Warlock without
   any cantrip receives no Agonizing Blast warning in either edition. The real 2014 prerequisite
   requires Eldritch Blast; the 2024 record requires a damaging Warlock cantrip. Validate explicit
   spell prerequisites and the structured choose/filter requirement against the actual class's
   selected spells, with positive and negative tests for both editions (a nondamaging cantrip
   must not qualify in 2024).

5. **[P2] Preserve independent free uses of the same spell.**
   `systems/dnd5e/routes.py:1073` and `systems/dnd5e/rules/character.py:1946` identify a free use
   only by spell ID. A level-5 2024 High Elf with Fey-Touched has two independent Misty Step
   grants, each with one use, but compute produces just one `spell:misty step|xphb` resource and
   both sheet rows point to it. Use a stable grant/source-specific resource identity; deduplicate
   only aliases for the same grant. The sheet's new lookup at `templates/dnd5e/sheet.html:537-540`
   also needs that identity, because spell ID, level and empty caster do not distinguish these
   rows. Add an end-to-end regression showing each grant can spend its own use while slots stay
   unchanged. Preserve existing saved usage deliberately when changing resource keys.

### Verification and limits

- Independently ran `tests/test_dnd5e_rehearsal_fixes.py` plus
  `tests/test_dnd5e_pdf_import.py`: **19 passed** with the existing local D&D bundle selected by
  `DND5E_DATA_DIR`. This covers the 13 rehearsal tests and six PDF tests, including both level-down
  trimming regressions. Trimming keeps earlier picks, drops unavailable spell levels, recomputes
  after changes and normalizes vitals; no additional blocker found in that change.
- Separate read-only probes reproduced all five boundaries above against real installed records;
  those probes are review evidence, not new committed regression tests.
- `python tools/check_templates.py`: **107 templates parse**. `git diff --check`: clean.
- An initial focused run had **16 passed, 3 setup errors** because pytest's default sandbox temp
  directory was unwritable. Re-running with a writable external `--basetemp` gave the clean result
  above; no product code or tests were changed for that environment issue.
- Did not repeat Claude's class/subclass sweep or browser rehearsal, or claim a full-suite pass.
  The private PDF and production state were not changed. The untracked `.tmp_review_5e07/` scratch
  directory must be excluded/removed before committing; it contains test artifacts, not task code.
- Verdict and queue log only; no implementation edits, commit, push, merge or deploy.

### Fixes (Claude, 2026-10-16, for recheck)

1. **Free casts are matched to the explicit grant.**
   - New `effects.free_cast_spells(entries)` returns a spell only when the sentence saying "without
     (expending) a spell slot" tags it, or says "cast it / the spell / either spell" with the spell
     tagged in one of the two sentences before.
   - `_link_feature_free_casts` uses it and links the feature's own resource, or a class-table
     resource named after the feature (Favored Enemy (Hunter's Mark) now links too). The substring
     fallback is gone.
   - The feature-resource de-duplication in `_detected_resources` uses the same grant set.
   - Checked against every class and subclass in both editions: 34 grants found, all genuine.
     Aid / Command / Charm Person / Hunter's Mark are no longer linked by mention.
   - Tests:
     - positive: Divine Smite and Find Steed;
     - negative: Aid while Faithful Steed is present, Knowledge Domain Read Thoughts, Glamour
       Beguiling Magic, and a "spend a slot to regain" sentence;
     - Grasping Tentacles de-duplication kept.
2. **Plain innate is classified from its owning trait, not assumed to be at will.**
   - `_classify_grants` runs `effects.innate_terms` on the trait or feature paragraph that names
     the spell. A stated limit becomes per-grant uses; a stated cost (ki etc.) becomes
     `cost_note`, and the sheet offers "Its own feature (... spend its cost by hand)" alongside
     slots. Only an explicit at-will / no-slot grant stays free. Anything unstated is marked
     `unknown` and the sheet says so.
   - The paragraph scoping matters: Merge with Stone also gives Blade Ward "PB times".
   - Tests: Aarakocra (MPMM) Gust of Wind and Earth Genasi Pass without Trace are 1/long rest and
     not at will; Clench of the North Wind (ki) gets a cost note; Armor of Shadows / Mage Armor
     stays at will.
3. **Spellbook rituals.**
   - Unprepared ritual rows in a Wizard's spellbook carry `ritual_book`. The sheet renders a
     "Ritual" button that casts with no slot; an unprepared non-ritual still has no action.
   - Button and payment logic moved into pure `castButtonHTML` / `castPlan` (between the
     "cast planning" markers), which a node test runs for both editions. The test checks the
     button, the no-slot plan, the non-ritual negative, and that a prepared ritual offers ritual
     plus slots.
4. **Invocation spell prerequisites.**
   - `prereq_ok` checks `spell` against the spells the character has:
     - a 2014 string ("eldritch blast#c");
     - a 2024 `choose` filter via `spells_for_filter`, which also requires `damageInflict` when the
       entry says it deals damage.
   - Tests, both editions: no cantrip, Mage Hand (non-damaging, 2024), Eldritch Blast.
5. **One resource per grant.**
   - Free-cast keys are now `spell:<id>@<origin-slug>`. Twin grants are labelled with their
     origin, and each sheet row carries its grant's key. The Cast button names its exact row
     (`data-row`), not spell id/level/caster.
   - Saved usage is migrated deliberately in `service._migrate_resource_keys`, run in `sheet_for`
     so the next write persists it:
     - an old `spell:<id>` count moves to the first grant (the one the old single resource was);
     - 2014 `feature:mystic arcanum (6th level)` moves to `mystic_arcanum_6`;
     - keys that match nothing are never deleted.
   - Tests: a High Elf with Fey-Touched spends each Misty Step use separately and leaves slots
     unchanged; legacy-key migration (including keeping an unknown key); 2014 arcanum migration.

**Tests now**
- `tests/test_dnd5e_rehearsal_fixes.py` -> **29 passed**.
- The dnd5e suite plus route policy, system guards and handler escaping -> **173 passed, 1 failed**:
  `test_db_matches_resolved_export`, local only, as before.
- 107 templates parse; `git diff --check` is clean.
- Browser, on the rehearsal character: Divine Smite offers Paladin's Smite then slots; Find Steed
  offers Faithful Steed then a level 2 slot. Both were cancelled, so nothing was spent.

### Codex recheck, 2026-10-16 — CHANGES REQUESTED

The original grant-matching, innate-cost, spellbook-ritual and independent-grant fixes are accepted.
Saved-use migration passes the supplied persistence checks, including 2014 arcanum keys and
preservation of unknown keys. Spell prerequisites now reject the original single-class negative
cases, but still lose class ownership. The casting refactor also introduces a cantrip regression.

Two required fixes remain:

1. **[P1] Non-item cantrip casting throws before completing.**
   `templates/dnd5e/sheet.html:546` tests `if (plan.now)`, but `castPlan` returns `{now: ''}` for
   every level-zero spell at line 573. That empty string is false, so the handler reaches
   `plan.opts.length` at line 555 with no `opts`. Running the actual `castSpell` and `castPlan`
   functions under Node with a Fire Bolt button reproduces
   `TypeError: Cannot read properties of undefined (reading 'length')`; no roll occurs.
   Check for the presence of the `now` property (or use an explicit immediate-cast discriminator),
   preserving the intended empty display suffix. Add a regression that invokes the real
   `castSpell` handler for a damaging cantrip and a utility cantrip, verifies completion without
   exceptions, checks the damage roll where applicable, and confirms that no resource API runs.
   Testing only `castPlan`'s return value does not cover this caller boundary.

2. **[P2] A Wizard cantrip still satisfies the Warlock-only prerequisite.**
   `systems/dnd5e/rules/character.py:1237-1245` collapses every class's selections into one
   `known_spells` set. `_knows_spell_for` at lines 242-255 intersects that set with spells on the
   Warlock list, which checks list eligibility rather than the class that granted the spell.
   Reproduction: a 2024 Wizard 1 / Warlock 2, Chill Touch selected only under
   `spells:wizard|xphb:cantrips`, no Warlock cantrips, and Agonizing Blast selected under
   `opt:warlock|xphb:Eldritch Invocations` computes with no errors and no prerequisite warning.
   Preserve spell ownership in the prerequisite context and apply the class-qualified filter to
   actual grants/selections from that class. Keep the damage check. Add this multiclass negative
   case and a positive case with the same damaging cantrip actually chosen as a Warlock cantrip.

Independent verification:

- `pytest -q tests/test_dnd5e_rehearsal_fixes.py tests/test_dnd5e_pdf_import.py`
  — **35 passed** in 5.64s (29 rehearsal cases and six PDF cases, including Node ritual checks),
  using the existing local bundle and writable external temporary directories.
- Re-ran the earlier record-based probes: Aid, Command, Charm Person and Frozen Haunt's
  Hunter's Mark no longer receive the false links; Divine Smite retains its intended link;
  Aarakocra/Genasi limited casts are not at will; both Misty Step grants have distinct keys;
  the original no-cantrip invocation cases now warn in both editions.
- Separate probes ran the actual sheet casting handler and a real multiclass compute to
  reproduce the two remaining findings above. No implementation or regression-test files edited.
- **107 templates parse**; `git diff --check` is clean before this review-only addition.
- Did not repeat the full suite or browser rehearsal. The reported local-data-only full-suite
  failure is separate from these two reproduced defects. Verdict and queue log remain
  uncommitted; no push, merge or deploy. Claude fixes these two items and returns for recheck.

### Fixes for recheck 2 (Claude, 2026-10-16)

1. **Cantrips no longer crash the Cast handler.**
   - The cause: `castPlan` returns `{now: ''}` for a cantrip, and `castSpell` tested `if (plan.now)`,
     so a cantrip fell through to `plan.opts.length`.
   - The fix: `castSpell` now tests property presence (`'now' in plan`, `'error' in plan`).
   - New test: the real `castSpell` (extracted from the template), run under node with stubs, for
     Fire Bolt (rolls 1d10) and Mage Hand (no roll). Both toast "Cast ...", call no API and open no
     chooser.
2. **Invocation spell prerequisites keep class ownership.**
   - A 2024 `choose` prerequisite naming a class (`class=Warlock`) is now checked against the spells
     picked *as that class* (`spells:warlock|...`), via a new `known_by_class` map.
   - The 2014 string form ("eldritch blast#c") still accepts the spell from any pick, which is what
     the rule asks for.
   - Tests:
     - Wizard 1 / Warlock 2 with Chill Touch picked as a Wizard cantrip: warned;
     - the same with Chill Touch picked as a Warlock cantrip: not warned;
     - 2014 Eldritch Blast: not warned.
- Mutation check: putting back `if (plan.now)` fails both cantrip tests; dropping the class filter
  fails the Wizard-owner case.
- `tests/test_dnd5e_rehearsal_fixes.py`: **34 passed**.
- dnd5e suite plus route policy, guards and escaping: **178 passed, 1 failed** (local-data only, as
  before).
- 107 templates parse; `git diff --check` is clean.

### Codex recheck 3, 2026-10-16 — ACCEPTED

Both remaining required fixes are verified. No required review fixes remain.

- The real `castSpell` handler checks property presence, so a cantrip's empty `now` value
  completes immediately. Fire Bolt rolls and Mage Hand completes without a roll; both show
  their success toast without a resource API call or chooser.
- The 2024 class-qualified invocation prerequisite uses `known_by_class`. A Wizard-only
  Chill Touch no longer qualifies for Agonizing Blast; the same cantrip chosen as a Warlock
  does. The 2014 explicit Eldritch Blast prerequisite still passes.
- Independently reran `tests/test_dnd5e_rehearsal_fixes.py` and
  `tests/test_dnd5e_pdf_import.py`: **40 passed** in 3.77s (34 rehearsal and six PDF cases),
  with the existing local bundle and writable external temporary directories. The suite includes
  the real-handler Node tests and the multiclass positive/negative regressions.
- Re-ran the two prior standalone reproductions: the actual cantrip handler now rolls and
  completes without exception, and the Wizard-only cantrip build now emits the expected
  prerequisite warning.
- `python tools/check_templates.py`: **107 templates parse**. `git diff --check`: clean.
- Earlier accepted fixes, saved-use migration and stated rehearsal limits remain as documented.
  This focused recheck does not claim a new full-suite, browser-rehearsal or CI run.

Acceptance and queue edits remain uncommitted. Claude may commit the implementation and all
review edits, push and open the PR, then merge only after every CI job is green. Exclude/remove
the untracked `.tmp_review_5e07/` scratch directory before committing. No commit, push, merge or
deploy was performed during this review.
