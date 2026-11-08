# PE-11: Bind D&D file locations to shared ownership and transfer tools

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked on PE-10 acceptance and merge
- **Branch:** `claude/pe-11-dnd5e-locator` from the recorded Stage 0 package commit (or current main after this package integrates), after all named dependencies are accepted and integrated. Record the exact starting SHA.
- **Depends on:** [PE-10](PE-10-dnd5e-identity.md), using the accepted PE-01 contract
- **Context:** [foundation baseline](../../player-release/foundation-baseline.md), [PR4B transfer/rollback boundaries](../../remediation/pr4b-runtime-cutover.md), `core/persistence/character_files.py::locator/authoritative_document`, `core/persistence/ownership.py::_validate_locator/_resolve_identity`.
- **Change budget:** roughly 500 changed lines including tests. If forward import, archive remapping and runtime export cannot fit coherently, stop and split those into dependent briefs before implementation expansion.

## Goal

Recognize the exact D&D character directory through the shared identity adapter and
preserve its ownership across supported import/export paths. Do not guess owners
from character names or make existing D&D routes depend on an incomplete adapter.

## Approach

Allowed files: `core/persistence/character_files.py`, `core/persistence/ownership.py`,
`core/persistence/campaign_import.py`, `tools/migrate_transactional_store.py`,
`tools/export_transactional_runtime.py`, and focused persistence tests. The migration
tool's storage enumeration (baseline lines 226/1069) and `build_plan` are the forward
import anchors; `_source_character`, `_write_legacy_tree`, and `_verify_authority`
are the runtime export anchors. Preserve current recovery barriers.

1. Extend `locator` for exactly `campaigns/<cid>/dnd5e/characters/<pid>.json`, mapped
   to PE-01's canonical storage discriminator plus one validated JSON basename.
   Retain path containment, identifier, symlink and traversal checks. Reject lookalike
   nested directories, encounter files, saved plans, staging files and uploads.
2. Extend `_validate_locator` and scoped identity resolution to the PE-10 discriminator.
   Enforce document system/campaign/ID consistency and SQL-authoritative grants.
3. Ensure supported forward import recognizes D&D identity metadata. Campaign archive
   restore assigns fresh campaign/character identities, remaps D&D references and
   grants only the importing GM; runtime reverse export preserves current identities
   and current authoritative ownership. These are intentionally different operations.
4. Add `tests/persistence/test_dnd5e_locator.py` and bounded fixture-based migration/
   archive/export tests. Use synthetic fixtures without private rules content.
5. Record a route/runtime integration follow-up. No D&D route may opt into this
   adapter until the complete identity/read/write/transfer path is accepted.

## Acceptance

- [ ] Exact valid D&D paths resolve consistently; traversal, symlinks, malformed IDs, wrong system/campaign and unsupported directories fail closed.
- [ ] Unknown or mismatched SQL identities never fall back to file-supplied ownership in SQL mode.
- [ ] Restore remaps character references and grants safely; runtime reverse export retains current assignments and IDs. Forward/import verification reports unresolved identities instead of guessing.
- [ ] PF2e/Cosmere locator, pending-publication, archive and export behavior remains intact.
- [ ] `python -m pytest -q tests/persistence/test_dnd5e_locator.py tests/persistence/test_sql_character_http.py tests/persistence/test_campaign_sql_import.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py` passes.
- [ ] `python -m pytest -q tests/character_workflows/test_archive_privacy.py tests/character_workflows/test_recovery.py tests/character_workflows/test_migration.py` passes.
- [ ] `git diff --check` passes; report includes actual test counts and any data/setup-dependent skips.

## Out of scope / do not touch

- D&D route/store cutover, arbitrary nested storage support, game-state migration,
  draft UI, new sharing capabilities, production data or deployment settings.
- Do not weaken publication-repair barriers or claim file-plus-SQL atomicity.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
