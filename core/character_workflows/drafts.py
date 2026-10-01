"""Author-private drafts with CAS, quotas, and durable request deduplication."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4

from core.request_context import PrincipalKind
from .capabilities import capabilities_for
from .identity import resolve_character
from .types import DraftInput, DraftSnapshot, WorkflowError, WorkflowReceipt

MAX_INPUT_BYTES = 2_097_152
MAX_ACTIVE_DRAFTS = 20


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise WorkflowError('invalid_draft_input', 'Draft inputs must be valid JSON.', 422) from None
    return sha256(raw).hexdigest()


def key_hash(request_key):
    if not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 256:
        raise WorkflowError('invalid_request_key', 'A request key is required.', 422)
    try:
        return sha256(request_key.encode('utf-8')).hexdigest()
    except UnicodeError:
        raise WorkflowError('invalid_request_key', 'Invalid request key.', 422) from None


def validate_inputs(inputs, system):
    if (not isinstance(inputs, DraftInput) or type(inputs.payload_version) is not int
            or inputs.payload_version != 1 or system not in ('pf2e', 'cosmere')
            or inputs.kind not in (f'{system}_builder', f'{system}_import')
            or any(not isinstance(v, dict) for v in (inputs.form, inputs.ui, inputs.submission))):
        raise WorkflowError('invalid_draft_input', 'Unsupported draft format or system.', 422)
    try:
        encoded = json.dumps(asdict(inputs), ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError):
        raise WorkflowError('invalid_draft_input', 'Draft inputs must be valid JSON.', 422) from None
    if len(encoded) > MAX_INPUT_BYTES:
        raise WorkflowError('draft_too_large', 'Draft inputs exceed the 2 MiB limit.', 413)
    # Incomplete choices are intentional. Rules and required fields run only on
    # publication; saves must preserve unfinished, invalid-but-editable forms.
    return deepcopy(inputs)


def authorize_personal(context, *, hide=False):
    if (context.principal.kind is not PrincipalKind.USER or not context.is_member
            or not context.campaign_exists):
        if hide:
            raise WorkflowError('draft_not_found', 'Draft not found.', 404)
        raise WorkflowError('membership_required', 'Campaign membership is required.', 403)
    if not context.is_live:
        raise WorkflowError('campaign_not_live', 'Switch to the live campaign to work on drafts.', 409)
    if context.system not in ('pf2e', 'cosmere'):
        raise WorkflowError('unsupported_system', 'This campaign system is unsupported.', 422)


def owned_draft(tx, context, draft_id):
    authorize_personal(context, hide=True)
    if not isinstance(draft_id, str) or not draft_id or len(draft_id) > 64:
        raise WorkflowError('draft_not_found', 'Draft not found.', 404)
    draft = tx.get_draft(draft_id)
    if draft is None or draft.author_id != context.principal.user_id:
        raise WorkflowError('draft_not_found', 'Draft not found.', 404)
    if draft.system != context.system:
        raise WorkflowError('draft_system_changed', 'The campaign system changed.', 409)
    return require_supported_draft(draft)


def require_supported_draft(draft):
    """Older code must not reinterpret or erase a newer saved input format."""
    if draft.inputs is not None:
        validate_inputs(draft.inputs, draft.system)
    elif draft.state in ('active', 'publishing'):
        raise WorkflowError('invalid_draft_input', 'The saved draft format is unsupported.', 422)
    return draft


def require_revision(draft, revision):
    if type(revision) is not int or revision < 0:
        raise WorkflowError('invalid_revision', 'Expected revision must be a nonnegative integer.', 422)
    if draft.revision != revision:
        raise WorkflowError('draft_revision_conflict', 'The draft revision changed. Reload before saving.', 409)


def require_active(draft):
    if draft.state != 'active':
        raise WorkflowError('draft_not_active', 'This draft is no longer editable.', 409)


class DraftService:
    def __init__(self, store, adapters):
        self.store, self.adapters = store, adapters

    def create(self, context, inputs, *, request_key, target_id=None):
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            return self._create(tx, fresh, inputs, request_key=request_key, target_id=target_id)

    def _create(self, tx, context, inputs, *, request_key, target_id=None, source=None):
        authorize_personal(context)
        inputs = validate_inputs(inputs, context.system)
        hashed = key_hash(request_key)
        request_digest = digest({'inputs': asdict(inputs), 'target_id': target_id,
                                 'source_id': source.id if source else None})
        prior = tx.get_receipt(context.principal.user_id, 'create_draft', hashed)
        if prior is not None:
            if prior.state == 'invalidated':
                raise WorkflowError('draft_not_found', 'Draft not found.', 404)
            if prior.request_digest != request_digest:
                raise WorkflowError('request_key_conflict', 'This request key belongs to different inputs.', 409)
            existing = tx.get_draft(prior.draft_id) if prior.draft_id else None
            if (prior.state != 'committed' or existing is None
                    or existing.author_id != context.principal.user_id
                    or existing.state not in ('active', 'publishing', 'committed')):
                raise WorkflowError('draft_invalidated', 'This earlier draft is no longer available.', 409)
            return require_supported_draft(existing)
        target = None
        if target_id is not None:
            if not isinstance(target_id, str) or not target_id:
                raise WorkflowError('character_not_found', 'Character not found.', 404)
            target = resolve_character(context.campaign_id, target_id, session=tx.session)
            if not capabilities_for(context, target).edit:
                raise WorkflowError('character_not_found', 'Character not found.', 404)
            if inputs.kind == 'pf2e_builder':
                raise WorkflowError('unsupported_update', 'Use import to update this character.', 422)
        active = [d for d in tx.list_drafts(context.principal.user_id)
                  if d.state in ('active', 'publishing')]
        if len(active) >= MAX_ACTIVE_DRAFTS:
            raise WorkflowError('draft_quota', 'Discard a draft before creating another (limit 20).', 429)
        fingerprint = (source.base_fingerprint if source else
                       self.adapters[context.system].fingerprint(inputs.kind, target.document) if target else None)
        now = timestamp()
        draft = DraftSnapshot(uuid4().hex, context.campaign_id, context.principal.user_id,
            context.system, inputs, 'active', 0, target_id, fingerprint, now, now, None)
        receipt = WorkflowReceipt(uuid4().hex, context.campaign_id, context.principal.user_id,
            'create_draft', hashed, request_digest, draft.id, target_id, 'committed', {})
        tx.put_draft(draft)
        tx.put_receipt(receipt)
        return draft

    def prepare_import(self, context, content, filename, *, request_key, target_id=None):
        # Check before running a potentially expensive PDF parser; create then
        # refreshes authority again at the actual persistence boundary.
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            authorize_personal(fresh)
        inputs = self.adapters[fresh.system].parse_import(content, filename)
        return self.create(context, inputs, request_key=request_key, target_id=target_id)

    def get(self, context, draft_id):
        with self.store.transaction(context) as tx:
            draft = owned_draft(tx, tx.fresh_context(), draft_id)
            if draft.state == 'discarded':
                raise WorkflowError('draft_not_found', 'Draft not found.', 404)
            return draft

    def list(self, context):
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            authorize_personal(fresh)
            return sorted((require_supported_draft(d) for d in tx.list_drafts(fresh.principal.user_id)
                           if d.system == fresh.system and d.state in ('active', 'publishing')),
                          key=lambda d: (d.updated_at, d.id), reverse=True)

    def save(self, context, draft_id, inputs, *, expected_revision):
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            draft = owned_draft(tx, fresh, draft_id)
            require_revision(draft, expected_revision)
            require_active(draft)
            inputs = validate_inputs(inputs, fresh.system)
            if inputs.kind != draft.inputs.kind:
                raise WorkflowError('draft_kind_changed', 'Draft type cannot change.', 422)
            updated = replace(draft, inputs=inputs, revision=draft.revision + 1, updated_at=timestamp())
            tx.put_draft(updated)
            return updated

    def discard(self, context, draft_id, *, expected_revision):
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            draft = owned_draft(tx, fresh, draft_id)
            require_revision(draft, expected_revision)
            require_active(draft)
            tx.put_draft(replace(draft, inputs=None, state='discarded',
                                 revision=draft.revision + 1, updated_at=timestamp()))
            tx.put_receipt(WorkflowReceipt(uuid4().hex, draft.campaign_id, draft.author_id,
                'discard', key_hash(f'{draft.id}:{draft.revision}'),
                digest({'draft_id': draft.id, 'revision': draft.revision}),
                draft.id, draft.target_id, 'committed', {}))

    def copy(self, context, draft_id, inputs, *, request_key):
        with self.store.transaction(context) as tx:
            fresh = tx.fresh_context()
            draft = owned_draft(tx, fresh, draft_id)
            require_active(draft)
            if not isinstance(inputs, DraftInput) or inputs.kind != draft.inputs.kind:
                raise WorkflowError('draft_kind_changed', 'Draft type cannot change.', 422)
            return self._create(tx, fresh, inputs, request_key=request_key,
                                target_id=draft.target_id, source=draft)
