# Player release: foundation baseline

Read-only audit recorded 2026-11-08 against `origin/main` at
`7aeb9549c1df2807935a4a44229284e13a5add1d`. The audit inspected source and existing
tests; it did not run those tests, inspect private character payloads, or verify
production backend settings. Line anchors refer to that baseline and may move.

## Observed implementation

| Boundary | Evidence | Consequence for this release |
|---|---|---|
| SQL identity | [models](../../core/persistence/models.py) cover users, campaigns, character identities/assignments, invites, drafts/receipts, audits and migration runs. | These models are reusable; authoritative gameplay payloads and a gameplay event outbox are still missing. |
| System constraints | `Campaign`/`Character` model constraints and [CharacterRepository.upsert_identity](../../core/persistence/repositories.py#L170) permit PF2e/Cosmere only. | D&D needs explicit shared-schema/repository support before ownership/draft integration. |
| Character locator | [character_files.locator](../../core/persistence/character_files.py#L15) accepts direct files in `party_data` or `cosmere_pcs`. | D&D's `dnd5e/characters/<id>.json` needs a deliberate locator adapter; do not relax validation for arbitrary nested paths. |
| File/SQL writes | [character_files.write_document](../../core/persistence/character_files.py#L80) documents that file write plus SQL identity registration is not a distributed transaction. | Existing SQL ownership does not make purchases, multi-target combat, or history atomic. |
| Request context | [CampaignContext](../../core/request_context.py#L207), `resolve_campaign_context` and `resolve_character_context` distinguish addressed and live campaigns. | Reuse this scope; opening a non-live character must not change the global live slot. |
| Access policy | [capabilities_for](../../core/character_workflows/capabilities.py#L7) refuses non-live contexts and non-PF2e/Cosmere systems. [RoutePolicy](../../core/route_policy.py) includes live-character gates. | Detach character reads/writes before lifting live-only policy. Keep encounter mutations tied to an active session. |
| D&D storage | [store.load_pc/save_pc/mutate_pc](../../systems/dnd5e/store.py#L59) and `load_encounter/mutate_encounter` already accept a campaign ID. | Reusable file-backed adapter. Per-file locks do not provide a transaction spanning several characters, an encounter and history. |
| D&D revisions | [service](../../systems/dnd5e/service.py#L296) maintains `build_rev` separately from the document revision. | Preserve this distinction so ordinary damage does not invalidate an open builder. |
| D&D mutation seams | `vitals_action`, `resource_action`, `inventory_action`, and `combat_action` in [service.py](../../systems/dnd5e/service.py). | Call or adapt these services; avoid duplicating their mechanics in the player UI or plugin. |
| PF2e live state | [project architecture](../../CLAUDE.md) and existing persistence tests document `PARTY_LIBRARY`, encounter globals, dirty-state flushing and name-based compatibility. | PF2e needs detached state/derivation and stable IDs before anytime access is safe. |
| PF2e loot | `_deposit_loot_to_pc`, `send_loot_to_player`, `api_loot_ledger_award` in [app.py](../../app.py#L24281). | Existing awards and duplicate-award checks are useful behavior references. Character writes and ledger writes are separate; items stack by name. |
| D&D inventory | [inventory_action](../../systems/dnd5e/service.py#L431) persists UIDs, quantity, equipped/attuned state and notes. | Retain item identity while adding containers, accepted transfers, shops, reservations and crafting. |

The accepted [persistence ADR](../adr/0002-transactional-persistence.md) selects
SQLAlchemy/PostgreSQL for mutable transactional state, SQLite for fast local tests,
and PostgreSQL concurrency tests. [PR4B's implementation record](../remediation/pr4b-runtime-cutover.md)
explicitly leaves gameplay state in files, keeps SQL opt-in and requires one worker.
Do not treat the ADR's future game-state backend or outbox as already implemented.

## Contracts needed before implementation

PE-01 must make these choices concrete enough for independent implementers:

1. Campaign/character/item/effect/operation identities and validated system/edition
   context; no identity inference from display names.
2. Separate build/play/inventory/encounter revisions and conflict/merge behavior.
   Define how a published build reconciles current HP/resources without erasing
   intervening valid gameplay changes.
3. The transaction unit for affected character and encounter state, command receipt
   and events; define pending-action stages without holding a transaction open while
   waiting for a human.
4. Authority selection and compatibility behavior for file versus transactional
   state, including dry-run, reconciliation, current-state export and rollback.
5. Non-live character permissions versus live encounter permissions, owner-private
   data, fresh authorization at commit and campaign-switch isolation.
6. Economy state machines: GM-approved purchases, GM-advanced crafting downtime,
   accepted transfers, and GM/quartermaster-finalized loot reservations. A
   quartermaster receives only the named loot capability.

Contracts must account for the unmerged Obsidian work found during the companion
audit. Review its preflight/replay/deduplication implementation before assigning new
integration work; do not assume the older main-branch plugin is the only baseline.

## Bounded implementation sequence

The initial concrete briefs are [PE-01](../handoff/tasks/PE-01-state-command-contract.md),
[PE-10](../handoff/tasks/PE-10-dnd5e-identity.md),
PE-11 (split on 2026-11-11 into [PE-11a](../handoff/tasks/PE-11a-dnd5e-locator.md),
[PE-11b](../handoff/tasks/PE-11b-dnd5e-archive-restore.md) and
[PE-11c](../handoff/tasks/PE-11c-dnd5e-runtime-export.md)), and
[PE-12](../handoff/tasks/PE-12-game-state-schema.md). The shared queue controls when
each is claimable. Further slices should remain separate reviewable changes:

- State repositories with compare-and-swap revisions.
- Command receipts and gameplay-event/outbox primitives.
- D&D state adapter behind the agreed authority selector.
- PF2e detached reads/derivation, then one mutation family per task.
- Migration/reconciliation dry-run and current-state reverse export.
- Anytime access policy only for routes already detached from live caches.
- Stable inventory instances, containers/equipment, accepted transfers, shop/request
  storage, purchase fulfillment, crafting projects, downtime progression, loot
  reservations and authorized finalization as separate domain slices.

Each implementation targets roughly 500 changed lines or fewer. Split a task if
its real dependency surface exceeds that budget; do not omit a safety invariant to
force it into one diff. Claude implements high-risk state/auth/rules work; Codex
reviews and can implement bounded UI ports or test backfill from accepted contracts.

## Existing verification anchors

These commands are proposed regression anchors, not executed results:

```text
python -m pytest -q tests/persistence/test_database_and_models.py tests/persistence/test_transactional_services.py tests/persistence/test_sql_character_http.py tests/persistence/test_export_runtime.py
python -m pytest -q tests/character_workflows/test_capabilities.py tests/character_workflows/test_identity.py tests/character_workflows/test_migration.py tests/character_workflows/test_recovery.py tests/character_workflows/test_publication.py
python -m pytest -q tests/test_access_policy_unit.py tests/test_route_policy_inventory.py tests/test_campaign_switch_isolation.py
python -m pytest -q tests/test_pc_save_reload_race.py tests/test_pc_state_persistence.py tests/test_pc_snapshots.py tests/test_pb_ground_truth.py
python -m pytest -q tests/test_dnd5e_service.py tests/test_dnd5e_combat.py tests/test_dnd5e_character_engine.py tests/test_dnd5e_effects.py
python -m pytest -q tests/test_inventory_bulk.py tests/test_loot_award.py
python -m pytest tests/persistence -q -m postgresql --strict-markers
```

Use only an isolated test database for PostgreSQL tests. D&D service tests explicitly
skip when `content.available()` is false; a successful command consisting of skips
does not establish D&D behavior. Template changes additionally require
`python tools/check_templates.py`.

## Three-profile coverage evidence

| Profile | Existing committed evidence | What still needs verification |
|---|---|---|
| PF2e | `tests/fixtures/gavin_l11.json`, `amadeus_l11.json`, `goel_l10.json`, `kyle_l10.json`; snapshot and Pathbuilder ground-truth suites. | Explicit supported rules/content versions, source-backed option/action coverage, and complete cross-client journeys. |
| D&D 2014 | `tests/fixtures/dnd5e_ddb_rogue_2014.json`; edition-aware engine/service tests. | Installed private content version and non-skipped execution for the agreed catalog and journeys. |
| D&D 2024 | `tests/fixtures/dnd5e_ddb_paladin_2024.json`; edition-aware engine/service tests. | Same; this import fixture alone does not prove all classes, spells or actions. |

Private D&D content remains outside Git. The presence of `tests/pf2e_rules/` schema,
package, evidence and derivation tests does not prove the full PF2e kernel is the
runtime authority. Reconcile existing PR6/PR7 work and freeze an explicit coverage
matrix before claiming the player release is complete.
