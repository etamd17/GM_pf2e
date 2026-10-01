"""Durable publication reconciliation and cheap campaign-scoped quarantine."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from pathlib import Path
import logging
import threading

from core import storage
from core.persistence import runtime
from core.request_context import CampaignContext, Principal
from .capabilities import capabilities_for
from .drafts import digest, owned_draft, timestamp
from .types import CharacterRecord, PublishResult, WorkflowError

_INDEX = {}
_INDEX_LOCK = threading.RLock()
_INTERNAL = ContextVar('character_publication_target', default=None)
_LOG = logging.getLogger(__name__)


def repair_required():
    return WorkflowError('publication_repair_required',
        'Publication needs recovery before this character can change. Retry or contact the GM.', 503)


def _cache_key(cid):
    return (runtime.backend(), str(Path(storage.DATA_DIR).absolute()),
            runtime.database().url if runtime.sql_enabled() else None, cid)


def invalidate_pending_index(cid):
    with _INDEX_LOCK:
        for key in list(_INDEX):
            if key[-1] == cid:
                _INDEX.pop(key, None)


def default_store():
    if runtime.sql_enabled():
        from core.persistence.workflow_store import SqlWorkflowStore
        return SqlWorkflowStore(runtime.database())
    from .json_store import JsonWorkflowStore
    return JsonWorkflowStore(Path(storage.DATA_DIR) / 'character_drafts')


def pending_receipts(cid):
    if not cid:
        return ()
    key = _cache_key(cid)
    # Never hold the index lock while acquiring the campaign lock: commit
    # invalidation takes them in the opposite direction.
    with _INDEX_LOCK:
        if key in _INDEX:
            return _INDEX[key]
    context = CampaignContext(Principal.anonymous(), cid, cid, False, None, None)
    if runtime.sql_enabled():
        # This read is also reached by legacy writers already holding their
        # campaign row lock. Do not open a second locking transaction here.
        from sqlalchemy import select
        from core.persistence.models import CharacterWorkflowReceipt
        from core.persistence.workflow_store import SqlWorkflowTransaction
        with runtime.database().session() as session:
            pending = tuple(SqlWorkflowTransaction._receipt(row) for row in session.scalars(
                select(CharacterWorkflowReceipt).where(
                    CharacterWorkflowReceipt.campaign_id == cid,
                    CharacterWorkflowReceipt.operation == 'publish',
                    CharacterWorkflowReceipt.state == 'publishing')))
        with _INDEX_LOCK:
            _INDEX[key] = pending
    else:
        with default_store().transaction(context) as tx:
            pending = tuple(r for r in tx.list_receipts()
                            if r.operation == 'publish' and r.state == 'publishing')
            with _INDEX_LOCK:
                _INDEX[key] = pending
    return pending


@contextmanager
def publication_target(cid, character_id):
    """Internal one-target bypass, never supplied by request input."""
    token = _INTERNAL.set((cid, character_id))
    try:
        yield
    finally:
        _INTERNAL.reset(token)


def pending_for(cid, *, character_id=None, store=None, filename=None):
    for receipt in pending_receipts(cid):
        if ((character_id and receipt.target_id == character_id)
                or (store == receipt.metadata.get('storage')
                    and filename == receipt.metadata.get('filename'))):
            if _INTERNAL.get() != (cid, receipt.target_id):
                return receipt
    return None


def assert_character_available(campaign_id, character_id, *, write):
    if pending_for(campaign_id, character_id=character_id):
        raise repair_required()


def assert_no_pending_workflows(campaign_id):
    if pending_receipts(campaign_id):
        raise repair_required()


def record_from_receipt(receipt):
    from core.persistence.ownership import _validate_locator
    meta = receipt.metadata
    try:
        _validate_locator(meta['storage'], meta['filename'])
        if meta['system'] not in ('pf2e', 'cosmere') or not receipt.target_id:
            raise ValueError('invalid target')
        return CharacterRecord(receipt.campaign_id, receipt.target_id, meta['system'],
            meta['storage'], meta['filename'], {}, meta.get('owner_id'),
            frozenset(meta.get('editor_ids', [])), frozenset(meta.get('viewer_ids', [])))
    except (KeyError, TypeError, ValueError):
        raise repair_required() from None


def _current_receipt(tx, receipt_id):
    receipt = next((r for r in tx.list_receipts() if r.id == receipt_id), None)
    if receipt is None:
        raise WorkflowError('publication_not_found', 'Publication not found.', 404)
    return receipt


def _authorize_receipt(tx, fresh, receipt):
    draft = owned_draft(tx, fresh, receipt.draft_id)
    if receipt.author_id != fresh.principal.user_id:
        raise WorkflowError('publication_not_found', 'Publication not found.', 404)
    if receipt.metadata.get('force') and not capabilities_for(fresh, None).override:
        raise WorkflowError('override_forbidden', 'GM permission is required to override rules.', 403)
    record = record_from_receipt(receipt)
    if not receipt.metadata['created'] or receipt.state == 'committed':
        from .identity import resolve_character
        with publication_target(receipt.campaign_id, receipt.target_id):
            record = resolve_character(receipt.campaign_id, receipt.target_id, session=tx.session)
        if not capabilities_for(fresh, record).edit:
            raise WorkflowError('character_not_found', 'Character not found.', 404)
    return draft, record


def _post_commit(files, record):
    # Notification failures cannot undo durable publication. A reload/reconnect
    # can recover the UI; exactly-once/durable event delivery belongs to PR8.
    try:
        files.refresh(record)
        files.notify(record)
    except Exception:
        _LOG.exception('Post-publication refresh failed for character %s', record.character_id)


def complete_publication(context, receipt_id, *, store, files):
    """Complete only a proven full-document publication, inside one SQL txn."""
    with store.transaction(context) as tx:
        fresh = tx.fresh_context()
        receipt = _current_receipt(tx, receipt_id)
        draft, record = _authorize_receipt(tx, fresh, receipt)
        if receipt.state == 'committed':
            return PublishResult(**receipt.metadata['result'])
        if (receipt.state != 'publishing' or draft.state != 'publishing'
                or draft.revision != receipt.metadata['intent_revision']):
            raise repair_required()
        with publication_target(record.campaign_id, record.character_id):
            document = files.read(record)
        if document is None or digest(document) != receipt.metadata['expected_digest']:
            raise repair_required()
        if tx.session is not None:
            from core.persistence.models import AuditEvent, Character
            from core.persistence.ownership import register_character_in_session
            registered = tx.session.get(Character, record.character_id)
            if registered is not None:
                if (registered.campaign_id != record.campaign_id
                        or registered.legacy_storage != record.storage
                        or registered.legacy_file != record.filename
                        or registered.content_checksum not in (
                            receipt.metadata.get('registry_before_checksum'),
                            receipt.metadata['expected_digest'])):
                    raise repair_required()
            result_document = register_character_in_session(tx.session, record.campaign_id,
                record.storage, record.filename, document, owner_user_id=record.owner_id)
            if digest(result_document) != receipt.metadata['expected_digest']:
                raise repair_required()
            tx.session.add(AuditEvent(actor_user_id=fresh.principal.user_id,
                campaign_id=record.campaign_id, action='character.workflow_published',
                target_type='character', target_id=record.character_id,
                details={'receipt_id': receipt.id, 'draft_id': draft.id}))
        result = PublishResult(record.character_id, record.system, receipt.metadata['created'],
                               draft.revision + 1)
        tx.put_draft(replace(draft, inputs=None, state='committed', revision=result.revision,
                             updated_at=timestamp(), result=asdict(result)))
        tx.put_receipt(replace(receipt, state='committed',
                               metadata={**receipt.metadata, 'result': asdict(result)}))
    invalidate_pending_index(context.campaign_id)
    _post_commit(files, replace(record, document=document))
    return result


def restore_active(context, receipt_id, *, store):
    with store.transaction(context) as tx:
        fresh = tx.fresh_context()
        receipt = _current_receipt(tx, receipt_id)
        draft, _ = _authorize_receipt(tx, fresh, receipt)
        if receipt.state != 'publishing' or draft.state != 'publishing':
            raise repair_required()
        tx.put_draft(replace(draft, state='active', revision=draft.revision + 1,
                             updated_at=timestamp()))
        tx.put_receipt(replace(receipt, state='failed'))
    invalidate_pending_index(context.campaign_id)


def recover_publication(context, receipt_id, *, store, files):
    with files.lock(None):
        with store.transaction(context) as tx:
            fresh = tx.fresh_context()
            receipt = _current_receipt(tx, receipt_id)
            _draft, record = _authorize_receipt(tx, fresh, receipt)
            if receipt.state == 'committed':
                invalidate_pending_index(context.campaign_id)
                return PublishResult(**receipt.metadata['result'])
            if receipt.state != 'publishing':
                return None
        with files.lock(record), publication_target(record.campaign_id, record.character_id):
            document = files.read(record)
        actual = digest(document) if document is not None else None
        if actual == receipt.metadata['expected_digest']:
            return complete_publication(context, receipt_id, store=store, files=files)
        if actual == receipt.metadata['before_digest']:
            restore_active(context, receipt_id, store=store)
            return None
        raise repair_required()
