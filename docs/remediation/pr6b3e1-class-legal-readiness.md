# PR6B3E1: class legal-readiness blockers

## Outcome

PR6B3E1 retains an immutable evidence inventory for the eight publications
bound to [PR6B3D](pr6b3d-class-source-license-review.md). It records one
observed artifact hash and final legal notice, seven missing final artifacts
and legal notices, and the blockers that prevent legal review. All eight
license reviews and all 29 class mechanics (`rules_review`) reviews remain
pending. PR6B3D remains the active review overlay; no successor is published.

The 45 PR6B3D gates still comprise eight approved publication-identity gates,
37 pending gates, and zero rejected gates. The other 18,493 base identities
remain pending. `activation` stays `none` and `enabled_mechanics` stays `0`.
This slice changes no runtime, persistence, route, template, class mechanic,
or GM/player UI behavior.

This evidence inventory is not legal advice or redistribution approval.

## Five distinct stages

| Stage | Meaning | PR6B3E1 state |
| --- | --- | --- |
| Observation | Record supported artifact, notice, printing, revision, and errata facts. | One artifact hash/notice observed; missing and unverified facts remain explicit. |
| Readiness | Determine whether sufficient evidence exists to start the supported legal review. | All eight sources blocked; zero ready. |
| Legal review | A named reviewer evaluates final-book legal evidence and downstream compliance obligations. | All eight license reviews pending; named reviewer missing. |
| Approval | Record a completed supported decision bound to exact retained evidence. | Zero license approvals; verification grants no approval. |
| Runtime activation | Make reviewed mechanics available through a separately authorized runtime change. | Zero enabled mechanics; all 29 mechanics reviews pending. |

Each readiness dossier supports only `license_readiness`, never
`license_review`. A successful offline verification proves the immutable
chain and bounded inventory, not readiness, legal approval, or activation.

## Immutable bundle and exact bindings

The checked-in bundle contains one canonical `manifest.json` and exactly
eight canonical JSON dossiers under `dossiers/`:

```text
systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/
```

| Binding | Exact value |
| --- | --- |
| Readiness bundle ID | `pf2e-class-legal-readiness-2026-10-11.1` |
| Readiness bundle hash | `4eeaef808fae0aa8b7ccc1356fcb7381b4d672a8d2281427f01a7ae9cc7cb410` |
| Schema / scope / authority | `1` / `class-license-readiness` / `paizo` |
| Created at | `2026-10-11T16:59:43Z` |
| Bound PR6B3D overlay ID | `pf2e-class-source-review-2026-10-10.1` |
| Bound PR6B3D overlay hash | `e7def3d3bc7f73100a83ea9d59e8d8c289e07d464ce729c5334cabde38d57dbb` |
| Bound source-evidence bundle ID | `pf2e-class-sources-2026-10-10.1` |
| Bound source-evidence bundle hash | `4057206e6b7850e376a5a8757aa6d0398a2dc4c015cda2bdcccb0fa04c4689f3` |
| PR6B3D parent ID | `pf2e-class-identities-2026-10-07.1` |
| PR6B3D parent hash | `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614` |
| Chain snapshot ID | `pf2e-aon-2026-10-07-player-build-v1` |
| Chain snapshot manifest SHA-256 | `c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de` |

For each exact slug below, the dossier path is `dossiers/<slug>.json`, its
source ID is `pf2e.source.<slug>`, its readiness evidence ID is
`paizo.class-legal-readiness.<slug>`, and its bound upstream evidence ID is
`paizo.class-source.<slug>`. The hashes in the second column bind canonical
readiness dossier bytes; the third column binds the exact upstream source
dossier. The bundle hash covers the canonical manifest with only
`bundle_hash` omitted. It is not the hash of the manifest file itself.

| Exact slug | Readiness dossier SHA-256 | Bound upstream source dossier SHA-256 |
| --- | --- | --- |
| `battlecry` | `0aa52077fd2f021ad2e0a50295837a4b6e5dad9cb938e40c4e3b1c31b26d1c44` | `8bf8c631db470a6be9e7ac97a862889ae65126242225232db5ad24b7fbf5472e` |
| `dark-archive-remastered` | `bb210c575898bd5b698aa38fcf0545fdb1f0430193c353b59125f7466136919f` | `8822d8d476b6995a9561857de07e6b76d1fd9b0dd7a901a53bdd4084f2e075d7` |
| `guns-and-gears-remastered` | `7986ba22031e1fa12a3eebb74940c1d08daec27fd9457ffd27ee99c576404ea8` | `b1e6c819fa8922b4289128810bdb2e9358228951fe5ba4979273c6898d37ff6d` |
| `impossible-magic` | `ba219b1e7bd860f5fe47b42a7121b51b148b6420589dc45207eed372b90baac4` | `bbc4c71c7ed58c17c899482329058d2ce4c96c7b42b154d233e97d645f0b514f` |
| `player-core` | `d3a504760533ddd27a10105c3da7d1944438facf9707979eb74691b3abc0ab1c` | `f18b63ad8ca08669f94d332d4f6414aa35ef26d42692a5e95ce9ff920559c443` |
| `player-core-2` | `0700b239b6c3f9c68c885d9786aefa55bbfe489c299bfc774351436bcfde543f` | `0b569a54aeb989dbc6d7879819a37120b5d0363d52e27edf257c0ca9e3e14064` |
| `rage-of-elements` | `ee899b285116997e3cbc9790eab4b6a9ef888ec6f2427f84ba366fc4afaaed17` | `335474b94b818d79ead8116d6ec309c34e6a687b871133c0ade5b9c759923d5e` |
| `war-of-immortals` | `e305d551ae246882ca249ee7b92b689d1c1a5684458089f655cbea29834923ec` | `7b3efef93d2b801a69fca3b90660f78279452bf81f4afa820662cd1ef503ed94` |

Each dossier also binds the upstream-frozen hardcover/PDF product IDs:

| Publication | Hardcover / PDF IDs |
| --- | --- |
| Battlecry! | `PZO12008-HC` / `PZO12008E` |
| Dark Archive (Remastered) | `PZO12012-HC` / `PZO12012E` |
| Guns & Gears (Remastered) | `PZO12010-HC` / `PZO12010E` |
| Impossible Magic | `PZO12014-HC` / `PZO12014E` |
| Player Core | `PZO12001-HC` / `PZO12001E` |
| Player Core 2 | `PZO12004-HC` / `PZO12004E` |
| Rage of Elements | `PZO2113-HC` / `PZO2113E` |
| War of Immortals | `PZO12006-HC` / `PZO12006E` |

PR6B3C, PR6B3D, and all upstream source-evidence files remain byte-identical.
The new bundle retains only bounded identifiers, hashes, dates, URLs, page
locators, enumerated facts, and blocker codes. It retains no PDF bytes, book
text, rule prose, errata correction text, legal-notice prose, product prose,
or art; every artifact observation has `retained: false`.

## Measured observations and blockers

Player Core alone records PDF product `PZO12001E` with observed artifact
SHA-256 `494c61daebe6d721137263b934065040d28121c57850eed3d19e97a7a3c613c9`.
Its observed ORC notice is located at printed page 463 / PDF page 464.
Neither observation identifies its exact printing/revision or approves
redistribution.

Player Core's official [Paizo FAQ and errata page](https://paizo.com/pathfinder/faq)
inventory is `complete_as_of_capture` at `2026-10-11T16:59:43Z`. Input and
final URLs were both `https://paizo.com/pathfinder/faq`, with HTTP `200` and
content type `text/html; charset=utf-8`. SHA-256
`2937aaea1df9eb41a6aa91fbd0eca5595f788a8483ecf70dd1863b4504005427`
identifies the decoded response-entity bytes written by curl with
`--compressed`, excluding HTTP headers and transfer/content encodings.
Four sections (Fall 2023, Fall 2024, Spring 2025, Spring 2026) supplied
23, 71, 4, and 46 raw entries respectively: 144 entries reduced to 137
distinct normalized release/printing/page/type tuples. The complete tuple
inventory is retained in the dossier and pinned by
[the current-artifact test](../../tests/pf2e_rules/test_current_class_legal_readiness.py).
Only explicit leading `(Clarification)` metadata determines that entry type;
no correction text is retained. Every applicability decision remains
`unresolved` because the artifact printing is unknown. This inventory is
complete only for the captured response, not a claim about later errata.

Player Core 2, Guns & Gears (Remastered), Rage of Elements, War of Immortals,
Battlecry!, Dark Archive (Remastered), and Impossible Magic all have
`missing` final artifacts and legal notices. Their notice family/locator and
artifact product/hash fields are null. Their errata inventories are
`unverified`, with null capture fields and empty locator lists. All eight
printings and all eight revisions remain `unverified` with null designations;
all eight errata applicability states remain `unresolved`.

Rage of Elements remains license-neutral. Its missing final legal page
supports neither an OGL nor an ORC conclusion, and the aggregate package
must not be classified as OGL-only or ORC-only.

The exact manifest counts are:

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

All eight downstream compliance packages are `missing`. Their outstanding
review codes are `attribution-review`, `branding-review`,
`distribution-review`, `notice-review`, `reserved-material-review`, and
`trademark-review`.

Player Core's readiness blockers are `downstream-compliance-missing`,
`errata-applicability-unresolved`, `named-reviewer-missing`,
`printing-unverified`, and `revision-unverified`. Each of the other seven
sources has those same blockers plus `errata-inventory-unverified`,
`final-artifact-missing`, and `final-legal-notice-missing`.

## Reproducible offline verification and report

From the repository root in PowerShell, using the repository virtual
environment, verify the exact trusted readiness bundle and complete chain:

```powershell
venv/Scripts/python.exe tools/pf2e_rules.py class-legal-readiness-verify `
  systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/manifest.json `
  --overlay systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1 `
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json `
  --corpus compendium_data `
  --parent systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1 `
  --source-evidence systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json `
  --expected-hash 4eeaef808fae0aa8b7ccc1356fcb7381b4d672a8d2281427f01a7ae9cc7cb410
```

Report the verified counts and per-source blocker codes with the same inputs:

```powershell
venv/Scripts/python.exe tools/pf2e_rules.py class-legal-readiness-report `
  systems/pf2e/rules/evidence/paizo/pf2e-class-legal-readiness-2026-10-11.1/manifest.json `
  --overlay systems/pf2e/rules/reviews/pf2e-class-source-review-2026-10-10.1 `
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json `
  --corpus compendium_data `
  --parent systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1 `
  --source-evidence systems/pf2e/rules/evidence/paizo/pf2e-class-sources-2026-10-10.1/manifest.json `
  --expected-hash 4eeaef808fae0aa8b7ccc1356fcb7381b4d672a8d2281427f01a7ae9cc7cb410
```

Both commands require every chain path, verify the full frozen chain before
reporting, and emit one sorted ASCII-safe JSON line after strict bounded
readback. Verification emits `path` (the absolute input manifest path),
`bundle_id`, `bundle_hash`, `scope`, `class_review_overlay_id`,
`class_review_overlay_hash`, `source_bundle_id`, `source_bundle_hash`, and
`counts`. Reporting emits `bundle_id`, `bundle_hash`, `counts`, and `sources`,
sorted by exact source ID with sorted unique blocker lists. Success exits
`0`; domain/I/O failure exits `2`, with empty stdout and compact error/path
JSON on stderr.

Verification/reporting are deterministic, offline, Flask-independent, and
`DATA_DIR`-independent. They neither mutate inputs nor grant an approval.
There is no readiness compiler, publication, approval, or activation command.
The application neither imports the readiness module nor reads the bundle.

## Source hierarchy and PR6B3E2 handoff

Official final publications and their legal pages, exact printing/revision
evidence, and applicable official Paizo errata come first. Product metadata,
release announcements, organized-play policy, previews, supplements, and
playtest PDFs cannot substitute for final-book legal evidence. Archives of
Nethys is used later for rules reconciliation as a transcription cross-check,
never as final-book legal evidence or redistribution approval. Its frozen
snapshot is a chain input here, not a way to clear the missing legal evidence.

PR6B3E2 cannot begin approval work until the seven missing final artifacts
and legal pages, evidence identifying all eight exact printings/revisions,
applicable official errata, and a named license reviewer are available. The
reviewer must be explicitly identified; none is supplied by this inventory.
The downstream package must address attribution, notices, reserved material,
trademarks, branding, and distribution treatment, including any mixed OGL/ORC
obligations established by the final evidence.

An approved PR6B3E2 design must define a successor evidence schema and, if
approvals are supported, a schema-v3 class-review overlay bound to exact
PR6B3D and PR6B3E2 hashes. Only supported license-review objects may change.
If all eight sources are later approved, 16 approved gates and 29 pending
gates would be the future target, with zero rejected gates and zero enabled
mechanics; those are not PR6B3E1 results.

The next mechanics slice remains gated by PR6B3E2 legal approval. Subsequent
source-backed level 1-20 mechanics review, remediation of the six source-drift
records, separate Necromancer/Runesmith implementation, and the other 18,493
pending identities remain separate work. Legal approval alone would not
activate mechanics. Any later GM/player UI change still requires an approved
mockup before implementation.
