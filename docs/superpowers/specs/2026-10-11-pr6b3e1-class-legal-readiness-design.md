# PR6B3E1 class legal-readiness design

**Status:** Design approved; written spec awaiting review
**Date:** 2026-10-11
**Scope:** Offline legal, printing, and errata readiness evidence; no review
approval, mechanics, runtime activation, or UI change

## Decision

Build a separate immutable readiness bundle for the eight publications already
bound to the PR6B3D class-source review. The bundle records what evidence is
actually available, what is still missing, and the exact blockers that prevent
license review. It does not reinterpret product metadata, Archives of Nethys,
or playtest notices as final-book legal evidence.

This increment is named PR6B3E1 because seven final-book artifacts and legal
pages are unavailable. It prepares a fail-closed contract without pretending
that the full redistribution gate has been completed. A later PR6B3E2 may
accept final artifacts and a named legal review, but that work is outside this
design.

PR6B3D remains the active immutable class-review overlay. Its eight source
reviews stay approved, its eight license reviews and 29 class-rules reviews
stay pending, `activation` stays `none`, and `enabled_mechanics` stays `0`.
PR6B3E1 publishes no successor overlay.

## Frozen inputs and output

- Class-source review ID: `pf2e-class-source-review-2026-10-10.1`
- Class-source review hash:
  `e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb`
- Source-evidence bundle ID: `pf2e-class-sources-2026-10-10.1`
- Source-evidence bundle hash:
  `4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3`
- New readiness bundle ID:
  `pf2e-class-legal-readiness-2026-10-11.1`
- New readiness scope: `class-license-readiness`

The checked-in bundle lives at
`systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/`
and contains one canonical `manifest.json` plus exactly eight canonical JSON
dossiers. The manifest binds both the exact PR6B3D overlay ID/hash and the
exact upstream source-evidence bundle ID/hash, and it hashes each readiness
dossier. Each dossier binds its exact upstream source ID, evidence ID, and
dossier SHA-256.

The manifest schema is closed to exactly `schema_version`, `bundle_id`,
`scope`, `authority`, `created_at`, `class_review_overlay_id`,
`class_review_overlay_hash`, `source_bundle_id`, `source_bundle_hash`,
`counts`, `dossiers`, and `bundle_hash`. `authority` is `paizo`.
`dossiers` contains exactly eight entries sorted by `source_id`; each entry is
closed to `evidence_id`, `source_id`, `path`, and `sha256`. Each dossier hash is
the SHA-256 of its canonical UTF-8 JSON bytes. `bundle_hash` is the SHA-256 of
the canonical manifest after omitting only `bundle_hash`.

The PR6B3C and PR6B3D directories and all upstream evidence files remain
byte-identical.

## Readiness dossier contract

The dossier schema is closed. It contains only bounded identifiers, hashes,
dates, URLs, page locators, enumerated observations, and blocker codes. It has
no arbitrary notes field and retains no PDF bytes, book text, rule prose,
notice prose, descriptive product prose, or art.

Each dossier is closed to exactly `schema_version`, `evidence_id`, `source_id`,
`source_evidence_id`, `source_dossier_sha256`, `product_ids`,
`artifact_observation`, `printing_observation`, `revision_observation`,
`legal_notice_observation`, `errata_observation`, `downstream_compliance`,
`review_readiness`, and `supported_review_scopes`. These fields record:

- `schema_version`, `evidence_id`, `source_id`, and the bound upstream evidence
  ID and SHA-256;
- the upstream-frozen hardcover and PDF product IDs;
- `artifact_observation`, including one of `hash_observed` or `missing`;
- `printing_observation` and `revision_observation`, both explicitly
  `unverified` with null designations in PR6B3E1;
- `legal_notice_observation`, including one of `observed` or `missing` and only
  normalized notice-family and locator facts supported by the observed
  artifact;
- `errata_observation`, including an inventory status, applicability status,
  official Paizo URL, and bounded date/page locators without correction text;
- `downstream_compliance`, whose status remains `missing` and whose blocker
  codes cover attribution, notices, reserved material, trademarks, branding,
  and distribution treatment where applicable;
- `review_readiness`, whose status is exactly `blocked` with at least one
  blocker; and
- `supported_review_scopes`, exactly `["license_readiness"]`.

The bundle deliberately exposes no evidence-hash lookup for `license_review`.
It cannot satisfy an overlay approval. Unknown keys, unbounded prose, a
completed-review identity, a `ready` status, or a verified printing/revision
is invalid in schema version 1.

The allowed values and cross-field rules are closed:

- `product_ids` is closed to `hardcover` and `pdf`, and both must equal the
  bound upstream dossier;
- `artifact_observation` is closed to `status`, `product_id`, `sha256`, and
  `retained`; `retained` is always false in PR6B3E1;
- printing and revision observations are each closed to `status` and
  `designation`;
- `legal_notice_observation` is closed to `status`, `family`, and
  `notice_locator`; a non-null locator is closed to positive integer
  `printed_page` and `pdf_page`;
- `errata_observation` is closed to `inventory_status`,
  `applicability_status`, `official_url`, `captured_at`, `response_sha256`, and
  `locators`;
- `downstream_compliance` and `review_readiness` are each closed to `status`
  and `blockers`;
- artifact status is `hash_observed` or `missing`; `hash_observed` requires a
  PDF product ID and SHA-256, while `missing` requires both to be null;
- printing and revision status are exactly `unverified` with null
  designations;
- legal-notice status is `observed` or `missing`; `observed` requires family
  `orc` and a printed/PDF page locator in this version, while `missing`
  requires a null family and locator;
- errata inventory status is `complete_as_of_capture` or `unverified`, and
  applicability status is exactly `unresolved`; a complete inventory requires
  an official URL, capture timestamp, response SHA-256, and a nonempty sorted
  locator list, while an unverified inventory requires null capture fields and
  an empty locator list;
- a normalized errata locator is closed to `release_label`, `printing_label`,
  `pages`, and `entry_type`; labels are newline-free section-heading metadata
  no longer than 64 characters, pages are sorted distinct positive integers,
  and `entry_type` is `erratum` or `clarification`;
- downstream compliance status is exactly `missing`; its sorted unique blocker
  list draws only from `attribution-review`, `notice-review`,
  `reserved-material-review`, `trademark-review`, `branding-review`, and
  `distribution-review`; and
- readiness status is exactly `blocked`; its sorted unique blocker list draws
  only from `final-artifact-missing`, `final-legal-notice-missing`,
  `printing-unverified`, `revision-unverified`,
  `errata-inventory-unverified`, `errata-applicability-unresolved`,
  `downstream-compliance-missing`, and `named-reviewer-missing`.

## Exact production observations

The production bundle contains exactly eight source dossiers.

For Player Core only:

- artifact status is `hash_observed` for PDF product `PZO12001E` and SHA-256
  `494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`;
- the observed legal notice locator is printed page 463 / PDF page 464;
- the legal-notice family observation is `orc`, without treating that fact as
  redistribution approval;
- exact printing and revision remain unverified;
- the errata inventory is `complete_as_of_capture` for the official
  [Pathfinder FAQ and errata page](https://paizo.com/pathfinder/faq) as captured
  on 2026-10-11, while every entry's applicability is `unresolved` because the
  PDF printing is unknown; the normalized list includes every Player Core
  entry visible in that captured response and retains only release, printing,
  page, and erratum/clarification locators; and
- the downstream compliance package is missing.

For Player Core 2, Guns & Gears (Remastered), Rage of Elements, War of
Immortals, Battlecry!, Dark Archive (Remastered), and Impossible Magic:

- final artifact and final legal-notice statuses are `missing`;
- legal-notice family and locator are null; printing and revision are
  `unverified` with null designations; errata inventory is `unverified` with
  null capture fields and an empty locator list; errata applicability is
  `unresolved`; and
- product pages, Archives of Nethys, release announcements, organized-play
  policy, previews, supplements, and playtest PDFs cannot substitute for the
  final-book artifact.

Rage of Elements remains license-neutral. No aggregate ORC-only or OGL-only
classification is permitted.

The manifest's exact readiness counts are:

| Measure | Count |
| --- | ---: |
| Sources | 8 |
| Artifact hashes observed | 1 |
| Final artifacts missing | 7 |
| Final legal notices observed | 1 |
| Final legal notices missing | 7 |
| Printings unverified | 8 |
| Revisions unverified | 8 |
| Errata inventories complete as captured | 1 |
| Errata inventories unverified | 7 |
| License reviews ready | 0 |
| License reviews blocked | 8 |
| License reviews approved | 0 |
| Enabled mechanics | 0 |

## Offline verifier and report

Add a focused `class_legal_readiness` ingestion module rather than expanding
the PR6B3D source-identity schema. Its public verifier requires the readiness
manifest, exact PR6B3D overlay and all inputs needed by the existing schema-v2
overlay verifier, and the exact upstream source-evidence manifest. It verifies
the complete PR6B3D chain before accepting the bound overlay ID/hash,
optionally accepts a trusted readiness bundle hash, and returns detached
canonical data. A separate pure report function returns the exact counts and
per-source blocker codes.

The PF2e CLI exposes read-only `class-legal-readiness-verify` and
`class-legal-readiness-report` commands. PR6B3E1 adds no compiler or approval
command: a later schema and design must define how completed legal evidence is
published and consumed.

Verification is deterministic, offline, Flask-independent, and
`DATA_DIR`-independent. It rejects:

- wrong, missing, self-referential, or drifted upstream bundle bindings;
- unknown, duplicate, cross-source, wrong-scope, tampered, or swapped
  dossiers;
- mismatched source rosters, product IDs, evidence IDs, or upstream dossier
  hashes;
- claims that missing evidence is observed or that blocked evidence is ready;
- non-canonical JSON, unexpected files, traversal, aliases, ancestors,
  symlinks, reparse points, hardlinks, and non-regular payloads; and
- any path overlap that could let a mutable input masquerade as an independent
  retained artifact.

All fixed readiness, overlay, parent, and source-evidence manifest/dossier
leaves are checked before their package bytes are read. The runtime application
must neither import this module nor read this bundle.

## Testing and acceptance

Focused tests cover canonical hashes, the exact eight-source roster, the
one-observed/seven-missing split, all readiness counts, upstream binding,
closed-schema validation, detached return data, CLI JSON, and deterministic
blocker reporting.

Adversarial tests cover tampering, duplicate identities and hashes, source
swaps, roster drift, unknown fields/statuses, false readiness claims,
non-canonical bytes, unexpected files, traversal, symlinks, reparse points,
hardlinks, aliases, ancestor overlap, and mutation after verification.

Integration tests prove:

- the retained PR6B3C, PR6B3D, and source-evidence hashes and bytes do not
  change;
- all eight class license reviews and all 29 class-rules reviews remain
  pending;
- no mechanics or application behavior is enabled;
- importing the Flask application does not import or read legal-readiness
  evidence; and
- in-process read-denial and subprocess canary tests prove verifier and CLI
  execution neither read nor create or mutate `DATA_DIR`;
- template parsing, the complete PF2e suite, the primary non-browser and
  non-PostgreSQL suite, `git diff --check origin/main...HEAD`, a fresh
  whole-branch review, and GitHub CI pass before merge.

## Later PR6B3E2 gate

PR6B3E2 cannot begin approval work until the seven missing final artifacts and
legal pages, evidence sufficient to identify exact printings/revisions,
applicable official errata, and a named license reviewer are available. That
design must define a successor evidence schema and, if approvals are supported,
a schema-v3 class-review overlay bound to exact PR6B3D and PR6B3E2 hashes.

Only supported license-review objects are permitted to change. If all eight
sources are approved, the later overlay would have 16 approved gates and 29
pending gates, with zero rejected gates and zero enabled mechanics. Those
counts are a future target, not a PR6B3E1 claim.

## Out of scope

This design does not provide legal advice, approve redistribution, identify an
unverified printing, copy source text, review class mechanics, change the six
source-drift classes, implement Necromancer or Runesmith, process the other
18,493 pending identities, activate runtime behavior, or make a GM/player UI
decision. Any later UI change still requires an approved mockup before code is
written.
