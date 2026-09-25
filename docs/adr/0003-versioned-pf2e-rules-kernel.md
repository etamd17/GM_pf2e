# ADR-0003: Versioned PF2e Rules Kernel

**Status:** Accepted
**Date:** 2026-09-24
**Deciders:** Project owner and GM_pf2e maintainers

## Context

GM_pf2e needs one trustworthy authority for Pathfinder Second Edition rules used by character creation, advancement, daily preparation, imports, the character sheet, and encounter management.

Rules are currently distributed across several independently maintained locations:

- app.py contains class metadata, save handlers, level-up handling, spellcasting defaults, companion handling, and combat mutations.
- class_matrix.py contains class progressions, proficiency changes, slot tables, and live class mechanics.
- templates/player_builder.html and templates/player_levelup.html contain additional parsing, validation, progression, and spell-slot logic.
- compendium_data contains records from different products and rules eras without an immutable package manifest.
- systems/pf2e currently describes a system profile but is not the runtime rules authority.

This creates several failure modes:

- Client and server can disagree while each appears internally consistent.
- Tests can repeat the same stale assumption as the implementation and therefore pass without proving current-rules correctness.
- A character does not reliably identify the rules and source versions under which it was created.
- Remaster and legacy records can be mixed without an explicit compatibility policy.
- Recomputing a character can overwrite player choices or mutable play state because source decisions, derived statistics, and runtime state are not cleanly separated.
- Rule updates require coordinated manual edits in Python, JavaScript, templates, and fixtures.
- Browser-side checks can be bypassed by a forged request or import.

The September 2026 target includes the current Remaster rules and Pathfinder Impossible Magic. The current class roster has 29 classes, including Necromancer and Runesmith. Magus and Summoner have current class chassis that differ materially from their legacy Secrets of Magic versions. Paizo also continues to make legacy material available, so the application must support explicit ruleset compatibility rather than silently rewriting every existing campaign.

The design must:

- Work inside the current application deployment without introducing a separately operated service.
- Keep server-side persistence authoritative.
- Support immutable current, legacy, campaign-policy, and homebrew content sets.
- Preserve existing campaigns while enabling safe, reviewable migration.
- Keep the builder responsive without delegating legality to the browser.
- Provide source and license provenance for every enabled rule record.
- Allow the GM to adjudicate rules that are not safe or practical to automate.

## Decision

We will implement a versioned PF2e rules kernel inside the application process.

The kernel will be the sole authority for:

- Legal character choices.
- Character-creation and level-up transitions.
- Prerequisite evaluation.
- Class progression and proficiency derivation.
- Spell-slot, spellbook, repertoire, preparation, and daily-state rules.
- Linked-actor rules such as Eidolons.
- Rules-sensitive combat state transitions.

The browser will receive a projection of legal choices and a derived preview from the kernel. It may provide immediate presentation feedback, but it will not determine whether data can be persisted.

### Immutable rulesets

Each campaign will pin an immutable ruleset ID and manifest hash. Characters inherit the campaign ruleset unless an explicit, supported exception is recorded.

Initial target identities are:

- pf2e-remaster-2026-09.1 for the current Remaster rules, Pathfinder Impossible Magic, and applicable published errata.
- pf2e-legacy-2023-10.1 only if the legacy package can be reconstructed and verified.
- pf2e-legacy-unknown as a quarantine identity for existing records whose exact source mix cannot be proven.

The user interface may expose a “current” alias when creating a campaign, but stored campaigns and characters will contain the resolved immutable ID and manifest hash. A published package will never be changed in place. Errata or corrected data will create a new package revision and a migration path.

### Stable rule identity

Every rule record will have a stable, namespaced ID independent of its display name, for example pf2e.class.magus.

Renames, remaster replacements, and legacy equivalents will be represented by reviewed alias or replacement records. Name matching will not be used as a migration strategy.

### Declarative records and a closed predicate language

Rules data will use typed records for:

- Rule identity and source references.
- Choice groups.
- Grants.
- Prerequisite predicates.
- Class progression.
- Spellcasting profiles.
- Linked-actor behavior.
- Combat transition requirements.

Prerequisites will use a closed declarative language supporting operations such as all, any, not, level, trait, proficiency, attribute, feat, class, ancestry, tradition, deity, sanctification, rarity, and access.

Rules data will not execute arbitrary Python or JavaScript.

Each record will declare an automation level:

- reference_only
- validated_choice
- derived
- scripted
- gm_adjudicated

The product will show this distinction rather than implying that every natural-language exception is automated.

### Character data boundaries

Character data will be separated into four logical areas.

| Boundary | Contents | Mutation policy |
| --- | --- | --- |
| Rules binding | Ruleset ID, manifest hash, ordered content packs, campaign variants, homebrew overlays | Changes only through an explicit migration or campaign operation |
| Player decisions | Choice-group selections, level chosen, source grant, replacement and retraining history | Written through validated build transitions |
| Derived snapshot | Proficiencies, statistics, entitlements, slot capacity, and other reproducible values | Rebuilt from rules plus decisions; cacheable but not authoritative |
| Runtime state | Current HP, temporary HP, conditions, prepared and expended spells, item quantities and charges, encounter resources, and notes | Preserved across derivation and rules migration unless a reviewed migration explicitly changes it |

The application will also retain:

- A build-event history for creation, level-up, retraining, rebuild, import, and migration.
- A migration journal with before and after checksums, unresolved choices, actor, timestamp, and rollback reference.
- An override audit with GM identity, reason, validation issue codes, and before and after state.

### Server-authoritative transitions

Character creation and level-up will use preview and commit transitions.

A preview will return:

- Structured validation issues with stable codes and field paths.
- Legal next choices.
- A derived before and after diff.
- The ruleset hash and character state version.
- A short-lived transition token.

A commit must present the same ruleset hash, expected character state version, and transition token. This prevents forged writes, stale-tab writes, and time-of-check/time-of-use divergence.

A normal player level-up must advance exactly one level. Catch-up advancement will be a distinct GM workflow that applies each intervening transition.

GM override will:

- Require the GM role.
- Require a reason and referenced validation issue codes.
- Create an audit record.
- Never bypass schema integrity, ownership, missing rule identity, or malformed state.

Unknown option names will require an enabled, schema-valid homebrew content pack.

### Spellcasting boundaries

The kernel will model the following separately:

- Slot capacity.
- Spell repository, including spellbook, familiar repository, repertoire, full-list access, innate source, and item source.
- Learned spell and learned rank.
- Prepared slot assignment.
- Expended slots and daily preparation state.
- Granted spells.
- Signature-spell assignments.
- Focus spells and focus pool.

Class profiles will support prepared full-list casting, prepared spellbook casting, prepared familiar/repository casting, spontaneous repertoire, class-specific casting, focus casting, innate casting, and item casting.

Non-spell subsystems, including kineticist impulses, will not be represented as spell slots merely to reuse a user interface.

### Linked actors and combat context

Eidolons will be linked actors, not generic companions. The linked-actor model must support the current Summoner rules for shared HP and temporary HP, shared actions, shared multiple attack penalty, Act Together, manifestation and tethering, duplicate area-effect handling, separate conditions where appropriate, and item/rune propagation.

Rules-sensitive HP changes will be represented as typed combat events rather than context-free deltas. Events will retain source, target, action or effect, degree of success, critical and nonlethal context, typed damage components, healing, temporary HP, persistent-damage identity, encounter and turn identity, and an idempotency key.

This is required to distinguish, among other cases, a normal knockout, a critical knockout, a nonlethal knockout, and critical damage while already dying.

### Campaign and homebrew overlays

Campaign variants, Organized Play policies, and homebrew will be versioned overlays rather than edits to the base package.

Examples include:

- Free Archetype.
- Automatic Bonus Progression.
- Proficiency Without Level.
- Gradual Ability Boosts.
- Pathfinder Society policy.

Overlay order will be explicit. Conflicts without a declared resolution will fail package validation.

## Target Package and Data Boundaries

The target source layout is:

    systems/pf2e/rules/
      schema.py
      registry.py
      manifest.py
      predicates.py
      derivation/
        character.py
        proficiencies.py
        spellcasting.py
      validation/
        creation.py
        levelup.py
        prerequisites.py
        spellcasting.py
      combat/
        events.py
        damage.py
        dying.py
      migration/
        legacy_to_remaster_2026_09.py
      ingestion/
        compile_pack.py
        validate_pack.py
        diff_pack.py
      packages/
        pf2e-remaster-2026-09.1/
          manifest.json
          classes/
          ancestries/
          backgrounds/
          feats/
          spells/
          conditions/
          linked_actors/

The boundaries are:

- systems/pf2e/rules owns rules schemas, package loading, derivation, validation, and rules migrations.
- compendium_data remains an ingestion source during transition, not a runtime authority.
- app.py owns HTTP, authorization, transactions, and orchestration, and calls the kernel for rules decisions.
- Templates own presentation only.
- class_matrix.py may temporarily expose a generated compatibility projection. It will not remain a hand-maintained authority.
- Campaign persistence owns ruleset selection and campaign overlays.
- Character persistence owns decisions and runtime state.

The kernel will return structured domain results rather than rendered HTML or framework response objects.

## Source and Version Governance

### Authority order

Rules research and review will use this order:

1. The applicable Paizo-published product and printing.
2. Paizo FAQ, errata, and clarifications.
3. Paizo campaign policy for the corresponding campaign overlay only.
4. Current Archives of Nethys records as a reference and transcription cross-check.
5. Third-party structured data only as an implementation comparison, never the sole authority.

Pathfinder Society policy will not silently become a universal base rule. For example, the September 2026 Organized Play instruction to treat Magus and Summoner chassis updates as immediate errata belongs to the PFS overlay, while the current Pathfinder Impossible Magic records belong in the current base content package.

### Manifest

Every package manifest will contain:

- Game system.
- Immutable ruleset ID.
- Content semantic version.
- Rules schema version.
- Effective and publication dates.
- Publication status.
- Base package and ordered overlays.
- Compiler version.
- SHA-256 for each input, output, and the complete package.
- Included source and errata IDs.
- Supported class roster.
- Explicit exclusions and reasons.
- Required migration IDs.
- License family and required notices.
- Creation timestamp.
- Named rules-review and license-review approvals.

### Per-record provenance

Every enabled record will identify:

- Publisher.
- Product title and SKU or product ID.
- Printing or edition status.
- Page number.
- Publication date.
- Errata or FAQ title, date, and URL where applicable.
- Archives of Nethys URL where useful for cross-checking.
- Verification date.
- Scope: base rules, optional sourcebook, or campaign policy.
- Distribution-rights classification.

An enabled record without adequate provenance will fail compilation. Content with unresolved distribution rights will be quarantined rather than published.

Normalized mechanical facts and citations are package inputs. Wholesale copies of descriptive source text are not.

### Ingestion and publication

Package publication follows:

    source ledger
      -> normalized authoring records
      -> schema validation
      -> semantic and reference validation
      -> deterministic compilation and sorting
      -> package hash
      -> human-readable diff
      -> rules and license review
      -> immutable publication

Production behavior will not depend on live scraping or network availability.

Continuous integration will reject:

- Missing provenance or license classification.
- Duplicate or unstable IDs.
- Dangling grants, predicates, spell IDs, or aliases.
- Unreviewed class-roster changes.
- Errata changes without migration notes.
- Nondeterministic compiler output.
- Automated records without an executable, tested model.
- Attempts to overwrite a published ruleset ID.
- Overlay conflicts without explicit precedence.

## Options Considered

### Option A: In-process versioned rules kernel

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium initially; concentrated rather than distributed |
| Runtime cost | Low; no network boundary |
| Transaction integrity | High |
| Testability | High |
| Migration path | Incremental through adapters and shadow evaluation |
| Operational burden | Low relative to a service |

**Pros**

- One authority for server validation and derivation.
- Works within existing transactions and authorization.
- Low-latency previews.
- Supports immutable campaign pinning and deterministic tests.
- Can be introduced behind compatibility adapters.

**Cons**

- Requires an up-front schema and content compilation investment.
- The application process still carries rules-engine memory and CPU cost.
- Maintainers must enforce module boundaries inside one repository.

### Option B: Continue patching existing Python and templates

| Dimension | Assessment |
| --- | --- |
| Complexity | Low per patch, high cumulatively |
| Runtime cost | Low |
| Transaction integrity | Inconsistent |
| Testability | Low because logic is duplicated |
| Migration path | No durable versioning model |
| Operational burden | Low deployment burden, high maintenance burden |

**Pros**

- Smallest immediate change.
- Familiar to current maintainers.

**Cons**

- Preserves conflicting authorities.
- Browser checks remain bypassable.
- Every erratum requires coordinated edits in several places.
- Cannot reliably identify the rules used by an existing character.

This option is rejected.

### Option C: Browser-side static rules package

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium |
| Runtime cost | Low server cost, larger client payload |
| Transaction integrity | Low without duplicate server logic |
| Testability | Medium |
| Migration path | Supports display versioning but not authoritative persistence |
| Operational burden | Low |

**Pros**

- Responsive builder interactions.
- Easy static caching.

**Cons**

- A forged request can bypass legality.
- Imports and background jobs still require server rules.
- Server duplication would recreate the original problem.

This option is rejected as an authority. Generated client projections remain part of Option A.

### Option D: Standalone rules microservice

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Runtime cost | Network and serialization overhead |
| Transaction integrity | Requires distributed coordination |
| Testability | High in isolation |
| Migration path | Clean boundary but disruptive |
| Operational burden | High |

**Pros**

- Strong organizational isolation.
- Could serve several products in the future.
- Independent deployment and scaling.

**Cons**

- Adds availability, latency, authentication, observability, and version-negotiation concerns.
- Makes character commits and rules validation a distributed transaction.
- The current product does not justify the operational cost.

This option is deferred. The in-process kernel will keep framework-independent domain boundaries so extraction remains possible later.

## Trade-off Analysis

The main trade-off is up-front modeling cost versus continuing correction risk.

Patching existing tables is cheaper for one erratum but expensive and unreliable across current classes, thousands of feats and spells, legacy compatibility, imports, and future releases. The kernel adds deliberate schema and migration work, but makes that work reviewable, deterministic, and reusable.

Keeping the kernel in process accepts less deployment isolation in exchange for transactional correctness and lower operational risk. Framework-independent inputs and outputs preserve a later service-extraction option without paying that cost now.

Declarative automation intentionally stops short of interpreting arbitrary rules prose. A closed predicate and effect model is safer, testable, and auditable. Complex exceptions remain visible as GM-adjudicated behavior until they have a reviewed model.

Immutable packages consume more storage and require explicit migrations, but they provide reproducibility, campaign stability, rollback, and meaningful bug reports.

## Consequences

### Positive

- Character legality and derived statistics have one server-side authority.
- Campaigns remain reproducible after later errata.
- Current, legacy, Organized Play, and homebrew rules can coexist without silent mixing.
- UI code becomes smaller and focused on workflow and presentation.
- Imports, builds, and level-ups share the same validation.
- Rule changes produce reviewable data diffs and migration requirements.
- Golden tests can assert source-backed rules rather than implementation duplication.
- Existing campaigns gain a safe, explicit migration path.

### Negative

- Content normalization is a substantial project.
- Source and license review become mandatory release work.
- Rules migrations must be maintained for published package transitions.
- During rollout, adapters and shadow evaluation temporarily increase complexity.
- Some existing records will be disabled or quarantined until their provenance and semantics are verified.
- Package loading and projection caching require performance monitoring.

### Constraints and follow-up decisions

- Published packages are immutable even when a data error is discovered.
- Legacy support duration must be documented separately.
- License compliance requires ongoing review and is not resolved solely by this ADR.
- Natural-language effects that are not safely modeled remain GM-adjudicated.
- Removing compatibility adapters is a release gate, not optional cleanup.

## Rollout

### Stage 3: Establish the rules authority

1. Freeze the September 2026 source ledger and record explicit exclusions.
2. Implement schemas, stable IDs, manifest loading, hashing, and package validation with no behavior cutover.
3. Normalize a vertical slice covering representative ancestry/background choices, Fighter, Wizard, Magus, Summoner, Psychic, spells, and one Eidolon.
4. Run derivation in shadow mode against existing characters and report differences.
5. Introduce authoritative preview and commit transitions with structured validation.
6. Cut the builder over to generated projections.
7. Cut level-up and spellcasting over to the kernel.
8. Complete and review the current 29-class package.
9. Deliver dry-run, journaled, reversible legacy-to-current migration.
10. Enable the current package for new campaigns, then offer opt-in migrations.

### Stage 6: Complete rules-sensitive workflows

1. Complete linked-actor support, beginning with Eidolons.
2. Introduce typed combat events and correct HP, dying, nonlethal, persistent-damage, and linked-pool transitions.
3. Complete retraining, rebuild, daily preparation, rest, import, and export round trips.
4. Add reviewed campaign-variant overlays.
5. Normalize or explicitly disable remaining supported ancestries, heritages, backgrounds, feats, spells, equipment, archetypes, afflictions, rituals, and related GM rules.
6. Remove hand-maintained PF2e tables from templates, app.py, and class_matrix.py.

### Deployment gates

Rollout order is:

1. Shadow comparison only.
2. Opt-in current rules for new campaigns.
3. Migration preview on cloned campaigns.
4. Explicit migration pilot.
5. Current rules become the new-campaign default.
6. Legacy adapters retire only after their acceptance gates pass.

Rollback uses the prior immutable package for unmigrated campaigns and the migration journal plus recoverable pre-migration snapshot for migrated characters.

## Acceptance Gates

### Architecture and governance gate

- Every campaign and character has an immutable rules binding or an explicit legacy-unknown state.
- Every enabled rule record has source and license provenance.
- Package compilation is deterministic and its hashes are verified.
- Published package IDs cannot be overwritten.
- Production operation does not require live source websites.
- No arbitrary code executes from rules data.

### Builder and level-up gate

- The server rejects invalid creation and advancement payloads independently of browser checks.
- A normal player level-up advances exactly one level.
- Feat, skill, attribute, spell, and class-feature entitlements are validated from the pinned package.
- Player-owned characters cannot invoke GM override.
- GM overrides are reasoned, scoped, and audited.
- No hand-maintained choice, progression, or spell-slot authority remains in templates.
- Stale transition state and duplicate submissions are safely rejected or handled idempotently.

### Current rules gate

- The manifest contains the reviewed 29-class September 2026 roster, including Necromancer and Runesmith.
- All 29 classes have reviewed level 1 through 20 progression goldens.
- Current Magus, Summoner, Psychic, Wizard, Witch, Gunslinger, Inventor, Thaumaturge, Barbarian, and Champion behavior matches their cited current sources.
- Spell slots, known spells or repository, preparation, granted spells, signature spells, focus resources, and daily expenditure are distinct.
- Unsupported or unverified content is visibly disabled rather than silently accepted.

### Migration gate

- Every migration provides a dry-run diff.
- Ambiguous player choices remain unresolved until a player or GM chooses.
- Migration is transactional and idempotent.
- Current HP, temporary HP, conditions, inventory, notes, and daily resource state are preserved unless an explicit reviewed rule requires a change.
- Before and after checksums, migration actor, and rollback information are recorded.

### Stage 6 completion gate

- Eidolon shared HP, temporary HP, action economy, multiple attack penalty, Act Together, manifestation, area-effect handling, and rune/item propagation have integration tests.
- Combat events correctly distinguish normal, critical, and nonlethal knockout and critical damage while dying.
- Creation, level-up, retraining, daily preparation, rest, export, and import form a validated round trip.
- Campaign variants and homebrew use explicit versioned overlays.
- The UI communicates which rules are automated and which require GM adjudication.
- Hand-maintained compatibility tables have been removed from app.py, class_matrix.py, and templates.

### Regression and operational gate

- The pre-ADR baseline of 2,356 passing tests remains green as coverage grows.
- All application templates continue to parse.
- Critical rules tests are not skipped.
- Package load, projection size, and validation latency are measured before setting final service-level objectives.
- Ruleset ID and manifest hash are included in diagnostics for every build, advancement, import, migration, and combat-event failure.

## Action Items

1. [ ] Create the rules schema and immutable manifest contract.
2. [ ] Create the source ledger and complete rules and license review for the September 2026 package.
3. [ ] Add campaign and character rules bindings, build-event history, migration journal, and override audit persistence.
4. [ ] Implement package compilation, semantic validation, deterministic hashing, and diff reporting.
5. [ ] Build the vertical-slice package and golden fixtures.
6. [ ] Add shadow derivation and comparison reporting.
7. [ ] Implement authoritative builder and level-up preview/commit transitions.
8. [ ] Implement the separated spellcasting model.
9. [ ] Normalize and review all 29 current classes.
10. [ ] Implement dry-run and reversible legacy migration.
11. [ ] Implement Eidolon linked-actor behavior.
12. [ ] Implement typed combat events and context-aware dying transitions.
13. [ ] Cut UI workflows over to server projections.
14. [ ] Remove compatibility authorities after all gates pass.

## References

- Paizo licenses: https://paizo.com/licenses
- Open RPG Creative License: https://paizo.com/orclicense
- Pathfinder Remaster FAQ: https://paizo.com/pathfinder/remaster/faq
- Pathfinder FAQ and errata: https://paizo.com/pathfinder/faq
- Pathfinder Impossible Magic: https://store.paizo.com/pathfinder-impossible-magic/
- Paizo September 2026 Organized Play update: https://cdn.paizo.com/blog/september-2026-organized-play-monthly-update
- Current class index: https://2e.aonprd.com/Classes.aspx
- Current character-creation rules: https://2e.aonprd.com/Rules.aspx?ID=2027&Redirected=1
- Current Magus: https://2e.aonprd.com/Classes.aspx?ID=74&Redirected=1
- Current Summoner: https://2e.aonprd.com/Classes.aspx?ID=77&Redirected=1
- Current Psychic: https://2e.aonprd.com/Classes.aspx?ID=68
- Current Wizard: https://2e.aonprd.com/Classes.aspx?ID=39
- Current Witch: https://2e.aonprd.com/Classes.aspx?ID=38
- Current HP, damage, and dying rules: https://2e.aonprd.com/Rules.aspx?ID=2263
