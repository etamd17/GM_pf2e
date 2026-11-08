# PE-02: First-release rules and automation coverage census

- **Owner:** codex
- **Reviewer:** claude
- **Status:** ready; documentation/evidence work only
- **Branch:** `codex/pe-02-rules-coverage` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** none; reconcile the baseline before recording results.
- **Context:** [approved plan](../../player-release/implementation-plan.md), [decisions](../../player-release/decisions.md), [existing work](../../player-release/existing-player-work.md), [PF2e kernel ADR](../../adr/0003-versioned-pf2e-rules-kernel.md), [PR6 status](../../remediation/README.md), [D&D handoff](../../dnd5e/HANDOFF.md).

## Goal

Make the first release's supported PF2e, D&D 2014 and D&D 2024 behavior explicit
so implementation can reuse working rules and expose real gaps. This task does
not choose a smaller release or activate unreviewed content.

## Approach

Produce `docs/player-release/rules-coverage.md` and a compact companion
`rules-coverage.csv`. Record the inspected commit and content/package versions.
Use rows by behavior family, with exact IDs or a linked local enumeration when
coverage differs within a family; an aggregate count alone is not completeness.

Inspect PF2e derivation and transitions in `app.py`, `class_matrix.py`,
`systems/pf2e/rules/`, `templates/player_builder.html` and
`templates/player_levelup.html`. Inspect D&D's `rules/character.py`,
`rules/grants.py`, `rules/effects.py`, `rules/spells.py`, `combat.py`, `service.py`
and `routes.py`. Read existing tests and reports before claiming a missing feature.
Record inspected evidence; do not copy private rules prose, data bundles or PCs
into the report.

Cover creation/imports, prerequisites/choices, advancement/retraining, spells and
preparation, archetypes/multiclassing, derived stats, targeted actions/saves,
resources, condition/effect stacking and expiry, physical/digital dice,
equipment/containers/investment or attunement, crafting and economy rules.

Columns: `profile`, `family`, `covered_ids_or_scope`, `implementation`,
`automation` (automatic / explicit choice / GM ruling / unsupported),
`evidence`, `validation_status`, `source_or_package_status`, `gap`, `next_task`.
Keep legacy runtime capability separate from new-package activation/review
status. Identify browser-only authority and contradictory documentation.
Recommend bounded implementation slices; do not generate a single whole-kernel
rewrite task. Record unresolved rules/source questions without treating this
census as a legal approval or a user scope decision.

## Acceptance

- [ ] All three profiles have an evidenced matrix covering the families above.
- [ ] Existing functional coverage is preserved; gaps are named rather than silently reclassified to reduce first-release scope.
- [ ] PR6/PR7 dependencies and D&D edition distinctions are explicit. Zero enabled mechanics in the new PF2e package is not misreported as zero working app mechanics.
- [ ] Report separates inspected code, historical test reports and tests actually rerun. No test result is inferred from file names or old counts.
- [ ] No private data or copied rules bundles/prose enter tracked artifacts.
- [ ] `git diff --check` passes; diff contains only these coverage artifacts and this task's report (plus normal handoff queue/log changes).

Useful read-only census commands from the task's full checkout:

```powershell
git rev-parse HEAD
git ls-tree -r --name-only HEAD -- systems/pf2e/rules systems/dnd5e tests
git grep -n -E 'def (submit_levelup|cast_spell|update_build|level_up|inventory_action|combat_action)' -- app.py systems/dnd5e
git diff --check
```

## Out of scope / do not touch

- Product code, tests, rules activation, source approval, private publication,
  installed rules data, deployment settings and live characters.
- Re-running the entire application suite merely to produce a documentation
  census. Any focused probe uses synthetic data and records its actual outcome.

## Report

Not started. Record files changed, exact commands/results, unverified areas,
coverage ambiguities and bounded follow-up tasks here.

## Review

Claude records acceptance or numbered findings against this brief.
