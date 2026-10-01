# PR5 Character Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let account players create/import, resume private drafts, and safely update their own characters in PF2e and Cosmere without GM-only endpoints.

**Architecture:** Add a shared character-workflow service with JSON and SQL storage adapters around existing normalization and persistence seams. Keep authority separate from user payloads, and retain durable workflow receipts across draft deletion for deduplication and recovery. Integrate the existing builders and roster pages without replacing the rules engine or live runtime.

**Tech Stack:** Python 3.11+, Flask/Jinja, SQLAlchemy/Alembic, PostgreSQL and SQLite tests, durable JSON files, vanilla JavaScript, pytest, and development-only Playwright.

**Spec:** `docs/superpowers/specs/2026-10-01-pr5-character-workflows-design.md` (approved).

## Global Constraints

- Preserve the current JSON default, opt-in SQL support, game calculations, existing GM workflows, and live-campaign restrictions.
- Shadow remains JSON-authoritative; do not introduce dual writes.
- Bound serialized inputs to 2 MiB per draft and allow at most 20 active/publishing drafts per author per campaign.
- No automatic expiry in this release. Explicit discard is confirmed in the UI.
- Personal drafts require real membership even for a site admin.
- Character payloads still live in files. This is not a distributed transaction.
- Production configuration changes or enabling SQL by default are excluded.
- PF2e level-up drafts, new sharing/permission-management screens, whole-site redesign, and combat rekeying are excluded.
- Work on `remediation/pr5-character-workflows` in the existing cloud checkout; do not create another worktree.
- `CLAUDE.md` overrides generic commit steps: no commits, push, PR creation, or merge without a user request. Leave reviewed checkpoints uncommitted.
- Keep one gevent worker. Preserve `_pc_spell_lock` before `ENCOUNTER_LOCK`, flush-before-read ordering, shared SSE usage, UTF-8 file reads, and Windows filesystem compatibility.
- New UI uses existing Cinzel/Inter tokens, no emojis, existing utility classes or scoped CSS, and explicit `[hidden]` handling when author styles set `display`.
- Deployment/staging access remains pending. Local checks are not evidence of a Railway deployment or production-data cutover.

## Review Focus

1. A lost create response followed by membership removal/rejoin must not resurrect the old draft or publish twice (Tasks 3, 5).
2. Choosing feats/talents without DOM input events, then refreshing, must preserve those choices; Cosmere initialization must not overwrite a restored draft (Task 8).
3. A GM reimporting an ID-file character by its existing name must update that file, not create a second name-file character (Tasks 2, 7).
4. A SQL viewer opening a PF2e sheet must not become the session's acting character or receive owner-private notes (Tasks 1, 7).
5. An HP tick during drafting must survive publication, while a newer build edit must stop stale publication (Tasks 2, 4).

## File map and execution order

| Files | Responsibility |
|---|---|
| `core/character_workflows/types.py`, `capabilities.py`, `identity.py` | Typed workflow contracts, pure permissions, exact campaign-scoped ID resolution |
| `core/character_workflows/systems.py` plus narrow helpers in `app.py` | Existing system parser/serializer adapters and build fingerprints |
| `core/character_workflows/store.py`, `json_store.py`, `drafts.py` | Storage contract, private JSON lifecycle, draft service |
| `core/persistence/workflow_store.py`, `models.py`, new migration | SQL lifecycle/receipts and session-bound integration |
| `core/character_workflows/publication.py`, `recovery.py` | Stable publish intents, file/SQL recovery, visibility/write guards |
| `core/character_workflows/lifecycle.py` | Membership/target invalidation and purge integration |
| `services/character_workflows.py` | Thin Flask blueprint and transport/error translation |
| `static/js/character_drafts.js`, existing builder/roster templates | Shared draft controller and small system-specific UI adapters |
| `tools/migrate_transactional_store.py`, `tools/export_transactional_runtime.py` | Resumable private drafts and historical receipts in migration/rollback |
| `tests/character_workflows/`, `tests/browser/` | Shared adapter contract, HTTP/security, JS, and real browser journeys |

Tasks 1–6 establish the service and recovery boundaries before new account-mode
routes are exposed in Task 7. Task 8 wires the existing UI; Tasks 9–10 prove the
journeys and record release gates. These are coupled layers of one workflow, not
independent product subsystems. Finish each task's red/green cycle before moving
to a dependent task. Parallel read-only reviews and independent validation are safe;
concurrent edits to `app.py` or persistence contracts are not.

Commands below assume the repository virtual environment is active. Expected
results are future acceptance conditions, not claims that tests have already run.

## Shared interface decisions

Define the following in `types.py`; use existing `CampaignContext`, `Principal`,
and SQLAlchemy `Session` rather than alternate identity models:

```python
JSON = dict[str, Any]
DraftKind = Literal['pf2e_builder', 'pf2e_import', 'cosmere_builder', 'cosmere_import']
DraftState = Literal['active', 'publishing', 'committed', 'discarded']
# Frozen dataclasses; no mutable default containers.
CharacterRecord(campaign_id: str, character_id: str, system: str,
                storage: str, filename: str, document: JSON,
                owner_id: str | None, editor_ids: frozenset[str], viewer_ids: frozenset[str])
Capabilities(create: bool, view: bool, edit: bool, delete: bool,
             manage_ownership: bool, override: bool, owner_private: bool)
DraftInput(kind: DraftKind, payload_version: int, form: JSON, ui: JSON, submission: JSON)
DraftSnapshot(id: str, campaign_id: str, author_id: str, system: str,
              inputs: DraftInput | None, state: DraftState, revision: int,
              target_id: str | None, base_fingerprint: str | None,
              created_at: str, updated_at: str, result: JSON | None)
WorkflowReceipt(id: str, campaign_id: str, author_id: str, operation: str,
                key_hash: str, request_digest: str, draft_id: str | None,
                target_id: str | None, state: str, metadata: JSON)
PublishResult(character_id: str, system: str, created: bool, revision: int)
WorkflowError(code: str, message: str, status: int, issues: tuple[str, ...] = ())
```

`payload_version=1`. `form` preserves editable fields/choice state, `ui` stores
wizard position and DOM-only selections, and `submission` is the existing system
submission shape collected from that same form snapshot. Only the saved snapshot
is published; Finish cannot inject a different build. These inputs remain untrusted
and pass the existing validation plus structural bounds. IDs/targets/base fingerprints
are service metadata, never restored from client-controlled form fields.

`store.py` defines `WorkflowStore.transaction(context: CampaignContext) ->
ContextManager[WorkflowTransaction]`. The transaction interface exposes:

```python
session: Session | None  # SQL session owned by this transaction, otherwise None
fresh_context() -> CampaignContext
get_draft(draft_id: str) -> DraftSnapshot | None
list_drafts(author_id: str) -> list[DraftSnapshot]
put_draft(draft: DraftSnapshot) -> None
delete_draft(draft_id: str) -> None
get_receipt(author_id: str, operation: str, key_hash: str) -> WorkflowReceipt | None
list_receipts() -> list[WorkflowReceipt]
put_receipt(receipt: WorkflowReceipt) -> None
```

All methods are campaign-scoped. Transactions acquire the campaign lock first,
refresh user existence and membership authority, and own commit/rollback. SQL uses
`Database.transaction()`; JSON reuses the campaign-store lock and a private durable
transaction journal for multi-record lifecycle changes. Repositories do not silently
authorize a cached request context. Filesystem publication uses a separate durable
intent as described in Task 4, not an assertion of cross-store atomicity.

### Task 1: Character IDs and explicit capabilities

**Files:** Create `core/character_workflows/__init__.py`, `core/character_workflows/types.py`, `core/character_workflows/capabilities.py`, `core/character_workflows/identity.py`; modify `core/request_context.py`; create `tests/character_workflows/test_capabilities.py`, `tests/character_workflows/test_identity.py`, `tests/character_workflows/conftest.py`.

**Interfaces:**
- `capabilities_for(context: CampaignContext, character: CharacterRecord | None) -> Capabilities`.
- `resolve_character(campaign_id: str, character_id: str, *, session: Session | None = None) -> CharacterRecord`.
- `resolve_legacy_name(campaign_id: str, name: str, *, session: Session | None = None) -> CharacterRecord | None` (None only for no match; ambiguity is a conflict).
- Extend `CharacterContext`/`resolve_character_context` with `viewer_user_ids=()` without changing existing mutation policies.

- [x] **1. Write the failing permission/locator tests.** Use parameterized JSON/SQL fixtures and the approved capability matrix; name tests `test_viewer_is_read_only`, `test_cosmere_does_not_gain_blanket_member_access`, `test_json_does_not_trust_editor_fields`, `test_ambiguous_id_or_name_fails_closed`, and `test_cross_campaign_and_symlink_locators_are_rejected`.

```python
caps = capabilities_for(viewer_context, pf2e_record)
assert caps.view and not caps.edit and not caps.delete and not caps.owner_private
assert not capabilities_for(admin_without_membership, None).create
assert capabilities_for(cosmere_owner_context, cosmere_record).delete
assert not capabilities_for(pf2e_owner_context, pf2e_record).delete
```

- [x] **2. Run the new tests red.** `python -m pytest -q tests/character_workflows/test_capabilities.py tests/character_workflows/test_identity.py`; expect missing new interfaces or unmet assertions, not fixture/setup errors.
- [x] **3. Implement these interfaces.** Resolve exact IDs/locators using SQL authority or validated JSON envelopes; reject wrong-system, duplicate, traversal, and symlink targets. Never use imported JSON editor/viewer grants. Preserve legacy context behavior. Only add typed contracts that subsequent tasks actually use.
- [x] **4. Run green and regression checks.** Run the same tests plus `tests/test_access_policy_unit.py` and `tests/test_route_policy_inventory.py`; expect all selected tests passing. Inspect the diff; leave this checkpoint uncommitted.

### Task 2: Extract existing normalization and preserve target state

**Files:** Create `core/character_workflows/systems.py`, `tests/character_workflows/test_system_adapters.py`; modify `app.py` at `save_new_character`, `import_pathbuilder`, `cosmere_builder`, `cosmere_import_pdf`. Reuse unchanged `pf2e_pdf_import.py` and `systems/cosmere/pdf_import.py`.

**Interfaces:**
- Extract `_build_new_pf2e_document(data: dict) -> dict`, `_merge_pf2e_import(existing: dict, imported: dict) -> dict`, and `_build_cosmere_document(data: dict, existing: dict | None) -> dict` in `app.py`; these perform no target selection, authority changes, or file I/O.
- `SystemAdapter.normalize(inputs: DraftInput, current: JSON | None, *, override: bool) -> JSON`.
- `SystemAdapter.parse_import(content: bytes, filename: str) -> DraftInput`.
- `SystemAdapter.fingerprint(kind: DraftKind, document: JSON) -> str`.
- `build_system_adapters(*, pf2e_build: Callable, pf2e_validate: Callable, pf2e_merge: Callable, pf2e_pdf: Callable, cosmere_build: Callable, cosmere_pdf: Callable) -> dict[str, SystemAdapter]`; callbacks bind the current campaign's catalogs/homebrew at use time.

- [x] **1. Write characterization tests before extraction.** Name tests `test_extracted_builder_matches_legacy_document`, `test_import_preserves_runtime_fields`, `test_cosmere_edit_preserves_wallet_and_play_state`, `test_fingerprint_ignores_hp_but_detects_build_changes`, and `test_import_discards_authority_envelope`.

```python
assert adapter.fingerprint(kind, hp_changed) == adapter.fingerprint(kind, original)
assert adapter.fingerprint(kind, feat_changed) != adapter.fingerprint(kind, original)
assert merged['build']['current_hp'] == current['build']['current_hp']
assert normalized.get('owner_user_id') is None
assert 'editor_user_ids' not in normalized and 'campaign_id' not in normalized
```

- [x] **2. Run new adapter-contract tests red.** `python -m pytest -q tests/character_workflows/test_system_adapters.py`; keep existing behavior characterization green before extracting bodies.
- [x] **3. Extract and adapt.** Move the existing PF2e payload-to-build block and exact `PB_IMPORT_KEYS`/`PRESERVE_KEYS` merge behavior without rule changes. Preserve Cosmere wallet/play-state/house-metal behavior. New inputs require a valid object, text name, supported system/kind, and current required fields; malformed shapes must not become 500s. Invalid explicit targets never become creation. Fingerprints cover overwritten fields, not preserved runtime fields. Service callers, not the adapter, authorize override and target access.
- [x] **4. Run green/parity.** Run new tests plus `tests/test_builder_validate.py`, `tests/test_pf2e_envelope.py`, `tests/test_cosmere_builder.py`, `tests/test_cosmere_builder_caps.py`, `tests/test_pc_snapshots.py`, and `tests/test_pb_ground_truth.py`. Do not regenerate expected snapshots to conceal a calculation change. Review extraction diff; no commit.

### Task 3: Private draft lifecycle and independent receipt storage

**Files:** Create `core/character_workflows/store.py`, `core/character_workflows/json_store.py`, `core/character_workflows/drafts.py`, `core/persistence/workflow_store.py`, `migrations/versions/20261001_0002_character_workflow_receipts.py`, `tests/character_workflows/test_drafts.py`, `tests/character_workflows/test_workflow_store.py`; modify `core/persistence/models.py`, `core/persistence/__init__.py`, `core/persistence/runtime.py`, `tests/persistence/test_database_and_models.py`.

**Interfaces:** Implement the shared store contract; `JsonWorkflowStore(root: Path)` and `SqlWorkflowStore(database: Database)` implement it.
- `DraftService(store: WorkflowStore, adapters: dict[str, SystemAdapter])`.
- `create(context, inputs: DraftInput, *, request_key: str, target_id: str | None = None) -> DraftSnapshot`.
- `prepare_import(context, content: bytes, filename: str, *, request_key: str, target_id: str | None = None) -> DraftSnapshot` (parse through the selected system adapter, then use the same create/deduplication path).
- `get(context, draft_id: str) -> DraftSnapshot`; `list(context) -> list[DraftSnapshot]`.
- `save(context, draft_id: str, inputs: DraftInput, *, expected_revision: int) -> DraftSnapshot`.
- `discard(context, draft_id: str, *, expected_revision: int) -> None`.
- `copy(context, draft_id: str, inputs: DraftInput, *, request_key: str) -> DraftSnapshot` (copies trusted target/base fingerprint; never refreshes a stale base).

- [x] **1. Add failing adapter contract tests.** Parameterize both stores. Name tests `test_author_private_draft_round_trip`, `test_revision_compare_and_swap`, `test_input_version_system_size_and_quota`, `test_create_retry_uses_original_digest`, and `test_discard_prevents_late_save`. Cover real membership, strict non-bool nonnegative revisions, UTF-8 byte limits, quota under contention, tombstones, no expiry, and payload restoration.

```python
assert service.create(ctx, inputs, request_key=key).id == first.id
assert saved.revision == first.revision + 1
with pytest.raises(WorkflowError, match='revision'):
    service.save(ctx, first.id, inputs, expected_revision=first.revision)
assert failure_for_other_author.status == 404
assert oversize_error.status == 413 and twenty_first_active_error.status == 429
assert service.copy(ctx, first.id, inputs, request_key=copy_key).base_fingerprint == first.base_fingerprint
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_drafts.py tests/character_workflows/test_workflow_store.py`; failure must identify missing behavior.
- [x] **3. Implement schema and repositories.** Add mapped model `CharacterWorkflowReceipt`, table `character_workflow_receipts`: string IDs, author/campaign/draft/target IDs, operation, key hash, request digest, state, `details` JSON, timestamps; unique `(campaign_id, author_id, operation, key_hash)`. Map DTO `metadata` to ORM `details` to avoid SQLAlchemy's reserved `metadata` attribute. No cascading foreign keys to membership/user/character/draft. Allowed operations are `create_draft`, `publish`, `discard`, `invalidate_member`, `invalidate_target`; validate receipt state by operation. Keep inputs in `Draft.payload`; map trusted metadata in a versioned envelope. SQL drafts retain their existing FKs.
- [x] **4. Implement lifecycle behavior.** JSON uses the spec's private root, a separate `_receipts` namespace, and an internal durable journal. Validate real paths; never infer access from directory ownership. Store SHA-256 request-key hashes and original request digests so later draft edits do not break an identical create retry. Start draft revision at 0; increment successful state-changing operations. Count active/publishing states under the campaign lock. Require `payload_version=1`, 2,097,152 input bytes maximum, and 20 active/publishing records maximum. Error responses never reveal another author's existence.
- [x] **5. Run green and schema checks.** Run new tests plus database/runtime tests; exercise explicit Alembic upgrade from `20260930_0001` to `20261001_0002`, preserving old rows. Update readiness's expected revision; no startup DDL. Refuse destructive downgrade while workflow records exist. JSON default must never open SQL. Leave the checkpoint uncommitted.

### Task 4: Authorized publication and crash recovery

**Files:** Create `core/character_workflows/publication.py`, `core/character_workflows/recovery.py`, `tests/character_workflows/test_publication.py`, `tests/character_workflows/test_recovery.py`; modify `core/persistence/ownership.py`, `core/persistence/character_files.py`, and narrow `app.py` write/load integration seams.

**Interfaces:**
- `WorkflowService(drafts: DraftService, store: WorkflowStore, adapters: dict[str, SystemAdapter], files: CharacterFileAdapter)`.
- `publish(context, draft_id: str, *, expected_revision: int, request_key: str, force: bool = False) -> PublishResult`.
- `CharacterFileAdapter.lock(record: CharacterRecord | None) -> ContextManager[None]`, `read(record) -> JSON`, `write(record, document: JSON) -> None`, `refresh(record) -> None`, `notify(record) -> None`; the app adapter flushes dirty PF2e state before reads and binds existing atomic writers without nested SQL registration.
- `recover_publication(context, receipt_id: str, *, store: WorkflowStore, files: CharacterFileAdapter) -> PublishResult | None`.
- `assert_character_available(campaign_id: str, character_id: str, *, write: bool) -> None` and `assert_no_pending_workflows(campaign_id: str) -> None`.
- Extract `ownership.register_character_in_session(session, cid, legacy_storage, filename, doc, *, owner_user_id=None) -> dict`; existing `register_character` delegates and keeps its public contract.

- [x] **1. Add failing publication/fault tests.** Name tests `test_publish_owns_new_character_and_retries_once`, `test_publish_rechecks_target_and_force_authority`, `test_stale_build_rejected_but_live_hp_preserved`, `test_name_conflict_and_system_specific_rename`, `test_pending_create_is_not_visible`, and `test_commit_uncertainty_preserves_receipt`. Include GM-unclaimed creation, forged envelopes, and restart recovery.

```python
assert first.character_id == retried.character_id
assert len(created_character_records) == 1
assert result_document['owner_user_id'] == ctx.principal.user_id
assert stored_target['build']['current_hp'] == latest_hp
assert stale_target_error.status == 409
assert pending_character_id not in visible_character_ids
assert uncertain_commit_error.code == 'publication_repair_required'
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_publication.py tests/character_workflows/test_recovery.py`.
- [x] **3. Implement the publication state machine.** Under the existing live dispatch boundary, use campaign, draft, target lock order and preserve existing per-PC/encounter ordering. Check an identical completed receipt before rejecting its now-old draft revision, but reauthorize even on a replay. Recheck target fingerprint, allocate the final ID/locator once per draft, and durably commit `publishing` intent/receipt before file publication. Retries, including a new request key after a known failure, reuse that reservation. Complete SQL draft/receipt/identity/assignment/audit in one transaction with the session-bound registration primitive. JSON uses durable intent plus atomic file/record writes. Never commit a success before the corresponding file write succeeds.
- [x] **4. Implement recovery and integrate all readers/writers of PR5 targets.** Store operation identity, exact expected file digest, previous/expected build fingerprints, and trusted locator in receipt metadata. On uncertainty, compare current file, registry, and receipt before completing; never overwrite divergent data or mint another ID. Known pre-publication failures return to active; ambiguous cases remain publishing with 503. Gate `_load_character_document`, loaders/cache reload, single/batch writes, and pending dirty flushes for affected targets. Unrelated old files keep their existing behavior. Refresh cache and emit SSE only after completion. Preload/index pending receipts per campaign and update that index on receipt changes rather than scanning every file on each combat tick.
- [x] **5. Run green and regression tests.** Include `tests/test_character_batch_atomicity.py`, `tests/test_pc_save_reload_race.py`, `tests/persistence/test_ownership_runtime.py`, and `test_sql_character_http.py`. Inject failure before/after file replacement, before/after SQL commit, and during completion receipt persistence; also restart in a new process. No duplicate or unauthorized result may appear. No commit.

### Task 5: Invalidation that survives cascades and rejoining

**Files:** Create `core/character_workflows/lifecycle.py`, `tests/character_workflows/test_invalidation.py`; modify `core/campaigns.py`, `core/persistence/campaign_runtime.py`, `core/persistence/services.py`, `core/persistence/ownership.py`, and `app.py::_delete_character_document`.

**Interfaces:**
- `invalidate_member(context, user_id: str, *, transaction: WorkflowTransaction) -> None`.
- `invalidate_target(context, character_id: str, *, transaction: WorkflowTransaction) -> None`.
- `purge_private_workflows(campaign_id: str, *, store: WorkflowStore) -> None` (only for already-authorized permanent purge; does not enable SQL purge).

- [x] **1. Write failing cascade/rejoin tests.** `test_receipts_survive_membership_and_character_deletion`, `test_delayed_create_retry_after_rejoin_stays_revoked`, `test_pending_publication_blocks_destructive_removal`, `test_trash_restore_preserves_author_scope`, and `test_json_expired_trash_purge_cleans_private_root`.

```python
assert draft_after_removal is None and receipt_after_removal.state == 'invalidated'
assert rejoined_old_create_response.status == 404
assert pending_removal_error.code == 'publication_repair_required'
assert existing_membership_remains_when_removal_blocked
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_invalidation.py`.
- [x] **3. Add atomic/recoverable hooks.** Cover `campaigns.remove_member`, SQL `_change_membership`, and the independent `TransactionalStore.remove_membership` path. Reconcile pending publications before cascades; invalidate every receipt capable of recreating/accessing the removed author's drafts, erase editable payloads, and retain historical evidence without requiring deleted FK rows. Hook both explicit JSON `purge_campaign` and `purge_expired_trash`. Do not treat `/api/leave_campaign`'s session-selection clearing as account-membership removal. Keep last-GM protection and unchanged SQL purge refusal.
- [x] **4. Run green/regression.** Run the new tests and existing campaign/service persistence tests. Simulate interruption between invalidation and JSON membership write and prove restart/rejoin cannot revive drafts. No commit.

### Task 6: Draft-aware migration and private backup retention

**Files:** Modify `tools/migrate_transactional_store.py`, `tools/export_transactional_runtime.py`, `core/backups.py` privacy assertions if needed, `DEPLOY.md`, `docs/remediation/pr4b-runtime-cutover.md`; create `tests/character_workflows/test_migration.py`, `tests/character_workflows/test_archive_privacy.py`.

**Interfaces:** Preserve public `build_plan`, `import_store`, `verify_store`, `export_store`, and `export_runtime` signatures. Add `character_workflow_receipts` to migration entities; represent private JSON drafts/receipts at the same paths consumed by Task 3. Pending-work checks use Task 4's `assert_no_pending_workflows`.

- [x] **1. Add failing round-trip/privacy tests.** Name tests `test_private_drafts_resume_after_json_sql_json_round_trip`, `test_historical_receipts_do_not_restore_grants`, `test_old_sources_remain_importable`, `test_pending_workflow_blocks_export`, and `test_portable_archives_exclude_private_drafts`. Include active/discarded/committed records, unsupported versions, and inconsistent live FK links.

```python
assert resumed.inputs == original.inputs and resumed.revision == original.revision
assert imported_historical_receipt.target_id == removed_target_id
assert removed_target_was_not_recreated and removed_membership_was_not_recreated
assert not any('character_drafts' in name for name in portable_campaign_zip.namelist())
assert pending_export_error.code == 'publication_repair_required'
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_migration.py tests/character_workflows/test_archive_privacy.py`.
- [x] **3. Extend discovery and transactional import/export.** Update `_snapshot_files`, `inspect_source`, `ENTITY_NAMES`, `_database_records`, `_target_has_rows`, forward/verify/reverse writers, and runtime exporter `_write_legacy_tree`/`_verify_authority`. Include draft/receipt counts and checksums, retain historical receipts without recreating grants, and preserve old-source compatibility. Reject non-quiesced/pending or changing sources; no silent omission. Use private permissions and existing Windows ACL guidance.
- [x] **4. Document backup boundaries.** Existing scheduled/campaign ZIP backups are not full-site backups and must continue excluding private drafts. Document required full-volume root plus SQL snapshot and add a filesystem-snapshot fixture proving the new root is included in operator-style full-volume copies; do not invent a new hosted backup service. Update PR4B's sidecar-only limitation accurately for PR5's draft-capable exporter, not for unrelated audit-history replay.
- [x] **5. Run green/regression.** Include `tests/persistence/test_migrate_transactional_store.py`, `test_export_runtime.py`, and `test_campaign_sql_import.py`. Verify no application grants/content are changed by plan/dry-run. No commit.

### Task 7: HTTP integration, canonical links, and legacy compatibility

**Files:** Create `services/character_workflows.py`, `tests/character_workflows/test_http.py`, `tests/character_workflows/test_legacy_compatibility.py`; modify `app.py`, `core/route_policy.py`, `core/access.py`, `core/request_context.py`, `templates/player_sheet.html`, `templates/_pc_sheet/_tab_inventory.html`, and affected private sheet partials identified by the read-only render test.

**Interfaces:** `create_blueprint(*, drafts: DraftService, workflows: WorkflowService, resolve_context: Callable[[], CampaignContext], render_sheet: Callable[[CharacterRecord, Capabilities], Response]) -> Blueprint`. Blueprint name `character_workflows`; use the following exact routes/endpoints:

| Method/path | Endpoint suffix | Request/response |
|---|---|---|
| GET `/api/character-workflows/drafts` | `draft_list` | Author's active drafts only |
| POST same path | `draft_create` | `inputs`, optional `target_id`, `request_key`; returns snapshot |
| GET `/api/character-workflows/drafts/<draft_id>` | `draft_get` | Snapshot without private internal receipts/paths |
| PATCH same path | `draft_save` | `inputs`, `expected_revision`; returns snapshot |
| POST `.../<draft_id>/discard` | `draft_discard` | `expected_revision`; returns success |
| POST `.../<draft_id>/copy` | `draft_copy` | `inputs`, `request_key`; returns copied snapshot |
| POST `.../<draft_id>/publish` | `draft_publish` | `expected_revision`, `request_key`, optional `force`; returns ID and URL |
| POST `/api/character-workflows/imports` | `import_prepare` | JSON object or multipart `file`, `request_key`, optional `target_id`; parses and creates a private import draft |
| GET `/characters/<character_id>` | `character_sheet` | Authorized canonical sheet rendering, selected live campaign scope |

Transport input uses snake_case fields matching the dataclasses; each `context`
parameter above is a `CampaignContext`. Initial create returns
201; successful reads/saves/publish return 200; existing receipt replays return 200.
Errors use `{error: {code, message, issues}}` and the spec's HTTP statuses. Preserve
existing response shapes on legacy endpoints. Require `force` to be a JSON boolean,
not a truthy string or object. Generated sheet URLs use `url_for`. Import preparation
calls `DraftService.prepare_import`; JSON-body imports pass encoded source JSON and
a synthetic `.json` filename, never an untrusted filesystem path.

- [x] **1. Add failing account-mode HTTP tests.** Name tests `test_player_completes_without_gm_endpoint`, `test_drafts_require_author_and_live_membership`, `test_viewer_does_not_select_actor_or_receive_private_notes`, `test_gm_reimport_resolves_id_filename`, and `test_legacy_open_builder_contract_unchanged`. Use isolated-process fixtures patterned on `test_campaign_containment.py` and `tests/persistence/test_runtime_http.py::run_sql`, CSRF enabled, both backends/systems, and separate role sessions.

```python
assert player_publish.status_code == 200 and player_publish.json['character_id']
assert player_gm_endpoint.status_code == 403
assert forged_update.status_code in (403, 404)
assert other_author_draft.status_code == 404 and stale_campaign.status_code == 409
assert owner_private_text not in viewer_sheet.get_data(as_text=True)
assert 'player_name' not in viewer_session
assert gm_reimport_file_count == original_file_count
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_http.py tests/character_workflows/test_legacy_compatibility.py`.
- [x] **3. Wire the service and route policies.** Resolve context through the existing request boundary, register every blueprint endpoint, require live member scope plus service-level real-account/author checks, and preserve CSRF. Add `ROUTE_CHARACTER_ID` locator and a distinct live-character-view policy for canonical PF2e viewer access; mutation policies must not inherit viewer grants. Do not bypass normal authorization by directly calling a route with a fabricated session. Add pending-work checks alongside existing live batch guards, with exemptions only for authorized recovery handlers.
- [x] **4. Preserve rendering and old workflows.** Split sheet render logic from `player_sheet` session mutation so read-only viewers neither select an acting character nor receive owner-private notes/controls. Keep Cosmere's current route boundary. Wire `_pc_sheet_url`, `_me_characters`, roster links, and safe legacy name resolution. Account-mode GM import/save must resolve ID-based files correctly; legacy no-account filenames and old GM API policies stay unchanged. New creates never implicitly merge by name; explicit invalid update IDs fail.
- [x] **5. Run green/regression.** Include `tests/test_route_policy_inventory.py`, `tests/test_campaign_containment.py`, `tests/persistence/test_sql_character_http.py`, `tests/test_proxy_csrf.py`, `tests/test_pr3_security_core.py`, `tests/test_security_hardening.py`, `tests/test_sse_security_and_revocation.py`, and `python tools/check_templates.py`. Check actual denied response bodies, not only status codes. No commit.

### Task 8: Existing builder and roster UI integration

**Files:** Create `static/js/character_drafts.js`, `templates/_character_draft_controls.html`, `tests/character_workflows/test_draft_controller.py`, `tests/character_workflows/test_builder_draft_contracts.py`; modify `templates/player_builder.html`, `templates/player_view.html`, `templates/cosmere_builder.html`, `templates/cosmere_pcs.html`, `templates/account_home.html`, and their GET adapters in `app.py`; add narrowly scoped styles in `static/css/system.css` where necessary.

**Interfaces:** `window.CharacterDrafts.create({apiBase, initialDraft, collect, restore, renderStatus, onPublished})` returns `{changed(), saveNow(), finish({force=false}), discard(), copy(), resume(draft), destroy()}`. `collect() -> DraftInput`, `restore(inputs) -> Promise<void>`. Controller states: `unsaved`, `saving`, `saved`, `conflict`, `error`, `forbidden`, `publishing`, `committed`. Debounce 750 ms; only one save in flight; coalesce to the latest pending snapshot. UUID request keys use `crypto.randomUUID()` with a secure-random fallback, never timestamps alone.

- [x] **1. Write failing JS behavior tests via Node subprocesses.** Name driver tests `test_serialized_autosave_and_finish`, `test_conflict_copy_preserves_base`, `test_pf2e_non_dom_choices_round_trip`, and `test_cosmere_restore_suppresses_initialization_saves`. Reuse the repository's `test_obsidian_plugin_behavior.py` harness style with controlled timers/fetch promises and fake DOM. Include lost responses, stale responses, and expiry of authority.

```javascript
assert.equal(maxConcurrentSaves, 1);
assert.equal(statusBeforeAcknowledgement, 'saving');
assert.equal(publishedRevision, lastAcknowledgedRevision);
assert.equal(savedInputs.ui.ancestryMethod, 'alternate');
assert.equal(restoredInputs.ui.step, savedInputs.ui.step);
assert.deepEqual(restoredTalents, savedTalents);
assert.equal(savesDuringRestore, 0);
```

- [x] **2. Run red.** `python -m pytest -q tests/character_workflows/test_draft_controller.py tests/character_workflows/test_builder_draft_contracts.py`; Node must actually execute the assertions.
- [x] **3. Implement controller and PF2e adapter.** Extract `collectPf2eSubmission()` from `finalizeCharacter`; support partial state without null-class exceptions. `collectPf2eDraft()` captures state plus `inp-deity`, `sel-anc-method`, and current step; `restorePf2eDraft()` restores controls before repaint. Call `changed()` from choice/state mutation hooks as well as input events. Account-mode Finish uses saved draft publication; legacy GM mode retains its existing flow. Show override controls only when server capability allows them.
- [x] **4. Implement Cosmere adapter and resume/import UI.** `collectCosmereDraft()` wraps `collect()` plus wizard/UI state; `restoreCosmereDraft()` restores independent fields, runs required picker initialization while suppressed, then reapplies TALENTS/INV/FABRIALS/INFECTED/IDEAL_WORDS and selected skills before final preview/review. Never finish restore with unconditional `goStep(0)`. Account-mode saves use drafts, retaining new versus existing ID intent. Resume through `/player/builder?draft_id=...` or `/cosmere/builder?draft_id=...`; GET authorizes the draft and derives target metadata from it, rejecting conflicting target query parameters. Add Import preview/confirm and explicit editable target selection; preserve existing GM multi-file option. Resume lists belong to the current author/campaign and GET alone creates nothing.
- [x] **5. Render ownership/status accurately.** Use capabilities for action links and owner/editor/read-only labels. Remove account-mode fake-claim stars; retain legacy bookmark behavior only as a clearly labeled bookmark. Status uses `role=status`/`aria-live=polite`; errors remain visible and actionable. Confirm discard, warn for unsaved navigation, preserve local inputs on failure, and use textContent/escaped rendering for imported names. No rules/UI redesign or new Tailwind class regeneration unless genuinely needed.
- [x] **6. Run green/template checks.** Run the two new tests, inline-handler escaping tests, and template parsing. Review account and legacy branches separately. No commit.

### Task 9: Real browser and PostgreSQL concurrency gates

**Files:** Create `tests/browser/conftest.py`, `test_character_workflows.py`, `requirements-browser.in`, generated `requirements-browser.txt`, `tests/persistence/test_workflow_postgresql.py`; modify `pytest.ini`, `.github/workflows/ci.yml`, `DEPLOY.md`.

**Interfaces:** Browser fixtures provide `server_url`, isolated data root, GM/player/editor/viewer credentials generated only for tests, and separate Playwright contexts. Server startup/restart uses the subprocess/readiness/cleanup patterns in `tools/smoke_production_runtime.py`. PostgreSQL fixtures reuse unique disposable schema patterns; never point destructive tests at staging/production schemas.

- [x] **1. Write failing real-journey tests.** Name tests `test_player_builder_and_import_journeys`, `test_refresh_and_restart_resume`, `test_two_tab_conflict_and_retry`, and `test_keyboard_status_and_capability_controls`. Parameterize both systems, JSON/SQL, and 390×844 / 1440×900 viewports. Import JSON/paste or a tiny synthetic AcroForm PDF made with existing `pypdf`. Record requests and assert no player publication calls `/api/save_new_character` or `/api/import_pathbuilder`. Assert no horizontal overflow, keyboard-accessible controls, restored focus after errors/dialogs, and acknowledged status announcements.
- [x] **2. Define and install the development-only browser test dependency during this task.** Resolve a current Python-3.11-compatible stable Playwright version, pin that exact version in `requirements-browser.in` alongside `-r requirements-dev.in`, and compile a universal Python-3.11 hash lock using the documented `uv pip compile` pattern. Record the chosen version. Use `/usr/bin/chromium` if compatible locally; CI installs the matching supported Chromium via Playwright. No production dependency/build step is added. Stop and report blocked network/browser installation rather than treating skips as success.
- [x] **3. Run browser tests red, then integrate the minimal harness and fix journey defects.** `python -m pytest -q tests/browser -m browser`; missing prerequisites are setup errors, not feature evidence. Keep only these focused browser tests in the new suite; do not expand into PR9's whole-site audit.
- [x] **4. Add/run PostgreSQL race tests.** Test simultaneous publication returns one ID, publication versus revocation, quota creation races, and stale draft saves with independent connections and deterministic barriers, not sleeps. `python -m pytest -q tests/persistence/test_workflow_postgresql.py -m postgresql --strict-markers`; expect every selected concurrency test to execute and pass.
- [x] **5. Add CI gates and run green.** Register `browser` marker; keep normal unit CI as `-m 'not postgresql and not browser'`, add an explicit browser job with a freshly migrated isolated database and mandatory prerequisites, and retain PostgreSQL tests. Import Playwright lazily in browser fixtures so normal test collection needs no browser dependency; explicit browser execution must fail clearly when prerequisites are absent, not silently skip. Verify lock regeneration parity. Save browser failure artifacts without credentials or real user data. No commit.

### Task 10: Integrated verification and release handoff

**Files:** Create `docs/remediation/pr5-character-workflows.md`; update only verification/release notes needed in `DEPLOY.md` and this plan's checkboxes.

**Interfaces:** No new product API. Consume all previous task contracts; report actual evidence and unresolved external gates.

- [x] **1. Audit requirements against code/tests.** Map every spec section and Review Focus case to an executed test. Confirm no broad permissions, new production dependencies, SQL-default change, hidden GM endpoint call, or mutation of real data. Inspect unresolved publication behavior through both legacy and new entry points.
- [x] **2. Run fresh integrated checks.** `python tools/check_templates.py`; `python -m pytest -q -m 'not postgresql and not browser' --strict-markers`; `python -m pytest -q tests/persistence -m postgresql --strict-markers`; `python -m pytest -q tests/browser -m browser`; and `python tools/smoke_production_runtime.py` in default JSON and explicitly migrated disposable SQL environments. Preserve passed/failed/skipped/xfail counts separately. Do not rerun the broad suite after documentation-only changes.
- [x] **3. Rehearse migration/rollback and inspect performance.** Use only synthetic/private disposable fixtures. Produce draft-aware plan/import/verify/current-runtime-export evidence, resume recovered drafts, and compare representative save/HP update latency to `/health` control with and without pending receipts. Report regressions; no arbitrary latency claim or performance expansion.
- [x] **4. Obtain a fresh whole-branch review and resolve material findings.** Use `superpowers:requesting-code-review` plus fresh verification before any completion claim. Per-task review depth follows the execution method selected by the user; security and data-recovery findings remain release blockers either way.
- [x] **5. Record exact deployment status.** Railway preview smoke and actual staging/database cutover rehearsal remain separate gates. Run them only with an authorized isolated target; if unavailable, name them pending and do not merge. Record current tests, branch/diff, migration instructions, JSON/SQL behavior, and the operator repair path. Keep all changes uncommitted until the user asks for commit/push/PR actions.

## Coverage and UI timing

Spec sections 1–6 map to Tasks 1, 2, and 7; draft lifecycle and publication (7–9)
map to Tasks 3–5; workflow UI (10) maps to Tasks 7–9; migration/privacy (11) maps
to Task 6; acceptance/delivery (12–13) map to Tasks 9–10. Each Review Focus item
has a named test/explicit assertion in its owning task.

Discuss PR5's workflow UI before executing Task 8: labels, placement of Save/Resume,
import target confirmation, and conflict presentation within existing styling.
Do not require a whole-site redesign to ship this journey. Broader navigation,
visual consistency, responsive migration, and site-wide accessibility are PR9;
specific blocking defects can be triaged earlier as separate bounded fixes with
their own scope. No additional UI requests have yet been supplied by the user.

## Execution handoff

This plan was approved for **Native** execution on 2026-10-01. No implementation tests
above ran as part of planning. The selected execution method is **Native**: the main
agent implements sequentially with focused read-only assistance and one fresh
whole-branch review, minimizing repeated context across tightly coupled transaction
and Flask seams. Progress and deviations are recorded in this plan's ignored
execution ledger; the code remains uncommitted until the user requests otherwise.
