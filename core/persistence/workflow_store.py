"""Session-bound SQL implementation of the private draft/receipt contract."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from core import storage
from core.request_context import Principal, PrincipalKind, resolve_campaign_context
from core.character_workflows.store import check_scope, decode_draft, encode_draft
from core.character_workflows.types import WorkflowError, WorkflowReceipt
from .campaign_runtime import _document
from .models import Campaign, CharacterWorkflowReceipt, Draft, User


class SqlWorkflowStore:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def transaction(self, context):
        # The row lock is the cross-process boundary on PostgreSQL. Reuse the
        # process lock for SQLite's development mode and JSON lifecycle hooks.
        from core.campaigns import _CAMPAIGN_STORE_LOCK
        with _CAMPAIGN_STORE_LOCK:
            tx = None
            try:
                with self.database.transaction() as session:
                    campaign = session.scalar(select(Campaign).where(
                        Campaign.id == context.campaign_id).with_for_update())
                    tx = SqlWorkflowTransaction(session, context, campaign)
                    yield tx
            except SQLAlchemyError:
                raise WorkflowError('workflow_storage_unavailable',
                                    'Private draft storage is unavailable.', 503) from None
            finally:
                if tx is not None and tx.receipts_changed:
                    from core.character_workflows.recovery import invalidate_pending_index
                    invalidate_pending_index(context.campaign_id)


class SqlWorkflowTransaction:
    def __init__(self, session, context, campaign):
        self.session = session
        self.context = context
        self.campaign = campaign
        self.receipts_changed = False

    def fresh_context(self):
        principal = self.context.principal
        if principal.kind is PrincipalKind.USER:
            user = self.session.get(User, principal.user_id, populate_existing=True)
            principal = (Principal.user(user.id, is_admin=user.is_admin)
                         if user else Principal.anonymous())
        campaign = self.campaign
        document = _document(self.session, campaign) if campaign and campaign.trashed_at is None else None
        return resolve_campaign_context(principal, campaign_id=self.context.campaign_id,
            campaign=document, live_campaign_id=storage.get_live_campaign_id())

    def _draft_row(self, draft_id):
        return self.session.scalar(select(Draft).where(
            Draft.campaign_id == self.context.campaign_id, Draft.id == draft_id))

    def _snapshot(self, row):
        if not row or 'workflow_version' not in row.payload:
            return None  # Pre-PR5 history is not a resumable personal draft.
        payload = deepcopy(row.payload)
        payload.update(id=row.id, campaign_id=row.campaign_id, author_id=row.user_id,
                       target_id=row.character_id, revision=row.revision, state=row.state)
        return decode_draft(payload)

    def get_draft(self, draft_id):
        return self._snapshot(self._draft_row(draft_id))

    def list_drafts(self, author_id):
        query = select(Draft).where(Draft.campaign_id == self.context.campaign_id)
        if author_id is not None:
            query = query.where(Draft.user_id == author_id)
        rows = self.session.scalars(query)
        return [draft for row in rows if (draft := self._snapshot(row)) is not None]

    def put_draft(self, draft):
        check_scope(draft, self.context.campaign_id)
        row = self._draft_row(draft.id)
        if row is None:
            row = Draft(id=draft.id, campaign_id=draft.campaign_id, user_id=draft.author_id)
            self.session.add(row)
        elif row.user_id != draft.author_id:
            raise WorkflowError('workflow_scope_mismatch', 'Draft author does not match.', 409)
        row.character_id = draft.target_id
        row.kind = draft.inputs.kind if draft.inputs else draft.system + '_builder'
        row.state, row.revision = draft.state, draft.revision
        row.payload = encode_draft(draft)
        row.created_at = datetime.fromisoformat(draft.created_at)
        row.updated_at = datetime.fromisoformat(draft.updated_at)
        row.expires_at = None
        self.session.flush()

    def delete_draft(self, draft_id):
        row = self._draft_row(draft_id)
        if row is not None:
            self.session.delete(row)
            self.session.flush()

    @staticmethod
    def _receipt(row):
        if row is None:
            return None
        return WorkflowReceipt(row.id, row.campaign_id, row.author_id, row.operation,
            row.key_hash, row.request_digest, row.draft_id, row.target_id, row.state,
            deepcopy(row.details))

    def get_receipt(self, author_id, operation, key_hash):
        row = self.session.scalar(select(CharacterWorkflowReceipt).where(
            CharacterWorkflowReceipt.campaign_id == self.context.campaign_id,
            CharacterWorkflowReceipt.author_id == author_id,
            CharacterWorkflowReceipt.operation == operation,
            CharacterWorkflowReceipt.key_hash == key_hash))
        return self._receipt(row)

    def list_receipts(self):
        return [self._receipt(row) for row in self.session.scalars(
            select(CharacterWorkflowReceipt).where(
                CharacterWorkflowReceipt.campaign_id == self.context.campaign_id))]

    def put_receipt(self, receipt):
        self.receipts_changed = True
        check_scope(receipt, self.context.campaign_id)
        row = self.session.get(CharacterWorkflowReceipt, receipt.id)
        if row is not None and (row.campaign_id, row.author_id, row.operation, row.key_hash) != (
                receipt.campaign_id, receipt.author_id, receipt.operation, receipt.key_hash):
            raise WorkflowError('workflow_scope_mismatch', 'Receipt identity does not match.', 409)
        if row is None:
            row = CharacterWorkflowReceipt(id=receipt.id, campaign_id=receipt.campaign_id,
                author_id=receipt.author_id, operation=receipt.operation, key_hash=receipt.key_hash)
            self.session.add(row)
        row.draft_id, row.target_id = receipt.draft_id, receipt.target_id
        row.request_digest, row.state = receipt.request_digest, receipt.state
        row.details = deepcopy(receipt.metadata)
        self.session.flush()
