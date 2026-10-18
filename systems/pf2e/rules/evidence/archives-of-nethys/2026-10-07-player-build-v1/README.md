# Archives of Nethys player-build identity snapshot

This directory freezes the metadata-only player-build census observed from
Archives of Nethys on 2026-10-07. It binds the concrete search index
`aon-20261007-063536`; it is not a live dependency and production code does
not query Archives of Nethys.

The snapshot accounts for all 30,389 current-mode search records across 97
categories:

- 18,522 identities in 66 included census/ledger shards;
- 6,278 records in 23 explicitly deferred categories; and
- 5,589 records in 10 excluded or partitioned category rows.

The class shard contains 29 identities: Alchemist, Animist, Barbarian, Bard,
Champion, Cleric, Commander, Druid, Exemplar, Fighter, Guardian, Gunslinger,
Inventor, Investigator, Kineticist, Magus, Monk, Necromancer, Oracle, Psychic,
Ranger, Rogue, Runesmith, Sorcerer, Summoner, Swashbuckler, Thaumaturge,
Witch, and Wizard.

## Trust boundary

This is an identity and source-locator census, not a rules database. It stores
no rules prose, descriptions, stat blocks, spell text, feat text, item text, or
HTML. Every included identity is deliberately `pending` and unreviewed with
the reason `Awaiting Paizo source, rules, and license review.` Completeness here
does not claim that mechanics, source rights, remaster classification, or app
implementations are correct.

The current-mode query excludes search-hidden records and records carrying a
`remaster_id`. A missing `legacy_id` is recorded conservatively as
`unverified`, never inferred from release date. Companion/familiar selector
families, siege-weapon variants, and GM/setting content remain visibly
deferred. Alias and embedded projection rows remain visibly excluded or
partitioned in `scope-policy.json`.

## Independent acquisition

The three authoritative runs used distinct timestamps, run IDs, field sets,
queries, and raw-response hashes:

- scope: `aon-scope-20261007t164129z` at `2026-10-07T16:41:29Z`;
- census: `aon-census-20261007t164152z` at `2026-10-07T16:41:52Z`; and
- ledger: `aon-ledger-20261007t164255z` at `2026-10-07T16:42:55Z`.

Raw responses were hashed into receipts and were not retained. The ledger was
enumerated independently and did not consume census artifacts. Exact query,
response, artifact, count, and run bindings are in `scope-receipt.json`,
`census-receipt.json`, `ledger-receipt.json`, and
`snapshot-manifest.json`.

## Layout and verification

- `scope-policy.json` accounts for every observed category and partition.
- `census/*.json` contains normalized identity and source-locator metadata.
- `ledger/*.json` contains the independently enumerated pending lifecycle.
- `snapshot-manifest.json` binds every JSON evidence artifact, hash, count, and
  capture. This explanatory README is intentionally outside the manifest.

The manifest was generated offline with:

```text
python tools/pf2e_aon_manifest.py --snapshot-root systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1 --snapshot-id pf2e-aon-2026-10-07-player-build-v1
```

Future recaptures must use a new empty directory, an explicitly approved
concrete index, and three new independent acquisition identities. Existing
artifacts are never overwritten.

Authority and licensing references:

- https://2e.aonprd.com/
- https://2e.aonprd.com/Licenses.aspx
- https://paizo.com/licenses
- https://paizo.com/orclicense
