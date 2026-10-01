"""Lifecycle hooks called only after the enclosing operation is authorized.

SQL hooks share the caller's locked transaction. JSON invalidation commits its
durable journal before the caller removes membership or a character file.
"""

from dataclasses import replace
from pathlib import Path
import shutil

from core import storage
from core.request_context import CampaignContext, Principal
from .recovery import repair_required
from .types import WorkflowError


def lifecycle_context(campaign_id):
    return CampaignContext(Principal.anonymous(), campaign_id, campaign_id, False, None, None)


def sql_transaction(session, campaign_id):
    from core.persistence.models import Campaign
    from core.persistence.workflow_store import SqlWorkflowTransaction
    return SqlWorkflowTransaction(session, lifecycle_context(campaign_id), session.get(Campaign, campaign_id))


def assert_no_pending(transaction):
    if any(r.operation == 'publish' and r.state == 'publishing' for r in transaction.list_receipts()):
        raise repair_required()


def _invalidate(transaction, *, author_id=None, character_id=None):
    receipts = transaction.list_receipts()
    drafts = transaction.list_drafts(None)
    draft_ids = {d.id for d in drafts if (author_id is not None and d.author_id == author_id)
                 or (character_id is not None and d.target_id == character_id)}
    draft_ids.update(r.draft_id for r in receipts if r.draft_id and (
        (author_id is not None and r.author_id == author_id)
        or (character_id is not None and r.target_id == character_id)))
    affected = [r for r in receipts if r.draft_id in draft_ids
                or (author_id is not None and r.author_id == author_id)
                or (character_id is not None and r.target_id == character_id)]
    if any(r.operation == 'publish' and r.state == 'publishing' for r in affected):
        # The authorized publish retry reconciles file/registry evidence. Never
        # erase its supporting records while that outcome remains unresolved.
        raise repair_required()
    for receipt in affected:
        if receipt.operation in ('create_draft', 'publish'):
            transaction.put_receipt(replace(receipt, state='invalidated'))
    for draft_id in draft_ids:
        transaction.delete_draft(draft_id)


def invalidate_member(context, user_id, *, transaction):
    if transaction.context.campaign_id != context.campaign_id:
        raise WorkflowError('workflow_scope_mismatch', 'Workflow campaign does not match.', 409)
    _invalidate(transaction, author_id=user_id)


def invalidate_target(context, character_id, *, transaction):
    if transaction.context.campaign_id != context.campaign_id:
        raise WorkflowError('workflow_scope_mismatch', 'Workflow campaign does not match.', 409)
    _invalidate(transaction, character_id=character_id)


def purge_private_workflows(campaign_id, *, store):
    """Purge only a verified trashed JSON campaign; SQL purge stays unsupported."""
    from core.campaigns import _CAMPAIGN_STORE_LOCK
    from .json_store import JsonWorkflowStore, _safe
    if not isinstance(store, JsonWorkflowStore):
        raise WorkflowError('campaign_operation_unavailable', 'SQL purge is unavailable.', 409)
    storage._check_id(campaign_id, 'campaign_id')
    with _CAMPAIGN_STORE_LOCK:
        if (Path(storage.campaign_dir(campaign_id)).exists()
                or not Path(storage.campaign_trash_dir(campaign_id)).is_dir()):
            return
        directory = _safe(store.root / campaign_id)
        if not directory.exists():
            return
        with store.transaction(lifecycle_context(campaign_id)) as tx:
            assert_no_pending(tx)
        shutil.rmtree(directory)
