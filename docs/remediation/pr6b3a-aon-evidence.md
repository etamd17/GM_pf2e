# PR6B3A: offline PF2e evidence inventory

## Outcome

PR6B3A adds a deterministic, offline evidence layer for the comprehensive PF2e
rules program. It can inventory the checked-in local corpus, compare an
independently captured Archives of Nethys census with a separately reviewed
disposition ledger, and report evidence drift. It does not enable rules, change
schema-v2 package bytes, alter the Flask application, or prove that any
mechanic, source right, or license conclusion is correct.

Archives of Nethys remains a transcription cross-check. The applicable Paizo
publication, printing, and errata remain the primary rules authority under
[ADR-0003](../adr/0003-versioned-pf2e-rules-kernel.md).

## Independent artifacts

The evidence model deliberately keeps three inputs separate:

1. The AoN census records what was observed on the reference site. Its identity
   is `(page_family, numeric_id)`, not a display name, full URL, redirect flag,
   or bare numeric ID.
2. The disposition ledger records one reviewed decision for each observed
   identity: `mapped`, `pending`, or `excluded`. A mapped row has a stable,
   namespaced rule ID; exclusions retain a reason.
3. The local corpus scan records repository-relative files, raw-byte SHA-256,
   pack-scoped Foundry identity, declared type, publication metadata, and
   classification. It never guesses an AoN identity.

The census and ledger must be independently produced. An inventory that creates
its own observed universe cannot expose omissions. Evidence fingerprints cover
identity and compact source metadata only; copied rules prose and scraped HTML
are rejected by the evidence contracts. Fingerprints and content hashes detect
changes; they are not signatures or proofs that an upstream source is authentic.

## Operator commands

All commands read local files only, write JSON to standard output, and emit
structured errors to standard error.

```text
python tools/pf2e_rules.py evidence-scan compendium_data
python tools/pf2e_rules.py evidence-scan compendium_data --summary
python tools/pf2e_rules.py evidence-audit aon-census.json evidence-ledger.json
python tools/pf2e_rules.py evidence-diff before-audit.json after-audit.json
```

`evidence-diff` consumes complete reports emitted by `evidence-audit`; it does
not consume raw census or ledger files.

| Exit | Meaning |
| ---: | --- |
| 0 | The command input is valid and the audit is complete or the diff is clean. `pending` dispositions and pending human reviews can still be present. |
| 1 | The input is valid, but an audit has missing dispositions or a diff contains evidence drift. |
| 2 | Input is malformed, unsafe, incomplete for comparison, internally inconsistent, unreadable, linked, or attempts an unstable identity rebind. |

A zero exit from `evidence-scan` means only that every supported local JSON file
was classified. A zero exit from `evidence-audit` means every observed external
identity has one disposition. Neither is a rules, source, or license approval.

The eight mutually exclusive drift categories are `added`, `removed`,
`evidence_changed`, `newly_excluded`, `restored`, `exclusion_changed`,
`disposition_changed`, and `review_changed`. Stable rule-ID rebinding and
external-identity rekeying fail instead of being hidden as ordinary drift.

## Measured local corpus

The 2026-10-06 scan of checked-in `compendium_data` produced these exact totals:

| Measure | Count |
| --- | ---: |
| JSON files | 18,482 |
| Canonical local records | 18,471 |
| Structural `_folders.json` files | 10 |
| Structural folder rows | 1,324 |
| Generated `spells/master_spells.json` files | 1 |
| Generated spell-projection rows | 1,795 |

The requested player-facing packs currently contain:

| Pack | Canonical records |
| --- | ---: |
| Classes | 27 |
| Ancestries | 50 |
| Heritages | 321 |
| Backgrounds | 484 |
| Feats | 5,845 |
| Actions | 544 |
| Conditions | 43 |
| Spells | 1,795 |
| Equipment | 5,566 |

Across all canonical records, publication metadata reports 11,530 ORC rows,
6,718 OGL rows, and 223 rows with no license value. Rules-era metadata reports
12,125 Remaster rows, 6,123 Legacy rows, and 223 unknown rows. License and
Remaster status are retained as independent evidence rather than inferred from
one another. The scan also keeps 158 adventure-scoped records, 74
campaign-scoped records, and 78 iconic example actors visibly separate from
global content.

Semantic kinds intentionally differ from pack totals because classification
uses both the declared Foundry document type and its pack context. For example,
the semantic inventory contains 686 actions, 5,868 feats, and 44 conditions.
Equipment is further visible as 957 weapons, 201 armor records, 115 shields,
203 ammunition records, 1,640 consumables, 2,249 general equipment records, 46
backpacks, 2 kits, and 153 treasure records. The feat pack contains 321 records
with skill category, 1,919 with the archetype trait, and 215 with the dedication
trait; those sets overlap and must not be added together.

## Declared gaps

- The accepted current target has 29 classes. The local corpus has 27 and does
  not contain Necromancer or Runesmith.
- Local records do not contain AoN page-family/ID identities, canonical AoN
  locators, source page numbers, or an export-version binding. Name matching is
  insufficient because names collide.
- Skills are not standalone local records. Skill actions and skill feats exist,
  while skill definitions are referenced indirectly.
- Archetypes and dedications are represented as feat records. Weapons and other
  item families are equipment subtypes. Coverage must respect those encodings
  without treating display names or paths as canonical rules identity.
- Class `system.spellcasting` is only a zero/one marker. It cannot represent
  prepared versus spontaneous casting, repertoire, spellbook, focus, innate,
  bounded, or class-specific slot progression. Twelve class records set it to
  one and fifteen set it to zero.
- Folder rows and the master-spell projection are noncanonical artifacts and
  are always reported separately.
- The legacy `build_db.py` importer is not a safe rules-ingestion path. Its path
  heuristics include projections and effects in unrelated families, omit some
  families, generate identities, and discard provenance and progression data.
- The local corpus remains an ingestion source, not a runtime or rules
  authority. Its 18,471 rows are not presumed legal, current, source-verified,
  licensed for redistribution, or mechanically executable.

## Trust and safety boundaries

- No command performs HTTP, follows redirects, reads the current clock, or
  imports the web application.
- JSON duplicate keys, non-finite values, excessive nesting, symlinks, and
  Windows reparse points fail closed. Finite floats are accepted only by the
  local Foundry-corpus parser.
- Local content hashes cover exact raw bytes. AoN evidence fingerprints use a
  documented canonical projection and exclude their own claimed fingerprint.
- Every timestamp and review identity is supplied evidence; the tool does not
  invent reviewers or capture dates.
- Publication title irregularities are preserved as observed evidence instead
  of being silently cleaned.
- Names are not identity: 195 duplicated name values cover 393 local records.
  The Foundry ID `fRlvmul3LbLo2xvR` also occurs in both
  `equipment-effects/effect-parry.json` and
  `feat-effects/effect-living-fortification.json`, proving that local identity
  must remain scoped by pack and declared type.
- Publication evidence is mixed and incomplete: 230 canonical records have no
  nonempty title, 597 are marked OGL and Remaster, and 2 are marked ORC and
  Legacy. License family and rules era therefore remain independent fields.
- No command writes an output file. An operator may explicitly redirect stdout
  into a new review artifact and then place that artifact under normal review.
- The existing CI PF2e job already executes this suite; no workflow expansion
  or production deployment behavior is required for PR6B3A.

## What comes next

PR6B3A creates the accounting machinery, not the comprehensive rules package.
The next evidence increment must freeze a real AoN census and independently
review every disposition. Subsequent source-review increments must bind those
identities to applicable Paizo products and errata, normalize mechanics into the
closed schema, add level 1-through-20 and cross-system goldens, and keep every
unverified record pending, excluded, or quarantined. Runtime builder and
level-up cutover remains a later PR with separate GM/player UI mockup approval.
