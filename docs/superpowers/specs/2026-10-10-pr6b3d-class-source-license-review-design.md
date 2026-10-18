# PR6B3D class source and license review design

**Status:** Approved
**Date:** 2026-10-10
**Scope:** Metadata-only publication-identity review; no mechanics or runtime activation

## Decision

Build an immutable schema-v2 successor to the PR6B3C class-identity overlay.
The successor is bound to the exact retained PR6B3C ID, hash, and bytes and to
a canonical bundle of eight Paizo product dossiers. It approves the eight
publication-identity gates while leaving every redistribution/license gate and
every class-rules gate pending.

The expected gate counts are eight approved, 37 pending, and zero rejected.
`activation` remains `none`; `enabled_mechanics` remains `0`; the 29 mapped
class identities and 18,493 other pending base identities do not change.

## Frozen parent and child boundary

- Parent ID: `pf2e-class-identities-2026-10-07.1`
- Parent hash: `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614`
- Child ID: `pf2e-class-source-review-2026-10-10.1`
- The v2 manifest carries non-null `evidence_bundle_id` and
  `evidence_bundle_hash` fields in addition to its parent ID/hash.
- V1 remains byte-compatible, requires a null parent, and requires all 45
  overlay gates pending.
- V2 requires an exact external parent and an exact external evidence bundle.
- Only source- or license-review objects may differ from the parent. This child
  changes only source-review objects. Source identities/titles, class
  identities, rule IDs, source assignments, local reconciliation, and all
  rules-review objects are frozen.
- Parent, child, evidence, snapshot, corpus, and store paths are subject to the
  existing fail-closed disjointness, link/reparse, alias, and bounded identity
  checks wherever applicable. A parent may be a retained sibling below the
  same review store as the child; therefore the store/parent ancestor relation
  is intentionally valid, while the exact child target, parent, evidence,
  snapshot, and corpus leaves must not overlap or alias.

## Evidence bundle

Retain one canonical manifest and exactly eight canonical metadata-only JSON
dossiers at
`systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/`.
The bundle contains no rules prose, descriptive product prose, or art. Its
manifest hashes every dossier and its own canonical content after omitting
only the `bundle_hash` field. Each dossier binds an evidence ID, source ID,
parent-frozen overlay title, official product title, separate hardcover/PDF
product IDs, release date, assigned class
rule-ID roster, official Paizo product/license/FAQ URLs, bounded artifact and
printing observations, bounded rights observations, and the supported
`source_review` scope.

The dossier schema is closed to exactly `schema_version`, `evidence_id`,
`source_id`, `overlay_title`, `official_product_title`, `publisher`,
`product_ids`, `release_date`, `class_rule_ids`, `official_urls`,
`artifact_observation`, `printing_observation`, `revision_observation`,
`rights_observation`, and `supported_review_scopes`. `publisher` is exactly
`Paizo Inc.`; printing and revision statuses are `unverified`; rights review is
`pending`; and `supported_review_scopes` is exactly `["source_review"]`.
There is no arbitrary notes or prose field. Common official URLs include
`https://paizo.com/licenses`, `https://paizo.com/orclicense`, and
`https://paizo.com/pathfinder/faq`.

| Overlay / official title | Hardcover / PDF IDs | Release evidence | Exact assigned classes |
| --- | --- | --- | --- |
| Player Core / Pathfinder Player Core | `PZO12001-HC` / `PZO12001E` | [2023-11-15](https://store.paizo.com/pathfinder-player-core/) | Bard, Cleric, Druid, Fighter, Ranger, Rogue, Witch, Wizard |
| Player Core 2 / Pathfinder Player Core 2 | `PZO12004-HC` / `PZO12004E` | [2024-08-01](https://store.paizo.com/pathfinder-player-core-2/) | Alchemist, Barbarian, Champion, Investigator, Monk, Oracle, Sorcerer, Swashbuckler |
| Guns & Gears (Remastered) / Pathfinder Guns & Gears (Remastered) | `PZO12010-HC` / `PZO12010E` | [2025-02-05](https://store.paizo.com/pathfinder-guns-gears-remastered-pdf/) | Gunslinger, Inventor |
| Rage of Elements / Pathfinder Rage of Elements | `PZO2113-HC` / `PZO2113E` | [2023-08-03](https://store.paizo.com/pathfinder-rage-of-elements/) | Kineticist |
| War of Immortals / Pathfinder: War of Immortals | `PZO12006-HC` / `PZO12006E` | [2024-10-30](https://store.paizo.com/pathfinder-war-of-immortals/) | Animist, Exemplar |
| Battlecry! / Pathfinder Battlecry! | `PZO12008-HC` / `PZO12008E` | [2025-07-31](https://store.paizo.com/pathfinder-battlecry/) | Commander, Guardian |
| Dark Archive (Remastered) / Pathfinder Dark Archive (Remastered) | `PZO12012-HC` / `PZO12012E` | [2026-02-04](https://paizo.com/blog/find-your-path-february-2026), [PFS corroboration](https://paizo.com/pathfindersociety/characteroptions) | Psychic, Thaumaturge |
| Impossible Magic / Pathfinder Impossible Magic | `PZO12014-HC` / `PZO12014E` | [2026-07-30](https://store.paizo.com/pathfinder-impossible-magic/) | Magus, Necromancer, Runesmith, Summoner |

Every approved review hash must resolve to a retained canonical dossier that
names the same source and supports the same review scope. Unknown, duplicate,
cross-source, wrong-scope, tampered, or swapped evidence fails closed.

The supplied Player Core PDF (`PZO12001E`) is observed by SHA-256
`494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`.
The PDF itself is not retained; only its hash and the printed-page 463/PDF-page
464 notice locator are retained. Its exact printing remains unverified. Its
legal notice observation is not a
redistribution approval because the repository lacks a reviewed downstream
notice package. The other seven final-book artifacts/legal pages are not
retained. All eight redistribution reviews therefore remain pending. Rage of
Elements uses a neutral `transition_schedule_unverified` rights context in
this slice; neither OGL nor ORC is inferred without the final book's legal
page, and no aggregate ORC-only claim is allowed.

## Review lifecycle

The eight production source reviews are approved by `OpenAI Codex` at
`2026-10-10T13:39:32Z`, each with exactly its same-source dossier SHA-256.
All eight license reviews and all 29 rules reviews remain the parent's exact
pending objects. Completed reviews require a reviewer, timestamp, and retained
evidence; pending reviews require all three to be absent/empty.

## Offline interfaces

The class-review Python APIs and CLI gain optional explicit parent and evidence
inputs. They are required for schema v2; schema v1 rejects either argument
rather than implying a binding it does not record. Omitting both arguments
remains source- and byte-compatible for v1. Compilation and verification remain
offline, deterministic, Flask-independent, `DATA_DIR`-independent, and use the
same atomic create-only four-file child publication contract. Parent and
evidence files remain external immutable inputs rather than copied child files.

Verification rejects wrong/missing/self parents, cyclic ancestry, parent or
snapshot drift, evidence tampering, forbidden child mutations, and unsafe path
relationships. V1 must still reproduce PR6B3C byte-for-byte.

## Out of scope

This design does not approve printings, errata applicability, redistribution,
descriptions, class mechanics, progressions, feats, spells, equipment, or
other rules. It does not remediate the six local source-drift classes, add the
two missing local classes, change runtime data, or make a GM/player UI choice.
Those remain separate review and implementation slices.
