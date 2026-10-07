# PR6B3B Current AoN Census Implementation Plan

> **For agentic workers:** Execute this approved plan on
> `remediation/pr6b3b-aon-census` with strict RED-GREEN TDD, generated-artifact
> verification, and one independent whole-branch review.

**Goal:** Freeze the October 7, 2026 Archives of Nethys Remaster-mode
player-build universe as independently enumerated, metadata-only evidence so
later rules increments can prove what remains unreviewed without changing any
runtime behavior.

**Architecture:** Preserve PR6B3A ledger schema v1 and its existing census-v1
reader while adding census schema v2 solely to represent the conservative
`unverified` rules-era state. A v2 census produces audit-report schema v2;
saved v1 census/audit artifacts remain readable and retain their closed v1
era vocabulary. Add a strict snapshot-collection manifest that
binds category-scoped census and ledger shards, a complete AoN category scope
policy, three independent acquisition runs (scope, census, and ledger), exact
hashes/counts, and the accepted 29-class roster. Network access is confined to
an explicit developer capture utility; verification and CI remain offline.
Every in-scope identity starts `pending` until Paizo source, rules, and license
review occurs in later PRs.

**Tech stack:** Python 3.11 standard library, the existing rules validation
primitives, pytest, and AoN's public search endpoint.

**Specs:** `docs/adr/0003-versioned-pf2e-rules-kernel.md` and
`docs/remediation/pr6b3a-aon-evidence.md`.

## Global constraints

- No Flask runtime, template, UI, database, deployment, package-v2, or current
  character behavior changes.
- No rules prose, HTML, markdown, summaries, images, or executable data may be
  stored. Capture only identity, name, category, source locator, release date,
  and legacy/remaster linkage needed to fingerprint evidence.
- AoN remains a transcription cross-check. Paizo publications and errata are
  the later mechanical authority, and this snapshot is not a license approval.
- Use AoN's Remaster-mode predicate: exclude search-hidden records and records
  with a `remaster_id`, while retaining current remaster replacements and
  unsuperseded legacy-era material.
- Derive `rules_era=remaster` only when AoN supplies a `legacy_id`; use an
  explicit `unverified` evidence state when linkage does not prove an era.
  Never infer era from a date alone or misuse `mixed` to mean unknown.
- Run census capture and ledger enumeration separately. The ledger generator
  must never read census files.
- Each AoN category is an independent shard and must have fewer than 10,000
  results. Equality between reported total and returned hits is a hard gate.
- Every current AoN category must appear once in the scope policy, with its
  observed rows partitioned among included, deferred, and excluded counts;
  policy totals must reconcile to the resolved index total.
- Timestamps, index identity, and snapshot ID are supplied capture inputs;
  offline verification never reads the clock or network.

## Scope

The included universe covers current character creation, advancement, and
sheet operation: classes and class options, class features/progression,
ancestries, heritages, backgrounds, archetypes/dedications, skills/actions,
feats, spells/rituals/traditions, equipment/items/weapons/armor/shields,
conditions and afflictions, deities/domains, traits/languages, and related
selectable subsystems whose canonical identity is exactly `(page_family,
numeric_id)`. AoN categories such as domain, language, trait, and weapon group
are normalized to the existing generic `rule` evidence kind; class-choice
pages such as eidolons are normalized to `class-feature`. The original AoN
category remains independently visible in the scope policy and shard name.
`follower` is provisionally normalized to `class-feature`, while `relic` and
`set-relic` are provisionally normalized to `item`; those are identity-census
projections, not claims about their eventual mechanical schemas.

The first snapshot explicitly defers site metadata, sample builds, setting
articles, creatures, creature templates/families, hazards, kingdoms/warfare,
planes, and other GM encounter-content families. It also defers companion and
familiar categories whose real AoN identity includes selectors such as
`Type=Advancement`, `Unique=true`, or `Specific=true`. Siege weapons are
deferred for the same identity revision because distinct variant records share
one `(page_family, numeric_id)` URL and differ only in their AoN record IDs.
Forcing those rows into the current identity would collide distinct records.
Their exact category counts remain in the scope policy, so they cannot
disappear silently and can be added after a reviewed selector- and
record-aware identity revision.

Aliases and embedded projections are excluded rather than treated as
standalone identities. This includes skill-general-action aliases, tradition
spell-list projections, embedded class-feature and item-bonus rows, equipment
child variants, and duplicate combination-weapon search rows. Most point to a
canonical current page; some item/equipment projections reference a parent
that is superseded or absent from the current-mode result, so exclusion does
not claim that every projection has an included parent. Categories may
therefore partition observed rows among included, deferred, and excluded
counts; all partitions must reconcile exactly.

## Target layout

```text
systems/pf2e/rules/
  evidence/archives-of-nethys/2026-10-07-player-build-v1/
    README.md
    scope-policy.json
    snapshot-manifest.json
    scope-receipt.json
    census-receipt.json
    ledger-receipt.json
    census/<category>.json
    ledger/<category>.json
  ingestion/
    evidence_snapshot.py
tools/
  pf2e_aon_capture.py
  pf2e_aon_manifest.py
  pf2e_rules.py
tests/pf2e_rules/
  fixtures/aon-capture/
  test_aon_capture.py
  test_evidence_snapshot.py
  test_current_aon_snapshot.py
docs/remediation/pr6b3b-aon-census.md
```

## Task 1: snapshot collection contract

Files: `systems/pf2e/rules/ingestion/evidence_snapshot.py`, synthetic snapshot
fixtures, `tests/pf2e_rules/test_evidence_snapshot.py`.

1. Write failing tests for closed manifest/policy shapes, relative-path
   containment, link refusal, exact file hashes, unique categories and paths,
   category-total reconciliation, distinct acquisition identities, matching
   resolved index/date/scope, cross-shard identity uniqueness, expected shard
   counts, and exact 29-class roster binding.
2. Confirm RED failures identify the missing snapshot interfaces.
3. Implement detached, bounded normalization and offline verification by
   reusing `normalize_aon_census`, `normalize_evidence_ledger`, and
   `audit_evidence_inventory` for each shard.
4. Return only a compact summary from collection verification; never assemble
   or print a multi-megabyte combined report.

Expected: tampering, path escape, stale rows, missing rows, duplicate identity,
   capture alias mismatch, and partial category accounting all fail closed with a
   stable error code/path. Capture receipts bind every aggregate query/result
   hash to the exact category artifact path, hash, and record count.

## Task 2: versioned evidence state and metadata-only AoN capture compiler

Files: `systems/pf2e/rules/ingestion/evidence_inventory.py`,
`systems/pf2e/rules/ingestion/evidence_snapshot.py`,
`systems/pf2e/rules/ingestion/aon_capture.py`,
`tools/pf2e_aon_capture.py`, `tests/pf2e_rules/test_evidence_inventory.py`,
`tests/pf2e_rules/test_evidence_snapshot.py`,
`tests/pf2e_rules/test_aon_capture.py`, and small response fixtures under
`tests/pf2e_rules/fixtures/aon-capture/`.

1. Write failing compatibility tests proving census v1 remains behavior- and
   byte-compatible and rejects `unverified`; census v2 accepts `unverified`;
   ledger remains v1; a v2 census plus v1 ledger produces and validates a v2
   audit report; snapshot verification accepts that pairing; and saved
   v1/v2 audit diff handling is deterministic and explicitly version-aware.
   Fingerprint validation receives the census schema version so a v2-only
   value can never leak into a v1 record.
2. Write failing tests for strict endpoint/index allowlists, fixed `_source`
   fields, Remaster-mode and search-hidden predicates, 10,000-result refusal,
   reported/returned count equality, response-index consistency, canonical
   URL/ID agreement, duplicate refusal, prohibited prose-field rejection,
   source-pair validation, conservative rules-era mapping, deterministic
   fingerprints, and census/ledger separation.
   Scope capture requires an operator-supplied, preapproved concrete index and
   stops if AoN resolves any other index. Query contract v1 has explicit
   versioned builders and literal golden hashes covering every included
   category in census and ledger modes; snapshot-schema-v1 verification calls
   those builders directly so later query versions cannot reinterpret old
   receipts.
   Elasticsearch status and aggregation counters are strict integers with
   complete shard reconciliation, and malformed output-directory components
   are rejected before any network request.
3. Implement separate census, ledger, and audit-report version constants and
   version-aware normalizers. Preserve the census-v1 and audit-v1 contracts;
   v2 adds only `unverified`, and v2 census input emits audit-report v2.
4. Implement a developer-only standard-library client with bounded responses,
   explicit user agent, conservative request pacing, retry limits, and no app
   imports. Make transformation functions independently testable without a
   network.
5. Census mode writes metadata-rich census-schema-v2 shards, using
   `unverified` when AoN linkage does not establish an era. The v1 reader and
   existing v1 artifacts remain valid, but v1 never accepts that new state.
   Ledger mode retains schema v1, runs a new enumeration using only identity
   fields, and writes all-pending ledger shards; it has no census input option.
   Snapshot verification requires one census schema version across the entire
   run and binds every category's evidence kind and page families to the
   frozen v1 query contract rather than trusting manifest self-declarations.
6. Generate canonical compact JSON with trailing newline. Capture commands may
   create generated artifacts; hand editing those artifacts is prohibited.

Expected: the capture utility cannot retain rules text, cannot silently accept
partial results, and cannot manufacture the ledger from the census.

## Task 3: freeze and verify the October 7 snapshot

Files: the target evidence directory, `tools/pf2e_aon_manifest.py`, and
`tests/pf2e_rules/test_current_aon_snapshot.py`.

1. Run scope, census, and ledger captures independently against the same
   preapproved concrete AoN index and approved scope policy, with distinct
   supplied run IDs/timestamps and result hashes. The scope run must stop if
   the observed index differs. The ledger run may not consume census artifacts.
2. Generate `snapshot-manifest.json` offline from the three independently
   produced capture receipts (scope, census, and ledger), with exact artifact
   hashes, query/result hashes, observed counts, page families,
   included/deferred/excluded totals, and the current class roster. The
   generator fully verifies the in-memory result before an overwrite-refusing
   canonical write; checked-in bytes must reproduce exactly.
3. Add failing then passing integration tests proving offline verification,
   29 current classes including Necromancer and Runesmith, every included row
   pending/unreviewed, no prose-shaped fields, per-file safety budgets, and
   deterministic readback/fingerprints.
4. Run the existing evidence audit independently over every shard and pin the
   compact aggregate totals.

Expected: the checked-in snapshot accounts for the complete current category
universe and proves which player-build identities await source review without
claiming their mechanics are correct.

## Task 4: operator CLI, documentation, and regression gates

Files: `tools/pf2e_rules.py`, `tests/pf2e_rules/test_cli.py`,
`docs/remediation/pr6b3b-aon-census.md`.

1. Add failing CLI tests for `evidence-snapshot-verify MANIFEST`, including
   compact deterministic output, invalid/tampered exits, no traceback, no
   network access, and no data mutation.
2. Add thin CLI wiring and document capture, recapture, drift, trust,
   licensing, category deferrals, and exact snapshot totals.
3. Run focused PF2e tests, all non-browser/non-PostgreSQL regressions, template
   parsing, `git diff --check`, artifact regeneration comparison, and an
   independent whole-branch review.
4. Fix every material finding through RED to GREEN. Commit, push, open the PR,
   wait for CI, and merge under the user's standing authorization only when all
   gates pass.

Expected: maintainers can verify the complete frozen snapshot offline and can
recapture later AoN releases without affecting production runtime.

## Likely failure points

1. **False independence:** generating ledger rows from census output would make
   completeness circular. Separate endpoint runs, field sets, run IDs, and
   result hashes are mandatory.
2. **Partial capture:** AoN category results can approach the 10,000-result
   limit. Reported totals must exactly equal returned hits, otherwise capture
   stops and the category must be repartitioned deliberately.
3. **Era/category overclaiming:** AoN linkage metadata is incomplete. Census
   schema v2 records linkage-unknown eras as `unverified`; `mixed` remains a
   substantive classification and is never used as an unknown placeholder.
   The scope policy retains AoN's category rather than pretending a more
   precise rules classification.
4. **Safety-budget overflow:** the existing 16 MiB file and 500,000-node limits
   remain unchanged. Category shards and compact summaries keep every artifact
   within those boundaries.
5. **Copyright/license drift:** query source fields are allowlisted and prose
   keys are rejected. Names and source locators are evidence only; later source
   and license approval remains mandatory.

## Acceptance criteria

- The current AoN resolved index and October 7 site update are immutably bound.
- All current AoN categories and counts are present once in the scope policy.
- Every included identity has one census row and one independently enumerated
  pending ledger row; there are no missing, stale, or duplicate identities.
- The current class shard contains exactly the accepted 29-class roster.
- No checked-in evidence artifact contains rules prose or scraped HTML.
- Snapshot verification is deterministic, offline, bounded, and fail-closed.
- Existing package bytes, hashes, production behavior, and UI are unchanged.
- Documentation clearly states that completeness is not mechanical, source,
  or license approval and identifies the deferred GM-content expansion.
