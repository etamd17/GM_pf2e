"""Publish saved snapshots through a durable, recoverable file/metadata intent."""

from contextlib import AbstractContextManager
from dataclasses import replace
from typing import Protocol
from uuid import uuid4

from .capabilities import capabilities_for
from .drafts import digest, key_hash, owned_draft, require_active, require_revision, timestamp
from .identity import resolve_character, resolve_legacy_name
from .recovery import (complete_publication, invalidate_pending_index, publication_target,
                       recover_publication, repair_required, restore_active)
from .types import CharacterRecord, JSON, WorkflowError


class CharacterFileAdapter(Protocol):
    def lock(self, record: CharacterRecord | None) -> AbstractContextManager: ...
    def read(self, record: CharacterRecord) -> JSON | None: ...
    def prepare(self, record: CharacterRecord, document: JSON) -> JSON: ...
    def write(self, record: CharacterRecord, document: JSON) -> None: ...
    def refresh(self, record: CharacterRecord) -> None: ...
    def notify(self, record: CharacterRecord) -> None: ...


class WorkflowService:
    def __init__(self, drafts, store, adapters, files):
        self.drafts, self.store, self.adapters, self.files = drafts, store, adapters, files

    def publish(self, context, draft_id, *, expected_revision, request_key, force=False):
        from .types import WorkflowReceipt
        if type(expected_revision) is not int or expected_revision < 0 or type(force) is not bool:
            raise WorkflowError('invalid_publication', 'Invalid publication revision or override.', 422)
        hashed = key_hash(request_key)
        request_digest = digest({'draft_id': draft_id, 'revision': expected_revision, 'force': force})
        with self.files.lock(None):
            # Resolve/authorize once to identify the dirty-state flush target,
            # then release SQL before flushing (the legacy flush owns a txn).
            with self.store.transaction(context) as tx:
                fresh = tx.fresh_context()
                draft = owned_draft(tx, fresh, draft_id)
                prior = tx.get_receipt(fresh.principal.user_id, 'publish', hashed)
                if prior and prior.request_digest != request_digest:
                    raise WorkflowError('request_key_conflict', 'This publish key belongs to another request.', 409)
                pending = next((r for r in tx.list_receipts() if r.operation == 'publish'
                                and r.draft_id == draft.id and r.state == 'publishing'), None)
                # Do not reject the old revision of an identical successful
                # retry. Recovery reauthorizes even already-committed receipts.
                replay = prior if prior and prior.state == 'committed' else pending
                target = None
                if replay is None and draft.target_id:
                    target = resolve_character(fresh.campaign_id, draft.target_id, session=tx.session)
                    if not capabilities_for(fresh, target).edit:
                        raise WorkflowError('character_not_found', 'Character not found.', 404)
            if replay:
                result = recover_publication(context, replay.id, store=self.store, files=self.files)
                if result is not None:
                    if replay.key_hash != hashed:
                        # Recovery is itself an idempotent publish request. Its
                        # acknowledgment may also be lost after completion.
                        from .recovery import _authorize_receipt
                        with self.store.transaction(context) as tx:
                            fresh = tx.fresh_context()
                            completed = next(r for r in tx.list_receipts() if r.id == replay.id)
                            _authorize_receipt(tx, fresh, completed)
                            tx.put_receipt(replace(completed, id=prior.id if prior else uuid4().hex,
                                key_hash=hashed, request_digest=request_digest))
                    return result
                raise WorkflowError('publication_retry_ready', 'Publication was not written. Reload the draft and retry.', 409)
            # Lock/flush happens outside the store transaction; retain the
            # outer live-dispatch boundary across all phases.
            with self.files.lock(target):
                if target is not None:
                    self.files.read(target)
            with self.store.transaction(context) as tx:
                fresh = tx.fresh_context()
                draft = owned_draft(tx, fresh, draft_id)
                require_revision(draft, expected_revision)
                require_active(draft)
                if force and not capabilities_for(fresh, None).override:
                    raise WorkflowError('override_forbidden', 'GM permission is required to override rules.', 403)
                target = None
                current = None
                registry_before = None
                if draft.target_id:
                    target = resolve_character(fresh.campaign_id, draft.target_id, session=tx.session)
                    if not capabilities_for(fresh, target).edit:
                        raise WorkflowError('character_not_found', 'Character not found.', 404)
                    current = self.files.read(target)
                    if current is None:
                        raise WorkflowError('character_not_found', 'Character not found.', 404)
                    if self.adapters[fresh.system].fingerprint(draft.inputs.kind, current) != draft.base_fingerprint:
                        raise WorkflowError('character_build_conflict', 'This character build changed. Reload before publishing.', 409)
                    if tx.session is not None:
                        from core.persistence.models import Character
                        registry_before = tx.session.get(Character, target.character_id).content_checksum
                document = self.adapters[fresh.system].normalize(draft.inputs, current, override=force)
                name = document.get('build', document).get('name')
                if fresh.system == 'pf2e':
                    existing = resolve_legacy_name(fresh.campaign_id, name, session=tx.session)
                    if existing is not None and (target is None or existing.character_id != target.character_id):
                        raise WorkflowError('character_name_conflict', 'A character with this name already exists.', 409)
                receipts = tx.list_receipts()
                name_hash = digest(name)
                if any(r.operation == 'publish' and r.state == 'publishing'
                       and (r.target_id == draft.target_id or
                            (fresh.system == 'pf2e' and r.metadata.get('name_hash') == name_hash))
                       for r in receipts):
                    raise repair_required()
                reservation = next((r.target_id for r in receipts if r.operation == 'publish'
                                    and r.draft_id == draft.id), None)
                created = target is None
                owner = (None if fresh.is_gm or fresh.principal.is_admin else fresh.principal.user_id) if created else target.owner_id
                character_id = (reservation or uuid4().hex) if created else target.character_id
                record = target or CharacterRecord(fresh.campaign_id, character_id, fresh.system,
                    'party_data' if fresh.system == 'pf2e' else 'cosmere_pcs',
                    character_id + '.json', {}, owner, frozenset(), frozenset())
                if created and self.files.read(record) is not None:
                    raise repair_required()
                document.update(id=character_id, campaign_id=fresh.campaign_id,
                    system=fresh.system, schema_version=1, owner_user_id=owner,
                    editor_user_ids=sorted(record.editor_ids), viewer_user_ids=sorted(record.viewer_ids))
                document = self.files.prepare(record, document)
                receipt = WorkflowReceipt(uuid4().hex, fresh.campaign_id, fresh.principal.user_id,
                    'publish', hashed, request_digest, draft.id, character_id, 'publishing', {
                        'operation_id': uuid4().hex, 'created': created, 'system': fresh.system,
                        'storage': record.storage, 'filename': record.filename, 'owner_id': owner,
                        'editor_ids': sorted(record.editor_ids), 'viewer_ids': sorted(record.viewer_ids),
                        'before_digest': digest(current) if current is not None else None,
                        'expected_digest': digest(document), 'registry_before_checksum': registry_before,
                        'base_fingerprint': draft.base_fingerprint,
                        'expected_fingerprint': self.adapters[fresh.system].fingerprint(draft.inputs.kind, document),
                        'intent_revision': draft.revision + 1, 'name_hash': name_hash, 'force': force,
                        'before_name_hash': digest((current.get('build') or current).get('name')) if current else None,
                    })
                # A failed attempt with the same request key retains identity;
                # update its receipt, not the unique request-key tuple.
                previous = tx.get_receipt(fresh.principal.user_id, 'publish', hashed)
                if previous:
                    if previous.request_digest != request_digest:
                        raise WorkflowError('request_key_conflict', 'This publish key belongs to another request.', 409)
                    receipt = replace(receipt, id=previous.id)
                tx.put_draft(replace(draft, state='publishing', revision=draft.revision + 1,
                                     updated_at=timestamp()))
                tx.put_receipt(receipt)
            invalidate_pending_index(fresh.campaign_id)
            try:
                with self.files.lock(record), publication_target(record.campaign_id, record.character_id):
                    before_write = self.files.read(record)
                    if (digest(before_write) if before_write is not None else None) != receipt.metadata['before_digest']:
                        raise repair_required()
                    self.files.write(record, document)
            except Exception:
                # A proven non-write can become editable; otherwise preserve
                # intent. Never overwrite a divergent file with the old copy.
                try:
                    with publication_target(record.campaign_id, record.character_id):
                        actual = self.files.read(record)
                    if (digest(actual) if actual is not None else None) == receipt.metadata['before_digest']:
                        restore_active(context, receipt.id, store=self.store)
                        raise WorkflowError('publication_write_failed', 'Character was not written. Reload the draft and retry.', 503)
                except WorkflowError as exc:
                    if exc.code == 'publication_write_failed':
                        raise
                except Exception:
                    pass
                raise repair_required() from None
            try:
                return complete_publication(context, receipt.id, store=self.store, files=self.files)
            except Exception:
                invalidate_pending_index(fresh.campaign_id)
                raise repair_required() from None
