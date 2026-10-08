# PR6B3C Immutable Class Review Overlay Design

## Goal

Create a deterministic, immutable review artifact that gives all 29 current
Archives of Nethys class identities a stable rule ID and reconciles them
against the repository's local PF2e class corpus. Two identities have no local
implementation. This is an evidence step only: it approves no rules, resolves
no license question, and activates no mechanics.

## Frozen inputs

The overlay binds to the released evidence snapshot:

- Snapshot ID: `pf2e-aon-2026-10-07-player-build-v1`
- Snapshot manifest SHA-256:
  `c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de`
- Base category: `class`
- Included identities: 18,522
- Class identities: 29

The PR6B3B snapshot's 137 JSON files remain byte-for-byte immutable. Applying
the overlay in memory changes only the effective class dispositions: 29 become
`mapped`, while 18,493 non-class identities remain `pending`. The base ledger's
18,522 generic reviews remain pending. The overlay separately adds 45 pending
gates: eight source reviews, eight license reviews, and 29 rules reviews.

## Artifact and lifecycle

The first overlay is stored at:

```text
systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/
  authoring.json
  sources.json
  records.json
  manifest.json
```

The directory is create-only and contains exactly those four canonical UTF-8
JSON files. Publication is atomic to observing processes: the target name is
created without replacement only after the private staging directory is
complete. No README, binary, prose, mechanics, or additional file is allowed.

`authoring.json` has exactly `manifest`, `sources`, and `records`. Its manifest
has exactly: `schema_version`, `overlay_id`, `created_at`, `authority`, `kind`,
`snapshot_id`, `snapshot_manifest_sha256`, `base_category`, `activation`,
`parent_overlay_id`, and `parent_overlay_hash`. The v1 parent fields must each
be exactly `null`; parent verification is a future schema change.

An authoring source contains exactly `source_id`, `title`, `source_review`, and
`license_review`. An authoring record contains exactly `identity`, `rule_id`,
`source_id`, and `rules_review`. Those identity-to-ID associations are explicit
operator decisions. Display names never create identifiers. Existing IDs are
reserved: Fighter remains `pf2e.class.fighter` and Player Core remains
`pf2e.source.player-core`.

`sources.json` is the normalized source array. `records.json` is the compiled
record array. Each compiled record has exactly `identity`, `name`,
`canonical_url`, `fingerprint`, `evidence_sha256`, `disposition`, `rule_id`,
`source_id`, `source_ref`, `local`, `reconciliation`, and `rules_review`.
`source_ref` has the exact AoN `title` and `locator`. `local` has `status`,
`relative_path`, `foundry_id`, `content_sha256`, and `local_key`; the last four
are null when status is `missing`.

`manifest.json` contains the normalized authoring manifest plus
`compiler_version`, `inputs`, `outputs`, `counts`, and `overlay_hash`.
`inputs` hashes `authoring.json`; `outputs` hashes only `sources.json` and
`records.json`. The manifest never hashes itself. `overlay_hash` hashes the
canonical manifest with only `overlay_hash` omitted, and `--expected-hash`
binds exactly that value.

## Reviews and activation boundary

Each of the eight source records carries separate `source_review` and
`license_review` objects. Each of the 29 class records carries a
`rules_review`. Review status is `pending`, `approved`, or `rejected`:

- `pending` requires a null reviewer, null review timestamp, and no evidence
  hashes.
- `approved` and `rejected` require a named reviewer, an RFC 3339 UTC
  timestamp, and at least one unique evidence SHA-256.

Every checked-in review in this PR is pending, and current-artifact compilation
and verification reject any non-pending gate. The detached normalizer accepts
the complete status vocabulary for future review tooling. `mapped` means only
that an AoN identity has a stable rule ID and a recorded local reconciliation;
it does not mean a local implementation exists. It never means source text,
mechanics, remaster status, or licensing is certified. Review status never
enables runtime behavior.

## Source and class coverage

The overlay must exactly cover these AoN source groups:

| Source | Classes |
| --- | ---: |
| Player Core | 8 |
| Player Core 2 | 8 |
| Guns & Gears (Remastered) | 2 |
| Rage of Elements | 1 |
| War of Immortals | 2 |
| Battlecry! | 2 |
| Dark Archive (Remastered) | 2 |
| Impossible Magic | 4 |

The compiler copies and verifies each identity, class name, canonical URL,
source title and locator, fingerprint, and evidence hash from the frozen class
census. It performs no HTTP requests.

Local reconciliation must produce exactly:

- 21 `aligned`
- 6 `source-drift`: Gunslinger, Inventor, Psychic, Thaumaturge, Magus, and
  Summoner
- 2 `missing-local`: Necromancer and Runesmith

Title comparison removes only one exact leading `Pathfinder ` prefix from the
local publication title. It does not erase `Remastered` or equate different
books. `aligned` therefore means only name and source-title identity alignment.

Present local records bind the exact `classes/<slug>.json` path, 16-character
Foundry ID, repository-text SHA-256, and filename-derived `local_key`. Missing
records must have null path, ID, hash, and local key. Rule IDs use
`pf2e.class.<kebab-name>` and are unique.

## Cross-platform bytes

Class JSON and review artifacts are repository text with LF endings:

```gitattributes
compendium_data/classes/*.json text eol=lf
systems/pf2e/rules/reviews/**/*.json text eol=lf
```

Local content hashes are computed after the single repository text
normalization `CRLF -> LF`; a lone carriage return is rejected. Equivalent
Windows and Linux checkouts therefore bind to the same content, while every
other byte remains significant. The 27 existing class Git blobs are already
LF, so this policy must not create a class-file content diff.

## Verification and safety

The implementation exposes strict authoring normalization, compilation,
create-only writing, verification, and pure in-memory application. It must:

- require an exact bijection with all 29 frozen class identities;
- scan only the flat 27-file `compendium_data/classes` directory, not the
  18,482-file corpus;
- reject unknown fields, duplicate IDs, unsafe paths, links/reparse points,
  unexpected files, malformed JSON, and hash drift;
- apply the existing 16 MiB per-file limit and bounded-JSON depth-32 / 500,000
  node limits, plus exact limits of eight sources and 29 records;
- require lexical and resolved store, snapshot, and corpus trees to be
  pairwise disjoint in both ancestor and descendant directions, including
  conservative Unicode-normalized case-folded component comparisons and
  bounded existing-object/ancestor `(st_dev, st_ino)` alias checks;
- produce deterministic bytes across input order and `PYTHONHASHSEED`;
- leave source objects and all frozen snapshot bytes unchanged;
- import neither Flask nor `app`, touch no `DATA_DIR`, and use no network;
- leave `CLASS_MATRIX`, `CLASS_PROGRESSION`, the 27 local class files, and all
  runtime manifests unchanged.

## Operator interface

```text
python tools/pf2e_rules.py class-review-compile AUTHORING \
  --snapshot SNAPSHOT_MANIFEST --corpus compendium_data --store STORE

python tools/pf2e_rules.py class-review-verify OVERLAY \
  --snapshot SNAPSHOT_MANIFEST --corpus compendium_data \
  [--expected-hash SHA256]
```

Both commands emit one bounded ASCII-safe JSON line. Validation and I/O
failures use the existing machine-readable exit-2 contract. Compile tests must
exercise multiple `PYTHONHASHSEED` values and injected write, flush, fsync, and
rename failures. A failed publish leaves no accepted target, cleans only a
matching private staging claim and an identity-plus-nonce-matched owned lock,
and never removes a pre-existing or replacement lock. Failure before the
nonce is completely written stops before staging and preserves the partial
lock for operator review because safe automatic ownership proof is absent.

Publication provides create-only, process-visible atomicity, not a promise of
power-loss durability. Each staged file is flushed and fsynced before the
directory rename; persistence of the directory entry across sudden power loss
is platform-dependent because Python has no clean cross-platform directory
fsync contract.

The review store is trusted against active same-account mutation. Random
private staging names and repeated `(st_dev, st_ino)` checks protect staging
cleanup. The create-only lock adds a per-lock random nonce so a replacement
lock cannot look owned merely because its inode was immediately reused.
Quarantine and repeated ownership checks protect normal concurrent writers and
every path swap observed before the final cleanup check. Python has no
portable unlink/rmdir operation conditional on a previously observed
ownership proof. An actor that reads the nonce or discovers and replaces a
random private claim after that final check can already mutate the review
artifacts themselves and is outside this boundary. Publication and
verification still fail closed on every observed mismatch.

## Excluded work

This PR does not add Necromancer or Runesmith to the runtime, alter any class
progression, certify rule descriptions, change the builder or level-up flow,
or make a UI decision. Those changes require later reviewed overlays and, for
player-facing work, approved mockups.
