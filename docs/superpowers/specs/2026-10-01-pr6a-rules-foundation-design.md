# PR6A: PF2e rules package foundation

Implements the first part of accepted ADR-0003. The owner authorized this scope
on 2026-10-01 after the PR5 handoff. The existing rules ADR and the approved
PR6A scope are the design authority; this document records concrete interfaces.

## Purpose and limits

Give future rules work a reproducible, reviewable format with trustworthy
identity and provenance. PR6A provides offline package tooling, not a rules
engine or a claim that current Pathfinder mechanics have been verified.
Python 3.11 and the standard library are sufficient. Existing application,
database, dependencies, character data, templates, and live behavior stay intact.
Player/GM interface work still requires approved mockups.

PR6B owns actual sourced mechanics and executable derivation; PR6C owns shadow
comparison. PR7 owns server-authoritative builder/advancement transitions.

## Format and trust

An authoring JSON document has exactly `manifest`, `sources`, and `records`.
Explicit strict parsers reject unknown fields, duplicate JSON keys, wrong types,
nonfinite numbers, unsupported versions, unsafe identifiers and deep structures.
Errors expose stable codes and paths, not arbitrary input values.

The manifest carries system, immutable ruleset ID, content/schema versions,
effective/publication dates and status, supplied UTC creation timestamp, base
package and ordered overlays, source/errata IDs, supported class IDs, exclusions,
required migration IDs, license family/notices, and named rules/license reviews.
Compiler identity plus input/output/package SHA-256 values are generated.
PR6A rejects nonempty base/overlay configuration until composition is implemented.

Each source ledger entry identifies publisher, product and product ID, edition,
publication/verification dates, scope, distribution-rights classification, license
family, notices, URL and errata references. Each rule cites source IDs and page
references. Test-only sources are permitted only in draft packages. Publication
requires named rules/license reviews and reviewed distribution rights. This
validates the presence and consistency of review records; it does not establish
legal clearance or authenticate reviewer identity.

Rule records have typed identity/kind/name, level, traits, references, source/page
citations, prerequisite, automation level, and explicit quarantine reason. Only
`reference_only` and `gm_adjudicated` are supported for enabled records in PR6A.
Other automation levels fail until a tested executable model exists. Prerequisite
nodes have a closed structural grammar; PR6A validates but does not evaluate them.
Unknown code or expression fields are rejected. Enabled references and predicate
rule IDs must resolve to enabled records of the expected kind. Quarantined records
are retained for review, excluded from registry lookup, and named as exclusions.
Quarantine permits unresolved provenance but never unknown structure or code.

## Reproducibility and integrity

Canonical JSON uses UTF-8, sorted object keys, compact separators, no ASCII
escaping and a trailing LF. Unordered source/record/ID collections sort by stable
ID. Predicate child order stays explicit. Timestamps come from authoring input.

A package directory contains exactly four files: normalized `authoring.json`,
`sources.json`, `records.json`, and generated `manifest.json`. The manifest hashes
the authoring input and the two output files. The package hash is SHA-256 of the
canonical manifest with only `package_hash` omitted. This binds all metadata and
the digests without a circular self hash. Loading reconstructs the package and
compares every byte. A caller-supplied expected package hash detects a consistent
replacement; self-contained hashes alone are integrity checks, not signatures.

Publication uses a store directory plus ruleset ID, an exclusive per-ID lock,
staged files, and rename. Existing IDs, including identical content, are never
overwritten. CLI reads only explicitly supplied local files, follows no symlink
inputs, and package loading rejects symlinks and unexpected directory entries.
No network or application imports occur. Inputs and files have bounded sizes.

Loaded manifests and records are deeply immutable. Registry lookup uses stable
IDs; aliases, base-package composition and mechanical derivation are deferred.
Diffs report added/removed/changed records and source/manifest changes in stable
order, including provenance changes that leave mechanical records unchanged.

## Interfaces and verification

- `compile_package(authoring: dict) -> dict[str, bytes]` is pure.
- `write_package(authoring: dict, store: Path) -> Path` writes a new immutable ID.
- `load_package(path: Path, *, expected_hash: str | None = None) -> RulePackage`
  verifies structure, bytes, semantic references and optional trusted binding.
- `RulePackage.get(rule_id: str) -> RuleRecord` rejects quarantined/missing IDs.
- `diff_packages(before: RulePackage, after: RulePackage) -> dict` is pure.
- `python tools/pf2e_rules.py compile|validate|diff` exposes operator tooling.

Acceptance tests cover input permutations and hash-seed reproducibility,
tampering and trusted-hash mismatch, strict schema/provenance/predicate failures,
quarantine isolation, immutable loaded objects, output collisions/concurrency,
symlinks, missing/unexpected files, CLI outcomes, and import independence from
Flask. The tracked fixture is openly synthetic and is never a production package.

## Decisions

- Use standard-library dataclasses and strict parsing, matching existing domain
  code and avoiding deployment dependency changes.
- Reject currently unsupported overlays and automated rule levels explicitly.
  PR6B may extend the schema with a version change; PR6A cannot imply those
  capabilities work.
- The project owner authorized committing and pushing the completed PR6A branch
  for review. Merge and production deployment require a separate decision.
