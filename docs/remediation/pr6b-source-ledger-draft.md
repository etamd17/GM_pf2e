# PR6B source ledger: review draft

Research snapshot: 2026-10-01. This ledger is a review queue for the accepted
ADR-0003, not a published Pathfinder rules package or a license determination.
Source IDs below are proposed stable identifiers; none is enabled in a package.

## Candidate source inventory

| Proposed ID | Work and scope | Evidence available | Review still needed |
| --- | --- | --- | --- |
| `pf2e.source.player-core` | [Player Core](https://store.paizo.com/pathfinder-player-core/), product `PZO12001-HC`; Remaster ancestry/background creation, Fighter, Wizard and core spells | Paizo product page; release listed as 2023-11-15 | Exact printing/PDF revision, record pages, book's ORC and reserved-material notices, approved mechanical transcription |
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
derivation. Source-backed golden expectations remain pending exact Player Core
page and printing review. Synthetic fixtures may test the mechanics contract
while this review proceeds. PR6C shadow comparison and PR7 runtime cutover are
later gates; no GM/player UI changes are part of this ledger.
