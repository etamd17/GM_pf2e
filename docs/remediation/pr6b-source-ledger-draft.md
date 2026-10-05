# PR6B source ledger: review draft

Research snapshot: 2026-10-01. This ledger is a review queue for the accepted
ADR-0003, not a published Pathfinder rules package or a license determination.
Source IDs below are proposed stable identifiers; none is enabled in a package.

## Candidate source inventory

| Proposed ID | Work and scope | Evidence available | Review still needed |
| --- | --- | --- | --- |
| `pf2e.source.player-core` | [Player Core](https://store.paizo.com/pathfinder-player-core/), product `PZO12001-HC`; Remaster ancestry/background creation, Fighter, Wizard and core spells | Paizo product page; release listed as 2023-11-15. User-supplied PDF: Fighter initial proficiencies on printed p. 137 (PDF p. 138); ORC Notice on printed p. 463 (PDF p. 464). | Exact printing/PDF revision, applicable errata, attribution/distribution review, independently approved mechanical transcription |
| `pf2e.source.dark-archive-remastered` | [Dark Archive (Remastered)](https://store.paizo.com/pathfinder-dark-archive-remastered/), product `PZO12012-HC`; current Psychic candidate | Paizo product page | Publication date and exact printing/PDF revision, Psychic pages, book notices, first-printing content review |
| `pf2e.source.impossible-magic` | [Impossible Magic](https://store.paizo.com/pathfinder-impossible-magic/), product `PZO12014-HC`; current Magus/Summoner candidate | Paizo product page; release listed as 2026-07-30 | Exact printing/PDF revision, class and Eidolon pages, book notices, first-printing content review |

The [Paizo FAQ and errata](https://paizo.com/pathfinder/faq) is the candidate
authority for these proposed errata IDs. The precise publication entry and
application to the selected printing need to be recorded during review:

| Proposed ID | Candidate scope | Risk to resolve |
| --- | --- | --- |
| `pf2e.errata.player-core-spring-2025` | Wizard spellbook advancement, p. 195 | Confirm the selected Player Core printing includes or needs this correction |
| `pf2e.errata.player-core-spring-2026` | Ancestry traits, p. 41 | Confirm applicability to the selected printing |
| `pf2e.errata.dark-archive-remastered-spring-2026` | First-printing Psychic amp timing | Keep distinct from older Dark Archive remaster-compatibility guidance |
| `pf2e.errata.impossible-magic-summer-2026` | First-printing Magus/Summoner corrections | Confirm the complete errata chain and exact affected pages |

## Player Core Fighter level-1 evidence (not package authoring)

The user-supplied *Pathfinder Player Core* PDF has SHA-256
`494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`.
The book identifies itself as Second Edition and carries a 2023 Paizo copyright,
but the inspected cover, notice and PDF metadata do not establish its
printing or whether later errata have been incorporated. File timestamps are not
printing evidence. Keep the proposed source unverified until its revision and
applicable errata are established.

Schema v2 records exact source evidence as `artifact_sha256` plus independent
`printing` and `revision` status/designation objects. Any source directly or
transitively cited by an enabled record must declare a captured artifact hash.
Base and optional-sourcebook sources must declare both statuses verified;
limited non-book scopes may use a reviewer-attested `not_applicable` status.
The schema validates the declaration's shape, not the external artifact or the
reviewer's conclusion. The current Player Core candidate would use the hash
above with both statuses `unverified` and both designations null. It remains in
this research ledger, not a package's source inventory, and cannot support an
enabled Fighter record.

The Fighter initial-proficiencies section on printed p. 137 (PDF p. 138) supports
these **candidate fixed level-1 grants** for the v2 derivation contract:

| Rank | Statistics |
| --- | --- |
| Expert | Perception; Fortitude; Reflex; simple weapons; martial weapons; unarmed attacks |
| Trained | Will; advanced weapons; light, medium and heavy armor; unarmored defense; Fighter class DC |

Under the proposed three-category armor normalization, these yield 13 fixed
statistics. The source says trained in all armor; separating that into light,
medium and heavy armor is a proposed v2 normalization, not an independently
approved rule record. The same page specifies Strength or Dexterity as the
key-attribute choice, 10 + Constitution modifier HP per level, and skill choices
of Acrobatics or Athletics plus 3 + Intelligence modifier additional skills.
Those choices and values are **not** v2 fixed-proficiency grants; their absence
from a derivation result must not be interpreted as untrained or zero.
Printed p. 138 (PDF p. 139) lists level-1 class features, including Reactive
Strike, a Fighter feat and Shield Block. Class features are outside PR6B1's
derivation contract.

The book's ORC Notice is on printed p. 463 (PDF p. 464). It identifies the ORC
license, attributes *Player Core* and its listed creators, identifies specified
Reserved Material including trademarks, proper nouns and art, and states that
there is no expressly designated Licensed Material. That last field does not by
itself resolve the ORC treatment of game mechanics. This location is evidence to
review, **not** a determination that our proposed transcription or distribution
is approved. The project does not yet have a reviewed ORC attribution/notice
artifact or a named license review declaration.
Keep actual Player Core mechanics out of enabled package records until those
gates are satisfied; do not relabel them as `test_only` synthetic facts.

Search-indexed text from Paizo's official
[Player Core FAQ and errata](https://paizo.com/pathfinder/faq) surfaced at least
two Fighter corrections: Aggressive Block (Fall 2023, p. 141) and Sudden Leap
(Spring 2025, p. 147), neither on the level-1 pages above.
[Paizo's Spring 2026 errata announcement](https://paizo.com/blog/spring-errata-2026)
also reports Player Core reprint updates. Direct access to the complete FAQ was
unavailable during this review, so absence of a p. 137-138 correction is **not**
certified; applicability to this unidentified PDF printing remains open.

The separately supplied pre-Remaster *Core Rulebook* (copyright 2019) is an
OGL 1.0a legacy source:
Fighter initial proficiencies appear on printed p. 141 (PDF p. 142), its
level-1 reaction is named Attack of Opportunity on printed p. 142 (PDF p. 143),
and the OGL notice is on printed p. 638 (PDF p. 639). Do not silently substitute
it for the remastered Player Core. The supplied *GM Core* has an ORC Notice on
printed p. 335 (PDF p. 336) but is not the source for Fighter initial
proficiencies.

The [September 2026 Organized Play update](https://cdn.paizo.com/blog/september-2026-organized-play-monthly-update)
is a separate campaign-policy candidate. Its immediate Magus/Summoner update
instruction must not become a universal base-package migration rule.

## Publication gates

- Record the actual product/PDF printing, exact rule pages, applicable errata
  entry, verification date, and independently reviewed normalized mechanics for
  each enabled record.
- Review the specific books' notices and selected content against
  [Paizo's license guidance](https://paizo.com/licenses) and the
  [final ORC license](https://downloads.paizo.com/ORC_LicenseFINAL.pdf). Document
  required attribution, reserved material, project branding and distribution
  rights. A store category or a manifest flag is not approval.
- Obtain named rules and license review declarations based on that evidence.
  Keep uncertain records quarantined. Do not put copyrighted prose into the
  package's retained authoring file.
- Freeze a complete September 2026 source/errata inventory and explicit
  exclusions before claiming the full current package. A small draft slice must
  have its own partial ruleset ID and only its actually enabled classes in
  `supported_classes`; it cannot claim the 29-class target.

The first implementation candidate is an offline Fighter level-1 proficiency
derivation. The page-level expected ranks are now documented above, but a
source-backed executable golden remains pending exact Player Core printing,
complete applicable errata and named rules/license review. Synthetic fixtures
may test the mechanics contract while this review proceeds. PR6C shadow
comparison and PR7 runtime cutover are later gates; no GM/player UI changes are
part of this ledger.
