"""Private durable JSON lifecycle records, outside portable campaign archives.

The fsynced redo journal is the commit point. Interrupted application is replayed
under the campaign-store lock before any subsequent read can observe records.
"""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path

from core import auth, campaigns, storage
from core.request_context import Principal, PrincipalKind, resolve_campaign_context
from .store import check_scope, decode_draft, decode_receipt, encode_draft, encode_receipt
from .types import WorkflowError


def _unavailable():
    return WorkflowError('workflow_storage_unavailable', 'Private draft storage is unavailable.', 503)


def _safe(path):
    path = Path(path).absolute()
    if path.resolve() != path:
        raise _unavailable()
    return path


def _read(path):
    try:
        return json.loads(_safe(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, RecursionError):
        raise _unavailable() from None


def _id(value):
    try:
        return storage._check_id(value)
    except ValueError:
        raise WorkflowError('draft_not_found', 'Draft not found.', 404) from None


def _sync_directory(path):
    if os.name == 'nt':
        return
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        # Not all volume drivers support directory fsync; files are fsynced.
        pass


class JsonWorkflowStore:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    @contextmanager
    def transaction(self, context):
        with campaigns._CAMPAIGN_STORE_LOCK:
            try:
                directory = _safe(self.root / _id(context.campaign_id))
                _safe(self.root).mkdir(mode=0o700, parents=True, exist_ok=True)
                directory.mkdir(mode=0o700, exist_ok=True)
                tx = JsonWorkflowTransaction(directory, context)
                tx.recover()
                yield tx
                tx.commit()
            except OSError:
                raise _unavailable() from None


class JsonWorkflowTransaction:
    session = None

    def __init__(self, directory, context):
        self.directory = directory
        self.context = context
        self.changes = {}

    def fresh_context(self):
        principal = self.context.principal
        if principal.kind is PrincipalKind.USER:
            try:
                users = auth._read_users_document() or {'users': {}}
            except auth.AccountStoreUnavailable:
                raise _unavailable() from None
            user = users['users'].get(principal.user_id)
            principal = (Principal.user(user['id'], is_admin=bool(user.get('is_admin')))
                         if user else Principal.anonymous())
        campaign = _read(storage.campaign_file(self.context.campaign_id))
        if campaign and campaign.get('_trashed_at'):
            campaign = None
        return resolve_campaign_context(principal, campaign_id=self.context.campaign_id,
            campaign=campaign, live_campaign_id=storage.get_live_campaign_id())

    def _path(self, kind, author_id, record_id):
        author_id, record_id = _id(author_id), _id(record_id)
        parent = self.directory if kind == 'draft' else self.directory / '_receipts'
        return _safe(parent / author_id / (record_id + '.json'))

    def _records(self, kind, author_id=None):
        parent = self.directory if kind == 'draft' else self.directory / '_receipts'
        pattern = (_id(author_id) if author_id is not None else '*') + '/*.json'
        records = {}
        for path in _safe(parent).glob(pattern):
            if kind == 'draft' and path.parent.name == '_receipts':
                continue
            payload = _read(path)
            if payload is None:
                continue
            record = decode_draft(payload) if kind == 'draft' else decode_receipt(payload)
            check_scope(record, self.context.campaign_id)
            if self._path(kind, record.author_id, record.id) != path:
                raise _unavailable()
            records[record.id] = record
        for (changed_kind, record_id), record in self.changes.items():
            if changed_kind == kind:
                records.pop(record_id, None)
                if record is not None and (author_id is None or record.author_id == author_id):
                    records[record_id] = deepcopy(record)
        return list(records.values())

    def get_draft(self, draft_id):
        _id(draft_id)
        matches = [d for d in self._records('draft') if d.id == draft_id]
        return matches[0] if matches else None

    def list_drafts(self, author_id):
        return self._records('draft', author_id)

    def put_draft(self, draft):
        check_scope(draft, self.context.campaign_id)
        self._path('draft', draft.author_id, draft.id)
        self.changes['draft', draft.id] = deepcopy(draft)

    def delete_draft(self, draft_id):
        draft = self.get_draft(draft_id)
        if draft is not None:
            self.changes['delete', draft.id] = draft
            self.changes['draft', draft.id] = None

    def get_receipt(self, author_id, operation, key_hash):
        matches = [r for r in self._records('receipt', author_id)
                   if r.operation == operation and r.key_hash == key_hash]
        if len(matches) > 1:
            raise _unavailable()
        return matches[0] if matches else None

    def list_receipts(self):
        return self._records('receipt')

    def put_receipt(self, receipt):
        check_scope(receipt, self.context.campaign_id)
        self._path('receipt', receipt.author_id, receipt.id)
        prior = self.get_receipt(receipt.author_id, receipt.operation, receipt.key_hash)
        if prior and prior.id != receipt.id:
            raise WorkflowError('request_key_conflict', 'Request key was already used.', 409)
        self.changes['receipt', receipt.id] = deepcopy(receipt)

    def recover(self):
        journal = _read(self.directory / '_journal.json')
        if journal is not None:
            self._apply(journal)

    def _apply(self, journal):
        if (not isinstance(journal, dict) or journal.get('workflow_version') != 1
                or not isinstance(journal.get('operations'), list)):
            raise _unavailable()
        operations = []
        # Validate every operation before changing any file. Paths are derived
        # from scoped IDs, never accepted as journal-provided filenames.
        for entry in journal['operations']:
            if not isinstance(entry, dict) or 'kind' not in entry or 'record' not in entry:
                raise _unavailable()
            kind = entry['kind']
            if kind not in ('draft', 'receipt', 'delete'):
                raise _unavailable()
            record = decode_receipt(entry['record']) if kind == 'receipt' else decode_draft(entry['record'])
            check_scope(record, self.context.campaign_id)
            operations.append((kind, record, self._path('receipt' if kind == 'receipt' else 'draft',
                                                       record.author_id, record.id)))
        for kind, record, path in operations:
            if kind == 'delete':
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                payload = encode_receipt(record) if kind == 'receipt' else encode_draft(record)
                storage.atomic_write_json(str(path), payload)
            _sync_directory(path.parent)
        (self.directory / '_journal.json').unlink()
        _sync_directory(self.directory)

    def commit(self):
        if not self.changes:
            return
        entries = []
        for (kind, _), record in self.changes.items():
            if record is not None:
                payload = encode_receipt(record) if kind == 'receipt' else encode_draft(record)
                entries.append({'kind': kind, 'record': payload})
        journal = {'workflow_version': 1, 'operations': entries}
        storage.atomic_write_json(str(self.directory / '_journal.json'), journal)
        _sync_directory(self.directory)
        try:
            self._apply(journal)
        finally:
            from .recovery import invalidate_pending_index
            invalidate_pending_index(self.context.campaign_id)
