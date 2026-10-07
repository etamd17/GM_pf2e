# PR6B3B: current Archives of Nethys identity census

## Outcome

PR6B3B freezes the Archives of Nethys (AoN) current-mode player-build
identity universe observed on 2026-10-07. The checked-in snapshot accounts for
every record and category returned by the approved scope query, independently
enumerates each included identity in census and ledger runs, and gives later
rules increments a deterministic queue of records that still require Paizo
source, rules, and license review.

This is an offline evidence increment. It does not enable a rule, change a
rules package, import the Flask application, alter persistence or deployment,
or change any GM or player UI. AoN remains a transcription cross-check; the
applicable Paizo publication, printing, and errata remain the mechanical
authority under [ADR-0003](../adr/0003-versioned-pf2e-rules-kernel.md).

The complete artifact layout and capture identities are also summarized in
the [snapshot README](../../systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/README.md).

## Frozen snapshot

| Field | Frozen value |
| --- | --- |
| Snapshot ID | `pf2e-aon-2026-10-07-player-build-v1` |
| Scope ID | `pf2e-player-build-current-v1` |
| Site update date | `2026-10-07` |
| Resolved concrete index | `aon-20261007-063536` |
| Unique AoN categories | 97 |
| Total current-mode records | 30,389 |
| Included identities | 18,522 in 66 census/ledger shards |
| Deferred records | 6,278 in 23 categories |
| Excluded records | 5,589 in 10 excluded or partitioned category rows |
| Census rules eras | 8,169 `remaster`; 10,353 `unverified` |
| Ledger lifecycle | 18,522 `pending`; 0 mapped; 0 reviewed |
| Manifest SHA-256 | `c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de` |
| Canonical verification-summary SHA-256 | `49c9830a3cf38b6fbe1fd98cdb08240c18b21d40feebcecc7e8ab8ff925101a9` |

The 29-class roster is: Alchemist, Animist, Barbarian, Bard, Champion, Cleric,
Commander, Druid, Exemplar, Fighter, Guardian, Gunslinger, Inventor,
Investigator, Kineticist, Magus, Monk, Necromancer, Oracle, Psychic, Ranger,
Rogue, Runesmith, Sorcerer, Summoner, Swashbuckler, Thaumaturge, Witch, and
Wizard.

The independent acquisitions are bound to distinct operator-supplied
identities and timestamps:

| Run | Run ID | Captured at |
| --- | --- | --- |
| Scope | `aon-scope-20261007t164129z` | `2026-10-07T16:41:29Z` |
| Census | `aon-census-20261007t164152z` | `2026-10-07T16:41:52Z` |
| Ledger | `aon-ledger-20261007t164255z` | `2026-10-07T16:42:55Z` |

Raw AoN responses are not retained. Their hashes, the immutable query hashes,
the generated-artifact hashes, counts, concrete index, and acquisition
identities are retained in the three receipts and bound by
`snapshot-manifest.json`.

## Trust boundary

The snapshot proves metadata accounting, not rule correctness. Its canonical
external identity is `(page_family, numeric_id)`. Stored census record fields
are exactly `identity`, `canonical_url`, `name`, `kind`, `rules_era`,
`source_refs`, `evidence_sha256`, and `fingerprint`. Optional upstream release
and linkage fields are validated during capture and bound only through
`evidence_sha256` and the derived `rules_era`; those raw fields are not
retained. Records contain no descriptions, rules prose, feat or spell text,
stat blocks, HTML, markdown, images, or executable mechanics.

Every ledger entry is deliberately unreviewed and has the exact reason
`Awaiting Paizo source, rules, and license review.` A complete verification
therefore means that every included identity has exactly one pending
disposition. It does not mean that any identity is source-approved,
license-approved, mechanically accurate, safe to redistribute, or implemented
by the application.

The current-mode query excludes search-hidden records and records carrying a
`remaster_id`. `rules_era=remaster` is derived only when AoN supplies a
`legacy_id`; absent linkage remains `unverified`. A date, title, or apparent
edition is never used to infer the era. The census uses schema v2 for this
conservative `unverified` state; the independently generated disposition
ledger remains schema v1.

Fingerprints and SHA-256 hashes detect changed bytes and metadata. They are not
signatures and do not establish that upstream content is authentic. The
snapshot also makes no claim about hidden, superseded, historical, or
non-search content outside the frozen query contract.

## Offline verification

From the repository root, verify the checked-in snapshot with:

```text
python tools/pf2e_rules.py evidence-snapshot-verify systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json
```

Success exits `0` and writes exactly one deterministic, ASCII-safe JSON line to
standard output. For this snapshot the expected line is:

```json
{"categories": {"deferred": 23, "excluded": 10, "included": 66}, "census_schema_version": 2, "class_count": 29, "complete": true, "dispositions": {"excluded": 0, "mapped": 0, "pending": 18522}, "kinds": {"action": 555, "affliction": 100, "ancestry": 68, "archetype": 251, "armor": 38, "background": 538, "class": 29, "class-feature": 428, "condition": 56, "deity": 488, "equipment": 3531, "feat": 6376, "heritage": 314, "item": 163, "ritual": 163, "rule": 3134, "shield": 16, "skill": 33, "spell": 1834, "vehicle": 99, "weapon": 308}, "page_families": {"Actions.aspx": 555, "Ancestries.aspx": 68, "Apparitions.aspx": 14, "ArcaneSchools.aspx": 29, "ArcaneThesis.aspx": 5, "Archetypes.aspx": 251, "Armor.aspx": 38, "ArmorGroups.aspx": 7, "Backgrounds.aspx": 538, "Bloodlines.aspx": 18, "CampMeals.aspx": 27, "Causes.aspx": 7, "ClassKits.aspx": 32, "Classes.aspx": 29, "Conditions.aspx": 56, "ConsciousMinds.aspx": 6, "Curses.aspx": 56, "Deities.aspx": 488, "DeviantFeats.aspx": 10, "Diseases.aspx": 44, "Doctrines.aspx": 3, "Domains.aspx": 61, "DruidicOrders.aspx": 9, "Eidolons.aspx": 26, "Elements.aspx": 6, "Epithets.aspx": 18, "Equipment.aspx": 3499, "FatalMethods.aspx": 2, "Feats.aspx": 6376, "Followers.aspx": 6, "GrimFascinations.aspx": 4, "HellknightOrders.aspx": 16, "Heritages.aspx": 314, "HuntersEdge.aspx": 4, "HybridStudies.aspx": 15, "Ikons.aspx": 21, "Implements.aspx": 11, "Innovations.aspx": 7, "Instincts.aspx": 10, "Languages.aspx": 117, "Lessons.aspx": 19, "Methodologies.aspx": 5, "Muses.aspx": 5, "Mysteries.aspx": 12, "MythicCallings.aspx": 15, "MythicRituals.aspx": 17, "MythicSpells.aspx": 16, "Patrons.aspx": 17, "Practices.aspx": 4, "Rackets.aspx": 6, "Relics.aspx": 122, "ResearchFields.aspx": 4, "Rituals.aspx": 146, "Rules.aspx": 2358, "RunesmithRunes.aspx": 44, "SetRelics.aspx": 14, "Shields.aspx": 16, "Skills.aspx": 33, "Spells.aspx": 1818, "Styles.aspx": 6, "SubconsciousMinds.aspx": 4, "Tactics.aspx": 37, "Tenets.aspx": 2, "Traits.aspx": 564, "Vehicles.aspx": 99, "Ways.aspx": 11, "WeaponGroups.aspx": 17, "Weapons.aspx": 308}, "records": {"deferred": 6278, "excluded": 5589, "included": 18522, "total": 30389}, "resolved_index": "aon-20261007-063536", "reviews": {"pending": 18522, "reviewed": 0}, "schema_version": 1, "scope_id": "pf2e-player-build-current-v1", "shard_count": 66, "site_update_date": "2026-10-07", "snapshot_id": "pf2e-aon-2026-10-07-player-build-v1"}
```

Malformed, tampered, unsafe, unreadable, or internally inconsistent input exits
`2`, writes one structured JSON error such as
`{"error": "hash_mismatch", "path": "$.shards[0].census.sha256"}` to standard
error, and emits no traceback. There is no exit-`1` state for this command.

The verifier is read-only and offline. It performs no HTTP request, reads no
clock, imports no application module, and changes no artifact. It verifies the
closed manifest and policy shapes, receipt/query/artifact hashes, frozen query
contract, distinct capture identities, index/date/scope agreement, exact
category accounting, shard kinds and page families, census/ledger identity
equality, global identity uniqueness, record counts, the 29-class roster, and
the all-pending lifecycle. Linked paths, path escapes, duplicate JSON keys,
non-finite values, excessive depth or node counts, oversized files, unknown
fields, inconsistent retained counts, stale rows, and missing or duplicated
identities fail closed. The capture tool separately rejects partial upstream
responses before producing those retained artifacts.

## Deferred and excluded scope

The 23 deferred categories are retained with exact counts in the scope policy;
they have not disappeared from the program.

Identity-v2 work is required before these 382 records can be represented
without collisions or selector loss:

| Category | Records |
| --- | ---: |
| `animal-companion` | 102 |
| `animal-companion-advanced` | 5 |
| `animal-companion-specialization` | 11 |
| `animal-companion-unique` | 2 |
| `familiar-ability` | 141 |
| `familiar-specific` | 37 |
| `siege-weapon` | 84 |

The GM and setting-content expansion owns the remaining 5,896 deferred
records:

| Category | Records |
| --- | ---: |
| `article` | 107 |
| `class-sample` | 116 |
| `creature` | 3,929 |
| `creature-ability` | 45 |
| `creature-adjustment` | 54 |
| `creature-family` | 442 |
| `creature-theme-template` | 16 |
| `cult-activity` | 5 |
| `hazard` | 662 |
| `kingdom-event` | 45 |
| `kingdom-structure` | 76 |
| `plane` | 25 |
| `source` | 330 |
| `warfare-army` | 11 |
| `warfare-tactic` | 21 |
| `weather-hazard` | 12 |

The 5,589 excluded records are aliases, embedded projections, or child
variants rather than standalone snapshot-v1 identities:

| Category or partition | Records |
| --- | ---: |
| `category-page` | 220 |
| `class-feature` | 774 |
| `deity-category` | 40 |
| `draconic-exemplar` | 44 |
| `equipment` child variants | 3,019 |
| `item-bonus` | 991 |
| `sidebar` | 459 |
| `skill-general-action` | 19 |
| `tradition` | 5 |
| `weapon` combination-melee projections | 18 |

`equipment` and `weapon` have both included canonical pages and excluded
projections. This is why 66 included, 23 deferred, and 10 excluded/partitioned
category rows should not be added as if they were 99 unique categories. The
scope contains 97 unique categories. Excluding a projection also does not
assert that its apparent parent is present in the included universe.

## Recapture workflow

Recapture is a deliberate evidence event, not an update-in-place operation.
Use a new empty directory and a new snapshot ID. Never overwrite or hand-edit a
released snapshot, receipt, shard, or manifest.

1. Independently identify and approve the concrete AoN index, site update date,
   scope ID, new snapshot ID, three distinct run IDs, and three explicit UTC
   timestamps. Do not infer or accept a different index returned during
   capture.
2. Run the scope capture first. Replace every angle-bracketed value below with
   the approved value:

   ```text
   python tools/pf2e_aon_capture.py scope --scope-id <scope-id> --site-update-date <YYYY-MM-DD> --expected-index <aon-YYYYMMDD-HHMMSS> --run-id <scope-run-id> --captured-at <scope-timestamp-Z> --output-root <new-snapshot-root>
   ```

3. Review `scope-policy.json` and `scope-receipt.json`. The command stops if
   AoN returns a different concrete index, a partial response, a new or missing
   category, an unreconciled category total, or a category outside the frozen
   scope contract. A changed upstream universe requires an explicit code and
   policy review; do not loosen the check during capture.
4. Run the metadata census against the generated policy:

   ```text
   python tools/pf2e_aon_capture.py census --scope-policy <new-snapshot-root>/scope-policy.json --run-id <census-run-id> --captured-at <census-timestamp-Z> --output-root <new-snapshot-root>
   ```

5. Run the ledger enumeration as a separate endpoint acquisition. Supply the
   census timestamp as a binding only; do not read or transform census shard
   files to create ledger rows:

   ```text
   python tools/pf2e_aon_capture.py ledger --scope-policy <new-snapshot-root>/scope-policy.json --run-id <ledger-run-id> --captured-at <ledger-timestamp-Z> --output-root <new-snapshot-root> --census-captured-at <census-timestamp-Z> --snapshot-id <new-snapshot-id>
   ```

6. Generate the manifest offline after all three receipts and both shard sets
   exist:

   ```text
   python tools/pf2e_aon_manifest.py --snapshot-root <new-snapshot-root> --snapshot-id <new-snapshot-id>
   ```

7. Verify the new manifest with `evidence-snapshot-verify`, run the focused
   snapshot and PF2e regression tests, inspect the complete artifact diff, and
   record the new exact totals and hashes in its README and remediation record.

The capture client is pinned to
`https://elasticsearch.aonprd.com/aon/_search`, refuses redirects, requests
only frozen allowlisted metadata fields, uses at least 0.5 seconds between
requests, and makes at most three attempts for narrowly retryable HTTP status
codes. Responses are capped at 16 MiB, 500,000 JSON nodes, and depth 32.
Duplicate keys, malformed JSON, partial shard results, truncated hit sets,
reported/returned count differences, unexpected page families, identity
collisions, and category results at or above 10,000 stop capture. Such a
failure requires a reviewed query/identity-contract revision, never silent
truncation.

Output files use canonical JSON with a trailing newline, are flushed before
publication, and are created exclusively. The tool refuses existing outputs,
linked targets, reparse points, unsafe paths, and malformed destination
components before a network request. The manifest builder is offline,
re-verifies the complete in-memory manifest, and refuses to replace an existing
`snapshot-manifest.json`.

## Regression evidence

The frozen Task 3 artifact at commit `01915ac8` passed all 286 tests under
`tests/pf2e_rules`. The pinned integration tests additionally establish:

- all 18,522 census identities exactly equal their independently enumerated
  ledger identities, globally without duplicates;
- every included record is pending and unreviewed with the exact pending
  reason;
- all 137 referenced JSON files are canonical and within the 16 MiB safety
  boundary;
- the 137 JSON files total 12,447,582 bytes, with `census/feat.json` largest at
  2,773,704 bytes;
- the manifest rebuilds byte-for-byte from the receipts and shards;
- the artifact tree contains exactly those 137 referenced JSON files plus its
  explanatory README; and
- the record allowlists, fingerprints, 29-class roster, aggregate summary,
  manifest hash, and rules-era totals are pinned.

Task 4 CLI coverage exercises the successful deterministic output, tampered and
invalid inputs, structured error exits, absence of tracebacks, no network
access, and no filesystem mutation. Final merge still requires the complete
PF2e suite, the repository's non-browser/non-PostgreSQL regression gate,
template parsing, artifact reproduction, `git diff --check`, and independent
whole-branch review.

## What comes next

This snapshot makes the size of the source-review queue explicit. Later
increments must review and map identities category by category, bind mechanics
to exact Paizo publications and applicable errata, record named rules and
license reviews, and leave unresolved records pending, excluded, or
quarantined. The selector-aware companion/familiar/siege identity revision and
the deferred GM/setting-content census remain separate expansions. Runtime
builder, character level-up, spellcasting, and any GM/player UI change remain
later PRs with their own acceptance and mockup gates.
