# PR6B3C: immutable class-review overlay

## Outcome

PR6B3C records a stable rule ID and source group for every class identity in
the frozen Archives of Nethys evidence snapshot. It also reconciles those 29
identities with the repository's 27 local class records. The result is a
metadata-only review artifact: it changes no character-building rule, class
progression, spellcasting model, database row, route, template, or runtime
registry.

The checked-in artifact is:

```text
systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/
  authoring.json
  manifest.json
  records.json
  sources.json
```

It contains exactly those four canonical JSON files. Its immutable binding is:

| Field | Value |
| --- | --- |
| Overlay ID | `pf2e-class-identities-2026-10-07.1` |
| Overlay hash | `273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614` |
| Snapshot ID | `pf2e-aon-2026-10-07-player-build-v1` |
| Snapshot manifest SHA-256 | `c61453ba1b1f03680ebda3a7a1ead875e3d838b5fc91d443f441c0401804d4de` |
| Activation | `none` |
| Enabled mechanics | 0 |

Archives of Nethys remains a transcription cross-check. Applicable Paizo
publications, printings, and errata remain the mechanical authority under
[ADR-0003](../adr/0003-versioned-pf2e-rules-kernel.md).

## What `mapped` means

All 29 class identities now have an effective disposition of `mapped` in a
detached in-memory view. Here, `mapped` means only that an observed AoN
identity has an explicit stable rule ID, an explicit source ID, and a recorded
local reconciliation result. It does not mean that:

- the class is implemented locally;
- its progression, features, feats, spells, or descriptions are correct;
- the cited source or license has been approved;
- its data is current to a particular Paizo printing or errata release; or
- any mechanic is available to the builder or level-up workflow.

The frozen base ledger is unchanged: all 18,522 included identities retain
their original pending reviews. The 29 class identities are overlaid only in
the effective metadata view, leaving the other 18,493 included identities
pending. Those 18,493 records are not rejected or ignored; they are the
non-class identities that later review overlays still need to address.

PR6B3C also adds 45 separate pending gates: eight source reviews, eight
license reviews, and 29 class-rules reviews. None is implicitly approved by a
successful compile or verification.

## Exact coverage

| Source group | Class identities |
| --- | ---: |
| Player Core | 8 |
| Player Core 2 | 8 |
| Guns & Gears (Remastered) | 2 |
| Rage of Elements | 1 |
| War of Immortals | 2 |
| Battlecry! | 2 |
| Dark Archive (Remastered) | 2 |
| Impossible Magic | 4 |

The local reconciliation result is:

| Result | Count | Meaning |
| --- | ---: | --- |
| `aligned` | 21 | Local class name and normalized publication title agree with the frozen evidence metadata. |
| `source-drift` | 6 | A local class exists, but its publication title differs from the assigned current source group. |
| `missing-local` | 2 | The frozen identity is mapped, but no local class file or runtime entry exists. |

The six source-drift records are Gunslinger, Inventor, Magus, Psychic,
Summoner, and Thaumaturge. Necromancer and Runesmith are the two missing-local
records. Their stable IDs do not add them to `CLASS_MATRIX` or
`CLASS_PROGRESSION`; both runtime tables remain at the same exact 27 classes.

## Reproducible verification

From the repository root, verify the checked-in artifact and its trusted hash
entirely offline:

```text
python tools/pf2e_rules.py class-review-verify \
  systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1 \
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json \
  --corpus compendium_data \
  --expected-hash 273d3180178eeea249db01ddcb778e13675c98ff74717d51dbc8efedc73a2614
```

To reproduce the bytes, compile the checked-in `authoring.json` into a new,
empty store rather than the immutable checked-in directory:

```text
python tools/pf2e_rules.py class-review-compile \
  systems/pf2e/rules/reviews/pf2e-class-identities-2026-10-07.1/authoring.json \
  --snapshot systems/pf2e/rules/evidence/archives-of-nethys/2026-10-07-player-build-v1/snapshot-manifest.json \
  --corpus compendium_data \
  --store <empty-review-store>
```

The current-artifact integration test recompiles through this public CLI and
requires all four generated files to be byte-identical. It also installs a
network-denying audit hook, proves that neither Flask nor `app` is imported,
and verifies that an explicit `DATA_DIR` remains untouched.

## Safety boundaries

- Compilation and verification are local and offline.
- The overlay directory is create-only; compilation cannot overwrite it.
- The snapshot and local class files remain inputs and are never modified.
- Present local classes bind their exact path, Foundry ID, and normalized
  repository-text SHA-256. Missing local classes bind no invented locator.
- Every source, license, and rules review is pending.
- The artifact contains evidence metadata, not copied rule prose or mechanics.
- `activation: none` and `enabled_mechanics: 0` are required by tests and the
  verifier.

## Next review batches

Later PRs must review source identity and redistribution rights, then review
source-backed class mechanics and levels 1 through 20 before any runtime
cutover. The six source-drift classes need explicit local-data remediation,
and Necromancer and Runesmith require separate implementation work rather than
being inferred from this overlay. The other 18,493 included identities remain
available for independently reviewable ancestry, heritage, background, feat,
archetype, skill, spell, weapon, equipment, and related overlays. Any
player-facing or GM-facing workflow change remains subject to mockup approval
before implementation.
