"""Shared transaction contract and versioned, portable workflow records."""

from dataclasses import asdict
from typing import ContextManager, Protocol, TYPE_CHECKING

from core.request_context import CampaignContext
from .types import DraftInput, DraftSnapshot, WorkflowError, WorkflowReceipt

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class WorkflowTransaction(Protocol):
    session: 'Session | None'

    def fresh_context(self) -> CampaignContext: ...
    def get_draft(self, draft_id: str) -> DraftSnapshot | None: ...
    def list_drafts(self, author_id: str | None) -> list[DraftSnapshot]: ...
    def put_draft(self, draft: DraftSnapshot) -> None: ...
    def delete_draft(self, draft_id: str) -> None: ...
    def get_receipt(self, author_id: str, operation: str, key_hash: str) -> WorkflowReceipt | None: ...
    def list_receipts(self) -> list[WorkflowReceipt]: ...
    def put_receipt(self, receipt: WorkflowReceipt) -> None: ...


class WorkflowStore(Protocol):
    def transaction(self, context: CampaignContext) -> ContextManager[WorkflowTransaction]: ...


RECEIPT_STATES = {
    'create_draft': {'committed', 'invalidated'},
    'publish': {'publishing', 'committed', 'failed', 'invalidated'},
    'discard': {'committed'},
    'invalidate_member': {'committed'},
    'invalidate_target': {'committed'},
}


def check_scope(record, campaign_id):
    if record.campaign_id != campaign_id:
        raise WorkflowError('workflow_scope_mismatch', 'Workflow campaign does not match.', 409)
    if isinstance(record, WorkflowReceipt):
        if record.state not in RECEIPT_STATES.get(record.operation, ()):
            raise WorkflowError('invalid_workflow_receipt', 'Invalid workflow receipt state.', 422)


def encode_draft(draft: DraftSnapshot) -> dict:
    return {'workflow_version': 1, **asdict(draft)}


def decode_draft(payload: dict) -> DraftSnapshot:
    try:
        values = dict(payload)
        if values.pop('workflow_version') != 1:
            raise ValueError('unknown version')
        if values['inputs'] is not None:
            values['inputs'] = DraftInput(**values['inputs'])
        return DraftSnapshot(**values)
    except (KeyError, TypeError, ValueError):
        raise WorkflowError('workflow_storage_unavailable', 'Draft storage is unavailable.', 503) from None


def encode_receipt(receipt: WorkflowReceipt) -> dict:
    return {'workflow_version': 1, **asdict(receipt)}


def decode_receipt(payload: dict) -> WorkflowReceipt:
    try:
        values = dict(payload)
        if values.pop('workflow_version') != 1:
            raise ValueError('unknown version')
        result = WorkflowReceipt(**values)
        check_scope(result, result.campaign_id)
        return result
    except (KeyError, TypeError, ValueError):
        raise WorkflowError('workflow_storage_unavailable', 'Receipt storage is unavailable.', 503) from None
