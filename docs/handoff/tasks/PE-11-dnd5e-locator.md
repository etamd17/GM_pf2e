# PE-11: Bind D&D file locations to shared ownership and transfer tools

<!-- ============ BRIEF (written before work starts) ============ -->

- **Owner:** claude
- **Reviewer:** codex
- **Status:** blocked on PE-10 acceptance and merge
- **Branch:** `claude/pe-11-dnd5e-locator` from current main after PE-10 is accepted and merged. Record the exact starting SHA.
- **Depends on:** [PE-10](PE-10-dnd5e-identity.md), using the accepted PE-01 contract
- **Context:** [PE-01 contract](../../player-release/state-command-contract.md) section 1 (D&D locator) and section 7 (character archive, runtime export), [foundation baseline](../../player-release/foundation-baseline.md), [PR4B transfer/rollback boundaries](../../remediation/pr4b-runtime-cutover.md), `core/persistence/character_files.py::locator/authoritative_document`, `core/persistence/ownership.py::_validate_locator/_resolve_identity`.
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
   to `legacy_storage = "dnd5e_characters"` and `legacy_file = "<pid>.json"`, with
   `legacy_file == id + ".json"`.
   Retain path containment, identifier, symlink and traversal checks. Reject lookalike
   nested directories, encounter files, saved plans, staging files and uploads.
2. Extend `_validate_locator` and scoped identity resolution to `dnd5e_characters` (the value PE-10 admits).
   Enforce document system/campaign/ID consistency and SQL-authoritative grants.
3. Ensure supported forward import recognizes D&D identity metadata. Campaign archive
   restore assigns fresh campaign/character identities, writes each restored D&D file
   as `<new id>.json` so every restored row satisfies section 1, remaps D&D references
   and grants only the importing GM; runtime reverse export preserves current identities
   and current authoritative ownership. These are intentionally different operations.
4. Add `tests/persistence/test_dnd5e_locator.py` and bounded fixture-based migration/
   archive/export tests. Use synthetic fixtures without private rules content.
5. Route/runtime integration belongs to PE-15 (and PE-41 for inventory routes); record
   it there. No D&D route may opt into this adapter until the complete
   identity/read/write/transfer path is accepted.

## Acceptance

- [ ] Exact valid D&D paths resolve consistently; traversal, symlinks, malformed IDs, wrong system/campaign and unsupported directories fail closed.
- [ ] Unknown or mismatched SQL identities never fall back to file-supplied ownership in SQL mode.
- [ ] Restore remaps character references and grants safely, and every restored D&D row has `legacy_file == id + '.json'` at the exact path; runtime reverse export retains current assignments and IDs. Forward/import verification reports unresolved identities instead of guessing.
- [ ] PF2e/Cosmere locator, pending-publication, campaign-archive and export behavior remains intact.
- [ ] `python -m pytest -q tests/persistence/test_dnd5e_locator.py tests/persistence/test_sql_character_http.py tests/persistence/test_campaign_sql_import.py tests/persistence/test_migrate_transactional_store.py tests/persistence/test_export_runtime.py` passes.
- [ ] `python -m pytest -q tests/character_workflows/test_archive_privacy.py tests/character_workflows/test_recovery.py tests/character_workflows/test_migration.py` passes.
- [ ] `git diff --check` passes; report includes actual test counts and any data/setup-dependent skips.

## Out of scope / do not touch

- D&D route/store cutover, arbitrary nested storage support, game-state migration,
  draft UI, new sharing capabilities, production data or deployment settings.
- Do not weaken publication-repair barriers or claim file-plus-SQL atomicity.
- Character archive and anything that reads `archived_at` (PE-41; the column and index
  are PE-12's): the archive command, archive-aware registry loading and file skipping
  in `identity._resolve`, and the archived-character filter in
  `tools/export_transactional_runtime.py`. PE-11 extends `_source_character`,
  `_write_legacy_tree`, `_verify_authority` and `build_plan` to D&D storage only, so
  PE-41's filter can wrap them. Open point for the PE-41 brief and Codex: contract
  section 7 says the D&D archived-row export case is added "when PE-10/11 extend that
  tool", but PE-11 lands before `archived_at` exists, so that case most likely
  belongs with PE-41's filter.

<!-- ============ REPORT (written by the implementer) ============ -->

## Report
- **Changed:** Pending.
- **Tests run:** Pending.
- **Skipped / not verified:** Pending.
- **Known wrong / limitations:** Pending.
- **Follow-ups (not done here):** Pending.

## Review
<!-- Codex appends numbered findings or acceptance. -->
