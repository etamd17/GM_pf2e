# PR6B3D: class publication-identity review design

## Approved outcome

PR6B3D advances only the publication-identity gate for the 29 class records
mapped by PR6B3C. It retains eight canonical Paizo product dossiers, binds a
new immutable schema-v2 overlay to those dossiers and to the exact PR6B3C
parent, and records all eight source reviews as approved. It does not approve
redistribution rights, a printing, errata applicability, or any class rule.

The production result must contain exactly 45 review gates:

| Gate | Approved | Pending | Rejected |
| --- | ---: | ---: | ---: |
| Publication identity | 8 | 0 | 0 |
| Redistribution/license | 0 | 8 | 0 |
| Class rules | 0 | 29 | 0 |
| **Total** | **8** | **37** | **0** |

`activation` remains `none` and `enabled_mechanics` remains `0`. No runtime,
database, route, template, or GM/player UI code is in scope.

## Immutable ancestry

The PR6B3C artifact is a retained root and must stay byte-identical:

| Field | Required value |
| --- | --- |
| Parent ID | `pf2e-class-identities-2026-10-07.1` |
| Parent hash | `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614` |
| Parent schema | `1` |
| Parent files | The same four canonical files and bytes already on `main` |

Schema v1 continues to require a null parent and 45 pending gates. Schema v2
requires a non-null parent ID and hash, a retained parent directory supplied to
the offline compiler/verifier, and non-null `evidence_bundle_id` and
`evidence_bundle_hash` fields for a retained evidence bundle. The child may
change source- or license-review objects only. Source IDs, titles, class
identities, rule IDs, source assignments, local reconciliation, and all
rules-review objects are frozen to the parent. This PR changes only the eight
source-review objects; license and rules reviews remain pending.

The checked-in child is:

```text
systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/
  authoring.json
  manifest.json
  records.json
  sources.json
```

Its immutable binding is:

| Field | Required value |
| --- | --- |
| Child ID | `pf2e-class-source-review-2026-10-10.1` |
| Child hash | `e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb` |
| Parent ID | `pf2e-class-identities-2026-10-07.1` |
| Parent hash | `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614` |
| Evidence bundle ID | `pf2e-class-sources-2026-10-10.1` |
| Evidence bundle hash | `4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3` |
| Compiler | `pf2e-class-review-2` |
| Activation | `none` |
| Enabled mechanics | 0 |

## Retained Paizo evidence bundle

The retained bundle is
`systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/`.
Its immutable bundle hash is
`4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3`.
It contains one canonical `manifest.json` plus exactly eight canonical JSON
dossiers under `dossiers/`. The bundle contains metadata and citations only;
it contains no copied rule prose, descriptive product prose, or art.

The bundle manifest binds its ID, creation time, `paizo` authority,
`class-publication-identity` scope, every dossier's relative path and SHA-256,
and a self-derived bundle hash. Each dossier strictly binds:

- schema and evidence IDs;
- the overlay source ID, parent-frozen overlay title, and official product title;
- separate Paizo hardcover/PDF product IDs and release date;
- the exact class rule-ID roster assigned to the product;
- official Paizo product, license, and FAQ/errata URLs;
- bounded artifact, printing, and legal-notice observations; and
- the sole supported review scope, `source_review`.

The dossier schema is closed to exactly `schema_version`, `evidence_id`,
`source_id`, `overlay_title`, `official_product_title`, `publisher`,
`product_ids`, `release_date`, `class_rule_ids`, `official_urls`,
`artifact_observation`, `printing_observation`, `revision_observation`,
`rights_observation`, and `supported_review_scopes`. It has no arbitrary notes
or prose field.

The eight product mappings are:

| Source ID | Overlay / official title | Hardcover / PDF IDs | Release evidence | Classes |
| --- | --- | --- | --- | --- |
| `pf2e.source.player-core` | Player Core / Pathfinder Player Core | `PZO12001-HC` / `PZO12001E` | [2023-11-15](https://store.paizo.com/pathfinder-player-core/) | Bard, Cleric, Druid, Fighter, Ranger, Rogue, Witch, Wizard |
| `pf2e.source.player-core-2` | Player Core 2 / Pathfinder Player Core 2 | `PZO12004-HC` / `PZO12004E` | [2024-08-01](https://store.paizo.com/pathfinder-player-core-2/) | Alchemist, Barbarian, Champion, Investigator, Monk, Oracle, Sorcerer, Swashbuckler |
| `pf2e.source.guns-and-gears-remastered` | Guns & Gears (Remastered) / Pathfinder Guns & Gears (Remastered) | `PZO12010-HC` / `PZO12010E` | [2025-02-05](https://store.paizo.com/pathfinder-guns-gears-remastered-pdf/) | Gunslinger, Inventor |
| `pf2e.source.rage-of-elements` | Rage of Elements / Pathfinder Rage of Elements | `PZO2113-HC` / `PZO2113E` | [2023-08-03](https://store.paizo.com/pathfinder-rage-of-elements/) | Kineticist |
| `pf2e.source.war-of-immortals` | War of Immortals / Pathfinder: War of Immortals | `PZO12006-HC` / `PZO12006E` | [2024-10-30](https://store.paizo.com/pathfinder-war-of-immortals/) | Animist, Exemplar |
| `pf2e.source.battlecry` | Battlecry! / Pathfinder Battlecry! | `PZO12008-HC` / `PZO12008E` | [2025-07-31](https://store.paizo.com/pathfinder-battlecry/) | Commander, Guardian |
| `pf2e.source.dark-archive-remastered` | Dark Archive (Remastered) / Pathfinder Dark Archive (Remastered) | `PZO12012-HC` / `PZO12012E` | [2026-02-04](https://paizo.com/blog/find-your-path-february-2026), [PFS corroboration](https://paizo.com/pathfindersociety/characteroptions) | Psychic, Thaumaturge |
| `pf2e.source.impossible-magic` | Impossible Magic / Pathfinder Impossible Magic | `PZO12014-HC` / `PZO12014E` | [2026-07-30](https://store.paizo.com/pathfinder-impossible-magic/) | Magus, Necromancer, Runesmith, Summoner |

The canonical product URLs are the corresponding `store.paizo.com` product
pages. Common official citations are `https://paizo.com/licenses` and
`https://paizo.com/pathfinder/faq`.

The user-supplied Player Core PDF (`PZO12001E`) is observed only by SHA-256
`494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`.
The PDF is not retained; its printed-page 463/PDF-page 464 notice locator is
metadata only. Its exact printing remains explicitly unverified. Its ORC notice was observed,
but redistribution review remains pending because this repository does not yet
have a reviewed downstream notice/compliance package. No final-book artifact
or legal page is retained for the other seven dossiers, so their exact
printings and redistribution reviews remain pending. Rage of Elements stays
license-neutral with `transition_schedule_unverified` context; without its
final legal page this slice infers neither OGL nor ORC and must not classify the
complete target package as ORC-only or silently mix OGL- and ORC-era
obligations.

## Evidence and lifecycle rules

Every completed review must carry at least one evidence SHA-256 and every hash
must resolve to a canonical retained dossier in the supplied bundle. The
dossier must name the same source and explicitly support that review scope.
Unknown hashes, repeated hashes, cross-source evidence, evidence that supports
the wrong review scope, or a tampered/swapped bundle fail closed. Pending
reviews continue to require no reviewer, timestamp, or evidence hash.

The production child requires exactly one dossier hash in each approved source
review, reviewer `OpenAI Codex`, and reviewed-at timestamp
`2026-10-10T13:39:32Z`. Its eight license reviews and 29 rules reviews remain
the parent's exact pending objects.

## Offline compiler and verifier boundary

The public class-review compile and verify APIs and CLI commands gain explicit
parent and evidence inputs for schema v2. Schema v1 remains source-compatible
and byte-compatible without those arguments. Compilation and verification:

- operate entirely offline and never import the Flask application;
- require the exact child target, parent, evidence, snapshot, and corpus leaves
  not to overlap or alias, including lexical, resolved, link/reparse, bounded
  component, and existing-object identity checks already used by PR6B3C;
- permit the intended layout in which parent and child are distinct retained
  siblings below the same review store while keeping that store disjoint from
  snapshot, corpus, and the evidence store;
- verify parent and evidence canonical bytes and trusted hashes before applying
  the child;
- reject self-parenting, wrong or swapped parents, parent/snapshot drift,
  missing evidence, evidence tampering, and any forbidden parent-child field
  change;
- preserve the create-only, atomic four-file overlay publication contract; and
- never read or write `DATA_DIR`.

The child remains a four-file overlay. Parent and evidence artifacts are
external immutable inputs and are not copied into it.

## Reproducible offline verification

From the repository root, verify the checked-in child against its exact parent,
evidence bundle, snapshot, corpus, and trusted hash:

```text
python tools/pf2e_rules.py class-review-verify \
  systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1 \
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json \
  --corpus compendium_data \
  --parent systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1 \
  --evidence systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json \
  --expected-hash e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb
```

Reproduce its canonical bytes into a new empty review store:

```text
python tools/pf2e_rules.py class-review-compile \
  systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1/authoring.json \
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json \
  --corpus compendium_data \
  --store <empty-review-store> \
  --parent systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1 \
  --evidence systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json
```

A successful reproduction creates the same four canonical files byte-for-byte.
Both commands remain offline, do not import the Flask application, and do not
read or write `DATA_DIR`.

Publication identity approval means only that the eight class-to-publication
mappings are supported by retained Paizo metadata. It does not approve a
printing, errata applicability, redistribution rights, or any class mechanic.
The child therefore has exactly eight approved source reviews, eight pending
license reviews, and 29 pending rules reviews.

## Acceptance contract

- PR6B3C's four files and trusted hash are byte-identical.
- The Paizo bundle has exactly eight dossiers and reproducible canonical hashes.
- The schema-v2 child reproduces byte-for-byte and verifies against exact
  parent, snapshot, corpus, bundle, and trusted child hash.
- Counts are exactly 29 mapped classes, 18,493 remaining base identities,
  eight approved gates, 37 pending gates, zero rejected gates, and zero enabled
  mechanics.
- All 29 rules reviews and all eight license reviews remain pending.
- Runtime imports and reads do not reach either review overlay or the evidence
  bundle.
- Focused PF2e tests, the complete PF2e suite, template parsing, and the primary
  non-browser/non-PostgreSQL suite pass before merge.

## Remaining blockers and next review slices

Redistribution remains blocked because exact printings are unverified for all
eight products. Seven products still lack a retained final-book artifact and
legal page; Player Core has an observed notice but no reviewed downstream
notice/compliance package. Rage of Elements remains license-neutral, with no
OGL- or ORC-only conclusion. Mechanics remain blocked because no level 1-20
class rule has been reviewed.

Later slices must, in order:

1. retain and review the missing final-book legal evidence, exact printings,
   applicable errata, and the mixed OGL/ORC notice and attribution package;
2. review source-backed level 1-20 mechanics for all 29 classes;
3. remediate the six local source-drift records;
4. implement Necromancer and Runesmith separately; and
5. continue independently reviewable overlays for the other 18,493 pending
   ancestry, heritage, background, archetype, feat, skill, spell, weapon,
   equipment, and related identities.

No runtime activation or GM/player UI work is authorized by this slice. Any
later UI change still requires an approved mockup before implementation.
